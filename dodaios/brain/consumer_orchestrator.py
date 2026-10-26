"""Consumer multi-engine orchestration: privacy gate, budget, parallel branches (ADR-0050).

The Consumer owner talks only to Jarvis. Jarvis may fan one task out to several explicitly
connected engines (a subscription integration, an API model, a local model) and assemble ONE
answer. This module is the policy and bookkeeping around that, with no I/O of its own: callers
hand it ready brain objects.

What it guarantees (each one has a test with fake providers):

* Privacy gate before every remote call. The branch prompt is built from the minimum context the
  caller allows for THAT destination. Items marked ``local_only`` never reach a remote branch, nor
  does anything a redactor masks. What left is recorded as destination + categories + counts,
  never as a second copy of the prompt. A remote model's answer is not forwarded to another
  remote model unless the caller passes it through the same gate as a new item.
* One budget for the whole fan-out. ``Budget.reserve`` is atomic across branches. An engine whose
  price is unknown is refused unless the caller opts in; unknown is never counted as zero.
* Connection kinds stay distinct. A branch that needs API access is refused on a ``subscription``
  connection: an app subscription is not an API key, and nothing silently falls back to another
  paid path.
* Bounded, cancellable, joined. Concurrency is capped, every branch has a timeout, a failed branch
  is reported (never hidden), losers are cancelled in ``first_success`` mode, and a result that
  arrives after its turn was cancelled or superseded is dropped.
* Review branches cannot act. Branches run without tools; tool execution stays centralized.
* Under the Work product (``JARVIS_PRODUCT=work``) any non-local branch is refused (ADR-0049).

Not here: choosing which branches to run, the final synthesis call (callers send it through the
same gate), provider terms/retention, real prices, subscription entitlement checks, UI.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from dodaios.brain import engine_policy
from dodaios.core.protocols import BrainMessage, BrainRequest

log = logging.getLogger(__name__)

KINDS = ("subscription", "api", "local")


class OrchestrationError(RuntimeError):
    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.code = code


# -- privacy gate ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextItem:
    text: str
    category: str  # e.g. "task", "note", "file", "history"
    local_only: bool = False


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SECRETISH = re.compile(r"\b(?:sk|pk|ghp|xox[bap]|AKIA)[-_A-Za-z0-9]{12,}\b")
_LONG_NUMBER = re.compile(r"\b\d[\d \-]{10,}\d\b")


def default_redactor(text: str) -> str:
    """Masks emails, key-shaped tokens and long digit runs. A floor, not anonymization."""
    text = _EMAIL.sub("[email]", text)
    text = _SECRETISH.sub("[secret]", text)
    return _LONG_NUMBER.sub("[number]", text)


@dataclass(frozen=True, slots=True)
class EgressRecord:
    """What left the device for one branch. Metadata only: never the prompt text."""

    turn_id: str
    branch_id: str
    destination: str
    categories: tuple[str, ...]
    items: int
    chars: int
    withheld_local_only: int


class PrivacyGate:
    def __init__(
        self,
        *,
        allowed_categories: frozenset[str] = frozenset({"task"}),
        redactor: Callable[[str], str] | None = default_redactor,
        max_chars: int = 8000,
    ) -> None:
        self.allowed_categories = allowed_categories
        self.redactor = redactor
        self.max_chars = max_chars
        self._log: list[EgressRecord] = []
        self._lock = threading.Lock()

    def prepare(
        self,
        items: Sequence[ContextItem],
        *,
        turn_id: str,
        branch_id: str,
        destination: str,
        local: bool,
    ) -> tuple[str, EgressRecord]:
        """The text a branch may see. A local destination keeps everything (still capped)."""
        kept: list[ContextItem] = []
        withheld = 0
        for item in items:
            if not local and item.local_only:
                withheld += 1
            elif not local and item.category not in self.allowed_categories:
                continue
            else:
                kept.append(item)
        parts: list[str] = []
        used = 0
        for item in kept:
            text = item.text if local or self.redactor is None else self.redactor(item.text)
            if used + len(text) > self.max_chars:
                break
            parts.append(text)
            used += len(text)
        record = EgressRecord(
            turn_id,
            branch_id,
            destination,
            tuple(sorted({i.category for i in kept})),
            len(parts),
            used,
            withheld,
        )
        if not local:
            with self._lock:
                self._log.append(record)
        return "\n\n".join(parts), record

    @property
    def egress_log(self) -> tuple[EgressRecord, ...]:
        with self._lock:
            return tuple(self._log)


# -- aggregate budget ------------------------------------------------------------------------------


def _finite(value: Any, *, minimum: float = 0.0, strict: bool = False) -> bool:
    """A real, finite number (not bool, NaN or infinity) at or above ``minimum``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    return value > minimum if strict else value >= minimum


