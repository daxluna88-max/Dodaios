"""The main-screen state of the assistant and its simultaneous indicators (ADR-0058).

A pure function over what the app observed. The rules that matter:

* **Never "Stopped" while something is still running or not known to have stopped.** After an
  explicit STOP the summary is ``stopped`` only if STOP confirmed every part AND nothing observed
  is active; otherwise ``partly_stopped``.
* **Missing or old telemetry is ``unknown``,** never ``idle``/``stopped``. A source with no reading,
  or older than :data:`STALE_AFTER_S`, counts as unknown.
* **The summary never hides what else is going on.** Audio, jobs and approvals are separate
  indicators that stay filled in even when an error or a pending decision wins the summary line.
* The summary is a fixed code the screen renders in the owner's language, next to an icon.

No clock, no I/O: the caller passes the age of each reading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from dodaios.control.stop import StopState, SubsystemResult

#: A reading older than this many seconds is not trusted.
STALE_AFTER_S = 15.0

Summary = Literal[
    "error", "stopping", "partly_stopped", "waiting", "working", "speaking", "thinking",
    "listening", "stopped", "idle", "unknown",
]
Level = Literal["active", "idle", "unknown"]

_ORB = frozenset({"idle", "listening", "thinking", "speaking", "error", "paused"})


@dataclass(frozen=True, slots=True)
class Reading:
    """One observed value and how old it is. ``value is None`` = no reading."""

    value: object | None = None
    age_s: float | None = None

    def known(self) -> bool:
        return self.value is not None and self.age_s is not None and self.age_s <= STALE_AFTER_S


@dataclass(frozen=True, slots=True)
class Observed:
    orb: Reading = field(default_factory=Reading)  # one of ``_ORB``
    audio_playing: Reading = field(default_factory=Reading)  # bool
    jobs_running: Reading = field(default_factory=Reading)  # int
    approvals_pending: Reading = field(default_factory=Reading)  # int
    #: A fixed error code, or None when no error is being reported.
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Indicators:
    audio: Level
    jobs: int | None
    approvals: int | None


@dataclass(frozen=True, slots=True)
class StripState:
    summary: Summary
    indicators: Indicators
    #: Parts that STOP could not confirm (names), for the 'partly stopped' text.
    not_confirmed: tuple[str, ...] = ()
    #: Fixed code of the error when ``summary == "error"`` or an error is also present.
    error: str | None = None


def _count(reading: Reading) -> int | None:
    value = reading.value
    if not reading.known() or isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _indicators(obs: Observed) -> Indicators:
    if not obs.audio_playing.known() or not isinstance(obs.audio_playing.value, bool):
        audio: Level = "unknown"
    else:
        audio = "active" if obs.audio_playing.value else "idle"
    return Indicators(audio, _count(obs.jobs_running), _count(obs.approvals_pending))


def derive(
    obs: Observed,
    stop_state: StopState = "running",
    stop_results: tuple[SubsystemResult, ...] = (),
) -> StripState:
    """The summary, by this priority: stopping / partly stopped > error > waiting > working >
    speaking > thinking > listening > unknown > stopped | idle."""
    ind = _indicators(obs)
    not_confirmed = tuple(r.name for r in stop_results if r.outcome != "confirmed")
    orb = obs.orb.value if obs.orb.known() and obs.orb.value in _ORB else None

    def result(summary: Summary) -> StripState:
        return StripState(summary, ind, not_confirmed, obs.error)

    observed_active = (
        ind.audio == "active"
        or (ind.jobs or 0) > 0
        or (ind.approvals or 0) > 0
        or orb in ("speaking", "thinking", "listening")
    )
    if stop_state == "stopping":
        return result("stopping")
    if stop_state == "partly_stopped":
        return result("partly_stopped")
    if stop_state == "stopped" and observed_active:
        # STOP said it was done, but something is still observed: never the word "Stopped".
        return result("partly_stopped")
    if obs.error is not None or orb == "error":
        return result("error")
    if (ind.approvals or 0) > 0:
        return result("waiting")
    if (ind.jobs or 0) > 0:
        return result("working")
    if ind.audio == "active" or orb == "speaking":
        return result("speaking")
    if orb == "thinking":
        return result("thinking")
    if orb == "listening":
        return result("listening")
    if ind.audio == "unknown" or ind.jobs is None or ind.approvals is None or orb is None:
        return result("unknown")
    if stop_state == "stopped":
        return result("stopped")
    return result("idle")


__all__ = [
    "Indicators",
    "Level",
    "Observed",
    "Reading",
    "STALE_AFTER_S",
    "StripState",
    "Summary",
    "derive",
]
