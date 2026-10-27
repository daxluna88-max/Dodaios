"""Kill-Switch wiring — connects the hotkey, tray, and voice intent to the bus.

All three paths converge on the same event: `KillRequested(source=...)`.
The `KillSwitch` aggregator (from `cancel.py`) is already bound to the bus
and fires the tokens — this file only provides the event sources.

The mandate requires: `Ctrl+Alt+Shift+K` aborts within <2 s. Voice command
phrases are matched against a regex in the existing `TranscriptFinal`
pipeline — no new skill/tool infrastructure needed.

Integration point is Task 12 (main app startup).
"""
from __future__ import annotations

import asyncio
import re
import weakref
from typing import TYPE_CHECKING

from dodaios.core.events import KillRequested, TranscriptFinal, TranscriptionUpdate

if TYPE_CHECKING:
    from dodaios.core.bus import EventBus


# ----------------------------------------------------------------------
# Voice-Intent
# ----------------------------------------------------------------------

# German + English. Deliberately broad — one false-positive is preferable to
# missing a genuine emergency.
_KILL_PHRASES = re.compile(
    r"""(?ix)
    \b(
        notfall[\s\-]?stopp?
      | not[\s\-]?stopp?
      | jarvis[,\s]+stopp?
      | stopp?[,\s]+jarvis
      | kill[\s\-]?switch
      | emergency[\s\-]?stop
      | abort[\s\-]?all
      | alles[\s\-]?stopp?(en)?
    )\b
    """,
)


def voice_matches_kill_intent(text: str) -> bool:
    """Return True when the transcript text contains a kill phrase."""
    return bool(_KILL_PHRASES.search(text or ""))


_VOICE_WIRED: weakref.WeakSet = weakref.WeakSet()
_STOP_DOOR_WIRED: weakref.WeakSet = weakref.WeakSet()


def wire_voice_kill_switch(bus: EventBus) -> None:
    """Subscribe to the final user transcripts and publish `KillRequested`
    whenever a kill phrase is detected.

    Two sources: `TranscriptFinal` (classic speech pipeline) and a FINAL
    `TranscriptionUpdate` (realtime session; it does not publish
    `TranscriptFinal`). Idempotent per bus.
    """
    try:
        if bus in _VOICE_WIRED:
            return
        _VOICE_WIRED.add(bus)
    except TypeError:  # a bus that cannot be weakly referenced: wire it, no memo
        pass

    async def on_transcript(ev: TranscriptFinal) -> None:
        if ev.transcript is None:
            return
        if voice_matches_kill_intent(ev.transcript.text):
            await bus.publish(KillRequested(source="voice",
                                             trace_id=ev.trace_id))

    async def on_update(ev: TranscriptionUpdate) -> None:
        if ev.is_final and voice_matches_kill_intent(ev.text):
            await bus.publish(KillRequested(source="voice",
                                             trace_id=ev.trace_id))

    bus.subscribe(TranscriptFinal, on_transcript)
    bus.subscribe(TranscriptionUpdate, on_update)


def wire_voice_stop_door(bus: EventBus) -> None:
    """A voice `KillRequested` closes the ADR-0058 STOP door like the button does.

    `KillSwitch` only cancels tokens; the door (generation, playback, realtime session) is the
    `StopController`. Only `source="voice"` is bridged here: tray and hotkey keep their
    behaviour (they are not changed by this block). The voice parts are registered first, so
    this works even if the control strip was never opened. Idempotent per bus.
    """
    try:
        if bus in _STOP_DOOR_WIRED:
            return
        _STOP_DOOR_WIRED.add(bus)
    except TypeError:
        pass

    async def on_kill(ev: KillRequested) -> None:
        if ev.source != "voice":
            return
        from dodaios.control.stop import get_stop_controller  # noqa: PLC0415
        from dodaios.core.runtime_refs import get_speech_pipeline  # noqa: PLC0415
        from dodaios.speech.stop_parts import voice_subsystems  # noqa: PLC0415

        controller = get_stop_controller()
        for part in voice_subsystems(get_speech_pipeline):
            controller.add(part)
        await controller.stop()

    bus.subscribe(KillRequested, on_kill)


# ----------------------------------------------------------------------
# Tray
# ----------------------------------------------------------------------

def wire_tray_kill_switch(
    tray_command_queue: asyncio.Queue,
    bus: EventBus,
    *,
    stop_event: asyncio.Event | None = None,
) -> asyncio.Task[None]:
    """Read tray commands from an asyncio queue and publish
    `KillRequested` for `action="kill"` commands.

    Returns the running task; the caller must cancel it on shutdown.
    """
    async def loop() -> None:
        while stop_event is None or not stop_event.is_set():
            try:
                cmd = await asyncio.wait_for(tray_command_queue.get(),
                                              timeout=0.2)
            except TimeoutError:
                continue
            if getattr(cmd, "action", None) == "kill":
                await bus.publish(KillRequested(source="tray"))

    return asyncio.create_task(loop(), name="kill-switch-tray-bridge")


# ----------------------------------------------------------------------
# Hotkey
# ----------------------------------------------------------------------

# Default hotkey per mandate §Assumptions. Config override:
# `jarvis.toml:[kill_switch] hotkey = "ctrl+alt+shift+k"`
DEFAULT_KILL_HOTKEY: str = "ctrl+alt+shift+k"


async def run_kill_hotkey_trigger(
    bus: EventBus,
    *,
    combo: str = DEFAULT_KILL_HOTKEY,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Register the global kill hotkey and publish `KillRequested`
    on every press.

    Runs as a long-running coroutine that cleans up cleanly on
    `stop_event.set()` or `CancelledError`.
    """
    # Lazy import — same pattern as HotkeyTrigger itself.
    from dodaios.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger({"kill": [combo]}) as trig:
        async for event_name in trig.events():
            if event_name == "kill":
                await bus.publish(KillRequested(source="hotkey"))
            if stop_event is not None and stop_event.is_set():
                break


# ----------------------------------------------------------------------
# Convenience — wire everything at once
# ----------------------------------------------------------------------

def wire_kill_switch_on_bus(
    bus: EventBus,
    *,
    tray_command_queue: asyncio.Queue | None = None,
    hotkey_combo: str = DEFAULT_KILL_HOTKEY,
    enable_voice: bool = True,
    stop_event: asyncio.Event | None = None,
) -> dict[str, asyncio.Task | None]:
    """Wire voice + tray + hotkey against a bus.

    Returns a dict of background tasks so the caller can cancel them
    cleanly on shutdown.
    """
    tasks: dict[str, asyncio.Task | None] = {}

    if enable_voice:
        wire_voice_kill_switch(bus)        # pure subscriber, no task
        wire_voice_stop_door(bus)

    if tray_command_queue is not None:
        tasks["tray"] = wire_tray_kill_switch(
            tray_command_queue, bus, stop_event=stop_event,
        )

    tasks["hotkey"] = asyncio.create_task(
        run_kill_hotkey_trigger(bus, combo=hotkey_combo,
                                 stop_event=stop_event),
        name="kill-switch-hotkey",
    )
    return tasks
