"""Live-server tests: a real uvicorn process, the real SSE stream, the real CLI.

Uses the mock provider, so no network and no credentials are needed.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    home = tmp_path_factory.mktemp("forge-home")
    port = free_port()
    env = {**os.environ, "FORGE_HOME": str(home), "FORGE_PORT": str(port),
           "FORGE_PROVIDER": "mock", "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
    env.pop("ANTHROPIC_API_KEY", None)
    proc = subprocess.Popen([sys.executable, "-m", "forge", "serve"], cwd=str(ROOT),
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if proc.poll() is not None:
            raise AssertionError(f"server died:\n{proc.stdout.read()}")
        try:
            if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        raise AssertionError("server did not become healthy")
    yield {"base": base, "env": env, "proc": proc, "home": home}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def project(server, tmp_path_factory):
    root = tmp_path_factory.mktemp("live-repo") / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n")
    for args in (["init", "-q", "-b", "main"], ["add", "."],
                 ["-c", "user.email=t@e.st", "-c", "user.name=T", "commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    r = httpx.post(server["base"] + "/api/projects", json={"path": str(root)}, timeout=20)
    r.raise_for_status()
    return r.json()


def cli(server, *args, stdin: str | None = None, timeout=90):
    return subprocess.run([sys.executable, "-m", "forge", *args], cwd=str(ROOT),
                          env=server["env"], capture_output=True, text=True,
                          input=stdin, timeout=timeout)


# --- the real event stream ------------------------------------------------
def test_sse_stream_delivers_live_task_events(server, project):
    base = server["base"]
    kinds, stages, messages = set(), [], []
    with httpx.Client(timeout=90) as client:
        r = client.post(base + "/api/tasks",
                        json={"project_id": project["id"], "request": "stream this task"})
        task_id = r.json()["task_id"]
        with client.stream("GET", base + "/api/stream",
                           params={"task_id": task_id}) as stream:
            assert stream.status_code == 200
            assert "text/event-stream" in stream.headers["content-type"]
            for line in stream.iter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                assert ev["task_id"] == task_id
                kinds.add(ev["kind"])
                messages.append(ev["message"])
                if ev["kind"] == "stage" and (ev.get("data") or {}).get("state") == "start":
                    stages.append(ev["stage"])
                if ev["kind"] == "done":
                    break

    assert {"status", "stage", "tool", "command", "file", "done"} <= kinds
    assert stages == ["ANALYSIS", "PLAN", "IMPLEMENT", "TEST", "REVIEW", "SUMMARY"]
    assert any(m.startswith("PLAN CREATED") for m in messages)
    assert any(m.startswith("$ echo mock-provider check") for m in messages)
    assert any("REVIEW COMPLETE" in m for m in messages)

    task = httpx.get(f"{base}/api/tasks/{task_id}", timeout=20).json()
    assert task["status"] == "completed"
    assert (Path(project["path"]) / "FORGE_MOCK_NOTE.md").exists()


def test_stream_survives_reconnect(server, project):
    """A client may disconnect and resubscribe; the backlog replays."""
    base = server["base"]
    with httpx.Client(timeout=30) as client:
        with client.stream("GET", base + "/api/stream") as s1:
            for line in s1.iter_lines():
                if line.startswith("data: "):
                    break
        with client.stream("GET", base + "/api/stream") as s2:
            seen = False
            for line in s2.iter_lines():
                if line.startswith("data: "):
                    seen = True
                    break
            assert seen


# --- the CLI against the live server --------------------------------------
def test_cli_status_and_project_commands(server, project):
    out = cli(server, "project", "add", project["path"])
    assert out.returncode == 0 and "registered" in out.stdout

    listing = cli(server, "project")
    assert project["name"] in listing.stdout and "main" in listing.stdout

    status = cli(server, "status")
    assert status.returncode == 0
    assert "ARKX FORGE" in status.stdout and "branch     main" in status.stdout
    # The mock provider must be named as such, never dressed up as a Claude model.
    assert "mock-provider" in status.stdout and "provider=mock" in status.stdout

    agents = cli(server, "agents")
    for role in ("BUILDER", "DEBUGGER", "REVIEWER", "ARCHITECT", "RESEARCHER", "TESTER"):
        assert role in agents.stdout


def test_cli_task_streams_and_reports(server, project):
    cli(server, "project", "add", project["path"])
    out = cli(server, "task", "add a note file")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "ANALYSIS" in out.stdout and "PLAN CREATED" in out.stdout
    assert "=== SUMMARY ===" in out.stdout
    assert "FILES CHANGED" in out.stdout and "FORGE_MOCK_NOTE.md" in out.stdout

    hist = cli(server, "history", "-n", "5")
    assert "COMPLETED" in hist.stdout

    review = cli(server, "review")
    assert "--- REVIEW ---" in review.stdout and "--- SUMMARY ---" in review.stdout

    tests = cli(server, "test", "-v")
    assert "test status:" in tests.stdout and "exit 0" in tests.stdout

    diff = cli(server, "diff")
    assert "FORGE_MOCK_NOTE.md" in diff.stdout

    commit = cli(server, "commit", "-m", "add mock note")
    assert commit.returncode == 0 and "committed" in commit.stdout
    assert "does not push" in commit.stdout

    after = cli(server, "status")
    assert "clean" in after.stdout and "add mock note" in after.stdout


def test_cli_reports_when_server_is_down(tmp_path):
    env = {**os.environ, "FORGE_HOME": str(tmp_path), "FORGE_PORT": str(free_port()),
           "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-m", "forge", "status"], cwd=str(ROOT),
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "not running" in (out.stdout + out.stderr)
