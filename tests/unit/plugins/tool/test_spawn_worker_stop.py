"""A background agent dispatch takes its STOP ticket when created and re-verifies it (ADR-0058)."""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.core.bus import EventBus
from dodaios.core.protocols import ExecutionContext
from dodaios.plugins.tool.spawn_worker import SpawnWorkerTool


@pytest.fixture(autouse=True)
def _fresh_controller():
    reset_stop_controller()
    yield
    reset_stop_controller()


class _Manager:
    def __init__(self, stop_during_dispatch: bool = False) -> None:
        self.dispatched: list[str] = []
        self.cancelled: list[str] = []
        self._stop_during = stop_during_dispatch

    async def dispatch(self, *, prompt: str, language: str, source_actor: str) -> str:
        if self._stop_during:
            await get_stop_controller().stop()
        self.dispatched.append(prompt)
        return "mission_1"

    async def transition_state(self, mission_id: str, to_state: Any, **_k: Any) -> None:
        self.cancelled.append(f"{mission_id}:{to_state.value}")


class _Runner:
    def __init__(self) -> None:
        self.runs: list[str] = []

    async def run_mission(self, mission_id: str) -> None:
        self.runs.append(mission_id)


def _ctx() -> ExecutionContext:
    return ExecutionContext(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)


async def test_a_dispatch_created_before_stop_does_not_start_after_resume():
    mgr, runner = _Manager(), _Runner()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr)
    tool._active_dispatches = 1
    ticket = get_stop_controller().ticket()  # taken when the dispatch was created
    await get_stop_controller().stop()
    get_stop_controller().resume()
    await tool._background_dispatch("x", "x", mgr, runner, stop_ticket=ticket)
    assert mgr.dispatched == [] and runner.runs == []
    assert tool._active_dispatches == 0  # the liveness gate is still released


async def test_stop_between_persisting_the_mission_and_running_it_cancels_it():
    mgr, runner = _Manager(stop_during_dispatch=True), _Runner()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr)
    tool._active_dispatches = 1
    await tool._background_dispatch("x", "x", mgr, runner)
    assert mgr.dispatched == ["x"] and runner.runs == []
    assert mgr.cancelled == ["mission_1:CANCELLED"]


async def test_a_dispatch_after_resume_runs_normally():
    mgr, runner = _Manager(), _Runner()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr)
    tool._active_dispatches = 1
    await get_stop_controller().stop()
    get_stop_controller().resume()
    ticket = get_stop_controller().ticket()
    await tool._background_dispatch("x", "x", mgr, runner, stop_ticket=ticket)
    assert runner.runs == ["mission_1"]


async def test_execute_is_refused_while_the_door_is_closed():
    mgr = _Manager()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr)
    await get_stop_controller().stop()
    result = await tool.execute({"utterance": "build me a site", "action": "x"}, _ctx())
    await asyncio.sleep(0.05)
    assert result.success is False and mgr.dispatched == []
    assert tool._active_dispatches == 0
