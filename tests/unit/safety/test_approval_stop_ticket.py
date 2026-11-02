"""A tool approval takes its STOP ticket when it is armed and is re-verified when it is used."""
from __future__ import annotations

from uuid import uuid4

import pytest

from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.core.bus import EventBus
from dodaios.safety.approval import ApprovalWorkflow


@pytest.fixture(autouse=True)
def _fresh_controller():
    reset_stop_controller()
    yield
    reset_stop_controller()


async def test_an_approval_given_after_stop_does_not_let_the_tool_run():
    workflow = ApprovalWorkflow(EventBus())
    trace_id = uuid4()
    workflow.arm(trace_id)
    await get_stop_controller().stop()
    await workflow.approve(trace_id, who="late-click")
    approved, reason = await workflow.wait(trace_id, timeout_s=0.05)
    assert approved is False and reason == "stopped_by_owner"


async def test_an_approval_armed_before_stop_stays_refused_after_resume():
    workflow = ApprovalWorkflow(EventBus())
    trace_id = uuid4()
    ticket = workflow.arm(trace_id)
    await get_stop_controller().stop()
    get_stop_controller().resume()  # a NEW generation: the old request is not the owner's now
    await workflow.approve(trace_id, who="old-click")
    assert ticket.peek() == (False, "stopped_by_owner")
    approved, reason = await workflow.wait(trace_id, timeout_s=0.05)
    assert approved is False and reason == "stopped_by_owner"


async def test_an_approval_armed_after_resume_works_normally():
    workflow = ApprovalWorkflow(EventBus())
    await get_stop_controller().stop()
    get_stop_controller().resume()
    trace_id = uuid4()
    workflow.arm(trace_id)
    await workflow.approve(trace_id, who="owner")
    assert await workflow.wait(trace_id, timeout_s=0.05) == (True, "owner")


async def test_a_denial_is_never_turned_into_something_else():
    workflow = ApprovalWorkflow(EventBus())
    trace_id = uuid4()
    workflow.arm(trace_id)
    await get_stop_controller().stop()
    await workflow.deny(trace_id, reason="no")
    assert await workflow.wait(trace_id, timeout_s=0.05) == (False, "no")
