"""Filesystem, shell and git tool behaviour."""
from pathlib import Path

import pytest

from forge.config import SETTINGS
from forge.tools import fs_tools, git_tools, shell_tools
from forge.tools.registry import ToolContext, definitions, dispatch, FULL, READ_ONLY


def ctx(repo: Path, **kw) -> ToolContext:
    return ToolContext(task_id=1, root=repo, settings=SETTINGS, stage="TEST", **kw)


# --- filesystem -----------------------------------------------------------
def test_read_write_edit_delete_cycle(repo):
    assert "def add" in fs_tools.read_file(repo, "src/app.py")

    created = fs_tools.write_file(repo, "src/new.py", "x = 1\ny = 2\n")
    assert (created.action, created.added) == ("created", 2)

    edited = fs_tools.edit_file(repo, "src/app.py", "a + b", "a + b + 0")
    assert edited.action == "modified" and edited.added == 1 and edited.removed == 1

    deleted = fs_tools.delete_file(repo, "src/new.py")
    assert deleted.action == "deleted" and not (repo / "src/new.py").exists()


def test_write_creates_parent_dirs(repo):
    fs_tools.write_file(repo, "a/b/c/deep.py", "pass\n")
    assert (repo / "a/b/c/deep.py").read_text() == "pass\n"


def test_edit_requires_unique_match(repo):
    (repo / "dup.py").write_text("v = 1\nv = 1\n")
    with pytest.raises(ValueError, match="appears 2 times"):
        fs_tools.edit_file(repo, "dup.py", "v = 1", "v = 2")
    edit = fs_tools.edit_file(repo, "dup.py", "v = 1", "v = 2", replace_all=True)
    assert edit.added == 2 and (repo / "dup.py").read_text() == "v = 2\nv = 2\n"


def test_edit_missing_text_raises(repo):
    with pytest.raises(ValueError, match="not found"):
        fs_tools.edit_file(repo, "src/app.py", "nonexistent", "x")


def test_read_missing_file_raises(repo):
    with pytest.raises(FileNotFoundError):
        fs_tools.read_file(repo, "nope.py")


def test_read_line_range_is_numbered(repo):
    (repo / "many.py").write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
    out = fs_tools.read_file(repo, "many.py", start_line=3, end_line=5)
    assert out.splitlines() == ["     3\tline3", "     4\tline4", "     5\tline5"]


def test_read_truncates_large_file(repo):
    (repo / "big.txt").write_text("a" * 5000)
    out = fs_tools.read_file(repo, "big.txt", max_bytes=100)
    assert out.startswith("a" * 100) and "truncated" in out


def test_binary_file_is_not_dumped(repo):
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    assert fs_tools.read_file(repo, "blob.bin").startswith("<binary file")


def test_grep_and_list_dir_skip_noise(repo):
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "junk.py").write_text("def add(): pass\n")
    hits = fs_tools.grep(repo, r"def add", "*.py")
    assert "src/app.py" in hits and "node_modules" not in hits
    assert "node_modules" not in fs_tools.list_dir(repo, ".", depth=2)


def test_grep_rejects_bad_regex(repo):
    with pytest.raises(ValueError):
        fs_tools.grep(repo, "([unclosed", "*.py")


def test_delete_refuses_directory(repo):
    with pytest.raises(IsADirectoryError):
        fs_tools.delete_file(repo, "src")


# --- shell ----------------------------------------------------------------
async def test_command_capture(repo):
    res = await shell_tools.run_command("echo out; echo err >&2; exit 7", repo, timeout=10)
    assert res.exit_code == 7
    assert res.stdout.strip() == "out" and res.stderr.strip() == "err"
    assert res.duration_ms >= 0 and not res.timed_out
    assert "exit_code: 7" in res.as_tool_output()


async def test_command_timeout_is_reported(repo):
    res = await shell_tools.run_command("sleep 5", repo, timeout=1)
    assert res.timed_out and res.exit_code != 0


async def test_command_runs_in_project_dir(repo):
    res = await shell_tools.run_command("pwd", repo, timeout=10)
    assert res.stdout.strip() == str(repo)


