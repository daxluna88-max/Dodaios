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


class PlanGate:
    """Accepts a plan only for the current product, session, turn and generation.

    The newest wins, an older one never comes back. ``begin`` is called when a turn starts,
    ``cancel`` on STOP or supersede.
    """

    def __init__(self) -> None:
        self._current: tuple[str, str, str] | None = None  # (product, session_id, turn_id)
        self._generation = -1

    def begin(self, product: str, session_id: str, turn_id: str, generation: int) -> bool:
        if generation <= self._generation:
            return False
        self._current, self._generation = (product, session_id, turn_id), generation
        return True

    def cancel(self, generation: int) -> None:
        """STOP / supersede: nothing made for this or any older generation is valid any more."""
        self._generation = max(self._generation, generation)
        self._current = None

    def accepts(self, plan: ExpressionPlan) -> bool:
        ident = plan.identity
        return (
            self._current == (ident.product, ident.session_id, ident.turn_id)
            and ident.generation == self._generation
        )


def avatar_event(plan: ExpressionPlan, seq: int) -> dict[str, object]:
    """The event the avatar core takes (``expression_plan``), under its own turn/sequence gating."""
    return {
        "type": "expression_plan", "turn_id": plan.identity.turn_id,
        "generation": plan.identity.generation, "seq": seq, "expression": plan.expression,
    }


__all__ = [
    "AVATAR_EXPRESSIONS",
    "ExpressionPlan",
    "PlanGate",
    "TTS_TURN_PARAMS",
    "TurnIdentity",
    "avatar_event",
    "avatar_expression",
    "build_plan",
]
