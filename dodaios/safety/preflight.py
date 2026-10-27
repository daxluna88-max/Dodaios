"""Checks for code that runs a tool WITHOUT ``ToolExecutor`` (ADR-0044, block 7).

``ToolExecutor`` is the one decision point. A few runners (the skill runner, a remote MCP harness)
call a tool object themselves and have no interactive approver. They must still honour the owner's
STOP, the policy decision (``decision.decide_call``, shared with the executor) and the
process-wide gate, and they fail closed: an ASK cannot be answered there, so it refuses.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable
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


def offline_refusal(tool_name: str, tool: Any = None) -> str | None:
    """The strict offline profile's refusal for this tool, else None. Pass the TOOL OBJECT when
    there is one so its declared capability is consulted (the name alone only matches lists)."""
    from dodaios.core import egress_guard  # noqa: PLC0415

    return egress_guard.tool_refusal(tool_name, tool)


def final_refusal(tool_name: str, ticket: int, tool: Any = None) -> str | None:
    """The check to run IMMEDIATELY before invoking a tool on a path without the executor:
    STOP for the generation captured at the start, then the offline profile. ``ticket`` is
    mandatory on purpose: a bare "is the door open" check passes after STOP and a resume."""
    return stop_refusal(ticket) or offline_refusal(tool_name, tool)


class _DeclaredTool:
    """Stand-in for a tool object the caller does not have (a remote descriptor, no object)."""

    def __init__(self, name: str, source: Any = None) -> None:
        self.name = name
        self.risk_tier = getattr(source, "risk_tier", None)
        self.read_only = getattr(source, "read_only", False)


def _default_evaluator() -> Any:
    from dodaios.core.config import load_config  # noqa: PLC0415

    from .risk_tier import RiskTierEvaluator  # noqa: PLC0415

    return RiskTierEvaluator(load_config().safety)


async def _unattended_decision(
    tool_name: str,
    args: dict[str, Any],
    *,
    timeout_s: float,
    ticket: int | None,
    tool: Any,
    evaluator: Any,
    plan_mode: bool,
    granted_tools: frozenset[str] | None,
) -> tuple[str | None, str]:
    """``(refusal, tier)``. The full boundary for a path without an approver (block 13)."""
    from dodaios.core.tool_read_only import any_chat_read_only  # noqa: PLC0415

    from .decision import decide_call  # noqa: PLC0415

    ticket = current_ticket() if ticket is None else ticket
    stopped = stop_refusal(ticket)
    if stopped:
        return stopped, "ask"
    offline = offline_refusal(tool_name, tool)
    if offline is not None:
        return offline, "ask"
    # The SAME policy decision the executor uses: plan mode, blacklist, risk tier, ASK. Without
    # an approver an ASK is a refusal; a gate "allow" is not an approval. Plan mode is a
    # per-session switch and these paths carry no session, so ANY session in plan mode counts.
    policy_tool = tool if tool is not None else _DeclaredTool(tool_name)
    if getattr(policy_tool, "name", None) != tool_name:
        policy_tool = _DeclaredTool(tool_name, tool)
    try:
        evaluator = evaluator if evaluator is not None else _default_evaluator()
        call = decide_call(
            evaluator, policy_tool, copy.deepcopy(args),
            plan_mode=plan_mode or any_chat_read_only(),
        )
    except Exception as exc:  # noqa: BLE001 - no policy answer, no effect
        log.warning("policy decision failed for %s (%r): refusing", tool_name, exc)
        return "policy unavailable (not run)", "ask"
    if call.verdict == "deny":
        return f"policy deny: {call.reason} (not run)", call.tier
    if call.verdict == "ask" and tool_name not in (granted_tools or frozenset()):
        return (
            f"approval required (risk_tier={call.tier}) and none can be given on this path "
            "(not run)", call.tier,
        )
    gate = get_default_gate()
    if gate is None:
        return None, call.tier
    try:
        # A copy: the gate must not be able to change the arguments that are executed.
        snapshot = copy.deepcopy(args)
    except Exception:  # noqa: BLE001 - arguments that cannot be snapshotted are not reviewed
        return "arguments cannot be snapshotted for review", call.tier
    request = GateRequest(
        tool_name=tool_name, args=snapshot, tier=call.tier, approved_by=None, effect="",
        target_origin="", origin_source="", trace_id=uuid4(),
    )
    try:
        decision = coerce_decision(await asyncio.wait_for(gate.review(request), timeout=timeout_s))
    except Exception as exc:  # noqa: BLE001 - a broken gate never allows
        log.warning("preflight gate failed for %s (%r): refusing", tool_name, exc)
        return "gate unavailable", call.tier
    # STOP can arrive while the gate was reviewing: the verdict is only good for its generation.
    stopped = stop_refusal(ticket)
    if stopped:
        return stopped, call.tier
    offline = offline_refusal(tool_name, tool)
    if offline is not None:
        return offline, call.tier
    if decision.verdict == "allow":
        return None, call.tier
    suffix = f": {decision.reason}" if decision.reason else ""
    return f"gate {decision.verdict}{suffix} (no approver on this path)", call.tier


async def unattended_refusal(
    tool_name: str,
    args: dict[str, Any],
    *,
    timeout_s: float = 5.0,
    ticket: int | None = None,
    tool: Any = None,
    evaluator: Any = None,
    plan_mode: bool = False,
    granted_tools: frozenset[str] | None = None,
) -> str | None:
    """Why a runner without an approver must not run this call, or None to proceed.

    Block 13: this is the full decision boundary, not only the optional gate. After STOP and the
    offline profile it asks :func:`dodaios.safety.decision.decide_call` (the function the
    executor uses): a ``deny`` (blacklist, plan mode, blocked tier) and an ``ask`` both REFUSE,
    because there is no approver; no gate configured, or a gate that says ``allow``, is not an
    approval. ``granted_tools`` is the one way to let an ``ask`` through: exact tool names from a
    validated policy the CALLER holds (nothing in the model's arguments), default none.

    The STOP generation is captured on entry (or taken from ``ticket``) and rechecked after the
    gate has answered. A caller must still run :func:`final_refusal` with the same ticket right
    before invoking."""
    refusal, _tier = await _unattended_decision(
        tool_name, args, timeout_s=timeout_s, ticket=ticket, tool=tool, evaluator=evaluator,
        plan_mode=plan_mode, granted_tools=granted_tools,
    )
    return refusal


async def guarded_call(
    tool_name: str,
    args: dict[str, Any],
    call: Callable[[dict[str, Any]], Awaitable[Any]],
    *,
    tool: Any = None,
    scope: str,
    key: str,
    ticket: int | None = None,
    side_effecting: bool | None = None,
    evaluator: Any = None,
    plan_mode: bool = False,
    granted_tools: frozenset[str] | None = None,
) -> tuple[bool, Any, str | None]:
    """Run ``call`` for a path WITHOUT ``ToolExecutor`` (skill runner, MCP harness), block 9.

    Returns ``(ran, result, refusal)``. Same decisions as the executor where an unattended
    surface can honour them: STOP ticket, offline profile (with the tool object), the shared
    policy decision (plan mode, blacklist, risk tier; an ASK refuses, block 13), gate, then the
    effect ledger. There is no approver here, so a ledger state that the executor would answer
    with a forced confirmation (``unknown``, ``duplicate``) is a REFUSAL, as are ``in_flight``,
    ``replay`` (not re-run) and an unavailable ledger. A raise or cancellation leaves the row
    ``unknown``; a failure is ``unknown`` too unless the tool vouched it applied nothing.
    Arguments are deep-copied once (reviewed copy, fingerprinted copy and executed copy are
    separate). A tool that does not declare ``read_only`` (or any call without a tool object)
    is treated as side-effecting."""
    from dodaios.core.redact import safe_preview  # noqa: PLC0415

    from .effects import (  # noqa: PLC0415
        call_fingerprint,
        get_default_ledger,
        is_side_effecting,
        no_effect_evidence,
    )

    ticket = current_ticket() if ticket is None else ticket
    try:
        snapshot = copy.deepcopy(args)
    except Exception:  # noqa: BLE001
        return False, None, "arguments cannot be snapshotted"
    held, tier = await _unattended_decision(
        tool_name, snapshot, timeout_s=5.0, ticket=ticket, tool=tool, evaluator=evaluator,
        plan_mode=plan_mode, granted_tools=granted_tools,
    )
    if held is not None:
        return False, None, held
    if side_effecting is None:
        try:
            side_effecting = True if tool is None else is_side_effecting(tool, snapshot, tier)
        except Exception:  # noqa: BLE001 - an unknown capability counts as an effect
            side_effecting = True
    fingerprint = call_fingerprint(tool_name, snapshot)
    # No await from the final check to the call below.
    held = final_refusal(tool_name, ticket, tool)
    if held is not None:
        return False, None, held
    ledger = None
    if side_effecting:
        try:
            ledger = get_default_ledger()
            reservation = ledger.reserve(scope, key, tool_name, fingerprint)
        except Exception as exc:  # noqa: BLE001 - an unrecorded effect must not run
            log.error("effect ledger unavailable (%r): refusing %s", exc, tool_name)
            return False, None, "effect-ledger-unavailable (not run: could not record it)"
        if reservation.kind != "reserved":
            return False, None, (
                f"effect-{reservation.kind} (not run: this path has no approver to confirm it)"
            )
    attempt = reservation.attempt if ledger is not None else ""
    try:
        result = await call(copy.deepcopy(snapshot))
    except asyncio.CancelledError:
        if ledger is not None:
            ledger.mark_unknown(scope, key, "cancelled while running", attempt)
        raise
    except Exception as exc:
        if ledger is not None:
            ledger.mark_unknown(scope, key, str(exc), attempt)
        raise
    if ledger is not None:
        ok = bool(getattr(result, "success", True)) and not bool(getattr(result, "isError", False))
        try:
            ledger.finish(
                scope, key, success=ok, attempt=attempt,
                no_effect=(not ok) and no_effect_evidence(tool, result),
                preview=safe_preview(getattr(result, "output", result)),
                error=str(getattr(result, "error", "") or ""),
            )
        except Exception:  # noqa: BLE001 - the row stays "started": unknown next time
            log.error("effect ledger could not record the outcome of %s", tool_name, exc_info=True)
    return True, result, None
