"""The optional pre-execution gate on ``ToolExecutor`` (ADR-0044).

The gate may only make a call stricter. These tests pin that: ``allow`` changes
nothing, ``ask`` forces a confirmation even on ``safe``/``monitor`` tiers and in
bypass mode, ``deny`` refuses before the tool runs, and any failure of the gate
itself counts as ``ask``.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dodaios.core.bus import EventBus
from dodaios.core.config import SafetyConfig
from dodaios.core.events import (
    ActionApprovalRequired,
    ActionApproved,
    ActionDenied,
)
from dodaios.core.protocols import ExecutionContext, ToolResult
from dodaios.safety.approval import ApprovalWorkflow
from dodaios.safety.approval_surface import INTERACTIVE
from dodaios.safety.gate import GATE_REASON, GateDecision, GateRequest
from dodaios.safety.risk_tier import RiskTierEvaluator
from dodaios.safety.tool_executor import ToolExecutor

pytestmark = pytest.mark.asyncio


class _Tool:
    name = "search_web"
    risk_tier = "safe"
    schema: dict[str, Any] = {}

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, output="ok")


class _MonitorTool(_Tool):
    name = "mcp_read"
    risk_tier = "monitor"


class _Gate:
    def __init__(self, decision: Any = None, *, exc: Exception | None = None,
                 delay: float = 0.0) -> None:
        self.decision = decision if decision is not None else GateDecision("allow")
        self.exc = exc
        self.delay = delay
        self.requests: list[GateRequest] = []

    async def review(self, request: GateRequest) -> Any:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.decision


def _build(gate: Any, *, mode: str = "ask", gate_timeout_s: float = 5.0):
    bus = EventBus()
    approval = ApprovalWorkflow(bus, timeout_s=5.0)
    executor = ToolExecutor(
        bus,
        RiskTierEvaluator(SafetyConfig(approval_mode=mode)),
        approval,
        default_timeout_s=5.0,
        gate=gate,
        gate_timeout_s=gate_timeout_s,
    )
    required: list[ActionApprovalRequired] = []
    denied: list[ActionDenied] = []

    async def _on_required(e: ActionApprovalRequired) -> None:
        required.append(e)

    async def _on_denied(e: ActionDenied) -> None:
        denied.append(e)

    bus.subscribe(ActionApprovalRequired, _on_required)
    bus.subscribe(ActionDenied, _on_denied)
    return executor, bus, required, denied


_SNAP = {"approval_surface": INTERACTIVE}


async def test_no_gate_leaves_safe_tools_untouched() -> None:
    executor, _bus, required, _d = _build(None)
    tool = _Tool()
    result = await executor.execute(tool, {}, config_snapshot=_SNAP)
    assert result.success and tool.calls == 1
    assert required == []


async def test_allow_changes_nothing() -> None:
    gate = _Gate(GateDecision("allow"))
    executor, _bus, required, _d = _build(gate)
    tool = _Tool()
    result = await executor.execute(tool, {"q": "x"}, config_snapshot=_SNAP)
    assert result.success and tool.calls == 1
    assert required == []
    assert gate.requests and gate.requests[0].tool_name == "search_web"
    assert gate.requests[0].tier == "safe"


@pytest.mark.parametrize("tool_cls", [_Tool, _MonitorTool])
async def test_ask_forces_confirmation_on_low_tiers(tool_cls: type[_Tool]) -> None:
    executor, bus, required, _d = _build(_Gate(GateDecision("ask", "learned")))
    tool = tool_cls()

    async def _approve(e: ActionApprovalRequired) -> None:
        await bus.publish(ActionApproved(trace_id=e.trace_id, tool_name=e.tool_name,
                                         approved_by="user",
                                         call_fingerprint=e.call_fingerprint,
                                         approval_id=e.approval_id))

    bus.subscribe(ActionApprovalRequired, _approve)
    result = await executor.execute(tool, {}, config_snapshot=_SNAP)
    assert [e.reason for e in required] == [GATE_REASON]
    assert result.success and tool.calls == 1


async def test_ask_is_not_bypassed_by_bypass_mode() -> None:
    executor, _bus, required, denied = _build(_Gate(GateDecision("ask")), mode="bypass")
    tool = _Tool()
    result = await executor.execute(tool, {}, config_snapshot=_SNAP)
    # Nobody answers: the call must not have run on its own.
    assert tool.calls == 0
    assert [e.reason for e in required] == [GATE_REASON]
    assert result.success is False
    assert denied  # refused (timeout/denied), never silently approved


async def test_deny_refuses_before_the_tool_runs() -> None:
    executor, _bus, required, denied = _build(_Gate(GateDecision("deny", "no way")))
    tool = _Tool()
    result = await executor.execute(tool, {}, config_snapshot=_SNAP)
    assert tool.calls == 0
    assert result.success is False
    assert "gate-denied" in (result.error or "")
    assert required == []
    assert [e.reason for e in denied] == ["gate: no way"]


@pytest.mark.parametrize(
    "gate",
    [
        _Gate(exc=RuntimeError("boom")),
        _Gate(delay=0.5),  # exceeds the 0.05 s timeout below
        _Gate("allow"),  # not a GateDecision
        _Gate(GateDecision("maybe")),  # type: ignore[arg-type]
    ],
)
async def test_gate_failure_counts_as_ask(gate: _Gate) -> None:
    executor, _bus, required, _d = _build(gate, mode="bypass", gate_timeout_s=0.05)
    tool = _Tool()
    await executor.execute(tool, {}, config_snapshot=_SNAP)
    assert tool.calls == 0
    assert [e.reason for e in required] == [GATE_REASON]


async def test_default_gate_applies_to_executors_without_their_own() -> None:
    from dodaios.safety.gate import set_default_gate

    gate = _Gate(GateDecision("deny", "no"))
    set_default_gate(gate)
    try:
        executor, _bus, _r, denied = _build(None)
        tool = _Tool()
        result = await executor.execute(tool, {}, config_snapshot=_SNAP)
    finally:
        set_default_gate(None)
    assert not result.success and tool.calls == 0
    assert denied and gate.requests


async def test_own_gate_wins_over_default_gate() -> None:
    from dodaios.safety.gate import set_default_gate

    default = _Gate(GateDecision("deny", "no"))
    set_default_gate(default)
    try:
        executor, _bus, _r, _d = _build(_Gate(GateDecision("allow")))
        tool = _Tool()
        result = await executor.execute(tool, {}, config_snapshot=_SNAP)
    finally:
        set_default_gate(None)
    assert result.success and default.requests == []


class _AskTool(_Tool):
    name = "send_mail"
    risk_tier = "ask"


async def _deny_on_request(bus: EventBus) -> None:
    async def _deny(e: ActionApprovalRequired) -> None:
        await bus.publish(ActionDenied(trace_id=e.trace_id, tool_name=e.tool_name, reason="owner said no"))

    bus.subscribe(ActionApprovalRequired, _deny)


async def test_gate_ask_on_top_of_tier_ask_is_not_bypassed() -> None:
    executor, bus, required, _d = _build(_Gate(GateDecision("ask")), mode="bypass")
    approved: list[ActionApproved] = []

    async def _on_approved(e: ActionApproved) -> None:
        approved.append(e)

    bus.subscribe(ActionApproved, _on_approved)
    await _deny_on_request(bus)
    tool = _AskTool()
    result = await executor.execute(tool, {}, config_snapshot=_SNAP)
    assert tool.calls == 0 and not result.success
    assert [e.reason for e in required] == [GATE_REASON]  # not "risk_tier"
    assert [e for e in approved if e.approved_by == "bypass"] == []


async def test_gate_ask_on_top_of_plausibility_keeps_the_gate_reason() -> None:
    executor, bus, required, _d = _build(_Gate(GateDecision("ask")), mode="bypass")
    executor._evaluate_plausibility = lambda tool, decision: type(  # type: ignore[method-assign]
        "P", (), {"reason": "low_confidence", "require_confirmation": True}
    )()
    await _deny_on_request(bus)
    tool = _Tool()
    result = await executor.execute(tool, {}, config_snapshot=_SNAP)
    assert tool.calls == 0 and not result.success
    assert [e.reason for e in required] == [GATE_REASON]  # so auto-approvers skip it
