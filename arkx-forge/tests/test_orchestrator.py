"""Workflow orchestration against a scripted provider (no network)."""
import asyncio
from dataclasses import replace
from pathlib import Path

from forge.agents.orchestrator import (Orchestrator, parse_plan_steps, parse_test_status,
                                       parse_verdict)
from forge.config import SETTINGS
from forge.llm.base import ProviderError, Turn, ToolCall


class ScriptedProvider:
    """Replies per stage, driven by the role name in the system prompt."""
    name = "scripted"
    model = "scripted-model"

    def __init__(self, test_line="TEST_RESULT: passed", fail_on=None):
        self.test_line = test_line
        self.fail_on = fail_on
        self.calls = []
        self.seen_tool_results = set()

    async def turn(self, system, messages, tools, hooks):
        role = system.split(" —")[0]
        self.calls.append(role)
        if self.fail_on == role:
            raise ProviderError(f"provider exploded during {role}")
        hooks.on_text(f"{role} output\n")

        names = {t["name"] for t in tools}
        first_pass = role not in self.seen_tool_results
        if first_pass and "write_file" in names and role == "BUILDER":
            self.seen_tool_results.add(role)
            return Turn(text="writing", tool_calls=[
                ToolCall("c1", "write_file", {"path": "src/added.py", "content": "v = 1\n"}),
                ToolCall("c2", "read_file", {"path": "src/app.py"})],
                stop_reason="tool_use", in_tokens=10, out_tokens=5,
                raw_content=[{"type": "text", "text": "writing"}])
        if first_pass and "run_command" in names and role == "TESTER":
            self.seen_tool_results.add(role)
            return Turn(text="running tests", tool_calls=[
                ToolCall("c3", "run_command", {"command": "echo 3 passed", "purpose": "tests"})],
                stop_reason="tool_use", in_tokens=10, out_tokens=5,
                raw_content=[{"type": "text", "text": "tests"}])

        body = {
            "RESEARCHER": "STACK: python\nCOMMANDS: pytest\n",
            "ARCHITECT": "1. Add src/added.py\n2. Run pytest\n3. Review\n",
            "BUILDER": "changed src/added.py\n",
            "TESTER": f"ran checks\n{self.test_line}",
            "DEBUGGER": "CAUSE: x\nFIX: y\nVERIFICATION: pytest exit 0\n",
            "REVIEWER": "VERDICT: approve\nno findings\n",
            "SUMMARY": "Added src/added.py. Verification: pytest passed.\n",
        }.get(role, f"{role} done")
        return Turn(text=body, stop_reason="end_turn", in_tokens=20, out_tokens=10,
                    raw_content=[{"type": "text", "text": body}])


def build(repo: Path, provider, events=None, confirm=None):
    events = events if events is not None else []
    stages = {}

    def start_stage(name, role):
        sid = len(stages) + 1
        stages[sid] = {"name": name, "role": role, "status": "running"}
        return sid

    def finish_stage(sid, status, output=None, in_tokens=0, out_tokens=0):
        stages[sid].update(status=status, output=output)

    async def default_confirm(cmd, decision):
        return True

    orch = Orchestrator(
        provider=provider, settings=SETTINGS, root=repo, task_id=1,
        request="add a helper module", emit=lambda k, m, **kw: events.append((k, m, kw)),
        record_command=lambda r: None, record_file=lambda e: None,
        start_stage=start_stage, finish_stage=finish_stage,
        request_confirmation=confirm or default_confirm, cancelled=asyncio.Event())
    return orch, events, stages


async def test_happy_path_runs_every_stage(repo):
    provider = ScriptedProvider()
    orch, events, stages = build(repo, provider)
    out = await orch.run()

    assert out.status == "completed"
    assert [s["name"] for s in stages.values()] == [
        "ANALYSIS", "PLAN", "IMPLEMENT", "TEST", "REVIEW", "SUMMARY"]
    assert all(s["status"] == "done" for s in stages.values())
    assert out.test_status == "passed"
    assert parse_plan_steps(out.plan) == ["Add src/added.py", "Run pytest", "Review"]
    assert parse_verdict(out.review) == "approve"
    assert "Added src/added.py" in out.summary
    assert (repo / "src" / "added.py").read_text() == "v = 1\n"
    assert "src/added.py" in orch.files_touched
    assert len(orch.commands_run) == 1 and orch.commands_run[0].exit_code == 0
    kinds = [k for k, _, _ in events]
    assert kinds.count("stage") >= 12 and "file" in kinds and "command" in kinds
    assert any(m.startswith("PLAN CREATED") for _, m, _ in events)


