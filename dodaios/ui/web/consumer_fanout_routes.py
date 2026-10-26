"""Consumer fan-out: send one task to several explicitly chosen engines (ADR-0050).

Opt-in and explicit. Nothing here runs by itself and nothing is chosen for the owner: the request
names the branches, the ceiling for the whole fan-out and, per item of context, whether it may leave
this machine at all. The privacy gate decides what each remote branch sees, the budget is one atomic
ceiling, a branch whose price is unknown is refused unless the request says otherwise, and every
branch runs WITHOUT tools. The route returns each branch's answer and the measured report; it does
not synthesize a final answer (that is the caller's step and goes through the same gate).

Refused under the Work product (local engines only, ADR-0049): HTTP 403.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dodaios.brain import engine_policy
from dodaios.brain.consumer_orchestrator import (
    Branch,
    Budget,
    ContextItem,
    Orchestrator,
    PrivacyGate,
    fan_out_report,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/consumer", tags=["consumer"])

#: Plan-login providers: they spend a subscription quota, not an API key.
SUBSCRIPTION_PROVIDERS = frozenset({"codex", "claude-cli", "antigravity"})
MAX_BRANCHES = 6
MAX_ITEMS = 20
MAX_TEXT = 20_000


class ContextBody(BaseModel):
    text: str = Field(max_length=MAX_TEXT)
    category: str = Field(default="task", max_length=40)
    #: True: never sent to a remote branch, whatever the category.
    local_only: bool = False


class BranchBody(BaseModel):
    provider: str = Field(max_length=60)
    model: str | None = Field(default=None, max_length=200)
    #: Estimated cost of this branch in the request's budget unit; null = unknown.
    est_cost: float | None = None
    instruction: str = Field(default="", max_length=2000)
    timeout_s: float = Field(default=60.0, gt=0, le=300)
    needs_api: bool = False


class FanOutBody(BaseModel):
    task: str = Field(min_length=1, max_length=MAX_TEXT)
    context: list[ContextBody] = Field(default_factory=list, max_length=MAX_ITEMS)
    branches: list[BranchBody] = Field(min_length=1, max_length=MAX_BRANCHES)
    #: Ceiling for the WHOLE fan-out (not per branch), in the unit of ``est_cost``.
    budget: float = Field(ge=0)
    allow_unknown_cost: bool = False
    first_success: bool = False
    #: Categories a remote branch may receive. Local-only items never go, whatever is listed here.
    allowed_categories: list[str] = Field(default_factory=lambda: ["task"], max_length=10)
    max_concurrent: int = Field(default=3, ge=1, le=6)
    #: When set, ONE final answer is written by this engine from the branch answers, through the
    #: same privacy gate, budget and cancellation as the branches.
    synthesizer: BranchBody | None = None


def connection_kind(provider: str) -> str:
    if provider in engine_policy.LOCAL_PROVIDERS:
        return "local"
    return "subscription" if provider in SUBSCRIPTION_PROVIDERS else "api"


def _authenticated(provider: str, kind: str, registry: Any) -> bool:
    """Whether the provider is usable now, WITHOUT contacting it (no call, no spend)."""
    if kind == "local":
        return True
    if kind == "subscription":
        probe = getattr(registry.get_class(provider), "subscription_connected", None)
        try:
            return bool(probe()) if callable(probe) else False
        except Exception:  # noqa: BLE001 - an unreadable login state means not connected
            log.debug("subscription probe for %s failed", provider, exc_info=True)
            return False
    from dodaios.core.config import get_provider_secret  # noqa: PLC0415

    return bool(get_provider_secret(provider))


def _status_rows() -> list[dict[str, Any]]:
    from dodaios.brain.provider_registry import BrainProviderRegistry  # noqa: PLC0415

    registry = BrainProviderRegistry()
    rows = []
    for name in sorted(registry.available()):
        kind = connection_kind(name)
        # "local" is where the call goes (loopback), not how it is billed (``kind``).
        rows.append({"id": name, "kind": kind,
                     "local": engine_policy.destination_of(name).local,
                     "authenticated": _authenticated(name, kind, registry)})
    return rows


@router.get("/status", summary="Whether fan-out is available and which engines could take part")
async def consumer_status() -> dict[str, Any]:
    """Providers the owner could pick, with their connection kind and whether they are usable now.

    Reads stored credentials and plan-login state only (off the event loop: the keyring can be
    slow); it never contacts a provider. Under the Work product (local only) fan-out is not
    available and the list is empty.
    """
    if engine_policy.current_product() == "work":
        return {"available": False, "reason": "work_local_only", "providers": []}
    return {"available": True, "reason": "", "providers": await asyncio.to_thread(_status_rows)}


@router.post("/fanout", summary="Send one task to several chosen engines")
async def consumer_fanout(body: FanOutBody) -> dict[str, Any]:
    from dodaios.brain.provider_registry import BrainProviderRegistry  # noqa: PLC0415

    if engine_policy.current_product() == "work":
        raise HTTPException(
            status_code=403,
            detail="Parallel remote engines are not available in the Work product (local only).",
        )
    registry = BrainProviderRegistry()
    known = set(registry.available())
    branches: list[Branch] = []
    refused: list[dict[str, Any]] = []
    def build(spec: BranchBody, branch_id: str) -> Branch | None:
        provider = spec.provider.strip().lower()
        if provider not in known:
            refused.append({"branch": branch_id, "provider": provider, "status": "refused",
                            "reason": f"{provider!r} is not an installed provider"})
            return None
        kind = connection_kind(provider)
        try:
            brain = (registry.instantiate(provider, model=spec.model) if spec.model
                     else registry.instantiate(provider))
        except engine_policy.EnginePolicyError as exc:  # reason returned to caller
            refused.append({"branch": branch_id, "provider": provider, "status": "refused",
                            "reason": str(exc)})
            return None
        except Exception as exc:  # noqa: BLE001 - one unbuildable provider must not fail the request
            log.warning("fan-out provider %s could not be built: %s", provider, type(exc).__name__)
            refused.append({"branch": branch_id, "provider": provider, "status": "refused",
                            "reason": f"could not be built ({type(exc).__name__})"})
            return None
        return Branch(
            branch_id, provider, brain, kind,
            authenticated=_authenticated(provider, kind, registry),
            needs_api=spec.needs_api, est_cost=spec.est_cost, timeout_s=spec.timeout_s,
            instruction=spec.instruction)

    for index, spec in enumerate(body.branches):
        built = build(spec, f"b{index + 1}")
        if built is not None:
            branches.append(built)
    synthesizer = build(body.synthesizer, "synthesis") if body.synthesizer else None
    items = [ContextItem(body.task, "task"),
             *(ContextItem(c.text, c.category, c.local_only) for c in body.context)]
    gate = PrivacyGate(allowed_categories=frozenset(body.allowed_categories) | {"task"})
    orchestrator = Orchestrator(gate, Budget(body.budget), max_concurrent=body.max_concurrent,
                                allow_unknown_cost=body.allow_unknown_cost)
    results = await orchestrator.run("fanout", items, branches, first_success=body.first_success)
    final: dict[str, Any] | None = None
    if body.synthesizer is not None:
        if synthesizer is None:
            final = {"status": "refused", "text": "", "reason": "the synthesizer is unavailable",
                     "sources": [], "withheld": 0}
        else:
            synth = await orchestrator.synthesize("fanout", items, results, synthesizer)
            final = {"status": synth.status, "text": synth.text, "reason": synth.reason,
                     "sources": list(synth.sources), "withheld": synth.withheld}
    report = fan_out_report(results, orchestrator.budget)
    return {
        "final": final,
        "branches": [{"branch": r.branch_id, "provider": r.provider, "status": r.status,
                      "text": r.text, "reason": r.reason} for r in results] + refused,
        "report": report,
        "egress": [{"branch": e.branch_id, "destination": e.destination,
                    "categories": list(e.categories), "items": e.items, "chars": e.chars,
                    "withheld_local_only": e.withheld_local_only} for e in gate.egress_log],
    }