class Budget:
    """One ceiling for the whole fan-out. Amounts are in the caller's unit (e.g. USD)."""

    def __init__(self, limit: float) -> None:
        if not _finite(limit):
            raise ValueError("budget limit must be a finite number >= 0")
        self.limit = float(limit)
        self._reserved = 0.0
        self._lock = threading.Lock()

    def reserve(self, amount: float | None, *, allow_unknown: bool = False) -> bool:
        if amount is None:
            return allow_unknown  # unknown price is not zero: refused unless explicitly allowed
        if not _finite(amount):
            return False
        with self._lock:
            if self._reserved + amount > self.limit:
                return False
            self._reserved += float(amount)
            return True

    def release(self, amount: float | None) -> None:
        """Give back a reservation whose request never started (never below zero)."""
        if amount is None or not _finite(amount):
            return
        with self._lock:
            self._reserved = max(0.0, self._reserved - float(amount))

    @property
    def reserved(self) -> float:
        with self._lock:
            return self._reserved


# -- branches --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Branch:
    branch_id: str
    provider: str
    brain: Any
    kind: str  # subscription | api | local
    authenticated: bool = True
    needs_api: bool = False  # the work requires API access, not an app subscription
    est_cost: float | None = None  # None = unknown
    timeout_s: float = 60.0
    instruction: str = ""


@dataclass(slots=True)
class BranchResult:
    branch_id: str
    provider: str
    status: str  # ok | failed | timeout | refused | cancelled | stale
    text: str = ""
    reason: str = ""
    egress: EgressRecord | None = None
    usage: dict[str, int] = field(default_factory=dict)
    #: Measured, not estimated: None when the branch never produced a token (never a made-up zero).
    first_token_s: float | None = None
    total_s: float | None = None
    #: What this branch consumes: ``subscription_quota`` (plan limits), ``api_charge`` (money) or
    #: ``local_resources`` (CPU/RAM/GPU of this machine). The three are never added together.
    cost_basis: str = ""


