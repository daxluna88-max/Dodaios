"""The two voice parts of STOP, with probes that only answer what can be observed (ADR-0058).

* ``voice_playback``: the request stops the output device (``AudioPlayer.stop``). The probe reads
  the player's own state: ``playback_in_flight`` is False once its stream is closed and no
  ``play_chunks`` call holds the play lock. A player that does not expose it (a fake, an older
  build) or no pipeline at all gives ``unknown``, never ``confirmed``.
* ``realtime_session``: the request is the existing voice hangup (the same absolute kill as the
  hangup hotkey), asked only while a duplex session handle exists. The probe is that handle: it
  is cleared only after the realtime lifecycle unwound. No pipeline -> ``unknown`` (a browser-held
  call is not observable from here).

Neither probe looks at the provider's servers or at the speaker: "confirmed" means this process
holds no open output stream / no session handle, not that sound physically stopped.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from dodaios.control.stop import ProbeResult, Subsystem

log = logging.getLogger(__name__)


def _playback_request(get_pipeline: Callable[[], Any]) -> Callable[[], Any]:
    async def request() -> None:
        pipeline = get_pipeline()
        player = getattr(pipeline, "_player", None)
        if player is not None:
            player.stop()

    return request


def _playback_probe(get_pipeline: Callable[[], Any]) -> Callable[[], Any]:
    async def probe() -> ProbeResult:
        player = getattr(get_pipeline(), "_player", None)
        busy = getattr(player, "playback_in_flight", None)
        if busy is None:
            return "unknown"
        return "still_active" if busy else "confirmed"

    return probe


def _realtime_request(get_pipeline: Callable[[], Any]) -> Callable[[], Any]:
    async def request() -> None:
        pipeline = get_pipeline()
        handle = getattr(pipeline, "_realtime_voice_handle", None)
        if callable(handle) and handle() is not None:
            pipeline.request_voice_hangup()

    return request


def _realtime_probe(get_pipeline: Callable[[], Any]) -> Callable[[], Any]:
    async def probe() -> ProbeResult:
        handle = getattr(get_pipeline(), "_realtime_voice_handle", None)
        if not callable(handle):
            return "unknown"
        return "confirmed" if handle() is None else "still_active"

    return probe


def voice_subsystems(get_pipeline: Callable[[], Any]) -> tuple[Subsystem, Subsystem]:
    """``voice_playback`` and ``realtime_session``; the pipeline is looked up at call time."""
    return (
        Subsystem("voice_playback", _playback_request(get_pipeline),
                  _playback_probe(get_pipeline)),
        Subsystem("realtime_session", _realtime_request(get_pipeline),
                  _realtime_probe(get_pipeline)),
    )


__all__ = ["voice_subsystems"]
