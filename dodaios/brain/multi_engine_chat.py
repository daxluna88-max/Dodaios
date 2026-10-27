"""Ask several engines from the chat and get ONE answer (ADR-0055).

The chat turn is the caller: ``BrainManager.generate`` hands the text here when the person asks
for it ("ask several engines: ...") and publishes the single result as the turn's response, which
is what the voice speaks. Nothing here speaks, publishes or writes history: branch answers stay
inside this module and never reach the response event, so there is one voice and one answer.

It adds no policy of its own. Branches and the synthesis go through ``consumer_orchestrator``
(privacy gate, one budget, no tools, cancellation); this module only turns the configuration into
branches, runs them, and says honestly why there is no answer when there is none.

Explicit by design: a branch has no tools, so a turn that needs an action ("open the browser") must
never be sent here by a setting alone. The message has to ask for it. The only context a branch
receives is the question itself (history is not sent).

Not here: the settings screen, an always-on mode, voice interruption of an engine already running.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from dodaios.brain import engine_policy, provenance
from dodaios.brain.consumer_orchestrator import (
    Branch,
    Budget,
    ContextItem,
    Orchestrator,
    PrivacyGate,
    fan_out_report,
)

log = logging.getLogger(__name__)

#: Plan-login providers: they spend a subscription quota, not an API key.
SUBSCRIPTION_PROVIDERS = frozenset({"codex", "claude-cli", "antigravity"})

# "ask several engines: <question>" in the languages that have system phrases, plus Italian.
_TRIGGER = re.compile(
    r"^\s*(?:"
    r"chiedi\s+a\s+pi[uù]\s+(?:modelli|motori|ai)"
    r"|ask\s+(?:several|multiple|more\s+than\s+one)\s+(?:models|engines|ais?)"
    r"|frag\s+(?:mehrere|verschiedene)\s+(?:modelle|engines|ki)"
    r"|pregunta\s+a\s+varios\s+(?:modelos|motores)"
    r"|pergunta\s+a\s+v[aá]rios\s+(?:modelos|motores)"
    r")\s*[:,\-–—]\s*(?P<task>.+)$",
    re.IGNORECASE | re.DOTALL,
)


def connection_kind(provider: str) -> str:
    if provider in engine_policy.LOCAL_PROVIDERS:
        return "local"
    return "subscription" if provider in SUBSCRIPTION_PROVIDERS else "api"


def authenticated(provider: str, kind: str, registry: Any) -> bool:
    """Whether the provider is usable now, WITHOUT contacting it (no call, no spend)."""
    if kind == "local":
        return True
    if kind == "subscription":
        probe = getattr(registry.get_class(provider), "subscription_connected", None)
        try:
            return bool(probe()) if callable(probe) else False
        except Exception:  # noqa: BLE001 - an unreadable login state means not connected
            log.warning("multi-engine: subscription probe for %s failed; treated as not connected",
                        provider, exc_info=True)
            return False
    from dodaios.core.config import get_provider_secret  # noqa: PLC0415

    return bool(get_provider_secret(provider))


def extract_task(text: str) -> str | None:
    """The question after an explicit "ask several engines:" request, else None."""
    match = _TRIGGER.match(text or "")
    if match is None:
        return None
    task = match.group("task").strip()
    return task or None


@dataclass(slots=True)
class MultiEngineOutcome:
    #: ok | work | off | no_engines | no_answer | synthesis_failed
    status: str
    text: str = ""
    #: Why there is no answer (per engine), already readable. Empty when ``status == "ok"``.
    reason: str = ""
    sources: tuple[str, ...] = ()
    #: The text derives from material a remote destination may not see (provenance).
    tainted: bool = False
    #: Typed label of ``text`` (R6). Fails closed: only an explicit UNRESTRICTED lets the text go
    #: to a remote destination; an outcome nobody labelled is UNAVAILABLE.
    provenance: provenance.Provenance = provenance.Provenance.UNAVAILABLE
    report: dict[str, Any] = field(default_factory=dict)


def _build(registry: Any, spec: Any, branch_id: str, refusals: list[str]) -> Branch | None:
    provider = spec.provider.strip().lower()
    if provider not in set(registry.available()):
        refusals.append(f"{provider}: not an installed provider")
        return None
    kind = connection_kind(provider)
    try:
        brain = (registry.instantiate(provider, model=spec.model) if spec.model
                 else registry.instantiate(provider))
    except engine_policy.EnginePolicyError as exc:  # said to the person through ``refusals``
        refusals.append(f"{provider}: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 - one unbuildable engine must not end the question
        log.warning("multi-engine provider %s could not be built: %s", provider, type(exc).__name__)
        refusals.append(f"{provider}: could not be built ({type(exc).__name__})")
        return None
    return Branch(
        branch_id, provider, brain, kind,
        authenticated=authenticated(provider, kind, registry),
        needs_api=spec.needs_api, est_cost=spec.est_cost, timeout_s=spec.timeout_s,
    )


#: The gate calls the question ``task``; the screen's fixed list calls it ``question``.
_CATEGORY_NAMES = {"task": "question"}


def _locality(provider: str) -> str:
    try:
        return "local" if engine_policy.destination_of(provider).local else "remote"
    except Exception:  # noqa: BLE001 - an unreadable endpoint is an unknown one, not a crash
        log.debug("locality of %s could not be told", provider, exc_info=True)
        return "unknown"


def _model_of(branch: Branch) -> str | None:
    """The model the branch's engine was built with, when it says so (else not known)."""
    model = getattr(getattr(branch, "brain", None), "model", None)
    return model if isinstance(model, str) and model else None


