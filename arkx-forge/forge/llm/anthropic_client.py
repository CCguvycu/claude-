"""Claude provider, built on the official `anthropic` SDK.

Streaming, adaptive thinking, and a tolerant tool-input path: with
eager_input_streaming the server no longer validates tool inputs, so unparseable
JSON surfaces as a ValueError from the stream iterator and is retried a bounded
number of times. Callers validate parsed inputs themselves (tools/registry.py).
"""
from __future__ import annotations

import anthropic

from ..config import Settings
from .base import ProviderError, StreamHooks, ToolCall, Turn

# Models where thinking is always on and `budget_tokens` / `disabled` are rejected.
_ADAPTIVE_ONLY = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5", "claude-fable-5-1",
                  "claude-sonnet-5-5", "claude-opus-4-8", "claude-opus-4-7")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        if not settings.api_key:
            # The SDK can also resolve an `ant auth login` profile; only fail if
            # neither is available, which we detect on the first call.
            pass
        self.settings = settings
        self.model = settings.model
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.api_key, max_retries=3, timeout=900.0)

    def _thinking(self) -> dict:
        if self.model.startswith(("claude-haiku", "claude-sonnet-4", "claude-opus-4-5")):
            return {"type": "enabled", "budget_tokens": 8000}
        return {"type": "adaptive", "display": "summarized"}

    async def turn(self, system: str, messages: list[dict], tools: list[dict],
                   hooks: StreamHooks) -> Turn:
        attempts = 0
        while True:
            try:
                return await self._one_turn(system, messages, tools, hooks)
            except ValueError as exc:
                # Tool-input JSON the SDK could not parse at all: re-issue the turn.
                attempts += 1
                if attempts > 2:
                    raise ProviderError(f"unparseable tool input after retries: {exc}") from exc
            except anthropic.AuthenticationError as exc:
                raise ProviderError(
                    "Claude rejected the credentials. Set ANTHROPIC_API_KEY in the "
                    "environment that runs the Forge server.") from exc
            except anthropic.BadRequestError as exc:
                raise ProviderError(f"Claude rejected the request: {exc}") from exc
            except anthropic.RateLimitError as exc:
                raise ProviderError(f"rate limited by the Claude API: {exc}") from exc
            except anthropic.APIStatusError as exc:
                raise ProviderError(f"Claude API error {exc.status_code}: {exc}") from exc
            except anthropic.APIConnectionError as exc:
                raise ProviderError(f"could not reach the Claude API: {exc}") from exc

    async def _one_turn(self, system: str, messages: list[dict], tools: list[dict],
                        hooks: StreamHooks) -> Turn:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.settings.max_tokens,
            "system": [{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "thinking": self._thinking(),
            "output_config": {"effort": self.settings.effort},
        }
        if tools:
            kwargs["tools"] = tools

        text_parts: list[str] = []
        think_parts: list[str] = []
        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                etype = getattr(event, "type", "")
                if etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if getattr(block, "type", "") == "tool_use":
                        hooks.on_tool_start(getattr(block, "name", "?"))
                elif etype == "content_block_delta":
                    delta = event.delta
                    dtype = getattr(delta, "type", "")
                    if dtype == "text_delta":
                        text_parts.append(delta.text)
                        hooks.on_text(delta.text)
                    elif dtype == "thinking_delta":
                        think_parts.append(delta.thinking)
                        hooks.on_thinking(delta.thinking)
            message = await stream.get_final_message()

        tool_calls = [ToolCall(b.id, b.name, b.input)
                      for b in message.content if b.type == "tool_use"]
        usage = message.usage
        refusal = None
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            refusal = (f"{getattr(details, 'category', 'unspecified')}: "
                       f"{getattr(details, 'explanation', '')}").strip()
        return Turn(
            text="".join(text_parts),
            thinking="".join(think_parts),
            tool_calls=tool_calls,
            stop_reason=message.stop_reason,
            in_tokens=(usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0),
            out_tokens=usage.output_tokens or 0,
            raw_content=message.content,
            refusal=refusal,
        )
