"""A dispatched mission is re-verified when its background start begins (ADR-0058)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.missions.manager import MissionManager
from dodaios.missions.state_machine import MissionState
from dodaios.ui.web import missions_routes
from dodaios.ui.web.missions_routes import router as missions_router


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


def _app(manager: MissionManager, started: list[str]) -> FastAPI:
    class Stub:
        async def run_mission(self, mission_id: str) -> None:
            started.append(mission_id)

    app = FastAPI()
    app.include_router(missions_router)
    app.state.mission_manager = manager
    app.state.kontrollierer = Stub()
    return app


def _stop_after_the_door_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """STOP lands after the route's own re-check, i.e. between the response and the start."""
    original = missions_routes._refuse_if_stopped_meanwhile

    async def then_stop(ticket, mission_id, request):
        await original(ticket, mission_id, request)
        await get_stop_controller().stop()

    monkeypatch.setattr(missions_routes, "_refuse_if_stopped_meanwhile", then_stop)


async def test_a_dispatched_mission_does_not_start_when_stop_landed_before_its_background_start(
    manager: MissionManager, monkeypatch: pytest.MonkeyPatch
):
    started: list[str] = []
    _stop_after_the_door_check(monkeypatch)
    with TestClient(_app(manager, started)) as client:
        response = client.post("/api/missions/dispatch", json={"prompt": "go"})
    assert response.status_code == 201
    assert started == []
    view = await manager.mission(response.json()["mission_id"])
    assert view is not None and view.state == MissionState.CANCELLED


async def test_a_dispatched_mission_starts_normally_when_nothing_stopped(
    manager: MissionManager,
):
    started: list[str] = []
    with TestClient(_app(manager, started)) as client:
        response = client.post("/api/missions/dispatch", json={"prompt": "go"})
    assert response.status_code == 201 and started == [response.json()["mission_id"]]