def _record_turn(
    ledger: Any, turn_id: str, branches: list[Branch], results: list[Any],
    synthesizer: Branch | None, synth: Any, *, key: Any = None, phase: str = "completed",
) -> None:
    """What this turn used, for the main screen: engines, planned and attempted categories.

    From the orchestrator's own egress records (metadata only), so it is "declared", not seen on
    the wire. A fault here must never touch the answer. With a ``key`` (the run's identity from
    the manager) the write is a compare-and-set: a run that is no longer the current one writes
    nothing. ``phase``: planned (before any call), attempted (reviewers done, synthesis pending),
    completed / failed (final). ``planned`` is partial: the synthesizer also receives the other
    engines' answers, which are not a listed category.
    """
    if ledger is None:
        return
    try:
        from dodaios.control import turn_record as tr  # noqa: PLC0415

        engines = [tr.EngineUse(b.provider, "reviewer", _locality(b.provider), _model_of(b))
                   for b in branches]
        if synthesizer is not None:
            engines.append(tr.EngineUse(synthesizer.provider, "synthesizer",
                                        _locality(synthesizer.provider), _model_of(synthesizer)))
        planned = tuple(
            tr.Flow(_CATEGORY_NAMES["task"], b.provider)
            for b in branches if _locality(b.provider) != "local"
        )
        egress = [r.egress for r in results if getattr(r, "egress", None) is not None]
        if synth is not None and getattr(synth, "egress", None) is not None:
            egress.append(synth.egress)
        attempted = tuple(
            tr.Flow(_CATEGORY_NAMES.get(c, c), e.destination)
            for e in egress for c in e.categories
        )
        refused = tuple(sorted({r.provider for r in results if r.status == "refused"}))
        record = tr.TurnRecord(
            turn_id=turn_id, engines=tuple(engines),
            planned=tr.sanitize_flows(planned), attempted=tr.sanitize_flows(attempted),
            observability="declared", refused=refused,
            phase=phase, state=tr._STATE_OF_PHASE[phase],  # noqa: SLF001
            planned_partial=True,
        )
        if key is not None:
            ledger.apply(key, record)
        else:
            ledger.record(record)
    except Exception:  # noqa: BLE001 - the screen's record never breaks a turn
        log.debug("turn record could not be written", exc_info=True)


def _close(ledger: Any, key: Any, phase: str) -> None:
    """End a run that used no engine (off, no engines, Work product) or died: no model, no flows."""
    if ledger is None or key is None:
        return
    try:
        ledger.update(key, phase=phase, planned_partial=False)
    except Exception:  # noqa: BLE001 - the screen's record never breaks a turn
        log.debug("turn record could not be closed", exc_info=True)


