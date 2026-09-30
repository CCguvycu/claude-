"""Workflow orchestration.

    user task -> ANALYSIS -> PLAN -> IMPLEMENT -> TEST -> [DEBUG -> TEST] -> REVIEW -> SUMMARY

Each stage is one agent role running its own tool loop against the project. Stage
outputs are passed forward as a context document rather than one growing
conversation, which keeps each role focused and the token cost predictable.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import Settings
from ..llm.base import ProviderError, StreamHooks, Turn
from ..tools import git_tools
from ..tools.registry import ToolContext, definitions, dispatch
from .roles import ROLES, Role

MAX_DEBUG_ROUNDS = 2


@dataclass
class StageResult:
    name: str
    role: str
    status: str            # done|failed|skipped|cancelled
    output: str = ""
    in_tokens: int = 0
    out_tokens: int = 0


@dataclass
class TaskOutcome:
    status: str = "completed"       # completed|failed|cancelled
    analysis: str = ""
    plan: str = ""
    review: str = ""
    summary: str = ""
    test_status: str = "unknown"    # passed|failed|none|unknown
    error: str | None = None
    stages: list[StageResult] = field(default_factory=list)


class Cancelled(Exception):
    pass


def parse_plan_steps(plan: str) -> list[str]:
    steps = [m.group(2).strip() for m in re.finditer(r"^\s*(\d+)[.)]\s+(.*)$", plan, re.M)]
    return [s for s in steps if s]


def parse_test_status(text: str, commands: list[Any]) -> str:
    m = re.search(r"^TEST_RESULT:\s*(passed|failed|none)\s*$", text or "", re.M | re.I)
    if m:
        return m.group(1).lower()
    test_cmds = [c for c in commands
                 if re.search(r"\b(pytest|jest|vitest|test|tox|go test|cargo test|mvn|gradle)\b",
                              c.command, re.I)]
    if not test_cmds:
        return "unknown"
    return "passed" if all(c.exit_code == 0 for c in test_cmds) else "failed"


def parse_verdict(review: str) -> str | None:
    m = re.search(r"^VERDICT:\s*(approve-with-nits|approve|needs-work)\s*$", review or "",
                  re.M | re.I)
    return m.group(1).lower() if m else None


class Orchestrator:
    def __init__(self, *, provider, settings: Settings, root: Path, task_id: int,
                 request: str, emit: Callable[..., Any],
                 record_command: Callable[..., Any], record_file: Callable[..., Any],
                 start_stage: Callable[[str, str], int],
                 finish_stage: Callable[..., None],
                 request_confirmation: Callable[[str, Any], Awaitable[bool]],
                 cancelled: asyncio.Event) -> None:
        self.provider = provider
        self.settings = settings
        self.root = root
        self.task_id = task_id
        self.request = request
        self._emit = emit
        self.record_command = record_command
        self.record_file = record_file
        self.start_stage_cb = start_stage
        self.finish_stage_cb = finish_stage
        self.request_confirmation = request_confirmation
        self.cancelled = cancelled
        self.stage = ""
        self.files_touched: dict[str, Any] = {}
        self.commands_run: list[Any] = []

    # -- plumbing -------------------------------------------------------
    def emit(self, kind: str, message: str, **data: Any) -> None:
        self._emit(kind, message, stage=self.stage, **data)

    def _check_cancel(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled()

    def _context(self, extra: dict[str, str]) -> str:
        snap = git_tools.snapshot(self.root)
        lines = [
            "=== TASK CONTEXT ===",
            f"Project root: {self.root}",
            f"Git branch: {snap['branch'] or 'n/a'}"
            + (f" (working tree dirty: {len(snap['status'])} paths)" if snap["dirty"] else " (clean)"),
            "",
            "USER REQUEST:",
            self.request.strip(),
        ]
        for title, body in extra.items():
            if body and body.strip():
                lines += ["", f"=== {title} ===", body.strip()]
        return "\n".join(lines)

    # -- one stage ------------------------------------------------------
    async def run_stage(self, name: str, role: Role, context: str,
                        instruction: str) -> StageResult:
        self.stage = name
        self._check_cancel()
        stage_row = self.start_stage_cb(name, role.key)
        self.emit("stage", f"{name} — {role.title} started", role=role.key, state="start")

        ctx = ToolContext(
            task_id=self.task_id, root=self.root, settings=self.settings, stage=name,
            emit=lambda kind, msg, **kw: self._emit(kind, msg, stage=name, **kw),
            record_command=self.record_command, record_file=self.record_file,
            request_confirmation=self.request_confirmation, cancelled=self.cancelled)

        tools = definitions(list(role.tools))
        messages: list[dict] = [{"role": "user", "content": instruction}]
        hooks = StreamHooks(
            on_text=lambda c: self._emit("assistant", c, stage=name, delta=True),
            on_thinking=lambda c: self._emit("thinking", c, stage=name, delta=True),
            on_tool_start=lambda n: None)

        final_text = ""
        in_tok = out_tok = 0
        try:
            for iteration in range(self.settings.max_iterations):
                self._check_cancel()
                turn: Turn = await self.provider.turn(role.system(context), messages, tools, hooks)
                in_tok += turn.in_tokens
                out_tok += turn.out_tokens
                if turn.text.strip():
                    final_text = turn.text

                if turn.stop_reason == "refusal":
                    raise ProviderError(f"model declined this request ({turn.refusal})")
                if turn.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": turn.raw_content})
                    continue
                if turn.stop_reason == "max_tokens" and turn.wants_tools:
                    raise ProviderError("tool input was truncated by max_tokens; "
                                        "raise FORGE_MAX_TOKENS and retry")
                if not turn.wants_tools:
                    break

                messages.append({"role": "assistant", "content": turn.raw_content})
                results = []
                for call in turn.tool_calls:
                    self._check_cancel()
                    res = await dispatch(ctx, call.name, call.input)
                    results.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": res.output or "<no output>",
                                    **({"is_error": True} if res.is_error else {})})
                messages.append({"role": "user", "content": results})
            else:
                self.emit("error", f"{name} hit the {self.settings.max_iterations}-step ceiling")

            self.files_touched.update(ctx.files_touched)
            self.commands_run.extend(ctx.commands_run)
            self.finish_stage_cb(stage_row, "done", final_text, in_tok, out_tok)
            self.emit("stage", f"{name} complete", role=role.key, state="done",
                      in_tokens=in_tok, out_tokens=out_tok)
            return StageResult(name, role.key, "done", final_text, in_tok, out_tok)

        except Cancelled:
            self.finish_stage_cb(stage_row, "cancelled", final_text, in_tok, out_tok)
            self.emit("stage", f"{name} cancelled", role=role.key, state="cancelled")
            raise
        except ProviderError as exc:
            self.files_touched.update(ctx.files_touched)
            self.commands_run.extend(ctx.commands_run)
            self.finish_stage_cb(stage_row, "failed", str(exc), in_tok, out_tok)
            self.emit("error", f"{name} failed: {exc}", role=role.key)
            raise

    # -- the pipeline ---------------------------------------------------
    async def run(self) -> TaskOutcome:
        out = TaskOutcome()
        try:
            analysis = await self.run_stage(
                "ANALYSIS", ROLES["researcher"], self._context({}),
                "Analyse this repository for the request above and report as instructed.")
            out.analysis = analysis.output
            out.stages.append(analysis)

            plan = await self.run_stage(
                "PLAN", ROLES["architect"], self._context({"REPOSITORY ANALYSIS": out.analysis}),
                "Produce the implementation plan for the request above.")
            out.plan = plan.output
            out.stages.append(plan)
            steps = parse_plan_steps(out.plan)
            self.emit("stage", f"PLAN CREATED — {len(steps)} steps", state="plan",
                      steps=steps, plan=out.plan)

            impl = await self.run_stage(
                "IMPLEMENT", ROLES["builder"],
                self._context({"REPOSITORY ANALYSIS": out.analysis, "APPROVED PLAN": out.plan}),
                "Implement the plan above now, using the tools.")
            out.stages.append(impl)

            changed = ", ".join(sorted(self.files_touched)) or "none recorded"
            test = await self.run_stage(
                "TEST", ROLES["tester"],
                self._context({"REPOSITORY ANALYSIS": out.analysis, "PLAN": out.plan,
                               "IMPLEMENTATION REPORT": impl.output,
                               "FILES CHANGED SO FAR": changed}),
                "Verify the change as instructed and report the TEST_RESULT line.")
            out.stages.append(test)
            out.test_status = parse_test_status(test.output, self.commands_run)
            self.emit("test", f"Test status: {out.test_status}", status=out.test_status)

            rounds = 0
            while out.test_status == "failed" and rounds < MAX_DEBUG_ROUNDS:
                rounds += 1
                dbg = await self.run_stage(
                    f"DEBUG#{rounds}", ROLES["debugger"],
                    self._context({"PLAN": out.plan, "TESTER REPORT": test.output,
                                   "FILES CHANGED SO FAR": ", ".join(sorted(self.files_touched))}),
                    "Diagnose and fix the failure reported above, then verify the fix.")
                out.stages.append(dbg)
                test = await self.run_stage(
                    f"TEST#{rounds + 1}", ROLES["tester"],
                    self._context({"PLAN": out.plan, "DEBUGGER REPORT": dbg.output}),
                    "Re-run the checks and report the TEST_RESULT line.")
                out.stages.append(test)
                out.test_status = parse_test_status(test.output, self.commands_run)
                self.emit("test", f"Test status after debug round {rounds}: {out.test_status}",
                          status=out.test_status)

            review = await self.run_stage(
                "REVIEW", ROLES["reviewer"],
                self._context({"ORIGINAL PLAN": out.plan,
                               "IMPLEMENTATION REPORT": impl.output,
                               "TEST OUTCOME": f"{out.test_status}\n\n{test.output}"}),
                "Review the change now. Start with git_diff.")
            out.stages.append(review)
            out.review = review.output
            verdict = parse_verdict(out.review)
            self.emit("review", f"REVIEW COMPLETE — {verdict or 'no verdict line'}",
                      verdict=verdict)

            cmd_lines = "\n".join(
                f"$ {c.command} -> exit {c.exit_code} ({c.duration_ms}ms)"
                for c in self.commands_run) or "none"
            file_lines = "\n".join(
                f"{e.action} {p} (+{e.added}/-{e.removed})"
                for p, e in sorted(self.files_touched.items())) or "none"
            summary = await self.run_stage(
                "SUMMARY", ROLES["summariser"],
                self._context({"PLAN": out.plan, "IMPLEMENTATION REPORT": impl.output,
                               "FILES CHANGED": file_lines, "COMMANDS RUN": cmd_lines,
                               "TEST OUTCOME": f"{out.test_status}\n{test.output}",
                               "REVIEW": out.review}),
                "Write the final summary.")
            out.stages.append(summary)
            out.summary = summary.output
            out.status = "completed"

        except Cancelled:
            out.status = "cancelled"
            out.error = "cancelled by the user"
        except ProviderError as exc:
            out.status = "failed"
            out.error = str(exc)
        except Exception as exc:  # unexpected: record it rather than losing the task
            out.status = "failed"
            out.error = f"{type(exc).__name__}: {exc}"
            self.emit("error", f"unexpected failure: {out.error}")
        return out
