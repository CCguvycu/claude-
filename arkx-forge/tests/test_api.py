"""End-to-end HTTP tests: the full stack with the mock provider, no network."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from forge.config import SETTINGS
from forge.db import Database
from forge.server.app import create_app


@pytest.fixture
def client(tmp_path, repo):
    settings = replace(SETTINGS, home=tmp_path / "home", provider="mock")
    settings.home.mkdir(parents=True, exist_ok=True)
    db = Database(settings.home / "forge.db")
    with TestClient(create_app(settings, db)) as c:
        c.repo = repo
        yield c


def add_project(client) -> dict:
    r = client.post("/api/projects", json={"path": str(client.repo)})
    assert r.status_code == 200, r.text
    return r.json()


def wait_for_task(client, task_id, timeout=30) -> dict:
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{task_id}").json()
        if t["status"] in ("completed", "failed", "cancelled"):
            return t
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} did not finish: {t['status']}")


# --- meta -----------------------------------------------------------------
def test_health_and_config(client):
    assert client.get("/api/health").json()["ok"] is True
    cfg = client.get("/api/config").json()
    assert cfg["provider"] == "mock" and len(cfg["agents"]) == 6
    # the UI must not be told a Claude model answered when the mock did
    assert cfg["model"] == "mock-provider (no Claude API call)"
    assert cfg["configured_model"] == SETTINGS.model
    assert {a["title"] for a in cfg["agents"]} == {
        "BUILDER", "DEBUGGER", "REVIEWER", "ARCHITECT", "RESEARCHER", "TESTER"}


def test_config_never_leaks_credentials(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    body = client.get("/api/config").text
    assert "sk-ant-secret-value" not in body
    assert "api_key" not in body
    for path in ("/api/projects", "/api/tasks", "/api/agents"):
        assert "sk-ant-secret-value" not in client.get(path).text


def test_index_and_static_served(client):
    assert "ARKX" in client.get("/").text
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/static/css/forge.css").status_code == 200


# --- projects -------------------------------------------------------------
def test_project_crud_and_git_snapshot(client):
    p = add_project(client)
    assert p["exists"] and p["git"]["branch"] == "main"
    assert p["git"]["commits"][0]["subject"] == "initial"

    listed = client.get("/api/projects").json()
    assert len(listed) == 1 and listed[0]["id"] == p["id"]

    again = add_project(client)          # re-adding is an upsert, not a duplicate
    assert again["id"] == p["id"] and len(client.get("/api/projects").json()) == 1

    assert client.get(f"/api/projects/{p['id']}/git").json()["branch"] == "main"
    assert client.delete(f"/api/projects/{p['id']}").json()["ok"]
    assert client.get("/api/projects").json() == []


def test_rejects_non_directory(client, tmp_path):
    f = tmp_path / "afile.txt"
    f.write_text("x")
    assert client.post("/api/projects", json={"path": str(f)}).status_code == 400
    assert client.post("/api/projects", json={"path": "/nope/nowhere"}).status_code == 400


def test_diff_and_commit_flow(client):
    p = add_project(client)
    assert client.get(f"/api/projects/{p['id']}/diff").json()["diff"] == "<no changes>"

    (client.repo / "src" / "app.py").write_text("def add(a, b):\n    return a + b + 1\n")
    diff = client.get(f"/api/projects/{p['id']}/diff").json()["diff"]
    assert "+++ b/src/app.py" in diff

    res = client.post(f"/api/projects/{p['id']}/commit", json={"message": "bump add()"})
    assert res.status_code == 200 and res.json()["sha"]
    assert client.get(f"/api/projects/{p['id']}/git").json()["commits"][0]["subject"] == "bump add()"
    assert client.post(f"/api/projects/{p['id']}/commit",
                       json={"message": "nothing to do"}).status_code == 400


def test_commit_requires_message(client):
    p = add_project(client)
    assert client.post(f"/api/projects/{p['id']}/commit", json={"message": ""}).status_code == 422


def test_unknown_ids_are_404(client):
    assert client.get("/api/projects/999").status_code == 404
    assert client.get("/api/tasks/999").status_code == 404
    assert client.post("/api/tasks",
                       json={"project_id": 999, "request": "a real request"}).status_code == 404


# --- task execution -------------------------------------------------------
def test_task_runs_end_to_end_and_persists_everything(client):
    p = add_project(client)
    r = client.post("/api/tasks", json={"project_id": p["id"],
                                        "request": "add a helper and test it"})
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    t = wait_for_task(client, task_id)
    assert t["status"] == "completed", t.get("error")
    stage_names = [s["name"] for s in t["stages"]]
    assert stage_names[:4] == ["ANALYSIS", "PLAN", "IMPLEMENT", "TEST"]
    assert "REVIEW" in stage_names and stage_names[-1] == "SUMMARY"
    assert all(s["status"] == "done" for s in t["stages"])

    # the mock provider writes one file and runs one command per tool-capable stage
    assert any(f["path"] == "FORGE_MOCK_NOTE.md" for f in t["files"])
    assert (client.repo / "FORGE_MOCK_NOTE.md").exists()
    assert t["commands"] and t["commands"][0]["exit_code"] == 0
    assert t["events"] and {"stage", "tool", "command", "file", "done"} <= {e["kind"] for e in t["events"]}
    assert t["summary"] and t["plan"] and t["review"]
    assert t["finished_at"] and t["model"].startswith("mock")

    # history reflects it
    hist = client.get(f"/api/tasks?project_id={p['id']}").json()
    assert hist[0]["id"] == task_id and hist[0]["status"] == "completed"


def test_second_task_on_same_project_is_rejected_while_running(client):
    p = add_project(client)
    first = client.post("/api/tasks", json={"project_id": p["id"], "request": "task one"}).json()
    second = client.post("/api/tasks", json={"project_id": p["id"], "request": "task two"})
    # Either the first is still running (409) or it already finished (200).
    assert second.status_code in (200, 409)
    wait_for_task(client, first["task_id"])


def test_task_request_validation(client):
    p = add_project(client)
    assert client.post("/api/tasks", json={"project_id": p["id"], "request": "x"}).status_code == 422


def test_cancel_unknown_task_conflicts(client):
    assert client.post("/api/tasks/424242/cancel").status_code == 409


def test_confirm_with_nothing_pending_conflicts(client):
    assert client.post("/api/tasks/1/confirm", json={"approve": True}).status_code == 409
    assert client.get("/api/pending").json() == []


def test_missing_credentials_blocks_real_provider(tmp_path, repo, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    settings = replace(SETTINGS, home=tmp_path / "h2", provider="anthropic")
    settings.home.mkdir(parents=True, exist_ok=True)
    db = Database(settings.home / "forge.db")
    with TestClient(create_app(settings, db)) as c:
        p = c.post("/api/projects", json={"path": str(repo)}).json()
        r = c.post("/api/tasks", json={"project_id": p["id"], "request": "do something"})
        assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_orphaned_running_tasks_are_marked_failed_on_restart(tmp_path, repo):
    settings = replace(SETTINGS, home=tmp_path / "h3", provider="mock")
    settings.home.mkdir(parents=True, exist_ok=True)
    db = Database(settings.home / "forge.db")
    project = db.upsert_project("p", str(repo))
    orphan = db.create_task(project["id"], "left running", "m", "main")
    db.update_task(orphan, status="running")
    with TestClient(create_app(settings, db)) as c:
        t = c.get(f"/api/tasks/{orphan}").json()
        assert t["status"] == "failed" and "restart" in t["error"]


# Note: the SSE stream is covered in tests/test_live_server.py against a real
# uvicorn server. Starlette's TestClient buffers a streaming response until the
# ASGI call returns, so it cannot exercise an open-ended event stream.


# --- event sequencing -----------------------------------------------------
def test_events_carry_monotonic_seq_for_replay_dedup(client):
    p = add_project(client)
    task_id = client.post("/api/tasks", json={"project_id": p["id"],
                                             "request": "sequence me"}).json()["task_id"]
    t = wait_for_task(client, task_id)
    seqs = [e["seq"] for e in t["events"]]
    assert all(s > 0 for s in seqs)
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_each_event_is_persisted_exactly_once(client):
    """Regression: a global event bus made a second app instance double-persist."""
    p = add_project(client)
    task_id = client.post("/api/tasks", json={"project_id": p["id"],
                                             "request": "persist me once"}).json()["task_id"]
    t = wait_for_task(client, task_id)
    seqs = [e["seq"] for e in t["events"]]
    assert len(seqs) == len(set(seqs)), "events were stored more than once"
    starts = [e for e in t["events"] if e["kind"] == "status" and "Task started" in e["message"]]
    assert len(starts) == 1


def test_importing_the_app_module_does_not_build_an_app():
    """Importing must not open a database or register a second event sink."""
    import importlib
    import forge.server.app as mod
    mod = importlib.reload(mod)
    assert "_APP" not in vars(mod)
    assert mod.app is mod.app                     # lazily built, then cached
    assert len(mod.app.state.bus._sinks) == 1


def test_two_apps_do_not_share_an_event_bus(tmp_path, repo):
    from dataclasses import replace as _replace
    s1 = _replace(SETTINGS, home=tmp_path / "a", provider="mock")
    s2 = _replace(SETTINGS, home=tmp_path / "b", provider="mock")
    for s in (s1, s2):
        s.home.mkdir(parents=True, exist_ok=True)
    a1 = create_app(s1, Database(s1.home / "f.db"))
    a2 = create_app(s2, Database(s2.home / "f.db"))
    assert a1.state.bus is not a2.state.bus
    assert len(a1.state.bus._sinks) == len(a2.state.bus._sinks) == 1


# --- the approval gate ----------------------------------------------------
@pytest.fixture
def guarded_client(tmp_path, repo):
    settings = replace(SETTINGS, home=tmp_path / "guard", provider="tests.guarded_provider:GuardedProvider")
    settings.home.mkdir(parents=True, exist_ok=True)
    db = Database(settings.home / "forge.db")
    with TestClient(create_app(settings, db)) as c:
        c.repo = repo
        yield c


def _drive_confirmations(client, task_id, approve: bool, timeout=60) -> dict:
    """Answer every approval request until the task reaches a terminal state.

    The guarded provider asks once per tool-capable stage, so a real session
    answers repeatedly — exactly what the UI does.
    """
    import time
    deadline = time.time() + timeout
    answered = 0
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{task_id}").json()
        if t["status"] in ("completed", "failed", "cancelled"):
            t["answered"] = answered
            return t
        if t.get("pending_confirm"):
            client.post(f"/api/tasks/{task_id}/confirm", json={"approve": approve})
            answered += 1
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} stuck in {t['status']}")


def _await_pending(client, timeout=20) -> dict:
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        pend = client.get("/api/pending").json()
        if pend:
            return pend[0]
        time.sleep(0.05)
    raise AssertionError("no confirmation was requested")


def test_guarded_command_waits_for_approval_then_runs(guarded_client):
    client = guarded_client
    (client.repo / "build").mkdir()
    p = add_project(client)
    task_id = client.post("/api/tasks", json={"project_id": p["id"],
                                             "request": "clean the build dir"}).json()["task_id"]
    pending = _await_pending(client)
    assert pending["command"] == "rm -rf build"
    assert pending["verdict"] == "confirm" and "deletes files" in pending["reason"]
    assert (client.repo / "build").exists()          # nothing ran while it waited

    t = _drive_confirmations(client, task_id, approve=True)
    assert t["status"] == "completed"
    assert not (client.repo / "build").exists()      # it ran only after approval
    assert any(c["confirmed"] for c in t["commands"])
    assert t["answered"] >= 1


def test_denied_command_never_runs_and_task_continues(guarded_client):
    client = guarded_client
    (client.repo / "build").mkdir()
    p = add_project(client)
    task_id = client.post("/api/tasks", json={"project_id": p["id"],
                                             "request": "clean the build dir"}).json()["task_id"]
    _await_pending(client)
    t = _drive_confirmations(client, task_id, approve=False)
    assert t["status"] == "completed"                # denial is not a failure
    assert (client.repo / "build").exists()
    denials = [e for e in t["events"] if e["kind"] == "command" and "DENIED" in e["message"]]
    assert denials


def test_cancelling_while_awaiting_approval_unblocks_the_task(guarded_client):
    client = guarded_client
    (client.repo / "build").mkdir()
    p = add_project(client)
    task_id = client.post("/api/tasks", json={"project_id": p["id"],
                                             "request": "clean the build dir"}).json()["task_id"]
    _await_pending(client)
    assert client.post(f"/api/tasks/{task_id}/cancel").json()["ok"]
    t = wait_for_task(client, task_id)
    assert t["status"] == "cancelled"
    assert (client.repo / "build").exists()


def test_custom_provider_is_labelled_as_such(guarded_client):
    cfg = guarded_client.get("/api/config").json()
    assert cfg["model"] == "tests.guarded_provider:GuardedProvider (custom provider)"


def test_unknown_provider_spec_is_rejected(tmp_path, repo):
    from forge.llm.factory import build_provider
    with pytest.raises(ValueError, match="unknown provider"):
        build_provider(replace(SETTINGS, provider="gpt-9"))
    with pytest.raises(ValueError, match="cannot load provider"):
        build_provider(replace(SETTINGS, provider="no.such.module:Thing"))


def test_file_changes_are_merged_per_path_but_the_log_is_kept(client):
    """The panel shows one row per file; the audit log keeps every operation."""
    p = add_project(client)
    task_id = client.post("/api/tasks", json={"project_id": p["id"],
                                             "request": "touch a file twice"}).json()["task_id"]
    t = wait_for_task(client, task_id)
    paths = [f["path"] for f in t["files"]]
    assert len(paths) == len(set(paths)), "the merged view must have one row per path"
    assert len(t["file_log"]) >= len(t["files"])          # nothing lost from the log
    note = next(f for f in t["files"] if f["path"] == "FORGE_MOCK_NOTE.md")
    assert note["action"] == "created"                     # created then edited stays created


def test_merge_marks_a_created_then_deleted_file_as_deleted(tmp_path, repo):
    from forge.db import Database as _Db
    db = _Db(tmp_path / "m.db")
    proj = db.upsert_project("p", str(repo))
    tid = db.create_task(proj["id"], "r", "m", "main")
    db.add_file_change(tid, "a.py", "created", None, 5, 0)
    db.add_file_change(tid, "a.py", "modified", None, 2, 1)
    db.add_file_change(tid, "a.py", "deleted", None, 0, 7)
    merged = db.merged_file_changes(tid)
    assert len(merged) == 1
    assert merged[0]["action"] == "deleted" and merged[0]["added"] == 7
