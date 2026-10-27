"""Turns REAL runtime events into ``AvatarSignal`` bus events (ADR-0058 block 13).

No signal exists without the underlying real event:

* turn_start    <- ``VoiceTurnStarted`` (a turn was really created)
* thinking      <- ``BrainTurnStarted`` or supervisor ``THINKING``
* listening     <- supervisor ``LISTENING``
* speaking_start<- ``AudioOutFirst`` (first audible sample / browser playback), only inside a turn
                   of the current STOP generation
* speaking_stop <- supervisor leaves ``SPEAKING`` or ``VoiceTurnCompleted``, only after a
                   speaking_start
* error         <- ``ErrorOccurred`` or supervisor ``ERROR``
* stop / idle   <- the STOP door closing (``StopController`` listener)
* expression_plan <- ``offer_plan`` with a plan the ``PlanGate`` (ADR-0056) accepts

After STOP the bridge drops everything until the door reopens and a NEW turn starts in the new
generation: a late AudioOutFirst of the stopped turn cannot make the face speak again. Signals
travel on the bus; the web server already forwards every bus event to ``/ws``.
This is speech ACTIVITY, never lip sync (see ``capabilities``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from dodaios.core.events import (
    AudioOutFirst,
    AvatarSignal,
    BrainTurnStarted,
    ErrorOccurred,
    SystemStateChanged,
    VoiceTurnCompleted,
    VoiceTurnStarted,
)

log = logging.getLogger(__name__)


class AvatarBridge:
    def __init__(self, bus: Any, controller: Any = None) -> None:
        if controller is None:
            from dodaios.control.stop import get_stop_controller  # noqa: PLC0415

            controller = get_stop_controller()
        self._bus = bus
        self._ctl = controller
        self._seq = 0
        #: STOP generation of the turn the avatar is following; -1 = no turn yet.
        self._turn_gen = -1
        self._turn_id = ""
        self._speaking = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._attached = False

    # -- wiring ------------------------------------------------------------------------------

    def attach(self) -> None:
        if self._attached:
            return
        self._attached = True
        with contextlib.suppress(RuntimeError):
            self._loop = asyncio.get_running_loop()
        self._bus.subscribe(VoiceTurnStarted, self._on_turn_started)
        self._bus.subscribe(BrainTurnStarted, self._on_brain_started)
        self._bus.subscribe(SystemStateChanged, self._on_state)
        self._bus.subscribe(AudioOutFirst, self._on_audio_first)
        self._bus.subscribe(VoiceTurnCompleted, self._on_turn_completed)
        self._bus.subscribe(ErrorOccurred, self._on_error)
        self._ctl.add_door_listener(self._on_door_close, self._on_door_open)

    def detach(self) -> None:
        self._ctl.remove_door_listener(self._on_door_close)

    # -- helpers -----------------------------------------------------------------------------

    def _current(self) -> bool:
        """The door is open and the followed turn belongs to the current generation."""
        return (
            self._ctl.door_open
            and self._turn_gen == self._ctl.generation
        )

    async def _emit(self, kind: str, reason: str, *, expression: str = "") -> None:
        self._seq += 1
        await self._bus.publish(AvatarSignal(
            source_layer="avatar.bridge", kind=kind, turn_id=self._turn_id,
            generation=self._ctl.generation, seq=self._seq, expression=expression,
            reason=reason,
        ))

    # -- real events -------------------------------------------------------------------------

    async def _on_turn_started(self, ev: VoiceTurnStarted) -> None:
        if not self._ctl.door_open:
            return
        self._turn_gen = self._ctl.generation
        self._turn_id = ev.turn_id
        self._speaking = False
        await self._emit("turn_start", "VoiceTurnStarted")

    async def _on_brain_started(self, _ev: BrainTurnStarted) -> None:
        if self._ctl.door_open:
            # A brain turn without a voice turn (chat) follows the current generation too.
            if self._turn_gen != self._ctl.generation:
                self._turn_gen, self._turn_id, self._speaking = self._ctl.generation, "", False
            await self._emit("thinking", "BrainTurnStarted")

    async def _on_state(self, ev: SystemStateChanged) -> None:
        if not self._ctl.door_open:
            return
        state = str(ev.new_state).upper()
        if state == "LISTENING":
            await self._emit("listening", "SystemStateChanged:LISTENING")
        elif state == "THINKING":
            await self._emit("thinking", "SystemStateChanged:THINKING")
        elif state == "ERROR":
            await self._emit("error", "SystemStateChanged:ERROR")
        elif state in {"IDLE", "PAUSED"}:
            await self._end_speaking(f"SystemStateChanged:{state}")
        if state in {"LISTENING", "THINKING"}:
            await self._end_speaking(f"SystemStateChanged:{state}")

    async def _on_audio_first(self, _ev: AudioOutFirst) -> None:
        if not self._current() or self._speaking:
            return
        self._speaking = True
        await self._emit("speaking_start", "AudioOutFirst")

    async def _on_turn_completed(self, _ev: VoiceTurnCompleted) -> None:
        if self._ctl.door_open:
            await self._end_speaking("VoiceTurnCompleted")

    async def _on_error(self, _ev: ErrorOccurred) -> None:
        if self._ctl.door_open:
            await self._emit("error", "ErrorOccurred")

    async def _end_speaking(self, reason: str) -> None:
        if self._speaking:
            self._speaking = False
            await self._emit("speaking_stop", reason)

    # -- expression plan (ADR-0056) ----------------------------------------------------------

    async def offer_plan(self, plan: Any, gate: Any) -> bool:
        """Emit the plan's expression only if the PlanGate accepts it (turn + generation)."""
        if not self._ctl.door_open or not gate.accepts(plan):
            return False
        if plan.identity.generation != self._ctl.generation:
            return False
        self._seq += 1
        await self._bus.publish(AvatarSignal(
            source_layer="avatar.bridge", kind="expression_plan",
            turn_id=plan.identity.turn_id, generation=plan.identity.generation,
            seq=self._seq, expression=plan.expression, reason="ExpressionPlan",
        ))
        return True

    # -- STOP --------------------------------------------------------------------------------

    def _on_door_close(self) -> None:
        """Synchronous, inside ``StopController.stop``: forget the turn, then say stop / idle."""
        self._turn_gen, self._speaking = -1, False
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        async def _announce() -> None:
            await self._emit("stop", "StopController")
            await self._emit("idle", "StopController")

        def _schedule() -> None:
            asyncio.ensure_future(_announce())

        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(_schedule)

    def _on_door_open(self) -> None:
        self._turn_gen, self._speaking = -1, False


__all__ = ["AvatarBridge"]
