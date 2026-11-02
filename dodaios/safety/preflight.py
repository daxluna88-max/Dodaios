"""Checks for code that runs a tool WITHOUT ``ToolExecutor`` (ADR-0044, block 7).

``ToolExecutor`` is the one decision point. A few runners (the skill runner, a remote MCP harness)
call a tool object themselves and have no interactive approver. They must still honour the owner's
STOP and the process-wide gate, and they fail closed: a gate that says ``ask`` cannot be answered
there, so it refuses.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from .gate import GateRequest, coerce_decision, get_default_gate

log = logging.getLogger(__name__)

STOPPED_REFUSAL = "stopped_by_owner"


def stop_refusal(ticket: int | None = None) -> str | None:
    """A reason when the STOP door is closed (or ``ticket`` predates a STOP), else None."""
    from dodaios.control.stop import get_stop_controller  # noqa: PLC0415

    admission = get_stop_controller().admit(ticket)
    return None if admission.allowed else f"{STOPPED_REFUSAL} ({admission.reason})"


async def unattended_refusal(
    tool_name: str, args: dict[str, Any], *, tier: str = "ask", timeout_s: float = 5.0
) -> str | None:
    """Why a runner without an approver must not run this call, or None to proceed."""
    stopped = stop_refusal()
    if stopped:
        return stopped
    gate = get_default_gate()
    if gate is None:
        return None
    request = GateRequest(
        tool_name=tool_name, args=dict(args), tier=tier, approved_by=None, effect="",
        target_origin="", origin_source="", trace_id=uuid4(),
    )
    try:
        decision = coerce_decision(await asyncio.wait_for(gate.review(request), timeout=timeout_s))
    except Exception as exc:  # noqa: BLE001 - a broken gate never allows
        log.warning("preflight gate failed for %s (%r): refusing", tool_name, exc)
        return "gate unavailable"
    if decision.verdict == "allow":
        return None
    suffix = f": {decision.reason}" if decision.reason else ""
    return f"gate {decision.verdict}{suffix} (no approver on this path)"