async def ask(
    task: str, config: Any, registry: Any, *, turn_id: str, style_instruction: str = "",
    ledger: Any = None, record_key: Any = None,
) -> MultiEngineOutcome:
    """Run the configured engines on ``task`` and write one final answer.

    ``config`` is ``[brain.multi_engine]``. ``style_instruction`` (enumerated style attributes from
    ``dodaios.brain.style``, never free text) is given to the synthesizer only, so the owner's
    personality shapes the one final answer while the branches stay neutral and precise.
    Never raises for an engine's failure: the outcome says what happened. Cancelling the
    awaiting task cancels every engine still running.
    """
    if engine_policy.current_product() == "work":
        _close(ledger, record_key, "completed")
        return MultiEngineOutcome("work", provenance=provenance.Provenance.UNRESTRICTED)
    if not config.enabled:
        _close(ledger, record_key, "completed")
        return MultiEngineOutcome("off", provenance=provenance.Provenance.UNRESTRICTED)
    if not config.engines:
        _close(ledger, record_key, "completed")
        return MultiEngineOutcome("no_engines", provenance=provenance.Provenance.UNRESTRICTED)

    refusals: list[str] = []
    # branch ids are b1..bN in the order of ``config.engines`` (an unbuildable one keeps its number)
    branches = [b for i, spec in enumerate(config.engines)
                if (b := _build(registry, spec, f"b{i + 1}", refusals)) is not None]

    items = [ContextItem(task, "task")]
    orchestrator = Orchestrator(
        PrivacyGate(allowed_categories=frozenset({"task"})),
        Budget(config.budget),
        max_concurrent=config.max_concurrent,
        allow_unknown_cost=config.allow_unknown_cost,
    )
    # planned: the reviewers are decided, no call issued yet (the synthesizer is not known yet)
    _record_turn(ledger, turn_id, branches, [], None, None, key=record_key, phase="planned")
    results = await orchestrator.run(turn_id, items, branches) if branches else []
    refusals += [f"{r.provider}: {r.reason or r.status}" for r in results if r.status != "ok"]
    report = fan_out_report(results, orchestrator.budget)
    if not any(r.status == "ok" and r.text.strip() for r in results):
        _record_turn(ledger, turn_id, branches, results, None, None, key=record_key,
                     phase="failed")
        return MultiEngineOutcome("no_answer", reason="; ".join(refusals), report=report)
    # Unset synthesizer: the first engine that actually answered writes the final answer (an
    # engine that just failed is not asked to do the harder job).
    synth_spec = config.synthesizer or next(
        config.engines[int(r.branch_id[1:]) - 1]
        for r in results if r.status == "ok" and r.text.strip()
    )
    synthesizer = _build(registry, synth_spec, "synthesis", refusals)
    if synthesizer is not None and style_instruction:
        synthesizer = dataclasses.replace(synthesizer, instruction=style_instruction)
    if synthesizer is None:
        _record_turn(ledger, turn_id, branches, results, None, None, key=record_key,
                     phase="failed")
        return MultiEngineOutcome("synthesis_failed", reason="; ".join(refusals), report=report)

    # attempted: reviewers answered and their calls are on record; the synthesis call is next
    _record_turn(ledger, turn_id, branches, results, synthesizer, None, key=record_key,
                 phase="attempted")
    synth = await orchestrator.synthesize(turn_id, items, results, synthesizer)
    _record_turn(ledger, turn_id, branches, results, synthesizer, synth, key=record_key,
                 phase="completed" if synth.status == "ok" and synth.text.strip() else "failed")
    report = fan_out_report(results, orchestrator.budget)
    if synth.status != "ok" or not synth.text.strip():
        reason = synth.reason or synth.status
        return MultiEngineOutcome("synthesis_failed", reason=reason, sources=synth.sources,
                                  report=report)
    text = synth.text.strip()
    if synth.tainted:  # the label must outlive this module: a cloud TTS or the curator check it
        provenance.mark_restricted(text)
    label = (provenance.Provenance.RESTRICTED if synth.tainted
             else provenance.Provenance.UNRESTRICTED)
    return MultiEngineOutcome("ok", text=text, sources=synth.sources, tainted=synth.tainted,
                              provenance=label, report=report)


__all__ = [
    "MultiEngineOutcome",
    "SUBSCRIPTION_PROVIDERS",
    "ask",
    "authenticated",
    "connection_kind",
    "extract_task",
]