async def test_api_key_is_stripped_from_child(repo, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    res = await shell_tools.run_command("echo [$ANTHROPIC_API_KEY]", repo, timeout=10)
    assert res.stdout.strip() == "[]"


# --- git ------------------------------------------------------------------
def test_git_snapshot(repo):
    snap = git_tools.snapshot(repo)
    assert snap["is_repo"] and snap["branch"] == "main" and not snap["dirty"]
    assert snap["commits"][0]["subject"] == "initial"

    (repo / "src" / "app.py").write_text("def add(a, b):\n    return a + b + 1\n")
    dirty = git_tools.snapshot(repo)
    assert dirty["dirty"] and any(r["path"] == "src/app.py" for r in dirty["status"])
    assert "+++ b/src/app.py" in git_tools.diff(repo)


def test_git_untracked_files_listed_with_diff(repo):
    (repo / "brand_new.py").write_text("x = 1\n")
    assert "brand_new.py" in git_tools.diff(repo)


def test_commit_creates_commit_and_never_pushes(repo):
    (repo / "src" / "app.py").write_text("def add(a, b):\n    return a + b + 2\n")
    res = git_tools.stage_and_commit(repo, "tweak add()")
    assert res["ok"] and res["sha"]
    assert git_tools.recent_commits(repo)[0]["subject"] == "tweak add()"
    assert not git_tools.snapshot(repo)["dirty"]


def test_commit_rejects_empty_message(repo):
    assert not git_tools.stage_and_commit(repo, "  ")["ok"]


def test_non_repo_snapshot(tmp_path):
    assert git_tools.snapshot(tmp_path)["is_repo"] is False


# --- registry -------------------------------------------------------------
def test_tool_definitions_are_well_formed():
    defs = definitions(FULL)
    assert len(defs) == len(FULL)
    for d in defs:
        assert d["name"] and d["description"] and d["input_schema"]["type"] == "object"
        assert d["eager_input_streaming"] is True
    assert "run_command" not in [d["name"] for d in definitions(READ_ONLY)]


async def test_dispatch_rejects_unknown_tool(repo):
    res = await dispatch(ctx(repo), "not_a_tool", {})
    assert res.is_error and "unknown tool" in res.output


async def test_dispatch_validates_types(repo):
    res = await dispatch(ctx(repo), "write_file", {"path": "x.py", "content": 42})
    assert res.is_error and "must be a string" in res.output
    res2 = await dispatch(ctx(repo), "read_file", "not-a-dict")
    assert res2.is_error


async def test_blocked_command_never_runs(repo):
    marker = repo / "canary.txt"
    marker.write_text("alive")
    res = await dispatch(ctx(repo), "run_command", {"command": "rm -rf / --no-preserve-root"})
    assert res.is_error and "safety policy" in res.output
    assert marker.exists()


async def test_delete_outside_root_is_blocked_not_confirmable(repo, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")

    async def allow(cmd, decision):        # even an approving human never sees this
        raise AssertionError("confirmation must not be requested for a blocked command")

    res = await dispatch(ctx(repo, request_confirmation=allow), "run_command",
                         {"command": f"rm -rf {outside}"})
    assert res.is_error and "safety policy" in res.output
    assert outside.exists()


async def test_confirm_gated_command_fails_closed_without_approver(repo):
    (repo / "build").mkdir()
    res = await dispatch(ctx(repo), "run_command", {"command": "rm -rf build"})
    assert res.is_error and "needs confirmation" in res.output
    assert (repo / "build").exists()


async def test_confirm_denied_command_never_runs(repo):
    async def deny(cmd, decision):
        return False
    c = ctx(repo, request_confirmation=deny)
    (repo / "build").mkdir()
    res = await dispatch(c, "run_command", {"command": "rm -rf build"})
    assert res.is_error and "declined" in res.output
    assert (repo / "build").exists()


async def test_confirm_approved_command_runs(repo):
    async def allow(cmd, decision):
        return True
    c = ctx(repo, request_confirmation=allow)
    (repo / "build").mkdir()
    res = await dispatch(c, "run_command", {"command": "rm -rf build"})
    assert not res.is_error and not (repo / "build").exists()
    assert len(c.commands_run) == 1 and c.commands_run[0].confirmed


async def test_delete_requires_reason_and_approval(repo):
    c = ctx(repo)
    res = await dispatch(c, "delete_file", {"path": "README.md", "reason": "  "})
    assert res.is_error and (repo / "README.md").exists()

    async def deny(cmd, decision):
        return False
    c2 = ctx(repo, request_confirmation=deny)
    res2 = await dispatch(c2, "delete_file", {"path": "README.md", "reason": "obsolete"})
    assert res2.is_error and (repo / "README.md").exists()


async def test_file_changes_are_tracked_and_merged(repo):
    c = ctx(repo)
    await dispatch(c, "write_file", {"path": "t.py", "content": "a = 1\n"})
    await dispatch(c, "edit_file", {"path": "t.py", "old_text": "a = 1", "new_text": "a = 2"})
    assert c.files_touched["t.py"].action == "created"   # created then edited stays created
    assert c.files_touched["t.py"].added == 2


async def test_cancelled_context_refuses_tools(repo):
    import asyncio
    flag = asyncio.Event()
    flag.set()
    res = await dispatch(ctx(repo, cancelled=flag), "read_file", {"path": "README.md"})
    assert res.is_error and "cancelled" in res.output


async def test_events_are_emitted_for_every_tool(repo):
    seen = []
    c = ctx(repo, emit=lambda kind, msg, **kw: seen.append(kind))
    await dispatch(c, "read_file", {"path": "README.md"})
    await dispatch(c, "run_command", {"command": "echo hi"})
    await dispatch(c, "write_file", {"path": "z.py", "content": "z = 1\n"})
    assert "tool" in seen and "command" in seen and "file" in seen
