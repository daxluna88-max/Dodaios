"""Block 8: review fixes R1-R5 (failure = unknown, atomic admission, fail-closed ledger,
STOP at the final boundary, call-bound one-use approvals).

WRITTEN, NOT RUN (owner order: no test execution in this change). Run before relying on them.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any
from uuid import uuid4

import pytest

from dodaios.core.bus import EventBus
from dodaios.core.config import SafetyConfig
from dodaios.core.events import ActionApprovalRequired, ActionApproved, ActionDenied
from dodaios.core.protocols import ExecutionContext, ToolResult
from dodaios.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor, effects
from dodaios.safety.effects import (
    EffectLedger,
    LedgerUnavailable,
    call_fingerprint,
    configure_default_ledger,
    is_side_effecting,
    set_default_ledger,
)
from dodaios.safety.gate import GateDecision, set_default_gate
from dodaios.safety.preflight import final_refusal, unattended_refusal

pytestmark = pytest.mark.asyncio


class _Tool:
    name = "run_shell"
    description = "x"
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, tier: str = "monitor", result: ToolResult | None = None, **attrs: Any):
        self.risk_tier = tier
        self.runs: list[dict[str, Any]] = []
        self._result = result or ToolResult(True, {"ok": True})
        for k, v in attrs.items():
            setattr(self, k, v)

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.runs.append(args)
        return self._result


def _executor(mode: str = "bypass", ledger: EffectLedger | None = None):
    bus = EventBus()
    approval = ApprovalWorkflow(bus)
    ex = ToolExecutor(
        bus, RiskTierEvaluator(SafetyConfig(approval_mode=mode)), approval,
        ledger=ledger or EffectLedger(),
    )
    return bus, approval, ex


@pytest.fixture(autouse=True)
def _clean():
    set_default_gate(None)
    yield
    set_default_gate(None)
    set_default_ledger(None)


# ---------------------------------------------------------------- R1: failure is unknown

async def test_error_result_of_a_side_effecting_tool_is_unknown_not_retryable() -> None:
    tool = _Tool(result=ToolResult(False, None, "exit 1 after writing half the files"))
    _bus, _a, ex = _executor()
    tid = uuid4()
    first = await ex.execute(tool, {"cmd": "x"}, trace_id=tid, effect_key="c1")
    assert not first.success
    retry = await ex.execute(
        tool, {"cmd": "x"}, trace_id=tid, effect_key="c1",
        config_snapshot={"approval_surface": "unattended"},
    )
    assert retry.error.startswith("approval-unavailable")  # forced confirmation, no auto retry
    assert len(tool.runs) == 1


async def test_error_with_trusted_no_effect_evidence_is_retryable() -> None:
    tool = _Tool(result=ToolResult(False, None, "bad argument", no_effect_guaranteed=True))
    _bus, _a, ex = _executor()
    tid = uuid4()
    await ex.execute(tool, {"cmd": "x"}, trace_id=tid, effect_key="c1")
    await ex.execute(tool, {"cmd": "x"}, trace_id=tid, effect_key="c1")
    assert len(tool.runs) == 2


async def test_tool_level_no_effect_declaration_counts_as_evidence() -> None:
    tool = _Tool(result=ToolResult(False, None, "nope"), failure_means_no_effect=True)
    _bus, _a, ex = _executor()
    tid = uuid4()
    await ex.execute(tool, {}, trace_id=tid, effect_key="c1")
    await ex.execute(tool, {}, trace_id=tid, effect_key="c1")
    assert len(tool.runs) == 2


async def test_known_success_replays_and_failure_does_not_replay_as_success() -> None:
    ok = _Tool()
    _bus, _a, ex = _executor()
    tid = uuid4()
    await ex.execute(ok, {}, trace_id=tid, effect_key="c1")
    again = await ex.execute(ok, {}, trace_id=tid, effect_key="c1")
    assert again.output["outcome"] == "already_executed" and len(ok.runs) == 1


async def test_unclassified_safe_tool_is_side_effecting_and_only_declared_reads_skip() -> None:
    assert is_side_effecting(_Tool(tier="safe"), {}, "safe") is True
    assert is_side_effecting(_Tool(tier="safe", read_only=True), {}, "safe") is False
    assert is_side_effecting(_Tool(tier="safe", read_only=False), {}, "safe") is True

    def boom(_args: dict[str, Any]) -> bool:
        raise RuntimeError("broken capability")

    assert is_side_effecting(_Tool(tier="safe", read_only_for_args=boom), {}, "safe") is True


async def test_safe_tool_without_declaration_goes_through_the_ledger() -> None:
    tool = _Tool(tier="safe")
    ledger = EffectLedger()
    _bus, _a, ex = _executor(ledger=ledger)
    tid = uuid4()
    await ex.execute(tool, {"a": 1}, trace_id=tid, effect_key="c1")
    assert ledger.get(str(tid), "c1") is not None


# ---------------------------------------------------------------- R2: atomic admission

async def test_reserve_is_one_atomic_decision_and_never_overwrites() -> None:
    ledger = EffectLedger()
    fp = call_fingerprint("t", {"a": 1})
    first = ledger.reserve("s", "k1", "t", fp)
    second = ledger.reserve("s", "k1", "t", fp)
    assert first.kind == "reserved" and second.kind == "in_flight"
    # same content under another call id while the first is running: not admitted either
    other = ledger.reserve("s", "k2", "t", fp)
    assert other.kind == "in_flight"
    assert ledger.get("s", "k1").state == "started"


async def test_two_racing_reservations_admit_exactly_one(tmp_path) -> None:
    path = tmp_path / "e.sqlite3"
    a, b = EffectLedger(path), EffectLedger(path)  # two connections stand in for two processes
    fp = call_fingerprint("t", {"a": 1})
    kinds: list[str] = []
    barrier = threading.Barrier(2)

    def go(ledger: EffectLedger, key: str) -> None:
        barrier.wait()
        kinds.append(ledger.reserve("s", key, "t", fp).kind)

    threads = [
        threading.Thread(target=go, args=(a, "k1")),
        threading.Thread(target=go, args=(b, "k2")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(kinds).count("reserved") == 1


async def test_same_content_new_id_is_a_duplicate_until_explicitly_approved() -> None:
    ledger = EffectLedger()
    fp = call_fingerprint("t", {"a": 1})
    assert ledger.reserve("s", "k1", "t", fp).kind == "reserved"
    ledger.finish("s", "k1", success=True)
    assert ledger.reserve("s", "k2", "t", fp).kind == "duplicate"
    deliberate = ledger.reserve("s", "k2", "t", fp, attempt="approval-1")
    assert deliberate.kind == "reserved"
    # the approval id is one-use
    assert ledger.reserve("s", "k2", "t", fp, attempt="approval-1").kind == "in_flight"
    # the earlier success is untouched
    assert ledger.get("s", "k1").state in ("done", "started")


async def test_unknown_row_is_not_overwritten_by_a_plain_claim() -> None:
    ledger = EffectLedger()
    fp = call_fingerprint("t", {})
    ledger.reserve("s", "k", "t", fp)
    ledger.mark_unknown("s", "k", "died")
    assert ledger.reserve("s", "k", "t", fp).kind == "unknown"
    assert ledger.reserve("s", "k", "t", fp, attempt="approved-1").kind == "reserved"


async def test_confirmed_unknown_reexecution_runs_once_with_the_approval_id_as_attempt() -> None:
    tool = _Tool(result=ToolResult(False, None, "died after sending"))
    bus, _a, ex = _executor(mode="ask")
    tid = uuid4()

    async def _approve(e: ActionApprovalRequired) -> None:
        await bus.publish(ActionApproved(
            trace_id=e.trace_id, tool_name=e.tool_name, approved_by="user",
            call_fingerprint=e.call_fingerprint, approval_id=e.approval_id,
        ))

    # first run needs the normal approval too
    bus.subscribe(ActionApprovalRequired, _approve)
    await ex.execute(tool, {"c": 1}, trace_id=tid, effect_key="c1")
    await ex.execute(tool, {"c": 1}, trace_id=tid, effect_key="c1")
    assert len(tool.runs) == 2  # the second one went through the effect_unknown confirmation


# ---------------------------------------------------------------- R3: fail closed

async def test_effectful_call_fails_closed_when_persistent_ledger_unavailable(tmp_path) -> None:
    bad = tmp_path / "is_a_file"
    bad.write_text("x")
    configure_default_ledger(bad / "effects.sqlite3")  # parent is a file: cannot open
    assert effects.protection_state().mode == "unavailable"
    with pytest.raises(LedgerUnavailable):
        effects.get_default_ledger()
    bus = EventBus()
    ex = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig(approval_mode="bypass")),
                      ApprovalWorkflow(bus))
    effectful, read = _Tool(), _Tool(read_only=True)
    refused = await ex.execute(effectful, {"a": 1})
    assert not refused.success and "effect-ledger-unavailable" in refused.error
    assert effectful.runs == []
    assert (await ex.execute(read, {"a": 1})).success  # reads keep working


async def test_reduced_protection_only_when_the_owner_enabled_it_and_is_reported(tmp_path) -> None:
    bad = tmp_path / "is_a_file"
    bad.write_text("x")
    configure_default_ledger(bad / "effects.sqlite3", allow_reduced_protection=True)
    state = effects.protection_state()
    assert state.mode == "reduced" and "NOT covered" in state.detail
    assert effects.get_default_ledger().persistent is False


# ---------------------------------------------------------------- R4: STOP at the boundary

async def test_stop_during_gate_review_blocks_the_call() -> None:
    from dodaios.control.stop import get_stop_controller

    controller = get_stop_controller()

    class _StopWhileReviewing:
        async def review(self, request):  # noqa: ANN001
            await controller.stop()
            return GateDecision("allow")

    set_default_gate(_StopWhileReviewing())
    _bus, _a, ex = _executor()
    tool = _Tool(tier="safe")
    try:
        result = await ex.execute(tool, {"a": 1})
    finally:
        controller.resume()
    assert not result.success and "stopped" in result.error and tool.runs == []


async def test_stop_and_resume_during_gate_review_still_blocks_a_voice_confirmation() -> None:
    from dodaios.control.stop import get_stop_controller

    controller = get_stop_controller()
    _bus, _a, ex = _executor(mode="ask")
    tool, tid = _Tool(tier="ask"), uuid4()
    deferred = await ex.execute(tool, {"a": 1}, trace_id=tid,
                                config_snapshot={"voice_confirm": True})

    class _StopThenResume:
        async def review(self, request):  # noqa: ANN001
            await controller.stop()
            controller.resume()  # a NEW generation: the door is open again
            return GateDecision("allow")

    set_default_gate(_StopThenResume())
    result = await ex.execute_confirmed(
        tid, expected_fingerprint=deferred.output["call_fingerprint"],
        expected_approval_id=deferred.output["approval_id"],
    )
    assert not result.success and tool.runs == []


async def test_unattended_refusal_rechecks_stop_after_the_gate_answers() -> None:
    from dodaios.control.stop import get_stop_controller

    controller = get_stop_controller()

    class _Slow:
        async def review(self, request):  # noqa: ANN001
            await controller.stop()
            return GateDecision("allow")

    set_default_gate(_Slow())
    try:
        held = await unattended_refusal("tool", {"a": 1})
    finally:
        controller.resume()
    assert held is not None and "stopped" in held


async def test_final_refusal_needs_the_original_generation() -> None:
    from dodaios.control.stop import get_stop_controller
    from dodaios.safety.preflight import current_ticket

    controller = get_stop_controller()
    ticket = current_ticket()
    await controller.stop()
    controller.resume()
    assert final_refusal("tool", ticket) is not None  # open door, stale generation
    assert final_refusal("tool", current_ticket()) is None


# ---------------------------------------------------------------- R5: bound approvals

async def test_approval_without_identity_is_ignored_but_denial_always_counts() -> None:
    bus = EventBus()
    wf = ApprovalWorkflow(bus)
    tid = uuid4()
    fp = call_fingerprint("send", {"to": "a"})
    ticket = wf.arm(tid, tool_name="send", fingerprint=fp)
    await bus.publish(ActionApproved(trace_id=tid))
    await bus.publish(ActionApproved(trace_id=tid, tool_name="send", call_fingerprint=fp))
    await bus.publish(
        ActionApproved(trace_id=tid, tool_name="send", approval_id=ticket.approval_id)
    )
    await bus.publish(ActionApproved(trace_id=tid, tool_name="send", call_fingerprint=fp,
                                     approval_id="someone-elses"))
    assert ticket.peek() is None
    await bus.publish(ActionDenied(trace_id=tid, reason="no"))
    assert ticket.peek() == (False, "no")
    ticket.close()


async def test_approval_id_is_unique_per_pending_approval_and_one_use() -> None:
    bus = EventBus()
    wf = ApprovalWorkflow(bus)
    tid = uuid4()
    fp = call_fingerprint("send", {})
    t1 = wf.arm(tid, tool_name="send", fingerprint=fp)
    first_id = t1.approval_id
    await bus.publish(ActionApproved(trace_id=tid, tool_name="send", call_fingerprint=fp,
                                     approval_id=first_id))
    assert t1.peek() == (True, "user")
    t1.close()
    t2 = wf.arm(tid, tool_name="send", fingerprint=fp)
    assert t2.approval_id != first_id
    await bus.publish(ActionApproved(trace_id=tid, tool_name="send", call_fingerprint=fp,
                                     approval_id=first_id))  # the spent id
    assert t2.peek() is None
    t2.close()


async def test_voice_confirm_without_identity_is_refused_and_keeps_the_question() -> None:
    _bus, _a, ex = _executor(mode="ask")
    tool, tid = _Tool(tier="ask"), uuid4()
    deferred = await ex.execute(tool, {"a": 1}, trace_id=tid,
                                config_snapshot={"voice_confirm": True})
    assert not (await ex.execute_confirmed(tid)).success
    assert not (await ex.execute_confirmed(
        tid, expected_fingerprint=deferred.output["call_fingerprint"])).success
    assert ex.has_pending_voice_confirm(tid) and tool.runs == []
    ok = await ex.execute_confirmed(
        tid, expected_fingerprint=deferred.output["call_fingerprint"],
        expected_approval_id=deferred.output["approval_id"],
    )
    assert ok.success and len(tool.runs) == 1


async def test_executed_arguments_are_the_approved_snapshot_even_if_the_caller_mutates() -> None:
    tool, tid = _Tool(tier="ask"), uuid4()
    _bus, _a, ex = _executor(mode="ask")
    args: dict[str, Any] = {"to": ["a"]}
    deferred = await ex.execute(tool, args, trace_id=tid, config_snapshot={"voice_confirm": True})
    args["to"].append("evil")  # nested mutation after the question was asked
    await ex.execute_confirmed(
        tid, expected_fingerprint=deferred.output["call_fingerprint"],
        expected_approval_id=deferred.output["approval_id"],
    )
    assert tool.runs == [{"to": ["a"]}]


async def test_bridges_echo_the_approval_id() -> None:
    from dodaios.tasks.approval_bridge import TaskAutoApprover

    bus, _a, ex = _executor(mode="ask")
    approver = TaskAutoApprover(bus)
    tid = uuid4()
    approver.arm(tid, {"run_shell"}, approved_by="task")
    tool = _Tool(tier="ask")
    result = await ex.execute(tool, {"a": 1}, trace_id=tid,
                              config_snapshot={"approval_surface": "unattended"})
    assert result.success and len(tool.runs) == 1


async def test_bypass_still_runs_with_the_identity_attached() -> None:
    _bus, _a, ex = _executor(mode="bypass")
    tool = _Tool(tier="ask")
    result = await ex.execute(tool, {"a": 1}, config_snapshot={"approval_timeout_s": 5})
    assert result.success and len(tool.runs) == 1


async def test_mission_coordinator_does_not_expose_the_approval_id() -> None:
    from dodaios.missions.tool_approvals import PendingMissionToolApproval

    item = PendingMissionToolApproval(
        trace_id=uuid4(), mission_id="m", worker_id=None, tool_name="t", risk_tier="ask",
        reason="risk_tier", args_preview="", requested_at_ns=0, expires_at_ns=1,
        call_fingerprint="f", approval_id="secret",
    )
    assert "approval_id" not in item.to_dict()
    assert "secret" not in repr(item)


async def _unused() -> None:  # keeps asyncio imported for editors
    await asyncio.sleep(0)
