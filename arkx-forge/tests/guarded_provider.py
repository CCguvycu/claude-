"""A test provider that deliberately asks for a command needing approval.

Loaded via FORGE_PROVIDER=tests.guarded_provider:GuardedProvider to exercise the
confirmation gate through the real server and the real UI.
"""
from __future__ import annotations

from forge.llm.base import ToolCall, Turn


class GuardedProvider:
    name = "guarded-test"

    def __init__(self, settings) -> None:
        self.settings = settings
        self.model = "guarded-test-provider"
        self._n = 0

    async def turn(self, system, messages, tools, hooks) -> Turn:
        names = {t["name"] for t in tools}
        asked = any(
            m.get("role") == "user" and isinstance(m.get("content"), list)
            and any((b.get("type") if isinstance(b, dict) else None) == "tool_result"
                    for b in m["content"])
            for m in messages)
        if "run_command" in names and not asked:
            self._n += 1
            hooks.on_text("clearing the build directory")
            return Turn(text="clearing the build directory", stop_reason="tool_use",
                        tool_calls=[ToolCall(f"g{self._n}", "run_command",
                                             {"command": "rm -rf build",
                                              "purpose": "remove stale build output"})],
                        in_tokens=10, out_tokens=5,
                        raw_content=[{"type": "text", "text": "clearing"}])
        body = "done\nTEST_RESULT: none\nVERDICT: approve\n"
        hooks.on_text(body)
        return Turn(text=body, stop_reason="end_turn", in_tokens=10, out_tokens=5,
                    raw_content=[{"type": "text", "text": body}])
