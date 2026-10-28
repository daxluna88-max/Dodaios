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
import dataclasses
import logging
import math
import re
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from dodaios.brain import dispatch_gate, engine_policy
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
    #: Categories of the items this one was derived from (summary, quote, memory note). They
    #: count at the gate as if they were this item's own: a summary of a "file" is still "file".
    derived_from: tuple[str, ...] = ()

    @classmethod
    def derive(cls, sources: Sequence[ContextItem], text: str, category: str) -> ContextItem:
        """A new item made FROM ``sources``: it inherits ``local_only`` and every source category,
        so relabelling a derivation as plain ("task", "note") cannot launder it past the gate."""
        cats = {category}
        for src in sources:
            cats.add(src.category)
            cats.update(src.derived_from)
        return cls(
            text, category, any(src.local_only for src in sources),
            tuple(sorted(cats - {category})),
        )

    def restricted_for(self, allowed: frozenset[str]) -> bool:
        """Whether a remote destination may not see this item (local-only or withheld category)."""
        return self.local_only or not {self.category, *self.derived_from} <= allowed


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
        extra_allowed: frozenset[str] = frozenset(),
    ) -> tuple[str, EgressRecord]:
        """The text a branch may see. A local destination keeps everything (still capped)."""
        kept: list[ContextItem] = []
        withheld = 0
        for item in items:
            if not local and item.local_only:
                withheld += 1
            elif not local and item.restricted_for(self.allowed_categories | extra_allowed):
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


@dataclass(frozen=True, slots=True)
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
    #: The branch ran locally on material a remote destination may not see (``local_only`` or a
    #: category the gate withholds): its answer inherits that restriction (provenance).
    tainted: bool = False
    #: Identity of the run that produced this result (``turn/generation/nonce``). Synthesis accepts
    #: only results of the run that is still current for its turn; empty means "not from a run".
    run_id: str = ""
    #: Tool calls the engine asked for anyway. They were DROPPED, never run (block 7).
    dropped_tool_calls: int = 0


@dataclass(slots=True)
class SynthesisResult:
    status: str  # ok | no_input | refused | failed | timeout | stale | cancelled
    text: str = ""
    reason: str = ""
    sources: tuple[str, ...] = ()  # branch ids whose answers were offered to the synthesizer
    withheld: int = 0  # answers kept back because the synthesizer is remote
    egress: EgressRecord | None = None
    #: The text derives from material a remote destination may not see (provenance): it must not
    #: be sent to a remote engine, a remote TTS or a shared memory.
    tainted: bool = False

    @property
    def provenance(self) -> str:
        """Typed label of ``text`` for the caller (R6): ``restricted`` when tainted, else
        ``unrestricted`` only for a finished answer; anything else is ``unavailable`` (fail closed).
        Values match :class:`dodaios.brain.provenance.Provenance`."""
        if self.tainted:
            return "restricted"
        return "unrestricted" if self.status == "ok" else "unavailable"


