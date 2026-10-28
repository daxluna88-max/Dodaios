"""Block 17 (written, NOT run): the STOP ticket is applied AT the write, not only on entry."""
from __future__ import annotations

import asyncio
import contextlib

from dodaios.control.stop import StopController
from dodaios.speech import stop_gate


def _with_controller(monkeypatch, ctl):
    monkeypatch.setattr("dodaios.control.stop.get_stop_controller", lambda: ctl)


async def test_fenced_binary_drops_after_stop_and_reports_the_drop(monkeypatch):
    ctl = StopController(settle_s=0.0)
    _with_controller(monkeypatch, ctl)
    ticket = stop_gate.session_ticket()
    sent, drops = [], []

    async def raw(data):
        sent.append(data)

    send = stop_gate.fenced_sender(ticket, raw, on_drop=lambda: drops.append(1))
    await send(b"a")
    await ctl.stop()
    await send(b"b")
    assert sent == [b"a"] and drops == [1]
    ctl.resume()
    await send(b"c")  # STOP and resume: the original ticket stays stale
    assert sent == [b"a"]


async def test_stop_during_an_await_between_entry_check_and_send_is_caught(monkeypatch):
    """Models native audio_delta: entry check passes, STOP lands in the awaited work, send."""
    ctl = StopController(settle_s=0.0)
    _with_controller(monkeypatch, ctl)
    ticket = stop_gate.session_ticket()
    sent = []

    async def raw(data):
        sent.append(data)

    send = stop_gate.fenced_sender(ticket, raw)
    assert not ticket.stale()  # the entry check passes
    stopper = asyncio.ensure_future(ctl.stop())
    await asyncio.sleep(0)  # the awaited work (e.g. _note_speaking) lets STOP in
    with contextlib.suppress(Exception):
        await stopper
    await send(b"old audio")
    assert sent == []


async def test_json_fence_only_covers_content_frames_not_control_or_user_text(monkeypatch):
    ctl = StopController(settle_s=0.0)
    _with_controller(monkeypatch, ctl)
    ticket = stop_gate.session_ticket()
    sent = []

    async def raw(msg):
        sent.append(msg["type"])

    send = stop_gate.fenced_sender(ticket, raw, content_types=stop_gate.STOPPABLE_JSON_TYPES)
    await ctl.stop()
    await send({"type": "transcript", "role": "assistant"})
    await send({"type": "audio_timed"})
    await send({"type": "speech_timing"})
    await send({"type": "error_spoken"})
    await send({"type": "audio_clear"})
    await send({"type": "transcript", "role": "user"})
    assert sent == ["audio_clear", "transcript"]
