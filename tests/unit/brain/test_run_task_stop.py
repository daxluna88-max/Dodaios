"""A scheduled/background turn (``run_task``) carries the STOP ticket it was created under."""
from __future__ import annotations

import pytest

from dodaios.brain import manager as mgr_mod
from dodaios.brain.manager import BrainManager
from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig


@pytest.fixture(autouse=True)
def _fresh_controller():
    reset_stop_controller()
    yield
    reset_stop_controller()


def _brain() -> tuple[BrainManager, list[str]]:
    brain = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    ran: list[str] = []

    async def inner(**kwargs) -> str:
        ran.append(kwargs["prompt"])
        assert mgr_mod._STOP_TURN.get() is not None  # its tools are gated by the loop
        if kwargs["prompt"] == "stops-midway":
            await get_stop_controller().stop()
        return "result"

    brain._run_task_with_selection = inner  # type: ignore[method-assign]
    return brain, ran


async def test_a_task_created_before_stop_does_not_start_after_resume():
    brain, ran = _brain()
    ticket = get_stop_controller().ticket()  # taken when the task was created
    await get_stop_controller().stop()
    get_stop_controller().resume()
    assert await brain.run_task(prompt="old", stop_ticket=ticket) == ""
    assert ran == []


async def test_a_task_is_not_started_while_the_door_is_closed():
    brain, ran = _brain()
    await get_stop_controller().stop()
    assert await brain.run_task(prompt="x") == ""
    assert ran == []


async def test_a_task_started_after_resume_runs_normally():
    brain, ran = _brain()
    await get_stop_controller().stop()
    get_stop_controller().resume()
    assert await brain.run_task(prompt="new") == "result"
    assert ran == ["new"]


async def test_a_reply_that_finishes_after_stop_is_dropped():
    brain, ran = _brain()
    assert await brain.run_task(prompt="stops-midway") == ""
    assert ran == ["stops-midway"]
    assert mgr_mod._STOP_TURN.get() is None  # nothing leaks out of the call
