"""Controlled shell execution inside the project directory.

Every invocation is classified first (see safety.py), recorded in full, and
surfaced to the UI. Nothing runs silently.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path

MAX_CAPTURE = 60_000


@dataclass
class CommandResult:
    command: str
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    confirmed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def as_tool_output(self) -> str:
        parts = [f"$ {self.command}", f"exit_code: {self.exit_code}",
                 f"duration_ms: {self.duration_ms}"]
        if self.timed_out:
            parts.append("TIMED OUT")
        parts.append(f"--- stdout ---\n{self.stdout or '<empty>'}")
        if self.stderr:
            parts.append(f"--- stderr ---\n{self.stderr}")
        return "\n".join(parts)


def _clip(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    if len(text) > MAX_CAPTURE:
        return text[:MAX_CAPTURE] + f"\n<output truncated at {MAX_CAPTURE} chars>"
    return text


async def run_command(command: str, cwd: Path, timeout: int = 300,
                      confirmed: bool = False) -> CommandResult:
    """Run `command` via the shell in `cwd`, capturing everything."""
    env = dict(os.environ)
    # The child never sees Forge's model credentials.
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(key, None)
    env["FORGE_AGENT"] = "1"
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=str(cwd), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
    except OSError as exc:
        return CommandResult(command, str(cwd), None, "", f"failed to start: {exc}",
                             int((time.monotonic() - started) * 1000), False, confirmed)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        timed_out = False
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        out, err = await proc.communicate()
    duration = int((time.monotonic() - started) * 1000)
    return CommandResult(
        command=command, cwd=str(cwd), exit_code=proc.returncode,
        stdout=_clip(out or b""), stderr=_clip(err or b""),
        duration_ms=duration, timed_out=timed_out, confirmed=confirmed)
