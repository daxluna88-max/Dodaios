"""Control strip, block 13 round 3 (G5): one coherent epoch per snapshot (ADR-0058).

Written, NOT run (owner order: no test runs in this block).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dodaios.brain.manager import BrainManager
from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig
from dodaios.ui.web import control_strip_routes as routes


class _Store:
    """A mission store whose read lets the test move STOP / resume while the snapshot waits."""

    def __init__(self, during):
        self.during = during
        self.calls = 0

    async def list_non_terminal_missions(self):
        self.calls += 1
        await asyncio.sleep(0)
        self.during(self.calls)
        await asyncio.sleep(0)
        return []

    async def list_missions(self, limit=200):
        return []


class _Mgr:
    def __init__(self, store):
        self.store = store


def _client(during):
    reset_stop_controller()
    app = FastAPI()
    app.state.brain = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    app.state.mission_manager = _Mgr(_Store(during))
    app.include_router(routes.router)
    return TestClient(app), app


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_stop_controller()


def test_transition_counter_moves_on_stop_and_resume():
    reset_stop_controller()
    c = get_stop_controller()
    t0 = c.transitions
    asyncio.run(c.stop())
    t1 = c.transitions
    c.resume()
    assert t0 < t1 < c.transitions


def test_resume_during_the_awaits_retries_and_never_mixes_generations():
    controller_box = {}

    def during(call):
        c = controller_box["c"]
        if call == 1:  # first attempt only: the door moves while the snapshot waits
            c._generation += 1
            c._transitions += 1

    client, _app = _client(during)
    controller_box["c"] = get_stop_controller()
    body = client.get("/api/control-strip").json()
    assert body["state"] != "unknown"
    assert body["generation"] == controller_box["c"].generation  # the NEW epoch, wholly
    assert client.app.state.mission_manager.store.calls == 2  # one retry


def test_epoch_that_keeps_moving_answers_unknown_with_the_reason():
    controller_box = {}

    def during(_call):
        c = controller_box["c"]
        c._transitions += 1  # moves on every attempt

    client, _app = _client(during)
    controller_box["c"] = get_stop_controller()
    body = client.get("/api/control-strip?client_seq=4").json()
    assert body["state"] == "unknown"
    assert body["unknown_reason"] == "epoch_changed"
    assert body["client_seq"] == 4
    assert body["indicators"]["jobs"] is None and body["approvals"] == []
    assert client.app.state.mission_manager.store.calls == routes._SNAPSHOT_ATTEMPTS


def test_write_routes_echo_client_seq():
    client, _app = _client(lambda _c: None)
    assert client.post("/api/stop?client_seq=11").json()["client_seq"] == 11
    assert client.post("/api/stop/resume?client_seq=12").json()["client_seq"] == 12
