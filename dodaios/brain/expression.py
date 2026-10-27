"""One plan for text, voice and avatar of a single turn (issue 17, ADR-0056).

The text style, the voice and the avatar must not disagree (cheerful words, a sad face, an
incompatible voice), so they are all derived from the SAME resolved profile and mood, once per turn,
and carry the identity of the turn they were made for. A plan from an old turn or an old generation
is refused: after STOP or a new turn nothing from the previous one can move the face or the voice.

What each channel gets, and what it honestly cannot get:

* text: the style instruction (``dodaios.brain.style``), from the same values;
* avatar: one expression out of the ones the avatar declares. Focused/serious/calm have no face of
  their own and stay neutral; ``curious``/``surprised`` are never taken from a mood. Unknown support
  is neutral, never a guess;
* voice: only parameters the chosen TTS provider accepts PER TURN. No shipped provider does today
  (``TTSProvider.synthesize`` takes text, voice and language only; speed is fixed when the
  provider is built), so the voice part is reported as unavailable. Nothing here promises an
  emotional voice.

Pure: no I/O, no audio, no clock.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from dodaios.brain.mood import MoodState
from dodaios.brain.personality import EffectiveProfile

#: Expressions the Consumer avatar core declares (prototypes/consumer-avatar/avatar-core.js).
AVATAR_EXPRESSIONS = ("neutral", "smile", "curious", "surprised")
#: Only these come from a mood; the others are for real events.
_MOOD_EXPRESSION = {"cheerful": "smile"}
#: provider -> per-turn parameters it accepts. Empty on purpose: add a provider here only together
#: with the code that passes the parameter to it.
TTS_TURN_PARAMS: dict[str, frozenset[str]] = {}


@dataclass(frozen=True, slots=True)
class TurnIdentity:
    product: str
    session_id: str
    turn_id: str
    #: Grows when the turn is cancelled or superseded; a plan of an older one is stale.
    generation: int


@dataclass(frozen=True, slots=True)
class ExpressionPlan:
    identity: TurnIdentity
    #: Hash of the profile and mood this plan came from (never the values themselves).
    version: str
    expression: str
    #: Per-turn voice parameters the provider accepts; empty when none do.
    voice_params: dict[str, float] = field(default_factory=dict)
    #: available | unavailable (the provider takes no per-turn parameter) | unknown_provider
    voice_status: str = "unavailable"
    #: Why a channel is neutral/unavailable, as fixed codes for the screen.
    notes: tuple[str, ...] = ()


def _version(profile: EffectiveProfile, state: MoodState) -> str:
    raw = json.dumps(
        [profile.mode, profile.preset, profile.tone, profile.formality, profile.warmth,
         profile.humor, profile.concision, state.mood, state.intensity],
        separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def avatar_expression(
    state: MoodState, supported: tuple[str, ...] | None
) -> tuple[str, tuple[str, ...]]:
    """The face for a mood and why it is neutral when it is. ``None`` = the avatar did not say."""
    if supported is None:
        return "neutral", ("avatar_support_unknown",)
    wanted = _MOOD_EXPRESSION.get(state.mood) if state.intensity > 0 else None
    if wanted is None:
        return "neutral", ()
    if wanted not in supported:
        return "neutral", ("avatar_expression_unsupported",)
    return wanted, ()


def build_plan(
    profile: EffectiveProfile,
    state: MoodState,
    identity: TurnIdentity,
    *,
    tts_provider: str | None,
    avatar_expressions: tuple[str, ...] | None = None,
) -> ExpressionPlan:
    expression, notes = avatar_expression(state, avatar_expressions)
    if not tts_provider:
        voice_status = "unknown_provider"
    elif TTS_TURN_PARAMS.get(tts_provider):
        voice_status = "available"
    else:
        voice_status = "unavailable"
    # No per-turn voice parameter is derived from the mood until a provider declares one above.
    return ExpressionPlan(
        identity=identity, version=_version(profile, state), expression=expression,
        voice_params={}, voice_status=voice_status,
        notes=notes + (("voice_unavailable",) if voice_status != "available" else ()),
    )


def stop_admits(generation: int) -> bool:
    """True while the STOP generation ``generation`` is still the current one and the door is open
    (``dodaios.control.stop``, ADR-0058). A fault reads as "not admitted": no style after doubt."""
    try:
        from dodaios.control.stop import get_stop_controller  # noqa: PLC0415

        return get_stop_controller().admit(generation).allowed
    except Exception:  # noqa: BLE001 - when STOP cannot be asked, nothing is applied
        return False


class PlanGate:
    """Accepts a plan only for the current product, session, turn and generation.

    ``generation`` is the STOP ticket the turn started under (``StopController.ticket()``): many
    turns share one generation until a STOP, so a NEW turn of the same generation replaces the
    previous one (``begin`` is called in turn order), but a generation that was cancelled or is
    older never comes back. ``cancel`` is STOP / supersede.

    With ``admit`` (use :func:`stop_admits`) a plan is also refused as soon as the controller says
    its generation is stale or the door is closed, even if ``cancel`` was never called on this gate:
    the gate does not depend on being told about STOP.
    """

    def __init__(self, admit: Callable[[int], bool] | None = None) -> None:
        self._admit = admit
        self._current: tuple[str, str, str] | None = None  # (product, session_id, turn_id)
        self._generation = -1
        self._cancelled = False

    def begin(self, product: str, session_id: str, turn_id: str, generation: int) -> bool:
        if generation < self._generation:
            return False
        if generation == self._generation and (self._cancelled or self._current is None):
            return False
        if self._admit is not None and not self._admit(generation):
            return False
        self._current, self._generation, self._cancelled = (
            (product, session_id, turn_id), generation, False)
        return True

    def cancel(self, generation: int) -> None:
        """STOP / supersede: nothing made for this or any older generation is valid any more."""
        if generation >= self._generation:
            self._cancelled = True
        self._generation = max(self._generation, generation)
        self._current = None

    def accepts(self, plan: ExpressionPlan) -> bool:
        ident = plan.identity
        if not (
            self._current == (ident.product, ident.session_id, ident.turn_id)
            and ident.generation == self._generation
        ):
            return False
        return self._admit is None or self._admit(ident.generation)


def avatar_event(plan: ExpressionPlan, seq: int) -> dict[str, object]:
    """The event the avatar core takes (``expression_plan``), under its own turn/sequence gating."""
    return {
        "type": "expression_plan", "turn_id": plan.identity.turn_id,
        "generation": plan.identity.generation, "seq": seq, "expression": plan.expression,
    }


#: How each channel carries the style, for the settings screen and the ADR. The truth, not a wish:
#: the realtime text rides in the session instructions; nothing modulates the voice; no avatar
#: runtime consumes the plan; there is no lip sync.
CHANNELS: dict[str, str] = {
    "text": "style_instruction",
    "realtime": "session_instructions",
    "voice": "unavailable",
    "avatar": "not_wired",
    "lip_sync": "none",
}
#: Realtime provider -> when the style text reaches the model. ``every_turn``: the provider
#: replaces its instructions on each update; ``connect_only``: the style in force when the call
#: opened stays for the whole call (a mood change or a new profile applies to the NEXT call).
#: Unknown providers are reported ``unknown``, never assumed to carry it.
REALTIME_STYLE_DELIVERY: dict[str, str] = {
    "openai-realtime": "every_turn",
    "local-realtime": "every_turn",
    "local-voice": "every_turn",
    "gemini-live": "connect_only",
    "vertex-live": "connect_only",
    "openai-live": "connect_only",
    "openai-live-subscription": "connect_only",
}


def realtime_delivery(provider: str | None) -> str:
    return REALTIME_STYLE_DELIVERY.get(provider or "", "unknown")


__all__ = [
    "AVATAR_EXPRESSIONS",
    "CHANNELS",
    "REALTIME_STYLE_DELIVERY",
    "realtime_delivery",
    "stop_admits",
    "ExpressionPlan",
    "PlanGate",
    "TTS_TURN_PARAMS",
    "TurnIdentity",
    "avatar_event",
    "avatar_expression",
    "build_plan",
]
