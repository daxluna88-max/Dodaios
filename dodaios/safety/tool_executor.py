"""ToolExecutor: orchestrates risk-eval → plausibility → approval → execute → event log.

The only authorized entry point for tool calls. Calling `Tool.execute()`
directly bypasses safety — that is a bug.

Phase 4 (persona mandate): a plausibility check runs before every approval
decision. If the voice pipeline has registered a ``plausibility_context_fn``,
the executor fetches transcript confidence + wake age and decides whether
``ask``/``monitor`` tools need an extra confirmation (see
``dodaios.brain.plausibility``).
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from dodaios.core.bus import EventBus
from dodaios.core.events import (
    ActionApprovalRequired,
    ActionApproved,
    ActionDenied,
    ActionExecuted,
    ActionProposed,
)
from dodaios.core.protocols import (
    CancelToken,
    ExecutionContext,
    Tool,
    ToolResult,
    Transcript,
)
from dodaios.core.redact import safe_preview

from .approval import TIMEOUT_REASON, ApprovalWorkflow
from .approval_surface import (
    CONVERSATIONAL,
    UNATTENDED,
    resolve_approval_surface,
)
from .effects import (
    EffectLedger,
    call_fingerprint,
    get_default_ledger,
    is_side_effecting,
    no_effect_evidence,
)
from .gate import (
    GATE_REASON,
    GateDecision,
    GateRequest,
    ToolGate,
    coerce_decision,
    get_default_gate,
)
from .origin import OriginLedger, TargetOrigin, classify_targets
from .preflight import stop_refusal
from .risk_tier import ActionBlocked, RiskTierEvaluator

if TYPE_CHECKING:
    from dodaios.brain.plausibility import PlausibilityDecision
    from dodaios.core.config import BrainPlausibilityConfig


log = logging.getLogger(__name__)


# Sentinel returned (as ``ToolResult.error``) when a confirmation-requiring tool
# is invoked on a CONVERSATIONAL turn (``config_snapshot["voice_confirm"]``).
# Instead of blocking in ``ApprovalWorkflow.wait()`` for a UI approval no voice/
# chat user can give (which is then beheaded by the 20 s no-first-frame ceiling →
# the misleading "took too long" phrase, forensic 2026-06-18), the executor stashes
# the action and returns this sentinel so the brain SPEAKS a confirmation question
# and ends the turn. The next "ja" re-runs the action via ``execute_confirmed``.
VOICE_CONFIRM_SENTINEL = "__voice_confirm_required__"

#: A deferred two-turn confirmation older than this is dropped, never executed by a late "ja".
VOICE_CONFIRM_TTL_S = 120.0

#: ``ActionApprovalRequired.reason`` of a confirmation forced because the ledger saw this call
#: before (same arguments under another call id) or its earlier outcome is unknown. Like
#: ``gate`` it is never auto-approved by a bypass mode or a pre-authorization bridge.
DUPLICATE_EFFECT_REASON = "duplicate_effect"
EFFECT_UNKNOWN_REASON = "effect_unknown"
FORCED_ASK_REASONS = frozenset({GATE_REASON, DUPLICATE_EFFECT_REASON, EFFECT_UNKNOWN_REASON})


# The three ways an approval-gated call can end WITHOUT running. They carry
# distinct ``ToolResult.error`` prefixes on purpose: until the GT-12 fix a
# 60-second timeout came back as ``approval-denied (timeout)``, so every
# consumer that classifies on ``startswith("approval-denied")`` — the mission
# ``WorkerToolBroker`` does exactly that — recorded "the user refused" for a
# decision nobody was ever asked to make. A refusal, a silence, and an absent
# human are three different facts and must read as three different facts.
APPROVAL_DENIED_PREFIX = "approval-denied"
APPROVAL_TIMEOUT_PREFIX = "approval-timeout"
APPROVAL_UNAVAILABLE_PREFIX = "approval-unavailable"

#: ``ToolResult.output["outcome"]`` for the unattended dead end, and the
#: ``ActionDenied.reason`` published alongside it. The word says what happened:
#: approval was impossible, not withheld.
APPROVAL_UNAVAILABLE_OUTCOME = "approval_unavailable"


# ``plausibility_context_fn`` returns (Transcript | None, wake_age_s | None).
# The voice pipeline registers a provider that supplies the last user-turn
# transcript and the seconds since the last wake trigger. On
# ``None`` returns the executor behaves as before (no plausibility check).
PlausibilityContextFn = Callable[[], "tuple[Transcript | None, float | None]"]


#: ``ToolResult.error`` of an effectful call refused because the ledger cannot record it.
LEDGER_UNAVAILABLE_ERROR = "effect-ledger-unavailable (not run: could not record it)"


@dataclass(frozen=True, slots=True)
class _PendingVoice:
    """One deferred two-turn confirmation. ``approval_id`` is its one-use identity: the "ja" must
    present it together with the fingerprint, and it becomes the ledger ``attempt`` when the
    confirmation was what allows an unknown/duplicate call to run again."""

    tool: Tool
    args: dict[str, Any]
    fingerprint: str
    key: str
    stop_ticket: int
    stashed_at: float
    approval_id: str
    effect_reason: str = ""


def _snapshot_args(tool: Tool, args: dict[str, Any]) -> dict[str, Any] | None:
    """A deep, private copy of the arguments: what is fingerprinted, shown, approved and run is
    this one object, so a later change to the caller's dict (or a tool mutating its input)
    cannot make the approved call differ from the executed one. None = cannot be copied."""
    try:
        return copy.deepcopy(args)
    except Exception:  # noqa: BLE001
        return None


def _door_ticket_now() -> int:
    from dodaios.control.stop import get_stop_controller  # noqa: PLC0415

    return get_stop_controller().ticket()


def _optional_string(value: Any) -> str | None:
    """Normalize optional correlation metadata without inventing identifiers."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


