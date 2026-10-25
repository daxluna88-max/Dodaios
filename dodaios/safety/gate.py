"""Optional pre-execution gate for ``ToolExecutor`` (ADR-0044).

The risk tier alone decides which calls pause for a confirmation. An external
policy (for example a decision controller that learns from the owner's past
approvals) may want a say on EVERY call, including ``safe`` and ``monitor``
ones, which publish no approval request and so cannot be stopped from the bus.

A gate can only make a call stricter:

* ``allow`` -- nothing changes;
* ``ask``   -- the call pauses for a confirmation even if its tier would not
  have asked, and even if bypass mode is on (see ``ToolExecutor``);
* ``deny``  -- the call is refused before the tool runs.

A gate that raises, times out or returns anything else counts as ``ask``,
never as ``allow``. With no gate installed the executor behaves exactly as it
did before.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

GateVerdict = Literal["allow", "ask", "deny"]

#: The only verdicts a gate may return. Anything else is treated as ``ask``.
VALID_VERDICTS: frozenset[str] = frozenset({"allow", "ask", "deny"})

#: ``ActionApprovalRequired.reason`` of a confirmation the gate forced. A
#: bridge that auto-approves for the tier (``MissionToolAutoApprover``) only
#: answers ``risk_tier`` and ``plausibility``, so it never answers this one.
GATE_REASON = "gate"


@dataclass(frozen=True, slots=True)
class GateRequest:
    """What the gate is shown about one tool call. Never holds secrets
    beyond the arguments the tool itself received."""

    tool_name: str
    args: dict[str, Any]
    tier: str
    approved_by: str | None  # "whitelist" when a standing allow rule matched
    effect: str
    target_origin: str
    origin_source: str
    trace_id: UUID


@dataclass(frozen=True, slots=True)
class GateDecision:
    verdict: GateVerdict = "allow"
    reason: str = ""


class ToolGate(Protocol):
    async def review(self, request: GateRequest) -> GateDecision: ...


def coerce_decision(raw: object) -> GateDecision:
    """Normalise whatever a gate returned; an unknown shape fails closed."""
    if isinstance(raw, GateDecision) and raw.verdict in VALID_VERDICTS:
        return raw
    return GateDecision("ask", "gate returned an invalid decision")


_default_gate: ToolGate | None = None


def set_default_gate(gate: ToolGate | None) -> None:
    """Install (or clear) the process-wide gate used by every ``ToolExecutor``
    that has no gate of its own. The app has several executor construction
    sites; an integration installs its gate once here instead of patching each.
    """
    global _default_gate
    _default_gate = gate


def get_default_gate() -> ToolGate | None:
    return _default_gate
