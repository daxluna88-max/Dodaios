"""Block 13: GPT round 3 (G1 bound approval request identity, G3 unattended policy boundary).

WRITTEN, NOT RUN (owner order: no test execution in this change). Run before relying on them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from dodaios.core.bus import EventBus
from dodaios.core.events import ActionApprovalRequired, ActionApproved, ActionDenied
from dodaios.missions.tool_approvals import MissionToolApprovalCoordinator
from dodaios.safety.approval import ApprovalWorkflow
from dodaios.safety.decision import decide_call
from dodaios.safety.preflight import guarded_call, unattended_refusal
from dodaios.ui.web.missions_routes import router

pytestmark = pytest.mark.asyncio


class _Manager:
    async def mission(self, mission_id: str) -> object | None:
        return SimpleNamespace(id=mission_id)


def _required(
    trace, *, approval_id="req-1", fp="fp-1", tool="gmail/send",
) -> ActionApprovalRequired:
    return ActionApprovalRequired(
        trace_id=trace, tool_name=tool, risk_tier="ask", reason="risk_tier", args_preview="{}",
        expires_at_ns=time.time_ns() + 30_000_000_000, mission_id="m1",
        call_fingerprint=fp, approval_id=approval_id,
    )


async def _setup():
    bus = EventBus()
    coord = MissionToolApprovalCoordinator(bus)
    seen: list[ActionApproved] = []

    async def _cap(e: ActionApproved) -> None:
        seen.append(e)

    bus.subscribe(ActionApproved, _cap)
    return bus, coord, seen


async def test_pending_payload_exposes_request_id() -> None:
    bus, coord, _ = await _setup()
    t = uuid4()
    await bus.publish(_required(t))
    (item,) = await coord.list_pending("m1")
    d = item.to_dict()
    assert d["request_id"] == "req-1" and "approval_id" not in d


@pytest.mark.parametrize(
    "kw",
    [
        {},  # nothing at all
        {"expected_fingerprint": "fp-1", "expected_tool": "gmail/send"},  # request id missing
        {"expected_request_id": "req-1", "expected_tool": "gmail/send"},  # fingerprint missing
        {"expected_request_id": "req-1", "expected_fingerprint": "fp-1"},  # tool missing
        {"expected_request_id": "old", "expected_fingerprint": "fp-1",
         "expected_tool": "gmail/send"},
        {"expected_request_id": "req-1", "expected_fingerprint": "fp-1",
         "expected_tool": "other"},
    ],
)
async def test_approve_without_exact_identity_consumes_nothing(kw) -> None:
    bus, coord, seen = await _setup()
    t = uuid4()
    await bus.publish(_required(t))
    assert await coord.approve("m1", t, approved_by="user", **kw) is None
    assert seen == []
    assert len(await coord.list_pending("m1")) == 1  # still there for the real decision


async def test_exact_identity_approves_once_and_is_spent() -> None:
    bus, coord, seen = await _setup()
    t = uuid4()
    await bus.publish(_required(t))
    ok = dict(expected_request_id="req-1", expected_fingerprint="fp-1", expected_tool="gmail/send")
    assert await coord.approve("m1", t, approved_by="user", **ok) is not None
    assert seen[0].approval_id == "req-1"
    assert await coord.approve("m1", t, approved_by="user", **ok) is None  # one use


async def test_late_old_id_does_not_approve_second_identical_proposal_same_trace() -> None:
    bus, coord, seen = await _setup()
    t = uuid4()
    await bus.publish(_required(t, approval_id="req-1"))
    await bus.publish(_required(t, approval_id="req-2"))  # same trace, same fingerprint
    old = dict(expected_request_id="req-1", expected_fingerprint="fp-1", expected_tool="gmail/send")
    assert await coord.approve("m1", t, approved_by="user", **old) is None
    assert seen == []


async def test_request_without_identity_cannot_be_approved() -> None:
    bus, coord, seen = await _setup()
    t = uuid4()
    await bus.publish(_required(t, approval_id="", fp=""))
    got = await coord.approve(
        "m1", t, approved_by="user",
        expected_request_id="x", expected_fingerprint="y", expected_tool="gmail/send")
    assert got is None and seen == []


async def test_deny_works_with_and_without_identity() -> None:
    bus, coord, _ = await _setup()
    denied: list[ActionDenied] = []

    async def _cap(e: ActionDenied) -> None:
        denied.append(e)

    bus.subscribe(ActionDenied, _cap)
    t1, t2 = uuid4(), uuid4()
    await bus.publish(_required(t1))
    await bus.publish(_required(t2))
    assert await coord.deny("m1", t1, reason="no") is not None
    assert await coord.deny("m1", t2, reason="no", expected_request_id="req-1") is not None
    assert len(denied) >= 2


async def test_rejected_legacy_approval_does_not_hide_pending_decision() -> None:
    bus, coord, _ = await _setup()
    t = uuid4()
    await bus.publish(_required(t))
    await bus.publish(ActionApproved(trace_id=t, tool_name="gmail/send"))  # legacy, no identity
    await bus.publish(ActionApproved(
        trace_id=t, tool_name="gmail/send", call_fingerprint="fp-1", approval_id="WRONG"))
    assert len(await coord.list_pending("m1")) == 1
    await bus.publish(ActionApproved(
        trace_id=t, tool_name="gmail/send", call_fingerprint="fp-1", approval_id="req-1"))
    assert await coord.list_pending("m1") == ()


async def test_workflow_still_rejects_legacy_event_and_coordinator_agrees() -> None:
    bus = EventBus()
    wf = ApprovalWorkflow(bus)
    t = uuid4()
    ticket = wf.arm(t, tool_name="gmail/send", fingerprint="fp-1")
    await bus.publish(ActionApproved(trace_id=t, tool_name="gmail/send"))
    assert ticket.peek() is None
    ticket.close()


async def _client():
    bus = EventBus()
    app = FastAPI()
    app.include_router(router)
    app.state.mission_manager = _Manager()
    app.state.mission_tool_approvals = MissionToolApprovalCoordinator(bus)
    transport = httpx.ASGITransport(app=app)
    return bus, app, httpx.AsyncClient(transport=transport, base_url="http://t")


@pytest.mark.parametrize("body", [None, {}, {"call_fingerprint": "fp-1", "generation": 0},
                                  {"request_id": "", "tool_name": "gmail/send",
                                   "call_fingerprint": "fp-1", "generation": 0}])
async def test_http_approve_without_identity_is_4xx_and_consumes_nothing(body) -> None:
    bus, app, client = await _client()
    t = uuid4()
    await bus.publish(_required(t))
    url = f"/api/missions/m1/tool-approvals/{t}/approve"
    r = await (client.post(url) if body is None else client.post(url, json=body))
    assert 400 <= r.status_code < 500
    assert len(await app.state.mission_tool_approvals.list_pending("m1")) == 1


async def test_http_deny_without_body_identity_still_works() -> None:
    bus, app, client = await _client()
    t = uuid4()
    await bus.publish(_required(t))
    r = await client.post(f"/api/missions/m1/tool-approvals/{t}/deny", json={"reason": "no"})
    assert r.status_code == 200


# ---- G3: the unattended boundary uses the same decision as the executor ----


@dataclass
class _Eval:
    tier: str = "ask"
    blocked: bool = False

    def evaluate(self, tool, args):
        from dodaios.safety.risk_tier import ActionBlocked, TierDecision
        if self.blocked:
            raise ActionBlocked("p", "m")
        return TierDecision(tier=self.tier, approved_by=None, matched_pattern=None,
                            command_string=tool.name)

    def needs_user_confirmation(self, d):
        return d.tier in ("ask", "monitor_never")


class _Tool:
    name = "t"
    read_only = False
    risk_tier = "ask"


async def test_decide_call_ask_deny_allow_and_plan_mode() -> None:
    assert decide_call(_Eval("ask"), _Tool(), {}).verdict == "ask"
    assert decide_call(_Eval("safe"), _Tool(), {}).verdict == "allow"
    assert decide_call(_Eval(blocked=True), _Tool(), {}).verdict == "deny"
    assert decide_call(_Eval("safe"), _Tool(), {}, plan_mode=True).verdict == "deny"


async def test_unattended_ask_with_no_gate_refuses() -> None:
    from dodaios.safety.gate import set_default_gate
    set_default_gate(None)
    why = await unattended_refusal("t", {}, tool=_Tool(), evaluator=_Eval("ask"))
    assert why and "approval required" in why


async def test_unattended_deny_and_plan_mode_refuse_even_for_safe_tier() -> None:
    assert await unattended_refusal("t", {}, tool=_Tool(), evaluator=_Eval(blocked=True))
    assert await unattended_refusal("t", {}, tool=_Tool(), evaluator=_Eval("safe"), plan_mode=True)


async def test_unattended_safe_proceeds_and_exact_grant_lets_ask_through() -> None:
    assert await unattended_refusal("t", {}, tool=_Tool(), evaluator=_Eval("safe")) is None
    assert await unattended_refusal(
        "t", {}, tool=_Tool(), evaluator=_Eval("ask"), granted_tools=frozenset({"t"})) is None
    assert await unattended_refusal(
        "t", {}, tool=_Tool(), evaluator=_Eval("ask"), granted_tools=frozenset({"t2"}))


async def test_guarded_call_ask_does_not_run_the_tool() -> None:
    ran: list[int] = []

    async def _call(a):
        ran.append(1)

    ok, _res, why = await guarded_call(
        "t", {}, _call, tool=_Tool(), scope="s13", key="k", evaluator=_Eval("ask"))
    assert not ok and ran == [] and why
