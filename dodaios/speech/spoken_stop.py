"""One spoken-STOP path for every voice entry point (ADR-0058, block 13).

A spoken stop phrase (the kill-intent regex of ``control.wiring`` or the voice-command gate's
``cancel``) closes the SAME door as the button: ``StopController.stop()``. The door closes before
this returns (the controller's first lines run before any await); the parts (playback, realtime
session, and whatever the strip registered) are then stopped in the background.

Used by the speech pipeline, the realtime session and the bus subscriber of
``control.wiring.wire_voice_stop_door``. Pure coordination: no audio, no network.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

#: Strong references to the background STOP tasks (a bare task may be garbage collected).
_TASKS: set[asyncio.Future[Any]] = set()
_LAST: list[asyncio.Future[Any]] = []


def last_stop_task() -> asyncio.Future[Any] | None:
    """The STOP task the latest spoken stop started (tests and callers that await the report)."""
    return _LAST[-1] if _LAST else None


def is_stop_phrase(text: str) -> bool:
    from dodaios.brain.voice_command_gate import match_voice_command  # noqa: PLC0415
    from dodaios.control.wiring import voice_matches_kill_intent  # noqa: PLC0415

    if voice_matches_kill_intent(text):
        return True
    match = match_voice_command(text)
    return match is not None and match.kind == "cancel"


def _pipeline() -> Any:
    try:
        from dodaios.core.runtime_refs import get_speech_pipeline  # noqa: PLC0415

        return get_speech_pipeline()
    except Exception:  # noqa: BLE001 - no pipeline: the voice parts answer "unknown"
        return None


async def close_door_on_stop_phrase(
    text: str, get_pipeline: Callable[[], Any] | None = None
) -> bool:
    """Close the STOP door when ``text`` is a stop phrase. Returns whether it was one."""
    if not is_stop_phrase(text):
        return False
    from dodaios.control.stop import get_stop_controller  # noqa: PLC0415
    from dodaios.speech.stop_parts import voice_subsystems  # noqa: PLC0415

    controller = get_stop_controller()
    for part in voice_subsystems(get_pipeline or _pipeline):
        controller.add(part)
    task = asyncio.ensure_future(controller.stop())
    _TASKS.add(task)
    _LAST[:] = [task]
    task.add_done_callback(_TASKS.discard)
    await asyncio.sleep(0)  # lets stop() run its synchronous door-closing lines
    return True


__all__ = ["close_door_on_stop_phrase", "is_stop_phrase", "last_stop_task"]
