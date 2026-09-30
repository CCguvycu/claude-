"""In-process pub/sub used to fan task activity out to SSE subscribers and the DB."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

EventKind = Literal[
    "status", "stage", "log", "thinking", "assistant",
    "tool", "command", "file", "test", "review", "error", "done", "confirm",
]


@dataclass
class Event:
    task_id: int
    kind: EventKind
    message: str
    stage: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    # Monotonic within one server process. Clients replay both the database
    # history and the live backlog, and use this to render each event once.
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventBus:
    """Fan-out broker. Subscribers get every event; they filter by task_id client-side."""

    def __init__(self, backlog: int = 400) -> None:
        self._subs: set[asyncio.Queue[Event]] = set()
        self._backlog: list[Event] = []
        self._backlog_max = backlog
        self._sinks: list[Any] = []
        self._seq = 0

    def add_sink(self, fn) -> None:
        """Register a synchronous persistence callback, called for every event."""
        self._sinks.append(fn)

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=2000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self._subs.discard(q)

    def backlog(self, task_id: int | None = None) -> list[Event]:
        if task_id is None:
            return list(self._backlog)
        return [e for e in self._backlog if e.task_id == task_id]

    def publish(self, event: Event) -> None:
        self._seq += 1
        event.seq = self._seq
        self._backlog.append(event)
        if len(self._backlog) > self._backlog_max:
            del self._backlog[: len(self._backlog) - self._backlog_max]
        for sink in self._sinks:
            try:
                sink(event)
            except Exception:  # a broken sink must never kill a running task
                pass
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._subs.discard(q)

    def emit(self, task_id: int, kind: EventKind, message: str,
             stage: str | None = None, **data: Any) -> Event:
        ev = Event(task_id=task_id, kind=kind, message=message, stage=stage, data=data)
        self.publish(ev)
        return ev
