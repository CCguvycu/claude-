"""Deterministic offline provider.

Not a demo mode and not shown as a real model: it exists so the orchestrator,
the tool loop, the event stream, the database and the UI can be exercised (and
tested in CI) without network access or credentials. The UI labels it clearly.
"""
from __future__ import annotations

import itertools

from ..config import Settings
from .base import StreamHooks, ToolCall, Turn


class MockProvider:
    name = "mock"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = "mock-provider (no Claude API call)"
        self._ids = itertools.count(1)

    def _tc(self, name: str, payload: dict) -> ToolCall:
        return ToolCall(f"mock_{next(self._ids)}", name, payload)

    async def turn(self, system: str, messages: list[dict], tools: list[dict],
                   hooks: StreamHooks) -> Turn:
        role = system.split("\n", 1)[0][:80]
        names = {t["name"] for t in tools}
        # Has this stage already run its tool round-trip?
        used_tools = any(
            isinstance(m.get("content"), list)
            and any(getattr(b, "type", b.get("type") if isinstance(b, dict) else None) == "tool_result"
                    for b in m["content"])
            for m in messages if m.get("role") == "user")

        if not used_tools and "list_dir" in names:
            hooks.on_thinking("Orienting in the repository.\n")
            calls = [self._tc("list_dir", {"path": ".", "depth": 1})]
            if "run_command" in names:
                calls.append(self._tc("run_command",
                                      {"command": "echo mock-provider check",
                                       "purpose": "verify shell wiring"}))
            if "write_file" in names:
                calls.append(self._tc("write_file", {
                    "path": "FORGE_MOCK_NOTE.md",
                    "content": "# Mock run\n\nWritten by the Forge mock provider.\n"}))
            hooks.on_text("Inspecting the project.")
            return Turn(text="Inspecting the project.", thinking="Orienting.",
                        tool_calls=calls, stop_reason="tool_use", in_tokens=100,
                        out_tokens=40, raw_content=[{"type": "text", "text": "Inspecting."}])

        body = (f"[mock provider] {role}\n"
                f"- offline double; no Claude API call was made\n"
                f"- tools offered: {', '.join(sorted(names)) or 'none'}\n"
                f"1. Step one\n2. Step two\n")
        hooks.on_text(body)
        return Turn(text=body, thinking="", tool_calls=[], stop_reason="end_turn",
                    in_tokens=120, out_tokens=60,
                    raw_content=[{"type": "text", "text": body}])
