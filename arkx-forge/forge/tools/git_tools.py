"""Read-mostly git integration. Forge commits only when a human asks; it never pushes."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(root: Path, *args: str, timeout: int = 30) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, "", "git timed out"


def is_repo(root: Path) -> bool:
    code, out, _ = _git(root, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == "true"


def current_branch(root: Path) -> str | None:
    code, out, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if code == 0 else None


def status(root: Path) -> list[dict]:
    """Porcelain status as structured rows."""
    code, out, _ = _git(root, "status", "--porcelain=v1")
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        if len(line) < 3:
            continue
        x, y, path = line[0], line[1], line[3:]
        rows.append({
            "index": x.strip(), "worktree": y.strip(), "path": path,
            "staged": x not in " ?", "untracked": x == "?" and y == "?",
        })
    return rows


def diff(root: Path, path: str | None = None, staged: bool = False,
         max_chars: int = 200_000) -> str:
    args = ["diff"]
    if staged:
        args.append("--cached")
    args.append("--no-color")
    if path:
        args += ["--", path]
    code, out, err = _git(root, *args, timeout=60)
    if code != 0:
        return f"<git diff failed: {err.strip()}>"
    untracked = ""
    if not staged and not path:
        names = [r["path"] for r in status(root) if r["untracked"]]
        if names:
            untracked = "\n\n--- untracked files (not in diff) ---\n" + "\n".join(names)
    text = out + untracked
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n<diff truncated at {max_chars} chars>"
    return text or "<no changes>"


def diff_stat(root: Path) -> str:
    code, out, _ = _git(root, "diff", "--stat", "--no-color")
    return out.strip() if code == 0 else ""


def recent_commits(root: Path, limit: int = 12) -> list[dict]:
    fmt = "%H%x1f%h%x1f%an%x1f%ar%x1f%s"
    code, out, _ = _git(root, "log", f"-{limit}", f"--pretty=format:{fmt}")
    if code != 0:
        return []
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 5:
            commits.append(dict(zip(("sha", "short", "author", "when", "subject"), parts)))
    return commits


def snapshot(root: Path) -> dict:
    """Everything the UI's git panel needs in one call."""
    if not is_repo(root):
        return {"is_repo": False, "branch": None, "status": [], "commits": [],
                "stat": "", "dirty": False}
    st = status(root)
    return {
        "is_repo": True,
        "branch": current_branch(root),
        "status": st,
        "commits": recent_commits(root),
        "stat": diff_stat(root),
        "dirty": bool(st),
    }


def stage_and_commit(root: Path, message: str, paths: list[str] | None = None) -> dict:
    """Stage the given paths (or everything) and commit. Never pushes."""
    if not is_repo(root):
        return {"ok": False, "error": "not a git repository"}
    if not message.strip():
        return {"ok": False, "error": "commit message is empty"}
    add_args = ["add", "--"] + (paths if paths else ["."])
    code, _, err = _git(root, *add_args)
    if code != 0:
        return {"ok": False, "error": f"git add failed: {err.strip()}"}
    code, out, err = _git(root, "commit", "-m", message)
    if code != 0:
        return {"ok": False, "error": (err or out).strip() or "git commit failed"}
    _, sha, _ = _git(root, "rev-parse", "--short", "HEAD")
    return {"ok": True, "sha": sha.strip(), "output": out.strip()}