class Orchestrator:
    def __init__(
        self,
        gate: PrivacyGate,
        budget: Budget,
        *,
        max_concurrent: int = 3,
        allow_unknown_cost: bool = False,
    ) -> None:
        if (
            not isinstance(max_concurrent, int)
            or isinstance(max_concurrent, bool)
            or max_concurrent < 1
        ):
            raise ValueError("max_concurrent must be an integer >= 1")
        self.gate, self.budget = gate, budget
        self.max_concurrent, self.allow_unknown_cost = max_concurrent, allow_unknown_cost
        self._current: dict[str, int] = {}  # turn_id -> generation

    # a turn's results are valid only while its generation is the current one
    def begin_turn(self, turn_id: str) -> int:
        gen = self._current.get(turn_id, 0) + 1
        self._current[turn_id] = gen
        return gen

    def cancel_turn(self, turn_id: str) -> None:
        self._current[turn_id] = self._current.get(turn_id, 0) + 1

    def _refusal(self, b: Branch) -> str | None:
        if b.kind not in KINDS:
            return f"unknown connection kind {b.kind!r}"
        if not _finite(b.timeout_s, strict=True):
            return "the branch timeout must be a finite number of seconds > 0"
        if not b.authenticated:
            return f"{b.provider} is not authenticated"
        if b.needs_api and b.kind == "subscription":
            return f"{b.provider} is an app subscription connection: it is not API access"
        try:
            engine_policy.check_provider(b.provider)
        except engine_policy.EnginePolicyError as exc:  # reason returned as status=refused
            return str(exc)
        if engine_policy.current_product() == "work" and b.kind != "local":
            return "the Work product runs local engines only"
        if not self.budget.reserve(
            0.0 if b.kind == "local" and b.est_cost is None else b.est_cost,
            allow_unknown=self.allow_unknown_cost,
        ):
            return "aggregate budget would be exceeded or the price is unknown"
        return None

    async def run(
        self,
        turn_id: str,
        items: Sequence[ContextItem],
        branches: Sequence[Branch],
        *,
        first_success: bool = False,
    ) -> list[BranchResult]:
        ids = [b.branch_id for b in branches]
        if len(set(ids)) != len(ids):  # results and tasks are keyed by id: a duplicate hides one
            raise ValueError("branch ids must be unique within one fan-out")
        gen = self.begin_turn(turn_id)
        sem = asyncio.Semaphore(self.max_concurrent)
        results: dict[str, BranchResult] = {}
        tasks: dict[str, asyncio.Task[BranchResult]] = {}

        async def one(b: Branch) -> BranchResult:
            refusal = self._refusal(b)  # a None here means the budget slot is reserved
            if refusal:
                return BranchResult(b.branch_id, b.provider, "refused", reason=refusal)
            state = {"began": False}
            try:
                return await serve(b, state)
            finally:
                if not state["began"]:  # cancelled, stale or refused before any request started
                    unpriced_local = b.kind == "local" and b.est_cost is None
                    self.budget.release(0.0 if unpriced_local else b.est_cost)

        async def serve(b: Branch, state: dict[str, bool]) -> BranchResult:
            # Locality is the REAL destination, never the provider name or the caller's ``kind``
            # (``kind`` only says how the engine is billed). Anything unproven is remote.
            dest = engine_policy.destination_of(b.provider, b.brain)
            local = b.kind == "local" and dest.local
            prompt, record = self.gate.prepare(
                items, turn_id=turn_id, branch_id=b.branch_id, destination=b.provider, local=local
            )
            text = f"{b.instruction}\n\n{prompt}".strip() if b.instruction else prompt
            # no tools: a review/parallel branch never executes anything
            req = BrainRequest(messages=(BrainMessage(role="user", content=text),), tools=())
            out: list[str] = []
            usage: dict[str, int] = {}
            basis = {"subscription": "subscription_quota", "api": "api_charge"}.get(
                b.kind, "local_resources"
            )
            timing: dict[str, float] = {}

            def done(status: str, **fields: Any) -> BranchResult:
                began = timing.get("start")
                return BranchResult(
                    b.branch_id, b.provider, status, egress=record,
                    first_token_s=timing.get("first"),
                    total_s=None if began is None else time.perf_counter() - began,
                    cost_basis=basis, **fields,
                )

            async with sem:
                # A turn cancelled or superseded while this branch waited for a slot must not
                # start a request now (it would be a late, unowned provider call).
                if self._current.get(turn_id) != gen:
                    return done("stale")
                # Bind the approved destination to the call: if the endpoint moved since the
                # prompt was prepared, do not send it (it was cleared for a different host).
                again = engine_policy.destination_of(b.provider, b.brain)
                if (again.local, again.host) != (dest.local, dest.host):
                    return done("refused", reason="the engine endpoint changed before the call")
                started = timing["start"] = time.perf_counter()
                state["began"] = True
                stream: Any = None
                try:
                    async with asyncio.timeout(b.timeout_s):
                        stream = b.brain.complete(req)
                        async for delta in stream:
                            if self._current.get(turn_id) != gen:
                                return done("stale")
                            if delta.tool_call is not None:
                                continue  # tools are centralized: a branch cannot ask to run one
                            if delta.content:
                                timing.setdefault("first", time.perf_counter() - started)
                                out.append(delta.content)
                            if delta.usage:
                                usage = dict(delta.usage)
                except TimeoutError:  # reported to the caller as status=timeout, never swallowed
                    return done("timeout")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a failed branch must not sink the rest
                    log.warning(
                        "branch %s (%s) failed: %s", b.branch_id, b.provider, type(exc).__name__
                    )
                    return done("failed", reason=type(exc).__name__)
                finally:
                    close = getattr(stream, "aclose", None)
                    if close is not None:  # never leave a provider stream or connection open
                        try:
                            await close()
                        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the result
                            log.debug("closing %s stream raised %s", b.provider, type(exc).__name__)
            if self._current.get(turn_id) != gen:
                return done("stale")
            return done("ok", text="".join(out), usage=usage)

        for b in branches:
            tasks[b.branch_id] = asyncio.ensure_future(one(b))
        try:
            pending = set(tasks.values())
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    res = task.result()
                    results[res.branch_id] = res
                    if first_success and res.status == "ok":
                        for loser in pending:
                            loser.cancel()
                        for bid, t in tasks.items():
                            if bid not in results and t in pending:
                                results[bid] = BranchResult(
                                    bid, _provider(branches, bid), "cancelled"
                                )
                        await asyncio.gather(*pending, return_exceptions=True)
                        pending = set()
                        break
        finally:
            for t in tasks.values():
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        return [results[b.branch_id] for b in branches if b.branch_id in results]


def _provider(branches: Sequence[Branch], branch_id: str) -> str:
    return next((b.provider for b in branches if b.branch_id == branch_id), "")


def fan_out_report(results: Sequence[BranchResult], budget: Budget) -> dict[str, Any]:
    """Measured numbers for one fan-out, apart by cost basis. Unknown stays None, never zero."""
    branches = []
    for r in results:
        branches.append({
            "branch": r.branch_id, "provider": r.provider, "status": r.status,
            "cost_basis": r.cost_basis or None, "first_token_s": r.first_token_s,
            "total_s": r.total_s, "usage": dict(r.usage) or None,
            "data_sent": None if r.egress is None else {
                "categories": list(r.egress.categories), "items": r.egress.items,
                "chars": r.egress.chars},
        })
    return {
        "budget_reserved": budget.reserved, "budget_limit": budget.limit,
        "branches_ok": sum(r.status == "ok" for r in results), "branches": branches,
        # Spend and quota are different currencies: report each basis on its own, never one total.
        "by_basis": {basis: sum(r.status == "ok" for r in results if r.cost_basis == basis)
                     for basis in ("subscription_quota", "api_charge", "local_resources")},
    }


__all__ = [
    "Branch",
    "BranchResult",
    "Budget",
    "ContextItem",
    "EgressRecord",
    "fan_out_report",
    "OrchestrationError",
    "Orchestrator",
    "PrivacyGate",
    "default_redactor",
]
