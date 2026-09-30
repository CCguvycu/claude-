"""Task lifecycle: launch, stream, confirm, cancel. One running task per project."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..db import Database
from ..events import EventBus, Event
from ..llm.factory import build_provider
from ..tools import git_tools
from ..tools.safety import Decision
from .orchestrator import Orchestrator


@dataclass
class PendingConfirm:
    task_id: int
    command: str
    reason: str
    verdict: str
    segment: str
    created: float = field(default_factory=time.time)
    future: asyncio.Future | None = None

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "command": self.command, "reason": self.reason,
                "verdict": self.verdict, "segment": self.segment, "created": self.created}


@dataclass
class RunState:
    task_id: int
    project_id: int
    root: Path
    cancelled: asyncio.Event
    pending: PendingConfirm | None = None
    task: asyncio.Task | None = None


class TaskManager:
    def __init__(self, db: Database, settings: Settings, bus: EventBus) -> None:
        self.db = db
        self.settings = settings
        self.bus = bus
        self.runs: dict[int, RunState] = {}
        bus.add_sink(self._persist_event)

    # --- persistence sink ---------------------------------------------
    def _persist_event(self, ev: Event) -> None:
        # Streamed token deltas are for the live UI only; storing each one would
        # bloat the database. The stage's final text is persisted on the stage row.
        if ev.data.get("delta"):
            return
        self.db.add_event(ev.task_id, ev.ts, ev.kind, ev.stage, ev.message,
                          ev.data or None, ev.seq)

    # --- queries -------------------------------------------------------
    def is_busy(self, project_id: int) -> int | None:
        for run in self.runs.values():
            if run.project_id == project_id:
                return run.task_id
        return None

    def pending_for(self, task_id: int) -> dict | None:
        run = self.runs.get(task_id)
        return run.pending.to_dict() if run and run.pending else None

    def all_pending(self) -> list[dict]:
        return [r.pending.to_dict() for r in self.runs.values() if r.pending]

    # --- control -------------------------------------------------------
    def cancel(self, task_id: int) -> bool:
        run = self.runs.get(task_id)
        if not run:
            return False
        run.cancelled.set()
        if run.pending and run.pending.future and not run.pending.future.done():
            run.pending.future.set_result(False)
        self.bus.emit(task_id, "status", "Cancellation requested", status="cancelling")
        return True

    def resolve_confirm(self, task_id: int, approve: bool) -> bool:
        run = self.runs.get(task_id)
        if not run or not run.pending or not run.pending.future:
            return False
        if run.pending.future.done():
            return False
        run.pending.future.set_result(approve)
        self.bus.emit(task_id, "confirm",
                 f"{'Approved' if approve else 'Denied'}: {run.pending.command}",
                 resolved=True, approved=approve, command=run.pending.command)
        run.pending = None
        return True

    # --- launch --------------------------------------------------------
    async def start(self, project: dict, request: str) -> int:
        root = Path(project["path"])
        if not root.is_dir():
            raise ValueError(f"project path no longer exists: {root}")
        busy = self.is_busy(project["id"])
        if busy:
            raise RuntimeError(f"task {busy} is already running for this project")

        provider = build_provider(self.settings)
        branch = git_tools.current_branch(root)
        task_id = self.db.create_task(project["id"], request, provider.model, branch)
        self.db.touch_project(project["id"])
        run = RunState(task_id=task_id, project_id=project["id"], root=root,
                       cancelled=asyncio.Event())
        self.runs[task_id] = run

        async def confirm(command: str, decision: Decision) -> bool:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bool] = loop.create_future()
            run.pending = PendingConfirm(task_id, command, decision.reason,
                                         decision.verdict.value, decision.segment, future=fut)
            self.bus.emit(task_id, "confirm", f"APPROVAL REQUIRED: {command}",
                     command=command, reason=decision.reason, segment=decision.segment,
                     resolved=False)
            try:
                return await asyncio.wait_for(fut, timeout=self.settings.confirm_timeout)
            except asyncio.TimeoutError:
                self.bus.emit(task_id, "confirm", f"Approval timed out, denied: {command}",
                         command=command, resolved=True, approved=False, timeout=True)
                return False
            finally:
                run.pending = None

        orch = Orchestrator(
            provider=provider, settings=self.settings, root=root, task_id=task_id,
            request=request,
            emit=lambda kind, msg, stage=None, **data: self.bus.emit(task_id, kind, msg, stage, **data),
            record_command=lambda r: self.db.add_command(
                task_id, None, r.command, r.cwd, r.exit_code, r.stdout, r.stderr,
                r.duration_ms, r.confirmed),
            record_file=lambda e: self.db.add_file_change(
                task_id, e.path, e.action, None, e.added, e.removed),
            start_stage=lambda name, role: self._start_stage(task_id, name, role),
            finish_stage=self.db.finish_stage,
            request_confirmation=confirm,
            cancelled=run.cancelled)

        run.task = asyncio.create_task(self._drive(task_id, orch, provider.model))
        return task_id

    def _start_stage(self, task_id: int, name: str, role: str) -> int:
        self.db.update_task(task_id, stage=name)
        return self.db.start_stage(task_id, name, role)

    async def _drive(self, task_id: int, orch: Orchestrator, model: str) -> None:
        self.db.update_task(task_id, status="running")
        self.bus.emit(task_id, "status", f"Task started on {model}", status="running", model=model)
        try:
            outcome = await orch.run()
        except Exception as exc:  # belt and braces: never leave a task "running"
            outcome = None
            self.db.update_task(task_id, status="failed", error=f"{type(exc).__name__}: {exc}",
                                finished_at=time.time())
            self.bus.emit(task_id, "error", f"task crashed: {exc}", status="failed")
            self.bus.emit(task_id, "done", "Task failed", status="failed")
        finally:
            self.runs.pop(task_id, None)
        if outcome is None:
            return
        self.db.update_task(
            task_id, status=outcome.status, analysis=outcome.analysis, plan=outcome.plan,
            review=outcome.review, summary=outcome.summary, test_status=outcome.test_status,
            error=outcome.error, finished_at=time.time())
        self.bus.emit(task_id, "done", f"Task {outcome.status}", status=outcome.status,
                 test_status=outcome.test_status, files=len(orch.files_touched),
                 commands=len(orch.commands_run), error=outcome.error)

    async def shutdown(self) -> None:
        for run in list(self.runs.values()):
            run.cancelled.set()
        tasks = [r.task for r in self.runs.values() if r.task]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
