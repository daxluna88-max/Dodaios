"""Block 13 (written, NOT run): a spoken stop closes the same door as the button, on every path.

Fakes only; no audio device, provider or app was involved.
"""
from __future__ import annotations

import asyncio

import pytest

from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.control.wiring import wire_voice_kill_switch, wire_voice_stop_door
from dodaios.core.bus import EventBus
from dodaios.core.events import KillRequested, TranscriptionUpdate
from dodaios.realtime.desktop import DesktopRealtimeBargeInDetector
from dodaios.speech import stop_gate
from dodaios.speech.spoken_stop import close_door_on_stop_phrase, is_stop_phrase


@pytest.fixture(autouse=True)
def _fresh():
    reset_stop_controller()
    yield
    reset_stop_controller()


async def test_stop_phrase_closes_the_door_before_returning():
    assert await close_door_on_stop_phrase("Jarvis, stopp", lambda: None) is True
    assert get_stop_controller().door_open is False


async def test_ordinary_text_leaves_the_door_open():
    assert is_stop_phrase("what is the weather") is False
    assert await close_door_on_stop_phrase("what is the weather", lambda: None) is False
    assert get_stop_controller().door_open is True


def test_session_ticket_is_stale_at_once_when_created_with_the_door_closed():
    ctl = get_stop_controller()
    asyncio.run(ctl.stop())
    assert stop_gate.session_ticket().stale() is True  # a call is never exempt


def test_session_ticket_goes_stale_on_stop_and_resume():
    ctl = get_stop_controller()
    ticket = stop_gate.session_ticket()
    assert ticket.stale() is False
    asyncio.run(ctl.stop())
    ctl.resume()
    assert ticket.stale() is True


async def test_realtime_final_transcript_with_kill_phrase_reaches_the_door_via_the_bus():
    bus = EventBus()
    wire_voice_kill_switch(bus)
    wire_voice_stop_door(bus)
    await bus.publish(TranscriptionUpdate(text="emergency stop", is_final=True))
    await asyncio.sleep(0.05)
    assert get_stop_controller().door_open is False


async def test_partial_transcript_does_not_stop():
    bus = EventBus()
    wire_voice_kill_switch(bus)
    wire_voice_stop_door(bus)
    await bus.publish(TranscriptionUpdate(text="emergency stop", is_final=False))
    await asyncio.sleep(0.05)
    assert get_stop_controller().door_open is True


async def test_only_voice_kill_closes_the_door_from_the_bus():
    bus = EventBus()
    wire_voice_stop_door(bus)
    await bus.publish(KillRequested(source="tray"))
    await asyncio.sleep(0.05)
    assert get_stop_controller().door_open is True
    await bus.publish(KillRequested(source="voice"))
    await asyncio.sleep(0.05)
    assert get_stop_controller().door_open is False


def test_wiring_is_idempotent_per_bus():
    bus = EventBus()
    wire_voice_stop_door(bus)
    wire_voice_stop_door(bus)
    assert len(bus._subscribers[KillRequested]) == 1  # noqa: SLF001


def test_barge_in_detector_forwards_nothing_after_stop():
    detector = DesktopRealtimeBargeInDetector(stale=lambda: True)
    detector._active = True  # noqa: SLF001
    detector._ready = True  # noqa: SLF001
    assert detector.feed(b"\x00\x01" * 512) is None
