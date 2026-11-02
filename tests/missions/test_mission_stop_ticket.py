"""A mission takes its STOP ticket at creation; it is re-verified at start and per worker."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.missions.kontrollierer.decomposer import MissionPlan, Step
from dodaios.missions.manager import MissionManager
from dodaios.missions.state_machine import MissionState
from tests.fakes.fake_mission_runtime import (
    ApprovingCritic,
    FakeMissionWorker,
    make_kontrollierer,
)


@pytest.fixture(autouse=True)
def _fresh_controller():
    reset_stop_controller()
    yield
    reset_stop_controller()


@pytest.fixture
async def manager(tmp_missions_db: Path):
    m = MissionManager(tmp_missions_db)
    await m.start()
    yield m
    await m.stop()


def _setup(manager: MissionManager, tmp_path: Path):
    worker = FakeMissionWorker()
    plan = MissionPlan(steps=[Step(slug="task", prompt="do it")], n_workers=1)
    k = make_kontrollierer(manager, tmp_path, plan, lambda _s: worker, ApprovingCritic())
    return k, worker, plan


async def _state(manager: MissionManager, mission_id: str) -> MissionState:
    view = await manager.mission(mission_id)
    assert view is not None
    return view.state


async def test_a_mission_created_before_stop_never_runs_after_resume(
    manager: MissionManager, tmp_path: Path
):
    k, worker, _plan = _setup(manager, tmp_path)
    mid = await manager.dispatch(prompt="p")
    ticket = get_stop_controller().ticket()  # taken at creation
    await get_stop_controller().stop()
    get_stop_controller().resume()
    assert await k.run_mission(mid, stop_ticket=ticket) == MissionState.CANCELLED
    assert worker.prompts == []
    assert await _state(manager, mid) == MissionState.CANCELLED


async def test_a_mission_is_not_started_while_the_door_is_closed(
    manager: MissionManager, tmp_path: Path
):
    k, worker, _plan = _setup(manager, tmp_path)
    mid = await manager.dispatch(prompt="p")
    await get_stop_controller().stop()
    assert await k.run_mission(mid) == MissionState.CANCELLED
    assert worker.prompts == []


async def test_stop_landing_after_start_but_before_the_worker_spawns_runs_no_worker(
    manager: MissionManager, tmp_path: Path
):
    k, worker, plan = _setup(manager, tmp_path)

    class StopsWhilePlanning:
        async def decompose(self, _prompt: str) -> MissionPlan:
            await get_stop_controller().stop()  # a STOP with no parts: only the door moves
            return plan

    k._decomposer = StopsWhilePlanning()  # type: ignore[assignment]
    mid = await manager.dispatch(prompt="p")
    await k.run_mission(mid)
    assert worker.prompts == []
    assert await _state(manager, mid) == MissionState.CANCELLED


async def test_a_mission_started_after_resume_runs_normally(
    manager: MissionManager, tmp_path: Path
):
    k, worker, _plan = _setup(manager, tmp_path)
    await get_stop_controller().stop()
    get_stop_controller().resume()
    mid = await manager.dispatch(prompt="p")
    state: Any = await k.run_mission(mid, stop_ticket=get_stop_controller().ticket())
    assert state == MissionState.APPROVED
    assert len(worker.prompts) == 1


async def test_parked_missions_are_not_resumed_while_the_door_is_closed(
    manager: MissionManager, tmp_path: Path
):
    k, _worker, _plan = _setup(manager, tmp_path)
    await get_stop_controller().stop()
    assert await k.resume_waiting_missions() == []
