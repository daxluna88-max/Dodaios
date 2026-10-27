"""Checks for code that runs a tool WITHOUT ``ToolExecutor`` (ADR-0044, block 7).

``ToolExecutor`` is the one decision point. A few runners (the skill runner, a remote MCP harness)
call a tool object themselves and have no interactive approver. They must still honour the owner's
STOP and the process-wide gate, and they fail closed: a gate that says ``ask`` cannot be answered
there, so it refuses.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any
from uuid import uuid4

from .gate import GateRequest, coerce_decision, get_default_gate

log = logging.getLogger(__name__)

STOPPED_REFUSAL = "stopped_by_owner"


def current_ticket() -> int:
    """The STOP generation to capture BEFORE any authorization wait starts."""
    from dodaios.control.stop import get_stop_controller  # noqa: PLC0415

    return get_stop_controller().ticket()


def stop_refusal(ticket: int | None = None) -> str | None:
    """A reason when the STOP door is closed (or ``ticket`` predates a STOP), else None."""
    from dodaios.control.stop import get_stop_controller  # noqa: PLC0415

    admission = get_stop_controller().admit(ticket)
    return None if admission.allowed else f"{STOPPED_REFUSAL} ({admission.reason})"


def offline_refusal(tool_name: str) -> str | None:
    """The strict offline profile's refusal for this tool, else None."""
    from dodaios.core import egress_guard  # noqa: PLC0415

    return egress_guard.tool_refusal(tool_name)


def final_refusal(tool_name: str, ticket: int) -> str | None:
    """The check to run IMMEDIATELY before invoking a tool on a path without the executor:
    STOP for the generation captured at the start, then the offline profile. ``ticket`` is
    mandatory on purpose: a bare "is the door open" check passes after STOP and a resume."""
    return stop_refusal(ticket) or offline_refusal(tool_name)


async def unattended_refusal(
    tool_name: str,
    args: dict[str, Any],
    *,
    tier: str = "ask",
    timeout_s: float = 5.0,
    ticket: int | None = None,
) -> str | None:
    """Why a runner without an approver must not run this call, or None to proceed.

    The STOP generation is captured on entry (or taken from ``ticket`` when the caller captured
    it earlier) and rechecked after the gate has answered, together with the offline profile.
    A caller must still run :func:`final_refusal` with the same ticket right before invoking."""
    ticket = current_ticket() if ticket is None else ticket
    stopped = stop_refusal(ticket)
    if stopped:
        return stopped
    offline = offline_refusal(tool_name)
    if offline is not None:
        return offline
    gate = get_default_gate()
    if gate is None:
        return None
    try:
        # A copy: the gate must not be able to change the arguments that are executed.
        snapshot = copy.deepcopy(args)
    except Exception:  # noqa: BLE001 - arguments that cannot be snapshotted are not reviewed
        return "arguments cannot be snapshotted for review"
    request = GateRequest(
        tool_name=tool_name, args=snapshot, tier=tier, approved_by=None, effect="",
        target_origin="", origin_source="", trace_id=uuid4(),
    )
    try:
        decision = coerce_decision(await asyncio.wait_for(gate.review(request), timeout=timeout_s))
    except Exception as exc:  # noqa: BLE001 - a broken gate never allows
        log.warning("preflight gate failed for %s (%r): refusing", tool_name, exc)
        return "gate unavailable"
    # STOP can arrive while the gate was reviewing: the verdict is only good for its generation.
    stopped = stop_refusal(ticket)
    if stopped:
        return stopped
    offline = offline_refusal(tool_name)
    if offline is not None:
        return offline
    if decision.verdict == "allow":
        return None
    suffix = f": {decision.reason}" if decision.reason else ""
    return f"gate {decision.verdict}{suffix} (no approver on this path)"
