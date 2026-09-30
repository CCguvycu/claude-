"""Verifies the real Claude request shape and streaming parse.

A local HTTP server impersonates POST /v1/messages, records the request body the
`anthropic` SDK actually sends, and replays a valid Messages streaming response.
No network, no credentials — but the request we would send to Claude is asserted
field by field, which is the part that cannot be checked any other way.
"""
import json
import socket
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from forge.config import SETTINGS
from forge.llm.base import ProviderError, StreamHooks

CAPTURED: list[dict] = []
SCRIPT: dict = {}


def sse(events: list[tuple[str, dict]]) -> bytes:
    out = []
    for name, payload in events:
        out.append(f"event: {name}\ndata: {json.dumps(payload)}\n\n")
    return "".join(out).encode()


def text_and_tool_stream() -> bytes:
    return sse([
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": [], "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 1,
                      "cache_read_input_tokens": 30}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "thinking", "thinking": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "thinking_delta",
                                           "thinking": "Checking the layout."}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("content_block_start", {"type": "content_block_start", "index": 1,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "text_delta", "text": "Reading "}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "text_delta", "text": "the app."}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ("content_block_start", {"type": "content_block_start", "index": 2,
                                 "content_block": {"type": "tool_use", "id": "toolu_9",
                                                   "name": "read_file", "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 2,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": '{"path": "src/'}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 2,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": 'app.py"}'}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 2}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                           "usage": {"output_tokens": 77}}),
        ("message_stop", {"type": "message_stop"}),
    ])


def refusal_stream() -> bytes:
    return sse([
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_2", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 1}}}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "refusal", "stop_sequence": None,
                                     "stop_details": {"type": "refusal", "category": "cyber",
                                                      "explanation": "declined"}},
                           "usage": {"output_tokens": 2}}),
        ("message_stop", {"type": "message_stop"}),
    ])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        CAPTURED.append({"path": self.path,
                         "headers": {k.lower(): v for k, v in self.headers.items()},
                         "body": json.loads(body)})
        status = SCRIPT.get("status", 200)
        if status != 200:
            payload = json.dumps({"type": "error",
                                  "error": {"type": "invalid_request_error",
                                            "message": "bad model"}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        queue = SCRIPT.get("streams")
        if queue:
            payload = queue.pop(0)
        else:
            payload = SCRIPT.get("stream", text_and_tool_stream())
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()


@pytest.fixture
def fake_api(monkeypatch):
    CAPTURED.clear()
    SCRIPT.clear()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def provider(**overrides):
    from forge.llm.anthropic_client import AnthropicProvider
    settings = replace(SETTINGS, provider="anthropic", **overrides)
    return AnthropicProvider(settings)


async def test_request_shape_matches_the_current_api(fake_api):
    from forge.tools.registry import definitions, FULL
    p = provider()
    chunks, thoughts = [], []
    hooks = StreamHooks(on_text=chunks.append, on_thinking=thoughts.append)
    await p.turn("BUILDER — system prompt", [{"role": "user", "content": "go"}],
                        definitions(FULL), hooks)

    req = CAPTURED[0]["body"]
    assert CAPTURED[0]["path"] == "/v1/messages"
    assert req["model"] == "claude-opus-5-5"
    assert req["stream"] is True
    assert req["max_tokens"] == SETTINGS.max_tokens
    # adaptive thinking: no budget_tokens, which current models reject
    assert req["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "budget_tokens" not in json.dumps(req["thinking"])
    assert req["output_config"] == {"effort": SETTINGS.effort}
    # no removed/rejected parameters
    for gone in ("temperature", "top_p", "top_k", "output_format"):
        assert gone not in req
    assert "tool_choice" not in req          # forced tool use 400s on this model
    # cacheable system prompt
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["system"][0]["text"].startswith("BUILDER")
    # tool definitions carried through intact
    names = {t["name"] for t in req["tools"]}
    assert {"read_file", "write_file", "edit_file", "delete_file", "run_command"} <= names
    for tool in req["tools"]:
        assert tool["input_schema"]["type"] == "object"
        assert tool["eager_input_streaming"] is True
    assert CAPTURED[0]["headers"]["x-api-key"] == "sk-ant-test-key"


async def test_streamed_turn_is_parsed(fake_api):
    p = provider()
    chunks, thoughts = [], []
    turn = await p.turn("RESEARCHER — x", [{"role": "user", "content": "go"}], [],
                        StreamHooks(on_text=chunks.append, on_thinking=thoughts.append))

    assert turn.text == "Reading the app."
    assert "".join(chunks) == "Reading the app."          # streamed incrementally
    assert turn.thinking == "Checking the layout."
    assert "".join(thoughts) == "Checking the layout."
    assert turn.stop_reason == "tool_use" and turn.wants_tools
    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert (call.id, call.name) == ("toolu_9", "read_file")
    assert call.input == {"path": "src/app.py"}           # reassembled from JSON deltas
    assert turn.in_tokens == 150                          # input + cache reads
    assert turn.out_tokens == 77
    assert turn.raw_content and any(b.type == "tool_use" for b in turn.raw_content)


async def test_refusal_is_surfaced(fake_api):
    SCRIPT["stream"] = refusal_stream()
    turn = await provider().turn("REVIEWER — x", [{"role": "user", "content": "go"}], [],
                                 StreamHooks())
    assert turn.stop_reason == "refusal" and "cyber" in turn.refusal


async def test_api_errors_become_provider_errors(fake_api):
    SCRIPT["status"] = 400
    with pytest.raises(ProviderError, match="rejected the request"):
        await provider().turn("BUILDER — x", [{"role": "user", "content": "go"}], [],
                              StreamHooks())


def plain_text_stream(text: str, in_tokens: int = 200, out_tokens: int = 9) -> bytes:
    return sse([
        ("message_start", {"type": "message_start", "message": {
            "id": "m3", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": [], "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": in_tokens, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": out_tokens}}),
        ("message_stop", {"type": "message_stop"}),
    ])


async def test_orchestrator_drives_the_real_client(fake_api, repo):
    """One stage over the real SDK path: tool call dispatched, then a final answer."""
    import asyncio

    from forge.agents.orchestrator import Orchestrator
    from forge.agents.roles import ROLES

    SCRIPT["streams"] = [text_and_tool_stream(), plain_text_stream("STACK: python\n")]

    events = []
    orch = Orchestrator(
        provider=provider(), settings=SETTINGS, root=repo, task_id=1, request="look around",
        emit=lambda k, m, **kw: events.append((k, m)),
        record_command=lambda r: None, record_file=lambda e: None,
        start_stage=lambda n, r: 1, finish_stage=lambda *a, **k: None,
        request_confirmation=None, cancelled=asyncio.Event())
    result = await orch.run_stage("ANALYSIS", ROLES["researcher"], "ctx", "analyse")

    assert result.status == "done"
    assert "STACK: python" in result.output
    assert any(k == "tool" and "Reading src/app.py" in m for k, m in events)
    assert result.in_tokens == 350 and result.out_tokens == 86      # summed over both turns
    assert len(CAPTURED) == 2
    # the second request replays the assistant turn plus the tool_result
    second = CAPTURED[1]["body"]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[2]["content"][0]["type"] == "tool_result"
    assert second[2]["content"][0]["tool_use_id"] == "toolu_9"
    assert "def add" in second[2]["content"][0]["content"]
