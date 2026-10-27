"""The style the assistant speaks with right now, built from the profile and the mood (issue 17).

Two things are produced from the SAME resolved values, so text, voice and avatar cannot disagree:

* :func:`instruction` - a short text for the prompt. It is generated from enumerations and small
  integers only: nothing the owner typed as free text, no notes, no reasons, no history of moods,
  so it is safe to send to any engine the owner chose and cannot override a policy.
* :class:`StyleRuntime` - one conversation's reducer plus the profile it follows, so the prompt
  builder, the settings screen and the commands read and change one state.

Style never changes facts, accuracy, privacy, approvals, tools, budgets or STOP, and says so in the
text itself. A mood is a simulated way of speaking, never a claim of feeling or a reading of the
person.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from dodaios.brain import mood as mood_mod
from dodaios.brain import personality
from dodaios.core.config import PersonalityConfig

_TONE = {
    "neutral": "neutral and even",
    "friendly": "friendly",
    "calm": "calm and unhurried",
    "energetic": "upbeat and lively",
    "serious": "serious and measured",
}
_FORMALITY = ["very casual", "casual", "balanced", "formal", "very formal"]
_WARMTH = ["reserved", "somewhat reserved", "moderately warm", "warm", "very warm"]
_HUMOR = [
    "no humor", "hardly any humor", "light humor now and then", "playful", "often playful",
]
_CONCISION = [
    "thorough", "fairly full", "balanced length", "brief", "as short as the answer allows",
]
_MOOD = {
    "cheerful": "a little more cheerful",
    "calm": "calmer and slower",
    "focused": "focused and to the point",
    "serious": "more serious and sober",
}
_STRENGTH = {1: "slightly", 2: "noticeably", 3: "strongly"}

_RULES = (
    "This is a style, not a feeling: do not claim to have emotions and do not state what the user "
    "feels. Style never changes facts, accuracy, privacy, approvals, tools or stopping. Never "
    "guilt, pressure or flatter the user to stay, share or spend. Be sober when you report an "
    "error or handle a sensitive task: no jokes there."
)


def _effective_humor(profile: personality.EffectiveProfile, state: mood_mod.MoodState) -> int:
    return 0 if state.mood == "serious" and state.intensity > 0 else profile.humor


def instruction(
    profile: personality.EffectiveProfile, state: mood_mod.MoodState, *, compact: bool = False
) -> str:
    """The style text for the prompt. ``compact`` drops the rules paragraph's second half."""
    humor = _effective_humor(profile, state)
    parts = [
        f"Speaking style: {_TONE[profile.tone]}; {_FORMALITY[profile.formality]}; "
        f"{_WARMTH[profile.warmth]}; {_HUMOR[humor]}; {_CONCISION[profile.concision]}."
    ]
    if state.intensity > 0 and state.mood in _MOOD:
        strength = _STRENGTH[state.intensity]
        parts.append(f"Right now your delivery is {strength} {_MOOD[state.mood]}.")
    parts.append(_RULES if not compact else _RULES.split(" Never guilt")[0] + ".")
    return " ".join(parts)


class StyleRuntime:
    """The profile in force and the mood of one conversation."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._profile: personality.EffectiveProfile | None = None
        self._reducer: mood_mod.MoodReducer | None = None

    def sync(
        self, cfg: PersonalityConfig, product: str | None
    ) -> tuple[personality.EffectiveProfile, mood_mod.MoodState]:
        """Follow the saved profile (a change applies at once) and read the mood now."""
        profile = personality.effective(cfg, product)
        now = self._clock()
        if self._reducer is None:
            self._reducer = mood_mod.MoodReducer(profile, now)
        elif profile != self._profile:
            self._reducer.set_profile(profile, now)
        self._profile = profile
        return profile, self._reducer.state(now)

    @property
    def adaptation_on(self) -> bool:
        return True if self._reducer is None else self._reducer.adaptation_on

    def apply(self, event: mood_mod.Event) -> mood_mod.Applied | None:
        """A mood event; ``None`` before the first ``sync`` (no profile yet to hold limits)."""
        if self._reducer is None:
            return None
        return self._reducer.apply(event, self._clock())


__all__ = ["StyleRuntime", "instruction"]
