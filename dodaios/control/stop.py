"""STOP for one product: close the door first, then stop each part on its own (ADR-0058).

Pure coordination, no I/O of its own: the parts that can be stopped are passed in as
:class:`Subsystem` objects (a ``request`` that asks it to stop and a ``probe`` that says whether
it did). What this module guarantees, and what it does not:

* **The door closes before anything is awaited.** ``stop`` bumps the generation and refuses new
  turns, dispatches and approvals synchronously, so a request that races with STOP finds the door
  closed. A turn that was queued before STOP carries the old generation and is refused even after
  the owner resumes.
* **Independent shutdowns.** Every part runs concurrently with its own timeouts; one that hangs
  or fails does not delay or block the others.
* **Four outcomes, never collapsed:** ``requested`` (asked, not confirmed), ``confirmed``,
  ``still_active``, ``unknown`` (no answer, no probe, or the probe failed). The report is
  ``stopped`` only when every part is ``confirmed``; otherwise ``partly_stopped``.
* **Idempotent.** A second STOP while one is running joins it; after it, it only asks the parts
  that are not confirmed again. It never bumps the generation twice.
* **Resuming is a separate, deliberate act** (``resume``). STOP never turns anything on, and
  nothing here undoes an effect that already happened.

Scope: one controller per product process. It does not know about the other product and keeps no
per-session state, so session overrides (mood, style) are neither read nor reset here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

Outcome = Literal["requested", "confirmed", "still_active", "unknown"]
ProbeResult = Literal["confirmed", "still_active", "unknown"]
StopState = Literal["running", "stopping", "stopped", "partly_stopped"]

#: Seconds a part gets to accept the request, then to answer the probe.
REQUEST_TIMEOUT_S = 3.0
PROBE_TIMEOUT_S = 2.0
#: How long the probe is allowed to keep saying ``still_active`` before the answer is final.
SETTLE_S = 1.5
SETTLE_STEP_S = 0.1


@dataclass(frozen=True, slots=True)
class Subsystem:
    """One part that can be stopped. Names are fixed codes, never free text."""

    name: str
    request: Callable[[], Awaitable[None]]
    #: ``None`` = no way to tell whether it stopped: the outcome stays ``unknown``.
    probe: Callable[[], Awaitable[ProbeResult]] | None = None


@dataclass(frozen=True, slots=True)
class SubsystemResult:
    name: str
    outcome: Outcome
    #: A fixed code: ``ok``, ``no_probe``, ``request_failed``, ``request_timeout``,
    #: ``probe_failed``, ``still_running``.
    detail: str = "ok"


@dataclass(frozen=True, slots=True)
class StopReport:
    generation: int
    state: StopState
    results: tuple[SubsystemResult, ...]
    #: True when the door was already closed (a repeated STOP).
    repeated: bool = False


class StopInProgress(RuntimeError):
    """``resume`` was asked while a STOP is still shutting parts down."""


@dataclass(frozen=True, slots=True)
class Admission:
    allowed: bool
    #: ``ok`` | ``stopped`` | ``stale_generation``
    reason: str = "ok"


class StopController:
    """Generation, door and the per-part shutdowns of one product."""

    def __init__(
        self,
        subsystems: Iterable[Subsystem] = (),
        *,
        request_timeout_s: float = REQUEST_TIMEOUT_S,
        probe_timeout_s: float = PROBE_TIMEOUT_S,
        settle_s: float = SETTLE_S,
    ) -> None:
        self._subsystems: list[Subsystem] = list(subsystems)
        self._request_timeout_s = request_timeout_s
        self._probe_timeout_s = probe_timeout_s
        self._settle_s = settle_s
        self._generation = 0
        self._transitions = 0
        self._open = True
        self._state: StopState = "running"
        self._last: StopReport | None = None
        self._inflight: asyncio.Task[StopReport] | None = None
        # (on_close, on_open) pairs: gates other modules keep, flipped in the same synchronous step
        # as the door, so nothing can slip through between "STOP pressed" and "gate closed".
        self._listeners: list[tuple[Callable[[], None], Callable[[], None]]] = []

    # -- the door --------------------------------------------------------------------------

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def door_open(self) -> bool:
        return self._open

    @property
    def transitions(self) -> int:
        """Monotonic count of every change of door / state (STOP, resume, STOP finishing).

        Read-only: the same value before and after an await means nothing moved."""
        return self._transitions

    @property
    def state(self) -> StopState:
        return self._state

    @property
    def last_report(self) -> StopReport | None:
        return self._last

    def ticket(self) -> int:
        """The generation to capture when a turn or a dispatch is queued."""
        return self._generation

    def admit(self, ticket: int | None = None) -> Admission:
        """May this turn/dispatch/approval start now? ``ticket`` is what ``ticket()`` returned
        when it was queued; ``None`` means it is being created right now."""
        if not self._open:
            return Admission(False, "stopped")
        if ticket is not None and ticket != self._generation:
            return Admission(False, "stale_generation")
        return Admission(True)

    def resume(self) -> int:
        """The owner deliberately lets work start again. Opens a NEW generation, so anything
        queued before STOP stays refused. Does nothing when the door is already open.

        Refused (``StopInProgress``) while STOP is still shutting parts down: reopening then would
        let new work start only to be cancelled by the shutdown that is still running."""
        if self._open:
            return self._generation
        if self.stopping:
            raise StopInProgress("stop is still in progress")
        self._generation += 1
        self._transitions += 1
        self._open = True
        self._state = "running"
        for _on_close, on_open in self._listeners:
            self._call(on_open)
        return self._generation

    @property
    def stopping(self) -> bool:
        """A STOP is still running its shutdowns."""
        return self._inflight is not None and not self._inflight.done()

    def add_door_listener(self, on_close: Callable[[], None], on_open: Callable[[], None]) -> None:
        """Register a gate that must close/open together with the door. The current door state is
        applied at once, so a gate registered late is never out of step."""
        self._listeners.append((on_close, on_open))
        self._call(on_open if self._open else on_close)

    def _call(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:  # noqa: BLE001 - one broken gate must not stop the door from moving
            log.warning("stop: a door listener failed", exc_info=True)

    def add(self, subsystem: Subsystem) -> None:
        """Register a part (replaces one with the same name)."""
        self._subsystems = [s for s in self._subsystems if s.name != subsystem.name]
        self._subsystems.append(subsystem)

    # -- stopping --------------------------------------------------------------------------

    async def stop(self) -> StopReport:
        """STOP. The first lines run before any await: that is the 'door closes first' rule."""
        repeated = not self._open
        self._transitions += 1
        if self._open:
            self._generation += 1
            self._open = False
            for on_close, _on_open in self._listeners:
                self._call(on_close)
        self._state = "stopping"
        if self._inflight is not None and not self._inflight.done():
            return await asyncio.shield(self._inflight)
        previous = {r.name: r for r in self._last.results} if repeated and self._last else {}
        pending = [
            s for s in self._subsystems
            if previous.get(s.name) is None or previous[s.name].outcome != "confirmed"
        ]
        kept = tuple(r for r in previous.values() if r.outcome == "confirmed")
        self._inflight = asyncio.ensure_future(self._run(pending, kept, repeated))
        return await asyncio.shield(self._inflight)

    async def _recheck(self, part: Subsystem, before: SubsystemResult) -> SubsystemResult:
        """A part confirmed by an earlier STOP is asked once more by a repeated one: work that
        slipped past the door since (a path that does not use it) must not stay forgotten. Still
        confirmed -> kept as is; anything else -> the part is asked to stop again."""
        if part.probe is not None:
            try:
                if await asyncio.wait_for(part.probe(), self._probe_timeout_s) == "confirmed":
                    return before
            except Exception:  # noqa: BLE001 - an unreadable probe is "not confirmed any more"
                log.warning("stop: re-probe of %s failed", part.name, exc_info=True)
        return await self._stop_one(part)

    async def _run(
        self, pending: list[Subsystem], kept: tuple[SubsystemResult, ...], repeated: bool
    ) -> StopReport:
        generation = self._generation
        by_part = {s.name: s for s in self._subsystems}
        again = [self._recheck(by_part[r.name], r) for r in kept if r.name in by_part]
        fresh_all = await asyncio.gather(
            *(self._stop_one(s) for s in pending), *again)
        kept = tuple(r for r in kept if r.name not in by_part)
        fresh = list(fresh_all)
        by_name = {r.name: r for r in (*kept, *fresh)}
        order = [s.name for s in self._subsystems]
        results = tuple(by_name[n] for n in order if n in by_name)
        done = all(r.outcome == "confirmed" for r in results)
        state: StopState = "stopped" if done else "partly_stopped"
        report = StopReport(generation, state, results, repeated)
        if self._generation == generation and not self._open:
            self._state = state
        self._transitions += 1
        self._last = report
        return report

    async def _stop_one(self, part: Subsystem) -> SubsystemResult:
        try:
            await asyncio.wait_for(part.request(), self._request_timeout_s)
        except TimeoutError:
            log.warning("stop: %s did not accept the request in time", part.name)
            return SubsystemResult(part.name, await self._final(part), "request_timeout")
        except Exception:  # noqa: BLE001 - a part that fails must not stop the others
            log.warning("stop: %s failed to take the request", part.name, exc_info=True)
            return SubsystemResult(part.name, "unknown", "request_failed")
        if part.probe is None:
            return SubsystemResult(part.name, "unknown", "no_probe")
        outcome = await self._final(part)
        detail = "ok" if outcome == "confirmed" else (
            "still_running" if outcome == "still_active" else "probe_failed")
        return SubsystemResult(part.name, outcome, detail)

    async def _final(self, part: Subsystem) -> Outcome:
        """Ask the probe until it confirms or the settle window ends."""
        if part.probe is None:
            return "unknown"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settle_s
        answer: ProbeResult = "unknown"
        while True:
            try:
                answer = await asyncio.wait_for(part.probe(), self._probe_timeout_s)
            except TimeoutError:
                log.warning("stop: probe of %s did not answer in time", part.name)
                return "unknown"
            except Exception:  # noqa: BLE001 - a broken probe is "unknown", never "confirmed"
                log.warning("stop: probe of %s failed", part.name, exc_info=True)
                return "unknown"
            if answer != "still_active" or loop.time() >= deadline:
                return answer
            await asyncio.sleep(SETTLE_STEP_S)


#: The STOP generation the unit of work running in THIS context was created under (a queued
#: task, a background agent, a scheduled run). The creator sets it when it takes the ticket;
#: code further down re-verifies it before a side effect without every signature in between
#: having to carry it. ``None`` = no ticket was handed down (the work is taken to start now).
work_ticket: ContextVar[int | None] = ContextVar("dodaios_stop_work_ticket", default=None)

_PROCESS_CONTROLLER: StopController | None = None


def get_stop_controller() -> StopController:
    """The product process's one :class:`StopController`, created on first use.

    One process is one product, so this is the product-wide scope of STOP: it reaches every
    session of this product and nothing of the other product or of another installation.
    """
    global _PROCESS_CONTROLLER
    if _PROCESS_CONTROLLER is None:
        _PROCESS_CONTROLLER = StopController()
    return _PROCESS_CONTROLLER


def reset_stop_controller() -> None:
    """Forget the singleton (tests)."""
    global _PROCESS_CONTROLLER
    _PROCESS_CONTROLLER = None


__all__ = [
    "Admission",
    "Outcome",
    "StopController",
    "StopInProgress",
    "StopReport",
    "StopState",
    "Subsystem",
    "SubsystemResult",
    "get_stop_controller",
    "reset_stop_controller",
    "work_ticket",
]
