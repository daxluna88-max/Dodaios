"""Replies of turns that started before STOP are not delivered (ADR-0058).

Written for the PC checklist (S02, S03, S04); not run when it was written.
"""
from __future__ import annotations

import pytest

from dodaios.brain import manager as mgr_mod
from dodaios.brain.manager import BrainManager
from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig
from dodaios.core.events import ResponseGenerated


@pytest.fixture(autouse=True)
def _fresh_controller():
    reset_stop_controller()
    yield
    reset_stop_controller()


def _brain() -> tuple[BrainManager, list[str]]:
    bus = EventBus()
    spoken: list[str] = []

    async def seen(event: ResponseGenerated) -> None:
        spoken.append(event.text)

    bus.subscribe(ResponseGenerated, seen)
    return BrainManager(config=JarvisConfig(), bus=bus, tools={}), spoken


async def test_a_response_of_a_turn_started_before_stop_is_not_published():
    brain, spoken = _brain()
    token = mgr_mod._STOP_TURN.set(mgr_mod._StopTurn(get_stop_controller().ticket()))
    try:
        await brain._publish_response_generated(trace_id=__import__("uuid").uuid4(), text="a")
        assert spoken == ["a"]  # the door is open: delivered
        await get_stop_controller().stop()
        await brain._publish_response_generated(trace_id=__import__("uuid").uuid4(), text="late")
        assert spoken == ["a"]  # after STOP: dropped
    finally:
        mgr_mod._STOP_TURN.reset(token)


async def test_still_dropped_after_a_resume_because_the_generation_changed():
    brain, spoken = _brain()
    token = mgr_mod._STOP_TURN.set(mgr_mod._StopTurn(get_stop_controller().ticket()))
    try:
        await get_stop_controller().stop()
        get_stop_controller().resume()
        await brain._publish_response_generated(trace_id=__import__("uuid").uuid4(), text="late")
        assert spoken == []
    finally:
        mgr_mod._STOP_TURN.reset(token)


async def test_the_refusal_reply_and_a_stop_phrase_are_exempt():
    brain, spoken = _brain()
    await get_stop_controller().stop()
    turn = mgr_mod._StopTurn(get_stop_controller().ticket(), exempt=True)
    token = mgr_mod._STOP_TURN.set(turn)
    try:
        await brain._publish_response_generated(trace_id=__import__("uuid").uuid4(), text="ok")
        assert spoken == ["ok"]
    finally:
        mgr_mod._STOP_TURN.reset(token)


async def test_streamed_text_stops_reaching_the_screen_after_stop():
    chunks: list[str] = []
    turn = mgr_mod._StopTurn(get_stop_controller().ticket())
    guarded = mgr_mod._guard_text_consumer(chunks.append, turn)
    guarded("one ")
    await get_stop_controller().stop()
    guarded("two")
    assert chunks == ["one "]
    assert mgr_mod._guard_text_consumer(None, turn) is None


async def test_history_is_not_written_for_a_late_turn():
    brain, _ = _brain()
    token = mgr_mod._STOP_TURN.set(mgr_mod._StopTurn(get_stop_controller().ticket()))
    try:
        await get_stop_controller().stop()
        before = len(brain._history)
        await brain._record_response_side_effects(
            user_text="q", response_text="late", use_history=True)
        assert len(brain._history) == before
    finally:
        mgr_mod._STOP_TURN.reset(token)
