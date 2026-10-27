"""Control strip API: STOP, resume and the combined state (ADR-0058).

Written for the PC checklist (S01-S09, O11); not run when it was written.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from dodaios.brain.manager import BrainManager
from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig
from dodaios.i18n import catalog
from dodaios.ui.web import control_strip_routes as routes
from dodaios.ui.web import missions_routes
from dodaios.ui.web.surface_security import _http_auth_exception


@pytest.fixture
def client():
    reset_stop_controller()
    app = FastAPI()
    app.state.brain = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    app.include_router(routes.router)
    yield TestClient(app)
    reset_stop_controller()


def test_the_new_routes_are_in_no_auth_exception_list():
    # S08: every request to them goes through Host / Origin / credential checks.
    for path, method in (("/api/stop", "POST"), ("/api/stop/resume", "POST"),
                         ("/api/control-strip", "GET")):
        assert _http_auth_exception(path, method) is False


def test_stop_closes_the_door_and_reports_each_part_with_its_own_outcome(client):
    got = client.post("/api/stop").json()
    assert got["door_open"] is False
    results = {r["name"]: r for r in got["stop"]["results"]}
    # No verified stop for playback or realtime: unknown, never confirmed.
    assert results["voice_playback"]["outcome"] == "unknown"
    assert results["realtime_session"]["outcome"] == "unknown"
    assert got["stop"]["state"] == "partly_stopped"
    assert got["state"] == "partly_stopped"
    assert set(got["not_confirmed"]) >= {"voice_playback", "realtime_session"}


def test_a_repeated_stop_keeps_the_generation(client):
    first = client.post("/api/stop").json()
    second = client.post("/api/stop").json()
    assert second["generation"] == first["generation"]
    assert second["stop"]["repeated"] is True


def test_resume_is_the_only_way_to_reopen_and_opens_a_new_generation(client):
    stopped = client.post("/api/stop").json()
    resumed = client.post("/api/stop/resume").json()
    assert resumed["door_open"] is True and resumed["generation"] > stopped["generation"]
    assert resumed["stop"] is None
    assert get_stop_controller().admit(stopped["generation"]).reason == "stale_generation"


def test_the_strip_before_any_stop_has_no_stop_report_and_an_open_door(client):
    got = client.get("/api/control-strip").json()
    assert got["door_open"] is True and got["stop"] is None
    assert got["engine"]["inference"] == "none" and got["engine"]["network_tools"] == "not_observed"
    assert set(got["indicators"]) == {"audio", "jobs", "approvals"}


def test_missions_refuse_new_work_and_approvals_while_stopped(client):
    client.post("/api/stop")
    with pytest.raises(HTTPException) as caught:
        missions_routes._require_door()
    assert caught.value.status_code == 409 and caught.value.detail["code"] == "stopped"
    client.post("/api/stop/resume")
    missions_routes._require_door()  # the door is open again: no exception


async def test_a_turn_is_refused_while_stopped_without_calling_an_engine():
    reset_stop_controller()
    brain = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    await get_stop_controller().stop()
    reply = await brain.generate("what time is it")
    assert reply in set(catalog.table("brain.stopped_waiting_resume").values())
    reset_stop_controller()
