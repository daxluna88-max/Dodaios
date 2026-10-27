"""The one policy decision for a tool call: plan mode, blacklist, tier, ASK (ADR-0044, block 13).

``ToolExecutor.execute`` and the paths that run a tool WITHOUT the executor (``preflight`` for the
skill runner and the remote MCP harness) call :func:`decide_call`, so there is a single place that
says "deny", "ask" or "allow". What happens with an ``ask`` is the caller's: the executor obtains
an approval; an unattended path has nobody to ask and refuses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .risk_tier import ActionBlocked, RiskTierEvaluator, TierDecision

PLAN_MODE_REASON = "Plan mode permits reads only"


def plan_mode_refusal(tool: Any, args: dict[str, Any]) -> str | None:
    """The refusal when plan mode is on and this call is not a declared read, else None."""
    from dodaios.core.tool_read_only import allows_read  # noqa: PLC0415

    return None if allows_read(tool, args) else PLAN_MODE_REASON


@dataclass(frozen=True, slots=True)
class CallDecision:
    """``deny``: never runs. ``ask``: needs an explicit approval. ``allow``: no approval needed."""

    verdict: Literal["allow", "ask", "deny"]
    tier: str
    reason: str = ""
    decision: TierDecision | None = None
    blocked: ActionBlocked | None = None


def decide_call(
    evaluator: RiskTierEvaluator,
    tool: Any,
    args: dict[str, Any],
    *,
    plan_mode: bool = False,
) -> CallDecision:
    """Plan mode (reads only), then blacklist / whitelist / tier default, then the ASK rule.

    A broken evaluator denies: this function never answers ``allow`` by failing."""
    if plan_mode:
        refused = plan_mode_refusal(tool, args)
        if refused is not None:
            return CallDecision("deny", "ask", refused)
    try:
        decision = evaluator.evaluate(tool, args)
    except ActionBlocked as exc:
        return CallDecision("deny", "block", f"blacklist: {exc.pattern}", blocked=exc)
    except Exception as exc:  # noqa: BLE001 - fail closed
        return CallDecision("deny", "ask", f"risk evaluation failed: {exc}")
    try:
        needs = bool(evaluator.needs_user_confirmation(decision))
    except Exception as exc:  # noqa: BLE001 - fail closed
        return CallDecision("deny", decision.tier, f"risk evaluation failed: {exc}")
    return CallDecision("ask" if needs else "allow", decision.tier, decision=decision)
