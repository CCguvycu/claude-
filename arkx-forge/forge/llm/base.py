"""Provider interface.

The canonical message shape is the Claude Messages API shape (a list of
{"role", "content"} dicts whose content is either a string or a list of content
blocks). A provider for another vendor adapts that shape at its own boundary,
so the agent layer above never changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    input: Any


@dataclass
class Turn:
    """One assistant turn, provider-independent."""
    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    in_tokens: int = 0
    out_tokens: int = 0
    # Provider-native content to append back to the conversation verbatim.
    raw_content: Any = None
    refusal: str | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class Callbacks(Protocol):
    def on_text(self, chunk: str) -> None: ...
    def on_thinking(self, chunk: str) -> None: ...
    def on_tool_start(self, name: str) -> None: ...


@dataclass
class StreamHooks:
    on_text: Callable[[str], None] = lambda c: None
    on_thinking: Callable[[str], None] = lambda c: None
    on_tool_start: Callable[[str], None] = lambda n: None


class Provider(Protocol):
    name: str
    model: str

    async def turn(self, system: str, messages: list[dict], tools: list[dict],
                   hooks: StreamHooks) -> Turn:
        """Run one assistant turn, streaming through hooks."""
        ...


class ProviderError(RuntimeError):
    """Raised for unrecoverable provider failures (auth, bad request, exhausted retries)."""
