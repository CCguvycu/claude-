"""ARKX Forge command line. Talks to the local Forge backend over HTTP.

    forge                        start the server (foreground)
    forge project                list projects
    forge project add <path>     register a repository
    forge project use <id|path>  make it the CLI's active project
    forge task "..."             run a task and stream it in the terminal
    forge status                 server, project, git and running-task status
    forge history [-n N]         recent tasks
    forge review [task_id]       reviewer output for a task
    forge test [task_id]         test outcome and the commands that produced it
    forge diff                   uncommitted diff for the active project
    forge commit -m "msg"        create a commit (never pushes)
    forge cancel [task_id]       cancel a running task
    forge agents                 list agent roles
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

try:                      # anthropic 1.x ships httpx2; fall back to httpx 0.x
    import httpx2 as httpx
except ImportError:       # pragma: no cover
    import httpx

from .config import SETTINGS

STATE = SETTINGS.home / "cli.json"
C = {"dim": "\033[2m", "red": "\033[31m", "grn": "\033[32m", "yel": "\033[33m",
     "cyn": "\033[36m", "mag": "\033[35m", "bold": "\033[1m", "off": "\033[0m"}


def colour(key: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{C[key]}{text}{C['off']}"


def _state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=2))


def base() -> str:
    return SETTINGS.base_url()


def get(path: str, **params: Any) -> Any:
    r = httpx.get(base() + path, params=params or None, timeout=30)
    r.raise_for_status()
    return r.json()


def post(path: str, body: dict | None = None) -> Any:
    r = httpx.post(base() + path, json=body, timeout=60)
    if r.status_code >= 400:
        try:
            raise SystemExit(colour("red", f"error: {r.json().get('detail', r.text)}"))
        except json.JSONDecodeError:
            raise SystemExit(colour("red", f"error: {r.text}"))
    return r.json()


def require_server() -> None:
    try:
        get("/api/health")
    except (httpx.HTTPError, OSError):
        raise SystemExit(colour("red",
            f"Forge is not running at {base()}. Start it with `forge` "
            f"(or `python -m forge serve`)."))


def active_project() -> dict:
    require_server()
    projects = get("/api/projects")
    if not projects:
        raise SystemExit("No projects registered. Run: forge project add /path/to/repo")
    want = _state().get("project_id")
    for p in projects:
        if p["id"] == want:
            return p
    return projects[0]


# ------------------------------------------------------------------ commands
def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from .server.app import create_app
    if SETTINGS.provider == "anthropic" and not SETTINGS.api_key:
        print(colour("yel", "warning: ANTHROPIC_API_KEY is not set; task execution will be "
                            "refused until it is. Set it and restart, or use FORGE_PROVIDER=mock."))
    print(colour("bold", "ARKX FORGE") + f"  {base()}   model={SETTINGS.model} "
          f"provider={SETTINGS.provider}")
    uvicorn.run(create_app(), host=SETTINGS.host, port=SETTINGS.port,
                log_level=args.log_level, access_log=False)
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    require_server()
    if args.action == "add":
        p = post("/api/projects", {"path": str(Path(args.value).expanduser().resolve())})
        _save_state({**_state(), "project_id": p["id"]})
        print(colour("grn", f"registered #{p['id']} {p['name']} -> {p['path']}"))
        return 0
    if args.action == "use":
        projects = get("/api/projects")
        match = next((p for p in projects
                      if str(p["id"]) == args.value or p["path"] == args.value
                      or p["name"] == args.value), None)
        if not match:
            raise SystemExit(f"no registered project matches {args.value!r}")
        _save_state({**_state(), "project_id": match["id"]})
        print(colour("grn", f"active project: #{match['id']} {match['name']}"))
        return 0

    projects = get("/api/projects")
    if not projects:
        print("no projects registered")
        return 0
    act = active_project()
    for p in projects:
        mark = colour("cyn", "*") if p["id"] == act["id"] else " "
        branch = p.get("git", {}).get("branch") or "-"
        flag = "" if p["exists"] else colour("red", " MISSING")
        print(f"{mark} #{p['id']:<3} {p['name']:<24} {branch:<22} {p['path']}{flag}")
    return 0


def cmd_agents(_: argparse.Namespace) -> int:
    require_server()
    for a in get("/api/agents"):
        print(f"{colour('cyn', a['title']):<22} {a['purpose']}")
        print(f"{'':<14} tools: {', '.join(a['tools'])}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    require_server()
    cfg = get("/api/config")
    p = active_project()
    git = p.get("git") or {}
    print(colour("bold", "ARKX FORGE"), f"@ {base()}")
    print(f"  model      {cfg['model']} (effort={cfg['effort']}, provider={cfg['provider']})")
    print(f"  credentials{'' if cfg['credentials_present'] else colour('red', ' MISSING')}"
          f" {'present' if cfg['credentials_present'] else 'ANTHROPIC_API_KEY not set'}")
    print(f"  project    #{p['id']} {p['name']}  {p['path']}")
    print(f"  branch     {git.get('branch') or 'not a git repo'}")
    if git.get("is_repo"):
        print(f"  tree       {'dirty: ' + str(len(git['status'])) + ' paths' if git.get('dirty') else 'clean'}")
        for row in git.get("status", [])[:12]:
            print(f"    {row['index'] or ' '}{row['worktree'] or ' '} {row['path']}")
        for c in git.get("commits", [])[:5]:
            print(colour("dim", f"    {c['short']} {c['when']:<14} {c['subject'][:60]}"))
    running = p.get("running_task")
    print(f"  running    {('task #' + str(running)) if running else 'nothing'}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    p = active_project()
    tasks = get("/api/tasks", project_id=p["id"], limit=args.number)
    if not tasks:
        print("no tasks yet")
        return 0
    for t in tasks:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(t["created_at"]))
        st = {"completed": "grn", "failed": "red", "running": "yel",
              "cancelled": "dim"}.get(t["status"], "dim")
        label = colour(st, t["status"].upper())
        pad = " " * max(0, 10 - len(t["status"]))
        print(f"#{t['id']:<4} {when}  {label}{pad} {(t.get('test_status') or '-'):<8}"
              f" {t['request'][:62]}")
    return 0


def _resolve_task(task_id: int | None) -> dict:
    p = active_project()
    if task_id is None:
        tasks = get("/api/tasks", project_id=p["id"], limit=1)
        if not tasks:
            raise SystemExit("no tasks yet for this project")
        task_id = tasks[0]["id"]
    return get(f"/api/tasks/{task_id}")


def cmd_review(args: argparse.Namespace) -> int:
    t = _resolve_task(args.task_id)
    print(colour("bold", f"task #{t['id']} — {t['status']}"), f"  {t['request'][:80]}")
    print(colour("cyn", "\n--- REVIEW ---"))
    print(t.get("review") or "(no review recorded)")
    print(colour("cyn", "\n--- SUMMARY ---"))
    print(t.get("summary") or "(no summary recorded)")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    t = _resolve_task(args.task_id)
    status = t.get("test_status") or "unknown"
    hue = {"passed": "grn", "failed": "red"}.get(status, "yel")
    print(f"task #{t['id']}  test status: {colour(hue, status.upper())}")
    for c in t.get("commands", []):
        code = c["exit_code"]
        print(f"  {colour('grn' if code == 0 else 'red', f'exit {code}')}"
              f"  {c['duration_ms']:>6}ms  {c['command']}")
        if args.verbose and (c["stdout"] or c["stderr"]):
            for line in (c["stdout"] + c["stderr"]).splitlines()[-25:]:
                print(colour("dim", f"      {line}"))
    return 0


def cmd_diff(_: argparse.Namespace) -> int:
    p = active_project()
    print(get(f"/api/projects/{p['id']}/diff")["diff"])
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    p = active_project()
    res = post(f"/api/projects/{p['id']}/commit", {"message": args.message})
    print(colour("grn", f"committed {res['sha']}"), "— Forge does not push to remotes.")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    p = active_project()
    task_id = args.task_id or p.get("running_task")
    if not task_id:
        raise SystemExit("no running task for this project")
    post(f"/api/tasks/{task_id}/cancel")
    print(colour("yel", f"cancellation requested for task #{task_id}"))
    return 0


def _print_event(ev: dict) -> None:
    ts = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
    kind, msg, data = ev["kind"], ev["message"], ev.get("data") or {}
    if kind in ("assistant", "thinking"):
        return
    hue = {"stage": "yel", "tool": "cyn", "command": "mag", "file": "grn",
           "error": "red", "confirm": "mag", "done": "bold", "test": "grn",
           "review": "mag"}.get(kind, "dim")
    print(f"{colour('dim', '[' + ts + ']')} {colour(hue, msg)}", flush=True)
    if kind == "command" and data.get("running") is False:
        for stream_name in ("stdout", "stderr"):
            body = (data.get(stream_name) or "").strip()
            if body:
                for line in body.splitlines()[-15:]:
                    print(colour("dim", f"    {line}"), flush=True)


def cmd_task(args: argparse.Namespace) -> int:
    p = active_project()
    cfg = get("/api/config")
    if not cfg["credentials_present"]:
        raise SystemExit(colour("red", "ANTHROPIC_API_KEY is not set in the server's "
                                       "environment; the server will refuse the task."))
    started = post("/api/tasks", {"project_id": p["id"], "request": args.request})
    task_id = started["task_id"]
    print(colour("bold", f"task #{task_id}") + f" on {p['name']} ({cfg['model']})")
    if args.detach:
        print(f"detached; follow with: forge review {task_id}")
        return 0

    with httpx.Client(timeout=None) as client:
        with client.stream("GET", f"{base()}/api/stream", params={"task_id": task_id}) as r:
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if ev["task_id"] != task_id:
                    continue
                _print_event(ev)
                if ev["kind"] == "confirm" and (ev.get("data") or {}).get("resolved") is False:
                    cmd = ev["data"].get("command", "")
                    reason = ev["data"].get("reason", "")
                    print(colour("mag", f"\n  APPROVAL REQUIRED ({reason})"))
                    print(colour("bold", f"  $ {cmd}"))
                    answer = input("  approve? [y/N] ").strip().lower()
                    post(f"/api/tasks/{task_id}/confirm", {"approve": answer in ("y", "yes")})
                if ev["kind"] == "done":
                    break

    t = get(f"/api/tasks/{task_id}")
    print(colour("cyn", "\n=== SUMMARY ==="))
    print(t.get("summary") or t.get("error") or "(none)")
    changed = t.get("files") or []
    if changed:
        print(colour("cyn", "\n=== FILES CHANGED ==="))
        for f in changed:
            print(f"  {f['action']:<9} {f['path']}  +{f['added']}/-{f['removed']}")
    print(f"\ntest status: {t.get('test_status')}   `forge diff` to inspect, "
          f"`forge commit -m ...` to commit.")
    return 0 if t["status"] == "completed" else 1


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="forge", description="ARKX Forge — local AI development "
                                                          "command center")
    p.add_argument("--log-level", default="warning", help="uvicorn log level when serving")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="start the Forge server (default action)")
    s.add_argument("--log-level", default="warning")
    s.set_defaults(func=cmd_serve)

    pr = sub.add_parser("project", help="list / add / select projects")
    pr.add_argument("action", nargs="?", choices=["list", "add", "use"], default="list")
    pr.add_argument("value", nargs="?")
    pr.set_defaults(func=cmd_project)

    t = sub.add_parser("task", help="run a development task")
    t.add_argument("request")
    t.add_argument("--detach", action="store_true", help="do not stream; return immediately")
    t.set_defaults(func=cmd_task)

    sub.add_parser("status", help="server, project and git status").set_defaults(func=cmd_status)
    h = sub.add_parser("history", help="recent tasks")
    h.add_argument("-n", "--number", type=int, default=20)
    h.set_defaults(func=cmd_history)

    rv = sub.add_parser("review", help="review + summary for a task")
    rv.add_argument("task_id", nargs="?", type=int)
    rv.set_defaults(func=cmd_review)

    ts = sub.add_parser("test", help="test outcome for a task")
    ts.add_argument("task_id", nargs="?", type=int)
    ts.add_argument("-v", "--verbose", action="store_true")
    ts.set_defaults(func=cmd_test)

    sub.add_parser("diff", help="uncommitted diff").set_defaults(func=cmd_diff)
    cm = sub.add_parser("commit", help="create a commit (never pushes)")
    cm.add_argument("-m", "--message", required=True)
    cm.set_defaults(func=cmd_commit)

    cn = sub.add_parser("cancel", help="cancel a running task")
    cn.add_argument("task_id", nargs="?", type=int)
    cn.set_defaults(func=cmd_cancel)

    sub.add_parser("agents", help="list agent roles").set_defaults(func=cmd_agents)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        args.func = cmd_serve          # bare `forge` starts the server
        args.log_level = args.log_level
    try:
        return args.func(args)
    except httpx.HTTPStatusError as exc:
        print(colour("red", f"HTTP {exc.response.status_code}: {exc.response.text}"),
              file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
