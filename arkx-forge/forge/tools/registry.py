"""Tool schemas handed to the model, plus dispatch back into the local tool layer.

Inputs arriving from the model are untrusted: every handler validates its own
arguments before touching the filesystem or the shell (eager input streaming
means the API no longer validates them for us).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import Settings
from . import fs_tools, git_tools, shell_tools
from .safety import Verdict, classify_command


@dataclass
class ToolResult:
    output: str
    is_error: bool = False


@dataclass
class ToolContext:
    """Everything a tool call needs, plus the hooks that make it visible."""
    task_id: int
    root: Path
    settings: Settings
    stage: str = ""
    emit: Callable[..., Any] = lambda *a, **k: None
    record_command: Callable[..., Any] = lambda *a, **k: None
    record_file: Callable[..., Any] = lambda *a, **k: None
    # async (command, decision) -> bool
    request_confirmation: Callable[[str, Any], Awaitable[bool]] | None = None
    files_touched: dict[str, fs_tools.FileEdit] = field(default_factory=dict)
    commands_run: list[shell_tools.CommandResult] = field(default_factory=list)
    cancelled: asyncio.Event | None = None


def _s(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _i(value: Any, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------
async def h_read_file(ctx: ToolContext, args: dict) -> ToolResult:
    path = _s(args.get("path"), "path")
    ctx.emit("tool", f"Reading {path}", tool="read_file", path=path)
    text = fs_tools.read_file(ctx.root, path, ctx.settings.max_read_bytes,
                              args.get("start_line"), args.get("end_line"))
    return ToolResult(text)


async def h_list_dir(ctx: ToolContext, args: dict) -> ToolResult:
    path = _s(args.get("path", "."), "path")
    depth = _i(args.get("depth"), "depth", 2)
    ctx.emit("tool", f"Inspecting {path}", tool="list_dir", path=path)
    return ToolResult(fs_tools.list_dir(ctx.root, path, depth=max(1, min(depth, 5))))


async def h_grep(ctx: ToolContext, args: dict) -> ToolResult:
    pattern = _s(args.get("pattern"), "pattern")
    glob = _s(args.get("glob", "*"), "glob")
    ctx.emit("tool", f"Searching /{pattern}/ in {glob}", tool="grep", pattern=pattern)
    return ToolResult(fs_tools.grep(ctx.root, pattern, glob))


def _register_edit(ctx: ToolContext, edit: fs_tools.FileEdit) -> None:
    prior = ctx.files_touched.get(edit.path)
    if prior:
        # A file created then modified in the same task stays "created".
        action = "created" if prior.action == "created" and edit.action != "deleted" else edit.action
        merged = fs_tools.FileEdit(edit.path, action,
                                   prior.added + edit.added, prior.removed + edit.removed)
        ctx.files_touched[edit.path] = merged
    else:
        ctx.files_touched[edit.path] = edit
    ctx.record_file(edit)
    ctx.emit("file", f"{edit.action.capitalize()} {edit.path} (+{edit.added}/-{edit.removed})",
             path=edit.path, action=edit.action, added=edit.added, removed=edit.removed)


async def h_write_file(ctx: ToolContext, args: dict) -> ToolResult:
    path = _s(args.get("path"), "path")
    content = _s(args.get("content"), "content")
    edit = fs_tools.write_file(ctx.root, path, content)
    _register_edit(ctx, edit)
    return ToolResult(f"{edit.action} {edit.path} (+{edit.added}/-{edit.removed})")


async def h_edit_file(ctx: ToolContext, args: dict) -> ToolResult:
    path = _s(args.get("path"), "path")
    old = _s(args.get("old_text"), "old_text")
    new = _s(args.get("new_text"), "new_text")
    replace_all = bool(args.get("replace_all", False))
    edit = fs_tools.edit_file(ctx.root, path, old, new, replace_all)
    _register_edit(ctx, edit)
    return ToolResult(f"edited {edit.path} (+{edit.added}/-{edit.removed})")


async def h_delete_file(ctx: ToolContext, args: dict) -> ToolResult:
    path = _s(args.get("path"), "path")
    reason = _s(args.get("reason", ""), "reason")
    if not reason.strip():
        return ToolResult("delete_file requires a reason explaining why the file must go.",
                          is_error=True)
    if ctx.request_confirmation is not None:
        from .safety import Decision
        decision = Decision(Verdict.CONFIRM, f"delete {path}: {reason}", f"delete {path}")
        approved = await ctx.request_confirmation(f"delete file {path}", decision)
        if not approved:
            return ToolResult(f"The user declined deletion of {path}. Leave it in place.",
                              is_error=True)
    edit = fs_tools.delete_file(ctx.root, path)
    _register_edit(ctx, edit)
    return ToolResult(f"deleted {edit.path}")


async def h_run_command(ctx: ToolContext, args: dict) -> ToolResult:
    command = _s(args.get("command"), "command").strip()
    purpose = _s(args.get("purpose", ""), "purpose")
    decision = classify_command(command, ctx.root)

    if decision.verdict is Verdict.BLOCK:
        ctx.emit("error", f"BLOCKED: {command}", command=command, reason=decision.reason)
        return ToolResult(
            f"Refused by Forge safety policy: {decision.reason}. "
            f"This command will never run. Choose a different approach.", is_error=True)

    confirmed = False
    if decision.verdict is Verdict.CONFIRM:
        if ctx.request_confirmation is None:
            return ToolResult(f"Command needs confirmation ({decision.reason}) but no "
                              f"approver is attached. Not run.", is_error=True)
        approved = await ctx.request_confirmation(command, decision)
        if not approved:
            ctx.emit("command", f"DENIED: {command}", command=command,
                     reason=decision.reason, denied=True)
            return ToolResult(f"The user declined to run `{command}`. Do not retry it; "
                              f"proceed another way.", is_error=True)
        confirmed = True

    ctx.emit("command", f"$ {command}" + (f"   ({purpose})" if purpose else ""),
             command=command, purpose=purpose, confirmed=confirmed, running=True)
    result = await shell_tools.run_command(
        command, ctx.root, timeout=ctx.settings.command_timeout, confirmed=confirmed)
    ctx.commands_run.append(result)
    ctx.record_command(result)
    ctx.emit("command", f"exit {result.exit_code} in {result.duration_ms}ms",
             command=command, exit_code=result.exit_code, duration_ms=result.duration_ms,
             stdout=result.stdout[-4000:], stderr=result.stderr[-4000:],
             timed_out=result.timed_out, confirmed=confirmed, running=False)
    return ToolResult(result.as_tool_output(), is_error=bool(result.exit_code))


async def h_git_status(ctx: ToolContext, args: dict) -> ToolResult:
    ctx.emit("tool", "Reading git status", tool="git_status")
    snap = git_tools.snapshot(ctx.root)
    if not snap["is_repo"]:
        return ToolResult("not a git repository")
    lines = [f"branch: {snap['branch']}"]
    lines += [f"{r['index'] or ' '}{r['worktree'] or ' '} {r['path']}" for r in snap["status"]]
    if snap["stat"]:
        lines.append(snap["stat"])
    return ToolResult("\n".join(lines))


async def h_git_diff(ctx: ToolContext, args: dict) -> ToolResult:
    path = args.get("path")
    ctx.emit("tool", f"Reading git diff{' for ' + path if path else ''}", tool="git_diff")
    return ToolResult(git_tools.diff(ctx.root, _s(path, "path") if path else None))


async def h_git_log(ctx: ToolContext, args: dict) -> ToolResult:
    limit = _i(args.get("limit"), "limit", 10)
    ctx.emit("tool", "Reading git log", tool="git_log")
    commits = git_tools.recent_commits(ctx.root, max(1, min(limit, 50)))
    return ToolResult("\n".join(f"{c['short']} {c['when']} {c['author']}: {c['subject']}"
                                for c in commits) or "<no commits>")


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[ToolContext, dict], Awaitable[ToolResult]]
    mutating: bool = False

    def definition(self, eager: bool = True) -> dict:
        d: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema,
        }
        if eager:
            # Stream large inputs (file contents) as they are generated.
            d["eager_input_streaming"] = True
        return d


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


STR = {"type": "string"}
INT = {"type": "integer"}
BOOL = {"type": "boolean"}

ALL_TOOLS: dict[str, Tool] = {
    t.name: t for t in [
        Tool("read_file", "Read a UTF-8 text file from the project. Optionally a line range "
             "(1-indexed, inclusive), which returns numbered lines.",
             _obj({"path": STR, "start_line": INT, "end_line": INT}, ["path"]), h_read_file),
        Tool("list_dir", "List a directory tree in the project. depth defaults to 2. "
             "Dependency and build directories are skipped.",
             _obj({"path": STR, "depth": INT}, []), h_list_dir),
        Tool("grep", "Search file contents with a Python regular expression. glob filters "
             "filenames, e.g. '*.py'.",
             _obj({"pattern": STR, "glob": STR}, ["pattern"]), h_grep),
        Tool("write_file", "Create a file or overwrite it completely. Parent directories are "
             "created. Prefer edit_file for small changes to an existing file.",
             _obj({"path": STR, "content": STR}, ["path", "content"]), h_write_file, True),
        Tool("edit_file", "Replace an exact string in an existing file. old_text must match "
             "byte for byte and be unique unless replace_all is true.",
             _obj({"path": STR, "old_text": STR, "new_text": STR, "replace_all": BOOL},
                  ["path", "old_text", "new_text"]), h_edit_file, True),
        Tool("delete_file", "Delete one file. Requires a reason and explicit human approval; "
             "use it only when the task genuinely requires removing the file.",
             _obj({"path": STR, "reason": STR}, ["path", "reason"]), h_delete_file, True),
        Tool("run_command", "Run a shell command in the project root. Output, exit code and "
             "duration are shown to the user. Destructive commands need their approval; some "
             "are refused outright. Never attempt to push to a remote.",
             _obj({"command": STR, "purpose": STR}, ["command"]), h_run_command, True),
        Tool("git_status", "Current branch and working tree status.", _obj({}, []), h_git_status),
        Tool("git_diff", "Unified diff of uncommitted changes, optionally for one path.",
             _obj({"path": STR}, []), h_git_diff),
        Tool("git_log", "Recent commits.", _obj({"limit": INT}, []), h_git_log),
    ]
}

READ_ONLY = ["read_file", "list_dir", "grep", "git_status", "git_diff", "git_log"]
FULL = READ_ONLY + ["write_file", "edit_file", "delete_file", "run_command"]
SHELL_ONLY = READ_ONLY + ["run_command", "write_file", "edit_file"]


def definitions(names: list[str]) -> list[dict]:
    return [ALL_TOOLS[n].definition() for n in names if n in ALL_TOOLS]


async def dispatch(ctx: ToolContext, name: str, args: Any) -> ToolResult:
    """Execute a tool call. Never raises: failures come back as error tool results."""
    tool = ALL_TOOLS.get(name)
    if tool is None:
        return ToolResult(f"unknown tool: {name}", is_error=True)
    if not isinstance(args, dict):
        return ToolResult(f"{name}: input was not a JSON object", is_error=True)
    if ctx.cancelled is not None and ctx.cancelled.is_set():
        return ToolResult("task cancelled by the user", is_error=True)
    try:
        return await tool.handler(ctx, args)
    except PermissionError as exc:
        ctx.emit("error", f"{name} refused: {exc}", tool=name)
        return ToolResult(f"refused: {exc}", is_error=True)
    except (FileNotFoundError, IsADirectoryError, ValueError) as exc:
        return ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
    except OSError as exc:
        return ToolResult(f"OSError: {exc}", is_error=True)
