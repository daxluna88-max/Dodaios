"""Block 18 (written, NOT run): raw sender re-check at the enqueue point (``send_allowed``).

Local sends only. These do not prove physical playback, WebRTC or provider cancellation.
"""
from __future__ import annotations

import asyncio

from dodaios.control.stop import StopController
from dodaios.speech import stop_gate


def _ticket(ctl):
    return stop_gate.VoiceTurnTicket(ctl.ticket())


async def test_send_allowed_is_true_outside_a_fenced_send():
    assert stop_gate.send_allowed() is True


async def test_raw_sender_sees_stop_that_landed_during_its_own_await(monkeypatch):
    ctl = StopController(settle_s=0.0)
    monkeypatch.setattr("dodaios.control.stop.get_stop_controller", lambda: ctl)
    ticket = _ticket(ctl)
    written: list[bytes] = []

    async def raw(data: bytes) -> None:
        await asyncio.sleep(0)  # e.g. a lock / state change
        await ctl.stop()  # STOP lands inside the sender
        if not stop_gate.send_allowed():
            return
        written.append(data)

    send = stop_gate.fenced_sender(ticket, raw)
    await send(b"x")
    assert written == []


async def test_raw_sender_without_stop_still_writes(monkeypatch):
    ctl = StopController(settle_s=0.0)
    monkeypatch.setattr("dodaios.control.stop.get_stop_controller", lambda: ctl)
    written: list[bytes] = []

    async def raw(data: bytes) -> None:
        await asyncio.sleep(0)
        if stop_gate.send_allowed():
            written.append(data)

    await stop_gate.fenced_sender(_ticket(ctl), raw)(b"x")
    assert written == [b"x"]


async def test_control_frames_are_not_fenced_and_leave_no_fence_behind(monkeypatch):
    ctl = StopController(settle_s=0.0)
    monkeypatch.setattr("dodaios.control.stop.get_stop_controller", lambda: ctl)
    ticket = _ticket(ctl)
    sent: list[dict] = []

    async def raw(msg: dict) -> None:
        sent.append(msg if stop_gate.send_allowed() else {"dropped": True})

    send = stop_gate.fenced_sender(
        ticket, raw, content_types=stop_gate.STOPPABLE_JSON_TYPES
    )
    await ctl.stop()
    await send({"type": "audio_clear"})
    assert sent == [{"type": "audio_clear"}]
    assert stop_gate.send_allowed() is True  # the context var was reset

