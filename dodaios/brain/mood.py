"""The simulated mood: a small deterministic reducer (issue 17, ADR-0056).

A mood here is an EXPRESSIVE state of the assistant, not a feeling and not a diagnosis of the
person. It is temporary, bounded, reversible and explainable, and it never touches permissions,
tools, budgets, approvals, accuracy or STOP: those do not read it.

What drives it, and nothing else:

* the owner's explicit request (highest priority),
* the activity kind the owner picked, or an explicit dialogue signal the app already has,
* the owner's profile limits (``PersonalityConfig``): mode, moods allowed, expressive range,
  and an optional mood the owner pinned.

No microphone or camera analysis, no model call, no hidden pass. Pure: the clock is passed in, so
cooldowns and expiry are exercised without waiting. The reducer only ever changes on an event; the
passage of time can only make the state fade towards neutral, never build up on its own.

Priority (a lower one never replaces a higher one that is still active):
owner request > activity preset > adaptation from dialogue signals.

Not here: the state is held in memory by the caller and is neutral after a restart unless the
owner pinned a mood in the profile; it is never written down as a memory.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from dodaios.brain.personality import EffectiveProfile

NEUTRAL = "neutral"

# Priorities.
ADAPTATION, ACTIVITY, OWNER = 1, 2, 3
_ORIGINS = {ADAPTATION: "rule", ACTIVITY: "activity", OWNER: "owner"}

#: Seconds an adaptation may not be replaced by another adaptation (no flipping every sentence).
COOLDOWN_S = 60.0
#: How long a rule- or activity-driven mood lasts before it fades.
ADAPT_TTL_S = 300.0
OWNER_TTL_S = 1800.0
MIN_TTL_S, MAX_TTL_S = 30.0, 86_400.0
#: After expiry the intensity drops by one level every this many seconds, then it is neutral.
DECAY_S = 20.0
MAX_INTENSITY = 3

#: Activity kinds the owner can choose -> (mood, intensity).
ACTIVITY_MOODS: dict[str, tuple[str, int]] = {
    "coding": ("focused", 1),
    "writing": ("focused", 1),
    "sensitive": ("serious", 2),
    "casual": ("cheerful", 1),
}
#: Dialogue signals the app already knows -> (mood, intensity).
DIALOGUE_MOODS: dict[str, tuple[str, int]] = {
    "task_failed": ("serious", 1),
    "task_done": ("cheerful", 1),
}


@dataclass(frozen=True, slots=True)
class MoodState:
    mood: str = NEUTRAL
    #: 0 = neutral. Adaptation is capped by the profile's expressive range; the owner up to 3.
    intensity: int = 0
    #: none | owner | activity | rule | profile
    origin: str = "none"
    since: float = 0.0
    #: None = until the owner changes it (this session only).
    expires_at: float | None = None
    #: A short fixed code (never free text); the screen renders it in the owner's language.
    reason: str = "default"
    priority: int = 0


NEUTRAL_STATE = MoodState()


# -- events ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OwnerMood:
    """«Be more serious» / «cheerful»: a mood the owner asked for."""

    mood: str
    intensity: int = 2
    ttl_s: float = OWNER_TTL_S


@dataclass(frozen=True, slots=True)
class OwnerHold:
    """«Stay like this»: keep the current mood until the owner changes it (this session only)."""


@dataclass(frozen=True, slots=True)
class OwnerNeutral:
    """«Back to neutral»."""


@dataclass(frozen=True, slots=True)
class OwnerAdaptation:
    """«Turn adaptation off / on» for this session. The profile on disk is untouched."""

    enabled: bool


@dataclass(frozen=True, slots=True)
class Activity:
    """The owner picked an activity kind (``ACTIVITY_MOODS``)."""

    kind: str


@dataclass(frozen=True, slots=True)
class Dialogue:
    """A dialogue signal the app already has (``DIALOGUE_MOODS``)."""

    signal: str


Event = OwnerMood | OwnerHold | OwnerNeutral | OwnerAdaptation | Activity | Dialogue


@dataclass(frozen=True, slots=True)
class Applied:
    state: MoodState
    accepted: bool
    #: Why an event changed nothing (fixed code), "" when accepted.
    refused: str = ""


def _finite_ttl(ttl_s: float) -> float | None:
    if isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float)) or not math.isfinite(ttl_s):
        return None
    return min(MAX_TTL_S, max(MIN_TTL_S, float(ttl_s)))


class MoodReducer:
    """One conversation's mood. ``state(now)`` reads, ``apply(event, now)`` changes."""

    def __init__(self, profile: EffectiveProfile, now: float = 0.0) -> None:
        self._profile = profile
        self._adaptation_on = True
        self._last_adapt_at = -math.inf
        self._state = self._pinned(now)

    @property
    def adaptation_on(self) -> bool:
        """False after «turn adaptation off» (this session only)."""
        return self._adaptation_on

    # -- profile ---------------------------------------------------------------------------

    def set_profile(self, profile: EffectiveProfile, now: float) -> None:
        """The owner changed the profile: limits apply at once, a session request survives."""
        self._profile = profile
        if self._state.origin in ("none", "profile"):
            self._state = self._pinned(now)
        elif self._state.mood not in profile.moods_allowed:
            self._state = self._pinned(now)

    def _pinned(self, now: float) -> MoodState:
        pin = self._profile.locked_mood
        if pin is None or pin == NEUTRAL:
            return NEUTRAL_STATE
        return MoodState(pin, 2, "profile", now, None, "profile_lock", OWNER)

    # -- reading ---------------------------------------------------------------------------

    def state(self, now: float) -> MoodState:
        """The state at ``now``: after expiry it fades one level per ``DECAY_S``, never grows."""
        s = self._state
        if s.expires_at is None or now < s.expires_at or s.intensity == 0:
            return s
        faded = s.intensity - 1 - int((now - s.expires_at) // DECAY_S)
        if faded <= 0:
            return self._pinned(now) if self._profile.locked_mood else NEUTRAL_STATE
        return replace(s, intensity=faded, reason="fading")

    # -- changing --------------------------------------------------------------------------

    def apply(self, event: Event, now: float) -> Applied:
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            return Applied(self.state(0.0), False, "bad_time")
        current = self.state(now)
        self._state = current  # persist the fade so a later priority check sees the real level
        match event:
            case OwnerMood(mood, intensity, ttl_s):
                return self._owner_mood(mood, intensity, ttl_s, now)
            case OwnerHold():
                if current.intensity == 0:
                    return Applied(current, False, "nothing_to_hold")
                self._state = replace(current, expires_at=None, reason="owner_hold",
                                      origin="owner", priority=OWNER, since=now)
                return Applied(self._state, True)
            case OwnerNeutral():
                self._state = NEUTRAL_STATE
                self._last_adapt_at = now  # no instant bounce back from a rule
                return Applied(self._state, True)
            case OwnerAdaptation(enabled):
                self._adaptation_on = bool(enabled)
                return Applied(current, True)
            case Activity(kind):
                return self._driven(ACTIVITY_MOODS.get(kind), ACTIVITY, f"activity_{kind}", now)
            case Dialogue(signal):
                return self._driven(
                    DIALOGUE_MOODS.get(signal), ADAPTATION, f"dialogue_{signal}", now)
        return Applied(current, False, "unknown_event")

    def _allowed(self, mood: str) -> bool:
        return mood == NEUTRAL or mood in self._profile.moods_allowed

    def _owner_mood(self, mood: str, intensity: int, ttl_s: float, now: float) -> Applied:
        ttl = _finite_ttl(ttl_s)
        valid_intensity = (
            isinstance(intensity, int) and not isinstance(intensity, bool)
            and 1 <= intensity <= MAX_INTENSITY
        )
        if ttl is None or not valid_intensity:
            return Applied(self._state, False, "bad_value")
        if mood == NEUTRAL:
            return self.apply(OwnerNeutral(), now)
        if not self._allowed(mood):
            return Applied(self._state, False, "mood_not_allowed")
        self._state = MoodState(mood, intensity, "owner", now, now + ttl, "owner_request", OWNER)
        return Applied(self._state, True)

    def _driven(
        self, target: tuple[str, int] | None, priority: int, reason: str, now: float
    ) -> Applied:
        """A rule- or activity-driven change: gated by mode, session switch, limits, priority,
        cooldown and the profile's expressive range."""
        current = self._state
        if target is None:
            return Applied(current, False, "unknown_signal")
        if self._profile.mode == "manual":
            return Applied(current, False, "manual_mode")
        if not self._adaptation_on:
            return Applied(current, False, "adaptation_off")
        mood, intensity = target
        if not self._allowed(mood):
            return Applied(current, False, "mood_not_allowed")
        cap = self._profile.expressive_range
        intensity = min(intensity, cap)
        if intensity <= 0:
            return Applied(current, False, "range_zero")
        if current.intensity > 0 and current.priority > priority:
            return Applied(current, False, "higher_priority_active")
        if current.mood == mood and current.priority >= priority and current.intensity > 0:
            # The same thing again: only renew how long it lasts, no change to show.
            self._state = replace(current, expires_at=now + ADAPT_TTL_S)
            return Applied(self._state, True)
        if priority == ADAPTATION and now - self._last_adapt_at < COOLDOWN_S:
            return Applied(current, False, "cooldown")
        self._last_adapt_at = now
        self._state = MoodState(
            mood, intensity, _ORIGINS[priority], now, now + ADAPT_TTL_S, reason, priority)
        return Applied(self._state, True)


__all__ = [
    "ACTIVITY_MOODS",
    "ADAPT_TTL_S",
    "Activity",
    "Applied",
    "COOLDOWN_S",
    "DECAY_S",
    "DIALOGUE_MOODS",
    "Dialogue",
    "MoodReducer",
    "MoodState",
    "NEUTRAL",
    "NEUTRAL_STATE",
    "OwnerAdaptation",
    "OwnerHold",
    "OwnerMood",
    "OwnerNeutral",
]