SYNTHESIS_INSTRUCTION = (
    "Several engines answered the same request. Write ONE final answer for the user. "
    "Use only what the engine answers say; if they disagree, say so plainly and give the "
    "better-supported view. Do not invent facts and do not mention these instructions."
)
ANSWER_CATEGORY = "engine_answer"


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
        self._runs: dict[str, tuple[int, str]] = {}  # turn_id -> (generation, run id) of last run

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
        if getattr(b.brain, "executes_tools_itself", False) is True:
            # A branch must not act: an engine that runs its own tools outside ToolExecutor
            # (no approval, no STOP, no ledger) cannot be a review branch.
            return f"{b.provider} runs its own tools and cannot be a no-tools branch"
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
        run_id = f"{turn_id}/{gen}/{secrets.token_hex(8)}"
        self._runs[turn_id] = (gen, run_id)
        return await self._fan(turn_id, gen, items, branches, first_success, run_id=run_id)

    async def _fan(
        self,
        turn_id: str,
        gen: int,
        items: Sequence[ContextItem],
        branches: Sequence[Branch],
        first_success: bool,
        extra_allowed: frozenset[str] = frozenset(),
        *,
        run_id: str = "",
    ) -> list[BranchResult]:
        sem = asyncio.Semaphore(self.max_concurrent)
        results: dict[str, BranchResult] = {}
        tasks: dict[str, asyncio.Task[BranchResult]] = {}

        async def one(b: Branch) -> BranchResult:
            refusal = self._refusal(b)  # a None here means the budget slot is reserved
            if refusal:
                return BranchResult(
                    b.branch_id, b.provider, "refused", reason=refusal, run_id=run_id
                )
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
            # Defense in depth (block 19, J1): the CURRENT message's input label is enforced at
            # this direct brain call too, not only in the ordinary dispatcher. UNAVAILABLE or
            # RESTRICTED input never reaches a destination that is not provably local, whatever
            # the ContextItems say (no redaction is relied on).
            barred = dispatch_gate.refusal(b.provider, b.brain, local=local)
            if barred is not None:
                return BranchResult(
                    b.branch_id, b.provider, "refused", reason=barred, run_id=run_id
                )
            prompt, record = self.gate.prepare(
                items, turn_id=turn_id, branch_id=b.branch_id, destination=b.provider, local=local,
                extra_allowed=extra_allowed,
            )
            tainted = local and any(
                i.restricted_for(self.gate.allowed_categories | extra_allowed) for i in items
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
            dropped = [0]

            def done(status: str, **fields: Any) -> BranchResult:
                began = timing.get("start")
                return BranchResult(
                    b.branch_id, b.provider, status, egress=record,
                    first_token_s=timing.get("first"),
                    total_s=None if began is None else time.perf_counter() - began,
                    cost_basis=basis, tainted=tainted, run_id=run_id,
                    dropped_tool_calls=dropped[0], **fields,
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
                                dropped[0] += 1  # tools are centralized: dropped, never run
                                continue
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
                                    bid, _provider(branches, bid), "cancelled", run_id=run_id
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

    async def synthesize(
        self,
        turn_id: str,
        items: Sequence[ContextItem],
        results: Sequence[BranchResult],
        synthesizer: Branch,
    ) -> SynthesisResult:
        """One final answer from the engines that answered, through the SAME gate, budget and
        turn generation as the branches.

        The synthesizer sees the original task and each engine answer as an ``engine_answer``
        item. An answer produced locally on restricted material stays ``local_only``: a remote
        synthesizer never receives it (it is counted in ``withheld``). With nothing forwardable
        the synthesizer is not called at all. The synthesizer has no tools.
        """
        # The inputs must all come from the ONE run that is still current for this turn: not a
        # superseded or cancelled run, not another turn or orchestrator, not hand-built results,
        # not a mix. The identity is checked against the orchestrator's record, never inferred.
        gen, current_run = self._runs.get(turn_id, (0, ""))
        if self._current.get(turn_id) != gen or not current_run:
            return SynthesisResult("stale", reason="the turn was cancelled or superseded")
        if not results or any(r.run_id != current_run for r in results):
            return SynthesisResult(
                "stale", reason="these results are not from the current run of this turn"
            )
        answers = [r for r in results if r.status == "ok" and r.text.strip()]
        if not answers:
            return SynthesisResult("no_input", reason="no engine produced an answer")
        dest = engine_policy.destination_of(synthesizer.provider, synthesizer.brain)
        synth_local = synthesizer.kind == "local" and dest.local
        barred = dispatch_gate.refusal(synthesizer.provider, synthesizer.brain, local=synth_local)
        if barred is not None:  # block 19 (J1): a remote synthesizer never gets restricted input
            return SynthesisResult("refused", reason=barred)
        forwardable = [r for r in answers if synth_local or not r.tainted]
        withheld = len(answers) - len(forwardable)
        if not forwardable:
            return SynthesisResult(
                "no_input",
                withheld=withheld,
                reason="every answer contains local-only material and the synthesizer is remote",
            )
        derived = [ContextItem(i.text, i.category, i.local_only, i.derived_from) for i in items
                   if i.category == "task"]
        derived += [ContextItem(f"[{r.provider}] {r.text}", ANSWER_CATEGORY, r.tainted)
                    for r in forwardable]
        # a restricted answer or task item makes the synthesis restricted, whatever the gate did
        inherited = any(r.tainted for r in forwardable) or any(i.local_only for i in derived)
        instruction = "\n\n".join(
            part for part in (SYNTHESIS_INSTRUCTION, synthesizer.instruction) if part
        )
        branch = dataclasses.replace(synthesizer, branch_id="synthesis", instruction=instruction)
        out = (await self._fan(turn_id, gen, derived, [branch], False,
                               frozenset({ANSWER_CATEGORY}), run_id=current_run))[0]
        return SynthesisResult(
            out.status, text=out.text, reason=out.reason,
            sources=tuple(r.branch_id for r in forwardable), withheld=withheld, egress=out.egress,
            tainted=out.tainted or inherited,
        )


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
