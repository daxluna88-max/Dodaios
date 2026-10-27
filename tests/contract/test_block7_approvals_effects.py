"""Block 7B: one decision point, no duplicated effects, approvals bound to the exact call.

WRITTEN, NOT RUN (owner order: no test execution in this change). Run before relying on them.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from dodaios.brain.consumer_orchestrator import (
    Branch,
    Budget,
    ContextItem,
    Orchestrator,
    PrivacyGate,
)
from dodaios.core.bus import EventBus
from dodaios.core.config import SafetyConfig
from dodaios.core.events import ActionApprovalRequired, ActionApproved
from dodaios.core.protocols import BrainDelta, ExecutionContext, ToolResult
from dodaios.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor
from dodaios.safety.effects import EffectLedger, call_fingerprint
from dodaios.safety.gate import GateDecision, set_default_gate
from dodaios.tasks.approval_bridge import TaskAutoApprover


class _Effect:
    """A side-effecting tool that counts how often it really ran."""

    name = "send_mail"
    description = "x"
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, tier: str = "monitor", boom: bool = False) -> None:
        self.risk_tier = tier
        self.runs: list[dict[str, Any]] = []
        self.boom = boom

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.runs.append(dict(args))
        if self.boom:
            raise RuntimeError("connection reset after send")
        return ToolResult(success=True, output={"sent": True})


def _executor(ledger: EffectLedger | None = None, mode: str = "bypass"):
    bus = EventBus()
    approval = ApprovalWorkflow(bus)
    executor = ToolExecutor(
        bus, RiskTierEvaluator(SafetyConfig(approval_mode=mode)), approval,
        ledger=ledger or EffectLedger(),
    )
    return bus, approval, executor


@pytest.fixture(autouse=True)
def _no_gate():
    set_default_gate(None)
    yield
    set_default_gate(None)


async def test_same_call_id_is_not_run_twice() -> None:
    _bus, _approval, ex = _executor()
    tool, tid = _Effect(), uuid4()
    first = await ex.execute(tool, {"to": "a"}, trace_id=tid, effect_key="call_1")
    again = await ex.execute(tool, {"to": "a"}, trace_id=tid, effect_key="call_1")
    assert first.success and again.success
    assert len(tool.runs) == 1
    assert again.output["outcome"] == "already_executed"


async def test_same_arguments_under_a_new_id_need_a_person_and_unattended_refuses() -> None:
    _bus, _approval, ex = _executor()
    tool, tid = _Effect(), uuid4()
    await ex.execute(tool, {"to": "a"}, trace_id=tid, effect_key="call_1")
    dup = await ex.execute(
        tool, {"to": "a"}, trace_id=tid, effect_key="call_2",
        config_snapshot={"approval_surface": "unattended"},
    )
    assert not dup.success and dup.error.startswith("approval-unavailable")
    assert len(tool.runs) == 1


async def test_bypass_does_not_approve_a_duplicate() -> None:
    bus, _approval, ex = _executor(mode="bypass")
    tool, tid = _Effect(), uuid4()
    asked: list[ActionApprovalRequired] = []

    async def _watch(ev: ActionApprovalRequired) -> None:
        asked.append(ev)

    bus.subscribe(ActionApprovalRequired, _watch)
    await ex.execute(tool, {"to": "a"}, trace_id=tid, effect_key="call_1")
    pending = asyncio.create_task(
        ex.execute(tool, {"to": "a"}, trace_id=tid, effect_key="call_2",
                   config_snapshot={"approval_timeout_s": 1})
    )
    await asyncio.sleep(0.05)
    assert asked and asked[-1].reason == "duplicate_effect"
    assert not pending.done()
    pending.cancel()


async def test_changed_arguments_are_a_different_call() -> None:
    _bus, _approval, ex = _executor()
    tool, tid = _Effect(), uuid4()
    await ex.execute(tool, {"to": "a"}, trace_id=tid, effect_key="call_1")
    other = await ex.execute(tool, {"to": "b"}, trace_id=tid, effect_key="call_2")
    assert other.success and len(tool.runs) == 2


async def test_a_tool_that_raised_has_unknown_outcome_and_is_not_retried_unattended() -> None:
    _bus, _approval, ex = _executor()
    tool, tid = _Effect(boom=True), uuid4()
    first = await ex.execute(tool, {"to": "a"}, trace_id=tid, effect_key="call_1")
    assert not first.success
    retry = await ex.execute(
        tool, {"to": "a"}, trace_id=tid, effect_key="call_1",
        config_snapshot={"approval_surface": "unattended"},
    )
    assert retry.error.startswith("approval-unavailable")
    assert len(tool.runs) == 1


async def test_a_started_row_from_a_crashed_process_is_unknown(tmp_path) -> None:
    path = tmp_path / "effects.sqlite3"
    ledger = EffectLedger(path)
    fp = call_fingerprint("send_mail", {"to": "a"})
    assert ledger.claim("turn", "call_1", "send_mail", fp)
    # simulate another process run: rewrite the boot id
    ledger._db.execute("UPDATE effects SET boot='old-boot'")  # noqa: SLF001
    assert ledger.inspect("turn", "call_1", "send_mail", fp).kind == "unknown"


async def test_stop_blocks_a_safe_tier_side_effect() -> None:
    from dodaios.control.stop import get_stop_controller

    _bus, _approval, ex = _executor()
    controller = get_stop_controller()
    await controller.stop()
    try:
        tool = _Effect(tier="safe")
        result = await ex.execute(tool, {"to": "a"})
        assert not result.success and "stopped" in (result.error or "")
        assert tool.runs == []
    finally:
        controller.resume()


async def test_approval_for_other_arguments_is_ignored() -> None:
    bus = EventBus()
    approval = ApprovalWorkflow(bus)
    tid = uuid4()
    fp = call_fingerprint("send_mail", {"to": "a"})
    ticket = approval.arm(tid, tool_name="send_mail", fingerprint=fp)
    evil = call_fingerprint("send_mail", {"to": "evil"})
    await bus.publish(ActionApproved(trace_id=tid, tool_name="send_mail", call_fingerprint=evil))
    assert ticket.peek() is None
    await bus.publish(ActionApproved(trace_id=tid, tool_name="other_tool"))
    assert ticket.peek() is None
    await bus.publish(ActionApproved(trace_id=tid, tool_name="send_mail", call_fingerprint=fp))
    assert ticket.peek() == (True, "auto")
    ticket.close()


async def test_task_auto_approver_never_answers_a_gate_confirmation() -> None:
    class _AskGate:
        async def review(self, request):  # noqa: ANN001
            return GateDecision("ask", "policy")

    set_default_gate(_AskGate())
    bus, _approval, ex = _executor()
    approver = TaskAutoApprover(bus)
    tid = uuid4()
    approver.arm(tid, {"send_mail"}, approved_by="task")
    tool = _Effect(tier="ask")
    result = await ex.execute(
        tool, {"to": "a"}, trace_id=tid, config_snapshot={"approval_surface": "unattended"}
    )
    assert result.error.startswith("approval-unavailable")
    assert tool.runs == []


async def test_voice_confirm_runs_the_stashed_arguments_once_and_only_when_matching() -> None:
    _bus, _approval, ex = _executor(mode="ask")
    tool, tid = _Effect(tier="ask"), uuid4()
    deferred = await ex.execute(
        tool, {"to": "a"}, trace_id=tid, effect_key="c1", config_snapshot={"voice_confirm": True}
    )
    fp = deferred.output["call_fingerprint"]
    wrong = await ex.execute_confirmed(tid, expected_fingerprint="0" * 64)
    assert not wrong.success and tool.runs == []
    ok = await ex.execute_confirmed(tid, expected_fingerprint=fp)
    assert ok.success and tool.runs == [{"to": "a"}]
    again = await ex.execute_confirmed(tid)
    assert not again.success and len(tool.runs) == 1


async def test_voice_confirm_after_stop_does_not_run() -> None:
    from dodaios.control.stop import get_stop_controller

    _bus, _approval, ex = _executor(mode="ask")
    tool, tid = _Effect(tier="ask"), uuid4()
    await ex.execute(tool, {"to": "a"}, trace_id=tid, config_snapshot={"voice_confirm": True})
    controller = get_stop_controller()
    await controller.stop()
    try:
        result = await ex.execute_confirmed(tid)
    finally:
        controller.resume()
    assert not result.success and tool.runs == []


async def test_a_fan_out_branch_cannot_run_a_tool() -> None:
    class _Brain:
        def __init__(self) -> None:
            self.seen_tools: Any = None

        async def complete(self, req):  # noqa: ANN001
            self.seen_tools = req.tools
            yield BrainDelta(tool_call={"name": "send_mail", "input": {}})
            yield BrainDelta(content="answer")

    brain = _Brain()
    orch = Orchestrator(PrivacyGate(), Budget(10.0))
    out = await orch.run(
        "t1", [ContextItem("hello", "task")],
        [Branch("b1", "ollama", brain, "local", est_cost=0.0)],
    )
    assert brain.seen_tools == ()
    assert out[0].dropped_tool_calls == 1


async def test_a_branch_whose_engine_runs_its_own_tools_is_refused() -> None:
    class _Agentic:
        executes_tools_itself = True

        async def complete(self, req):  # noqa: ANN001
            yield BrainDelta(content="x")

    orch = Orchestrator(PrivacyGate(), Budget(10.0))
    out = await orch.run(
        "t2", [ContextItem("hello", "task")],
        [Branch("b1", "ollama", _Agentic(), "local", est_cost=0.0)],
    )
    assert out[0].status == "refused"