async def test_failing_tests_trigger_debug_then_retest(repo):
    provider = ScriptedProvider(test_line="TEST_RESULT: failed")
    orch, events, stages = build(repo, provider)
    out = await orch.run()
    names = [s["name"] for s in stages.values()]
    assert "DEBUG#1" in names and "TEST#2" in names and "DEBUG#2" in names
    assert out.status == "completed" and out.test_status == "failed"
    assert names[-1] == "SUMMARY"          # it still finishes and reports honestly


async def test_provider_failure_fails_the_task(repo):
    provider = ScriptedProvider(fail_on="BUILDER")
    orch, events, stages = build(repo, provider)
    out = await orch.run()
    assert out.status == "failed" and "exploded" in out.error
    assert [s["status"] for s in stages.values()][-1] == "failed"
    assert out.analysis and out.plan          # earlier stages are preserved


async def test_cancel_before_start_runs_nothing(repo):
    orch, events, stages = build(repo, ScriptedProvider())
    orch.cancelled.set()
    out = await orch.run()
    assert out.status == "cancelled" and stages == {}


async def test_cancel_between_stages_stops_the_pipeline(repo):
    class CancelAtPlan(ScriptedProvider):
        def __init__(self, orch_ref):
            super().__init__()
            self.orch_ref = orch_ref
        async def turn(self, system, messages, tools, hooks):
            if system.startswith("ARCHITECT"):
                self.orch_ref[0].cancelled.set()
            return await super().turn(system, messages, tools, hooks)

    ref = []
    provider = CancelAtPlan(ref)
    orch, events, stages = build(repo, provider)
    ref.append(orch)
    out = await orch.run()
    names = [s["name"] for s in stages.values()]
    assert out.status == "cancelled"
    assert names == ["ANALYSIS", "PLAN"]          # IMPLEMENT never started
    assert not (repo / "src" / "added.py").exists()


async def test_cancel_mid_tool_loop_marks_stage_cancelled(repo):
    class CancelMidLoop:
        name = "c"; model = "c"
        def __init__(self): self.flag = None; self.n = 0
        async def turn(self, system, messages, tools, hooks):
            self.n += 1
            if self.n == 1:
                self.flag.set()
                return Turn(text="t", tool_calls=[ToolCall("x", "read_file",
                            {"path": "README.md"})], stop_reason="tool_use",
                            raw_content=[{"type": "text", "text": "t"}])
            return Turn(text="done", stop_reason="end_turn", raw_content=[])

    provider = CancelMidLoop()
    orch, events, stages = build(repo, provider)
    provider.flag = orch.cancelled
    out = await orch.run()
    assert out.status == "cancelled"
    assert list(stages.values())[0]["status"] == "cancelled"
    assert any(m.endswith("cancelled") for _, m, _ in events)


async def test_iteration_ceiling_is_enforced(repo):
    class Looper:
        name = "loop"
        model = "loop"
        def __init__(self): self.n = 0
        async def turn(self, system, messages, tools, hooks):
            self.n += 1
            return Turn(text="again", tool_calls=[ToolCall(f"i{self.n}", "read_file",
                        {"path": "README.md"})], stop_reason="tool_use",
                        raw_content=[{"type": "text", "text": "again"}])

    looper = Looper()
    orch, events, _ = build(repo, looper)
    settings = replace(SETTINGS, max_iterations=3)
    orch.settings = settings
    await orch.run_stage("ANALYSIS", __import__("forge.agents.roles", fromlist=["ROLES"]).ROLES["researcher"],
                         "ctx", "go")
    assert looper.n == 3
    assert any("ceiling" in m for _, m, _ in events)


async def test_refusal_surfaces_as_error(repo):
    class Refuser:
        name = "r"; model = "r"
        async def turn(self, system, messages, tools, hooks):
            return Turn(stop_reason="refusal", refusal="cyber: nope", raw_content=[])

    orch, events, _ = build(repo, Refuser())
    out = await orch.run()
    assert out.status == "failed" and "declined" in out.error


def test_test_status_falls_back_to_exit_codes():
    class Cmd:
        def __init__(self, c, e): self.command, self.exit_code = c, e
    assert parse_test_status("no marker", [Cmd("pytest -q", 0)]) == "passed"
    assert parse_test_status("no marker", [Cmd("pytest -q", 1)]) == "failed"
    assert parse_test_status("no marker", [Cmd("ls", 0)]) == "unknown"
    assert parse_test_status("TEST_RESULT: none", [Cmd("pytest", 1)]) == "none"