class ToolExecutor:
    """Pipeline: evaluate → (plausibility) → (approve) → execute → log."""

    def __init__(
        self,
        bus: EventBus,
        evaluator: RiskTierEvaluator,
        approval: ApprovalWorkflow,
        *,
        default_timeout_s: float = 60.0,
        plausibility_config: BrainPlausibilityConfig | None = None,
        plausibility_context_fn: PlausibilityContextFn | None = None,
        gate: ToolGate | None = None,
        gate_timeout_s: float = 5.0,
        ledger: EffectLedger | None = None,
    ) -> None:
        self._bus = bus
        self._evaluator = evaluator
        self._approval = approval
        self._default_timeout_s = default_timeout_s
        self._plausibility_config = plausibility_config
        self._plausibility_context_fn = plausibility_context_fn
        # ADR-0044: optional pre-execution gate. It can only make a call
        # stricter, and a gate that fails counts as "ask".
        self._gate = gate
        self._gate_timeout_s = gate_timeout_s
        # Two-turn voice/chat confirmation: actions deferred by ``execute`` on a
        # conversational turn, keyed by trace_id, awaiting an ``execute_confirmed``
        # (user said "ja") or ``cancel_pending`` (user said "nein"). The tool +
        # args live here OUT-OF-BAND — never in the serialized ToolResult.output.
        self._pending_voice: dict[UUID, _PendingVoice] = {}
        # Effect ledger (block 7): None = the process-wide one.
        self._ledger = ledger
        # ADR-0040, record only: the addressable values tool results held, per
        # trace, so a call's target can be told apart by where it came from.
        # Kept here because this is the one entry point every tool call takes.
        self._origins = OriginLedger()

    #: The longest a surface may keep an approval open, whatever it asks for.
    MAX_APPROVAL_TIMEOUT_S = 900.0

    def _approval_timeout(self, config_snapshot: dict[str, Any] | None) -> float:
        """The wait for this call: the surface's ``approval_timeout_s`` when it
        declares one (never below the default, never above the cap), else the
        default the executor was built with."""
        raw = (config_snapshot or {}).get("approval_timeout_s")
        if raw is None:
            return self._default_timeout_s
        try:
            asked = float(raw)
        except (TypeError, ValueError):
            return self._default_timeout_s
        return max(self._default_timeout_s, min(asked, self.MAX_APPROVAL_TIMEOUT_S))

    def _effects(self) -> EffectLedger:
        return self._ledger if self._ledger is not None else get_default_ledger()

    @staticmethod
    def _already_executed(tool: Tool, tid: UUID, row: Any) -> ToolResult:
        """The stored outcome of a call this app already issued; the tool is NOT run again."""
        log.info("effects: %s (trace %s) already executed; not running it again", tool.name, tid)
        return ToolResult(
            success=bool(row.success),
            output={
                "outcome": "already_executed",
                "tool_name": tool.name,
                "trace_id": str(tid),
                "previous_result_preview": row.preview,
                "message": "This exact call was already executed; it was not run a second time.",
            },
            error=None if row.success else (row.error or "earlier attempt failed"),
        )

    @staticmethod
    def _in_flight(tool: Tool, tid: UUID) -> ToolResult:
        return ToolResult(
            success=False,
            output={"outcome": "effect_in_flight", "tool_name": tool.name, "trace_id": str(tid)},
            error="effect-in-flight (this exact call is already running; not started twice)",
        )

    async def _invoke(
        self,
        tool: Tool,
        args: dict[str, Any],
        ctx: ExecutionContext,
        *,
        tid: UUID,
        key: str,
        fingerprint: str,
        side_effecting: bool,
        t_start: float,
        stop_ticket: int,
        attempt: str = "",
    ) -> ToolResult:
        """The final execution boundary: recheck, reserve the ledger row, run exactly once.

        Everything that can say no is checked HERE, after the last authorization await and with
        no await between the reservation and the tool: STOP for the generation this call
        started under (``stop_ticket`` is mandatory; "the door is open now" is not enough after a
        resume), the offline profile, and that the arguments still are the approved ones. A
        raise or a cancellation leaves the effect UNKNOWN; so does a returned error unless the
        tool vouched that it applied nothing."""
        from dodaios.core.tool_read_only import allows_read as _reads

        async def refuse(reason: str, error: str) -> ToolResult:
            await self._bus.publish(ActionDenied(trace_id=tid, tool_name=tool.name, reason=reason))
            return ToolResult(success=False, output=None, error=error)

        stopped = stop_refusal(stop_ticket)
        if stopped is not None and not _reads(tool, args):
            return await refuse(stopped, f"stopped ({stopped})")
        from dodaios.core import egress_guard

        offline = egress_guard.tool_refusal(tool.name, tool)
        if offline is not None:
            return await refuse(offline, offline)
        if call_fingerprint(tool.name, args) != fingerprint:
            return await refuse(
                "arguments changed after the decision", "not run: arguments changed after approval"
            )
        ledger: EffectLedger | None = None
        scope = str(tid)
        if side_effecting:
            try:
                ledger = self._effects()
                reservation = ledger.reserve(scope, key, tool.name, fingerprint, attempt)
            except Exception as exc:  # noqa: BLE001 - an unrecorded effect must not run
                log.error("effect ledger unavailable (%r): refusing %s", exc, tool.name)
                return ToolResult(False, None, LEDGER_UNAVAILABLE_ERROR)
            if reservation.kind == "reserved":
                attempt = reservation.attempt  # may be a retry-N identity chosen by the ledger
            if reservation.kind == "replay" and reservation.row is not None:
                return self._already_executed(tool, tid, reservation.row)
            if reservation.kind == "in_flight":
                return self._in_flight(tool, tid)
            if reservation.kind != "reserved":
                # became unknown/duplicate since the decision was taken: needs its own approval
                return ToolResult(
                    False, None,
                    f"effect-{reservation.kind} (not run: a new confirmation is required)",
                )
        # No await between the reservation above and the tool below.
        try:
            result = await tool.execute(copy.deepcopy(args), ctx)
        except asyncio.CancelledError:
            if ledger is not None:
                ledger.mark_unknown(scope, key, "cancelled while running", attempt)
            raise
        except Exception as exc:  # noqa: BLE001
            if ledger is not None:
                ledger.mark_unknown(scope, key, str(exc), attempt)
            duration_ms = int((time.perf_counter() - t_start) * 1000)
            await self._bus.publish(ActionExecuted(
                trace_id=tid,
                tool_name=tool.name,
                success=False,
                duration_ms=duration_ms,
                error=str(exc),
            ))
            return ToolResult(success=False, output=None, error=str(exc))
        if ledger is not None:
            ok = bool(getattr(result, "success", False))
            try:
                ledger.finish(
                    scope, key, success=ok, attempt=attempt,
                    no_effect=(not ok) and no_effect_evidence(tool, result),
                    preview=safe_preview(getattr(result, "output", None)),
                    error=str(getattr(result, "error", "") or ""),
                )
            except Exception:  # noqa: BLE001 - the row stays "started": unknown next time
                log.error("effect ledger could not record the outcome of %s", tool.name,
                          exc_info=True)
        self._record_result_origin(tool, tid, result, args)
        duration_ms = int((time.perf_counter() - t_start) * 1000)
        await self._bus.publish(ActionExecuted(
            trace_id=tid,
            tool_name=tool.name,
            success=result.success,
            duration_ms=duration_ms,
            error=result.error,
            output_preview=safe_preview(result.output),
        ))
        return result

    def set_gate(self, gate: ToolGate | None) -> None:
        """Install or remove the pre-execution gate (idempotent)."""
        self._gate = gate

    def _active_gate(self) -> ToolGate | None:
        """This executor's own gate, else the process-wide default (ADR-0044)."""
        return self._gate if self._gate is not None else get_default_gate()

    async def _review_with_gate(
        self,
        tool: Tool,
        args: dict[str, Any],
        decision: Any,
        origin: TargetOrigin,
        tid: UUID,
    ) -> GateDecision:
        """Ask the gate about one call. Never raises and never returns an
        implicit ``allow``: an error, a timeout or a malformed answer is ``ask``."""
        gate = self._active_gate()
        if gate is None:
            return GateDecision("allow")
        request = GateRequest(
            tool_name=tool.name,
            args=_snapshot_args(tool, args) or {},
            tier=decision.tier,
            approved_by=decision.approved_by,
            effect=origin.effect,
            target_origin=origin.origin,
            origin_source=origin.source,
            trace_id=tid,
        )
        try:
            raw = await asyncio.wait_for(gate.review(request), timeout=self._gate_timeout_s)
        except Exception as exc:  # noqa: BLE001 - a broken gate must not allow a call
            log.warning("gate failed for %s (%r): asking for a confirmation", tool.name, exc)
            return GateDecision("ask", "gate unavailable")
        return coerce_decision(raw)

    def set_plausibility_context_fn(
        self, fn: PlausibilityContextFn | None,
    ) -> None:
        """Late registration of the plausibility-context provider.

        The voice pipeline calls this after its own ``run()`` setup, because
        the ToolExecutor is built earlier in the bootstrap order than
        the pipeline. Idempotent — ``None`` resets the hook.
        """
        self._plausibility_context_fn = fn

    def _evaluate_plausibility(
        self,
        tool: Tool,
        decision: Any,
    ) -> PlausibilityDecision | None:
        """Fetches the current plausibility context and checks it.

        Returns ``None`` if no context provider is registered, or the
        tool was downgraded to ``safe`` via whitelist — whitelist logic
        is sacred (mandate: "whitelist-downgraded tools keep running
        without a plausibility check").
        """
        if self._plausibility_context_fn is None:
            return None
        # Whitelist downgrade: skip plausibility.
        if decision.approved_by == "whitelist":
            return None
        try:
            transcript, wake_age = self._plausibility_context_fn()
        except Exception as exc:  # noqa: BLE001
            log.debug("plausibility_context_fn failed: %s", exc)
            return None
        from dodaios.brain.plausibility import check_plausibility

        return check_plausibility(
            tool_name=tool.name,
            risk_tier=decision.tier,
            transcript=transcript,
            wake_age_s=wake_age,
            config=self._plausibility_config,
        )

    def _target_origin(
        self, tool: Tool, args: dict[str, Any], tid: UUID, user_utterance: str
    ) -> TargetOrigin:
        """Where this call's target came from. Never decides anything and never
        raises: a classification that fails is an empty record, not a failed
        call."""
        try:
            return classify_targets(
                tool, args, ledger=self._origins, trace=str(tid), user_text=user_utterance
            )
        except Exception as exc:  # noqa: BLE001 — a record must never cost the call
            log.debug("target-origin classification failed for %s: %s", tool.name, exc)
            return TargetOrigin()

    def _record_result_origin(
        self, tool: Tool, tid: UUID, result: ToolResult, args: dict[str, Any]
    ) -> None:
        """Remember the addressable values a tool result held (best effort)."""
        try:
            self._origins.record(str(tid), tool, result.output, args)
        except Exception as exc:  # noqa: BLE001 — a record must never cost the call
            log.debug("origin ledger could not read the result of %s: %s", tool.name, exc)

    async def publish_guard_denied(
        self,
        tool_name: str,
        reason: str,
        *,
        trace_id: UUID | None = None,
    ) -> None:
        """Surface a tool call refused BEFORE reaching :meth:`execute`.

        The tool-use loop's deterministic guards (how-to question, research
        intent, STT hallucination, unknown tool name, …) block a call without
        ever entering the executor — so no ActionProposed/ActionExecuted event
        fires and the session timeline shows NO trace of why a turn refused
        (2026-07-06 audit: the unknown-tool 'run-shell' incident left zero
        events). Publishing the same ``ActionDenied`` the blacklist path uses
        makes the refusal visible; the recorder already subscribes to it.
        Best-effort: observability must never break tool execution.
        """
        try:
            await self._bus.publish(ActionDenied(
                trace_id=trace_id or uuid4(),
                tool_name=tool_name,
                reason=reason,
            ))
        except Exception:  # noqa: BLE001
            log.debug("publish_guard_denied failed", exc_info=True)

    async def _approval_unavailable(
        self,
        tool: Tool,
        tid: UUID,
        risk_tier: str,
        config_snapshot: dict[str, Any] | None,
    ) -> ToolResult:
        """The honest dead end: this action needed a human and had none.

        Publishes ``ActionDenied`` so the refusal is visible in the timeline
        (same reasoning as :meth:`publish_guard_denied` — a call that silently
        vanishes is worse than one that failed), but says in the reason WHY it
        did not run. The returned error carries its own prefix so no consumer
        can mistake it for a user's "no", and the output carries a
        plain-language sentence a person can actually read in a run log.
        """
        reason = f"{APPROVAL_UNAVAILABLE_OUTCOME}: no approval channel on this surface"
        await self._bus.publish(ActionDenied(
            trace_id=tid,
            tool_name=tool.name,
            reason=reason,
        ))
        log.info(
            "approval-unavailable: %s (tier=%s) needed a confirmation and this "
            "surface has nobody to give it — failing fast",
            tool.name, risk_tier,
        )
        output: dict[str, Any] = {
            "outcome": APPROVAL_UNAVAILABLE_OUTCOME,
            "tool_name": tool.name,
            "trace_id": str(tid),
            "risk_tier": risk_tier,
        }
        # Plain-language sentence in the turn's ALREADY-RESOLVED output
        # language (Runtime Output Language doctrine: this layer reads the
        # resolved value, it never re-derives one). Phrasing lives in the one
        # channel-agnostic table, next to the confirmation question it belongs
        # to. Lazy import — ``dodaios.voice`` couples to ``dodaios.core.self_mod``
        # via its package __init__, so importing at module load would create an
        # order-dependent cycle (same pattern as the tool-use loop).
        try:
            from dodaios.voice.tool_confirmation import format_approval_unavailable

            output["message"] = format_approval_unavailable(
                tool.name,
                language=str((config_snapshot or {}).get("output_language") or ""),
            )
        except Exception as exc:  # noqa: BLE001 — phrasing must not decide safety
            log.debug("approval-unavailable phrasing failed: %s", exc)
        return ToolResult(
            success=False,
            output=output,
            error=f"{APPROVAL_UNAVAILABLE_PREFIX} ({reason})",
        )

    async def execute(
        self,
        tool: Tool,
        args: dict[str, Any],
        *,
        user_utterance: str = "",
        config_snapshot: dict[str, Any] | None = None,
        memory_read: Any | None = None,
        trace_id: UUID | None = None,
        rationale: str = "",
        cancel_token: CancelToken | None = None,
        effect_key: str | None = None,
    ) -> ToolResult:
        """Run one tool call through the single decision point.

        ``effect_key`` identifies this call within its turn (the provider's tool-call id). The
        same key again returns the stored outcome instead of running the tool a second time."""
        tid = trace_id or uuid4()
        t_start = time.perf_counter()
        from dodaios.core.tool_read_only import allows_read as _allows_read

        # One private deep copy is what gets fingerprinted, shown, approved and executed.
        snapshot = _snapshot_args(tool, args)
        if snapshot is None:
            if not _allows_read(tool, args):
                return ToolResult(False, None, "not run: arguments cannot be snapshotted")
            snapshot = dict(args)
        args = snapshot

        # STOP is a property of the executor, not of each caller: with the owner's door closed
        # nothing but a declared read runs, whatever the tier and whoever asked.
        stop_ticket = _door_ticket_now()
        stopped = stop_refusal()
        if stopped is not None and not _allows_read(tool, args):
            await self._bus.publish(ActionDenied(trace_id=tid, tool_name=tool.name, reason=stopped))
            return ToolResult(False, None, f"stopped ({stopped})")

        if (config_snapshot or {}).get("chat_read_only"):
            from dodaios.core.tool_read_only import allows_read

            if not allows_read(tool, args):
                await self._bus.publish(ActionDenied(
                    trace_id=tid, tool_name=tool.name, reason="Plan mode permits reads only",
                ))
                return ToolResult(False, None, "Plan mode permits reads only")

        from dodaios.core import egress_guard

        offline = egress_guard.tool_refusal(tool.name, tool)
        if offline is not None:
            await self._bus.publish(ActionDenied(trace_id=tid, tool_name=tool.name, reason=offline))
            return ToolResult(False, None, offline)

        if cancel_token is not None and cancel_token.is_cancelled():
            return ToolResult(
                success=False,
                output=None,
                error=f"cancelled ({cancel_token.reason or 'requested'})",
            )

        # 1. Evaluate
        try:
            decision = self._evaluator.evaluate(tool, args)
        except ActionBlocked as exc:
            await self._bus.publish(ActionDenied(
                trace_id=tid,
                tool_name=tool.name,
                reason=f"blacklist: {exc.pattern}",
            ))
            return ToolResult(success=False, output=None, error=str(exc))

        # Where the call's target came from (ADR-0040, record only). Settled
        # here, before an approval ticket exists, so nothing but the publish
        # sits between arming it and ``ActionProposed``.
        origin = self._target_origin(tool, args, tid, user_utterance)

        # 1.5 Optional gate (ADR-0044). It runs after the blacklist and can
        # only tighten: deny refuses now, ask forces a confirmation below.
        gate_confirm = False
        if self._active_gate() is not None:
            gate_decision = await self._review_with_gate(tool, args, decision, origin, tid)
            if gate_decision.verdict == "deny":
                reason = f"gate: {gate_decision.reason}" if gate_decision.reason else "gate"
                await self._bus.publish(ActionDenied(
                    trace_id=tid, tool_name=tool.name, reason=reason,
                ))
                return ToolResult(success=False, output=None, error=f"gate-denied ({reason})")
            gate_confirm = gate_decision.verdict == "ask"
            # STOP can arrive while the gate is reviewing: its verdict is only good for the
            # generation the call started under.
            stopped = stop_refusal(stop_ticket)
            if stopped is not None and not _allows_read(tool, args):
                await self._bus.publish(
                    ActionDenied(trace_id=tid, tool_name=tool.name, reason=stopped)
                )
                return ToolResult(False, None, f"stopped ({stopped})")

        # 1.6 Effect ledger (block 7): a side-effecting call already issued is not issued again.
        fingerprint = call_fingerprint(tool.name, args)
        side_effecting = is_side_effecting(tool, args, decision.tier)
        key = effect_key or f"call:{uuid4().hex}"
        effect_reason = ""
        if side_effecting:
            try:
                inspection = self._effects().inspect(str(tid), key, tool.name, fingerprint)
            except Exception as exc:  # noqa: BLE001 - no history, no effect: fail closed
                log.error("effect ledger unavailable (%r): refusing %s", exc, tool.name)
                await self._bus.publish(ActionDenied(
                    trace_id=tid, tool_name=tool.name, reason=LEDGER_UNAVAILABLE_ERROR,
                ))
                return ToolResult(False, None, LEDGER_UNAVAILABLE_ERROR)
            if inspection.kind == "replay" and inspection.row is not None:
                return self._already_executed(tool, tid, inspection.row)
            if inspection.kind == "in_flight":
                return self._in_flight(tool, tid)
            if inspection.kind == "unknown":
                effect_reason = EFFECT_UNKNOWN_REASON
            elif inspection.kind == "duplicate":
                effect_reason = DUPLICATE_EFFECT_REASON

        # 2. Plausibility check (Phase 4): the result can force confirmation
        # even when the tier workflow does not (for example, ``monitor``).
        plaus = self._evaluate_plausibility(tool, decision)
        if plaus is not None and plaus.reason != "ok":
            log.info(
                "Plausibility[%s]: tier=%s reason=%s require_confirm=%s",
                tool.name, decision.tier, plaus.reason, plaus.require_confirmation,
            )

        # 3. Arm approval BEFORE publishing ActionProposed. Subscribers such as
        # TaskAutoApprover may answer synchronously from that event; registering
        # afterward loses the answer and turns a valid grant into a timeout.
        approved_by = decision.approved_by or "auto"
        tier_confirm = self._evaluator.needs_user_confirmation(decision)
        plaus_confirm = plaus is not None and plaus.require_confirmation
        # Explicit intent (Claude-Code permission model, mandate 2026-08-08):
        # a consequential action the user's own utterance already asked for by
        # name ("delete the folder X") skips the redundant confirmation. Only
        # the TIER requirement may be waived — a plausibility-forced
        # confirmation (low STT confidence, stale wake) stays binding, because
        # the whole doubt there is whether the utterance was heard right.
        if tier_confirm and not plaus_confirm and user_utterance:
            confirms = getattr(tool, "intent_confirms_args", None)
            if callable(confirms):
                try:
                    if confirms(args, user_utterance):
                        tier_confirm = False
                        approved_by = "explicit-intent"
                        log.info(
                            "explicit-intent: %s authorized by the utterance, "
                            "skipping confirmation", tool.name,
                        )
                except Exception as exc:  # noqa: BLE001 — keep the confirmation on a broken hook
                    log.warning(
                        "intent_confirms_args on %r raised %r — keeping confirmation",
                        tool.name, exc,
                    )
        # A gate "ask" is not waived by explicit intent (that waiver is applied
        # above, to the tier requirement only).
        if gate_confirm or effect_reason:
            tier_confirm = True
        needs_confirm = tier_confirm or plaus_confirm
        # A bound chat owns its approval stance through ChatApprovalBridge.
        # Calls from voice, tasks, missions and other app surfaces use the
        # global default. This never overrides the block/read-only/cancel
        # checks, and model-controlled tool arguments cannot set the policy.
        bypass_default = self._evaluator.bypass_permissions and not (
            config_snapshot or {}
        ).get("approval_ref")
        # A confirmation the gate asked for is never approved by bypass, even
        # when the tier or plausibility check ALSO asked for one: the gate
        # exists to stop what the other checks would let through.
        if gate_confirm or effect_reason:
            bypass_default = False
        # WHO can answer this gate — declared by the calling layer, never by
        # the model (see ``approval_surface``). It changes only how the
        # approval is obtained, never whether one is needed: ``needs_confirm``
        # above is the untouched tier decision.
        surface = resolve_approval_surface(config_snapshot)
        voice_confirm = surface == CONVERSATIONAL and not bypass_default
        # How long a card may stay open — declared by the calling layer like
        # the surface itself (a person reading a chat card must not lose the
        # tool to a clock built for a spoken "ja"), clamped so no surface can
        # park the executor forever.
        approval_timeout_s = self._approval_timeout(config_snapshot)
        approval_ticket = None
        attempt = ""
        if needs_confirm and not voice_confirm:
            # An unattended call arms too: the pre-authorization bridges answer
            # synchronously on the ActionApprovalRequired publish below, and
            # ADR-0031 relies on the ticket existing before that publish.
            approval_ticket = self._approval.arm(tid, tool_name=tool.name, fingerprint=fingerprint)

        # 3.5 Proposed event (the UI can use this as a live indicator). The
        # rationale is redacted and capped so no raw secret reaches the bus.
        try:
            await self._bus.publish(ActionProposed(
                trace_id=tid,
                tool_name=tool.name,
                args=args,
                risk_tier=decision.tier,
                rationale=safe_preview(rationale),
                effect=origin.effect,
                target_origin=origin.origin,
                origin_source=origin.source,
            ))
        except BaseException:
            if approval_ticket is not None:
                approval_ticket.close()
            raise

        if approval_ticket is not None:
            if gate_confirm:
                # Wins over the other reasons: the pre-authorization bridges only
                # answer ``risk_tier`` and ``plausibility``, so a request the
                # gate also asked for is never auto-approved by them.
                reason = GATE_REASON
            elif effect_reason:
                reason = effect_reason
            elif plaus is not None and plaus.require_confirmation:
                reason = "plausibility"
            else:
                reason = "risk_tier"

            # An unattended call expires immediately: it will not be waited on,
            # so advertising a 60-second window would invite a UI to offer a
            # decision that can no longer reach anybody.
            window_ns = (
                0
                if surface == UNATTENDED
                else int(approval_timeout_s * 1_000_000_000)
            )
            await self._bus.publish(
                ActionApprovalRequired(
                    trace_id=tid,
                    tool_name=tool.name,
                    risk_tier=decision.tier,
                    reason=reason,
                    args_preview=safe_preview(args),
                    expires_at_ns=time.time_ns() + window_ns,
                    mission_id=_optional_string(
                        (config_snapshot or {}).get("mission_id")
                    ),
                    worker_id=_optional_string(
                        (config_snapshot or {}).get("worker_id")
                    ),
                    approval_ref=_optional_string(
                        (config_snapshot or {}).get("approval_ref")
                    ),
                    call_fingerprint=fingerprint,
                    approval_id=approval_ticket.approval_id,
                    effect=origin.effect,
                    target_origin=origin.origin,
                    origin_source=origin.source,
                )
            )
            # Scoped authorizers have the first decision, including explicit
            # denials. Bypass supplies an immediate standing approval only
            # when none has answered; it never overwrites their decision.
            if bypass_default and approval_ticket.peek() is None:
                await self._bus.publish(
                    ActionApproved(
                        trace_id=tid,
                        tool_name=tool.name,
                        approved_by="bypass",
                        call_fingerprint=fingerprint,
                        approval_id=approval_ticket.approval_id,
                    )
                )

        if needs_confirm:
            # Two-turn confirmation on a conversational turn: do NOT block on the
            # UI-approval future (no voice/chat user can resolve it within the
            # turn's latency window). Stash the action and return the sentinel so
            # the brain speaks a confirmation question; the user's next "ja" calls
            # ``execute_confirmed`` (AD-OE: the talker never awaits heavy/blocking
            # work on the turn). ``needs_confirm`` already excludes whitelist
            # downgrades, so this fires only for genuinely consequential tools.
            if voice_confirm:
                if tid in self._pending_voice:
                    # A new question for this trace replaces the old one: the old action is
                    # dropped (and audited), never left to be approved by a "ja" meant for this.
                    await self.cancel_pending(tid, reason="superseded_by_new_confirmation")
                voice_approval_id = uuid4().hex
                self._pending_voice[tid] = _PendingVoice(
                    tool, args, fingerprint, key, stop_ticket, time.monotonic(),
                    voice_approval_id, effect_reason,
                )
                log.info(
                    "voice-confirm: deferring %s (tier=%s) for two-turn confirmation",
                    tool.name, decision.tier,
                )
                sentinel_output: dict[str, Any] = {
                    "tool_name": tool.name,
                    "trace_id": str(tid),
                    "risk_tier": decision.tier,
                    "call_fingerprint": fingerprint,
                    "approval_id": voice_approval_id,
                }
                # Optional hook (like ``risk_tier_for_args``): a tool may
                # summarize what the deferred action would DO so the spoken
                # confirmation question can say it in plain language.
                # Best-effort — phrasing must never block the confirm flow.
                describe = getattr(tool, "describe_args", None)
                if callable(describe):
                    try:
                        summary = describe(args)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("describe_args on %r failed: %s", tool.name, exc)
                        summary = None
                    if isinstance(summary, dict) and summary:
                        sentinel_output["impact"] = {
                            str(k): str(v) for k, v in summary.items()
                        }
                return ToolResult(
                    success=False,
                    output=sentinel_output,
                    error=VOICE_CONFIRM_SENTINEL,
                )
            if surface == UNATTENDED:
                # Nobody is there to ask. The pre-authorization bridges have
                # already had their say — they answer inline while the
                # ActionApprovalRequired publish above is awaited — so a ticket
                # that is still undecided will stay undecided forever. Waiting
                # the full timeout would buy a minute of silence and then
                # report a refusal nobody made (audit GT-12).
                decided = (
                    approval_ticket.peek() if approval_ticket is not None else None
                )
                if decided is None:
                    if approval_ticket is not None:
                        approval_ticket.close()
                    return await self._approval_unavailable(
                        tool, tid, decision.tier, config_snapshot,
                    )
                approved, who_or_reason = decided
                if approval_ticket is not None:
                    approval_ticket.close()
            else:
                try:
                    approved, who_or_reason = await self._approval.wait(
                        tid, approval_timeout_s
                    )
                finally:
                    if approval_ticket is not None:
                        approval_ticket.close()
            if not approved:
                await self._bus.publish(ActionDenied(
                    trace_id=tid,
                    tool_name=tool.name,
                    reason=who_or_reason,
                ))
                # A silence is not a "no". Keep the two apart so the caller,
                # the mission broker, and the run log can each say which
                # actually happened.
                if who_or_reason == TIMEOUT_REASON:
                    error = (
                        f"{APPROVAL_TIMEOUT_PREFIX} (nobody decided within "
                        f"{approval_timeout_s:.0f}s)"
                    )
                else:
                    error = f"{APPROVAL_DENIED_PREFIX} ({who_or_reason})"
                return ToolResult(success=False, output=None, error=error)
            approved_by = who_or_reason  # "user" or "auto"
            # An approval that let an unknown/duplicate call run again is the explicit identity
            # of that re-execution (its one-use id becomes the ledger attempt).
            if effect_reason and approval_ticket is not None:
                attempt = approval_ticket.approval_id

        if cancel_token is not None and cancel_token.is_cancelled():
            await self._bus.publish(ActionDenied(
                trace_id=tid,
                tool_name=tool.name,
                reason=f"cancelled: {cancel_token.reason or 'requested'}",
            ))
            return ToolResult(
                success=False,
                output=None,
                error=f"cancelled ({cancel_token.reason or 'requested'})",
            )

        # STOP pressed (or pressed and released) while the approval was pending: an approval is
        # only good for the generation it was asked under.
        stopped = stop_refusal(stop_ticket)
        if stopped is not None and not _allows_read(tool, args):
            await self._bus.publish(ActionDenied(trace_id=tid, tool_name=tool.name, reason=stopped))
            return ToolResult(False, None, f"stopped ({stopped})")

        # 4. Execute
        ctx = ExecutionContext(
            trace_id=tid,
            user_utterance=user_utterance,
            config=config_snapshot or {},
            memory_read=memory_read,
            approved_by=approved_by,
        )
        return await self._invoke(
            tool, args, ctx, tid=tid, key=key, fingerprint=fingerprint,
            side_effecting=side_effecting, t_start=t_start,
            stop_ticket=stop_ticket, attempt=attempt,
        )

    # ------------------------------------------------------------------
    # Two-turn voice/chat confirmation resume (turn N+1)
    # ------------------------------------------------------------------

    def has_pending_voice_confirm(self, trace_id: UUID) -> bool:
        """True while an action deferred for ``trace_id`` still awaits a yes/no."""
        return trace_id in self._pending_voice

    async def execute_confirmed(
        self,
        trace_id: UUID,
        *,
        user_utterance: str = "",
        config_snapshot: dict[str, Any] | None = None,
        memory_read: Any | None = None,
        expected_fingerprint: str | None = None,
        expected_approval_id: str | None = None,
    ) -> ToolResult:
        """Run the action stashed by a prior voice-confirm deferral ("ja").

        Single-use: the pending entry is popped first, so a repeated "ja" cannot
        double-fire the side effect. ``approved_by="user"`` records that the human
        authorized it. Publishes ``ActionExecuted`` for the audit trail, mirroring
        the normal execute path.

        A "ja" authorizes the exact action that was asked about: the stashed arguments (never
        re-supplied). For any action that is not a declared read the caller MUST present both
        the fingerprint and the one-use approval id the question carried; a missing or different
        value is refused and the pending question is kept for its own answer. The confirmation
        does not outlive ``VOICE_CONFIRM_TTL_S``, the STOP generation it was asked under (checked
        again after every await and at the final boundary), plan mode, the offline guard, the
        blacklist or a gate ``deny``, and it never re-runs a call the ledger marks as executed.
        """
        pending = self._pending_voice.get(trace_id)
        if pending is None:
            return ToolResult(
                success=False,
                output=None,
                error="voice-confirm expired (no pending action for this turn)",
            )
        tool, args, fingerprint = pending.tool, pending.args, pending.fingerprint
        key, stop_ticket = pending.key, pending.stop_ticket
        from dodaios.core.tool_read_only import allows_read

        identity_ok = (
            bool(expected_fingerprint) and expected_fingerprint == fingerprint
            and bool(expected_approval_id) and expected_approval_id == pending.approval_id
        )
        if not identity_ok and not allows_read(tool, args):
            # The "ja" was given to another question, or carries no identity at all. Keep the
            # pending one for its own answer.
            return ToolResult(False, None, "voice-confirm does not match the pending action")
        self._pending_voice.pop(trace_id, None)

        async def refuse(reason: str, error: str) -> ToolResult:
            await self._bus.publish(
                ActionDenied(trace_id=trace_id, tool_name=tool.name, reason=reason)
            )
            return ToolResult(success=False, output=None, error=error)

        if time.monotonic() - pending.stashed_at > VOICE_CONFIRM_TTL_S:
            return await refuse("voice_confirm_expired", "voice-confirm expired (too old to run)")
        stopped = stop_refusal(stop_ticket)
        if stopped is not None:
            return await refuse(stopped, f"stopped ({stopped})")
        if (config_snapshot or {}).get("chat_read_only"):
            if not allows_read(tool, args):
                return await refuse("Plan mode permits reads only", "Plan mode permits reads only")
        from dodaios.core import egress_guard

        offline = egress_guard.tool_refusal(tool.name, tool)
        if offline is not None:
            return await refuse(offline, offline)
        try:
            decision = self._evaluator.evaluate(tool, args)
        except ActionBlocked as exc:
            return await refuse(f"blacklist: {exc.pattern}", str(exc))
        if self._active_gate() is not None:
            origin = self._target_origin(tool, args, trace_id, user_utterance)
            verdict = await self._review_with_gate(tool, args, decision, origin, trace_id)
            if verdict.verdict == "deny":
                reason = f"gate: {verdict.reason}" if verdict.reason else "gate"
                return await refuse(reason, f"gate-denied ({reason})")
            # STOP can arrive while the gate is reviewing.
            stopped = stop_refusal(stop_ticket)
            if stopped is not None:
                return await refuse(stopped, f"stopped ({stopped})")
        side_effecting = is_side_effecting(tool, args, decision.tier)
        attempt = ""
        if side_effecting:
            try:
                inspection = self._effects().inspect(str(trace_id), key, tool.name, fingerprint)
            except Exception as exc:  # noqa: BLE001 - fail closed
                log.error("effect ledger unavailable on confirm (%r): refusing", exc)
                return await refuse(LEDGER_UNAVAILABLE_ERROR, LEDGER_UNAVAILABLE_ERROR)
            kind = inspection.kind
            if kind == "replay" and inspection.row is not None:
                return self._already_executed(tool, trace_id, inspection.row)
            if kind == "in_flight":
                return self._in_flight(tool, trace_id)
            if kind in ("unknown", "duplicate"):
                if pending.effect_reason:
                    # This very question was the forced confirmation: its one-use id is the
                    # explicit identity of the re-execution.
                    attempt = pending.approval_id
                else:
                    return await refuse(
                        f"effect-{kind}", f"effect-{kind} (not run: a new confirmation is required)"
                    )
        ctx = ExecutionContext(
            trace_id=trace_id,
            user_utterance=user_utterance,
            config=config_snapshot or {},
            memory_read=memory_read,
            approved_by="user",
        )
        return await self._invoke(
            tool, args, ctx, tid=trace_id, key=key, fingerprint=fingerprint,
            side_effecting=side_effecting, t_start=time.perf_counter(),
            stop_ticket=stop_ticket, attempt=attempt,
        )

    async def cancel_pending(self, trace_id: UUID, *, reason: str = "voice_vetoed") -> bool:
        """Drop the action stashed for ``trace_id`` ("nein"). Returns whether one
        existed. Publishes ``ActionDenied`` for the audit trail; ``reason`` says
        why, so a call that merely ended is not recorded as the user's veto."""
        pending = self._pending_voice.pop(trace_id, None)
        if pending is None:
            return False
        tool = pending.tool
        await self._bus.publish(ActionDenied(
            trace_id=trace_id,
            tool_name=tool.name,
            reason=reason,
        ))
        return True
