"""Control strip, block 10: ordering fields, real limits, approval identity (ADR-0058).

Written, NOT run (owner order: no test runs in this block).
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dodaios.brain.manager import BrainManager
from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig
from dodaios.core.events import ActionApprovalRequired
from dodaios.missions.tool_approvals import MissionToolApprovalCoordinator
from dodaios.ui.web import control_strip_routes as routes
from dodaios.ui.web import missions_routes


@pytest.fixture
def client():
    reset_stop_controller()
    app = FastAPI()
    app.state.brain = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    app.include_router(routes.router)
    yield TestClient(app)
    reset_stop_controller()


def test_every_payload_carries_the_fields_that_order_responses(client):
    first = client.get("/api/control-strip?client_seq=7").json()
    second = client.get("/api/control-strip").json()
    assert first["boot_id"] == second["boot_id"]
    assert second["seq"] > first["seq"]
    assert first["client_seq"] == 7 and second["client_seq"] is None
    assert isinstance(first["server_time"], int)
    assert first["server_time"] <= int(time.time() * 1000) + 1000
    assert first["etag"] and first["etag"] != second["etag"]
    assert first["generation"] == get_stop_controller().generation


def test_a_stop_reply_is_newer_than_a_read_taken_before_it(client):
    before = client.get("/api/control-strip").json()
    stopped = client.post("/api/stop").json()
    assert stopped["seq"] > before["seq"] and stopped["generation"] >= before["generation"]
    resumed = client.post("/api/stop/resume").json()
    assert resumed["seq"] > stopped["seq"] and resumed["generation"] > stopped["generation"]


def test_limits_come_from_the_guard_and_the_stop_report_not_from_assumptions(client, monkeypatch):
    from dodaios.core import egress_guard

    monkeypatch.setattr(egress_guard, "status", lambda: {
        "python_guard_enforced": True, "os_enforcement_verified": False})
    got = client.get("/api/control-strip").json()["limits"]
    assert got["python_guard_enforced"] is True
    assert got["os_enforcement_verified"] is False
    assert got["network_tools"] == "not_observed" and got["tools_used_observed"] is False
    # No STOP yet: nothing was observed about voice, so it is unknown, never "stopped".
    assert got["voice_stop"] == {"voice_playback": "unknown", "realtime_session": "unknown"}


def test_an_unreadable_guard_is_unknown_not_false(client, monkeypatch):
    from dodaios.core import egress_guard

    def boom() -> dict:
        raise RuntimeError("no status")

    monkeypatch.setattr(egress_guard, "status", boom)
    got = client.get("/api/control-strip").json()["limits"]
    assert got["python_guard_enforced"] is None and got["os_enforcement_verified"] is None


def test_memory_block_states_the_limits_and_does_not_claim_a_count_it_cannot_read(client):
    mem = client.get("/api/control-strip").json()["memory"]
    assert mem["archive_is_deletion"] is False
    assert mem["per_note_approval"] is False
    assert mem["backups_and_indexes_covered"] is False
    assert mem["count"] is None  # no vault configured in this app: unknown, not 0


async def _pending(coordinator: MissionToolApprovalCoordinator, bus: EventBus, fingerprint: str):
    trace = uuid.uuid4()
    await bus.publish(ActionApprovalRequired(
        trace_id=trace, tool_name="write_file", mission_id="m-1",
        expires_at_ns=time.time_ns() + 60_000_000_000, call_fingerprint=fingerprint))
    return trace


async def test_a_decision_for_another_call_than_the_one_waiting_decides_nothing():
    reset_stop_controller()
    bus = EventBus()
    coordinator = MissionToolApprovalCoordinator(bus)
    trace = await _pending(coordinator, bus, "fp-A")
    wrong = await coordinator.approve(
        "m-1", trace, approved_by="user", expected_fingerprint="fp-B")
    assert wrong is None
    assert await coordinator.deny("m-1", trace, reason="x", expected_fingerprint="fp-B") is None
    # Still waiting: the wrong identity did not consume it.
    assert await coordinator.pending_count() == 1
    done = await coordinator.approve("m-1", trace, approved_by="user", expected_fingerprint="fp-A")
    assert done is not None and await coordinator.pending_count() == 0


async def test_without_an_expected_fingerprint_behaviour_is_unchanged():
    bus = EventBus()
    coordinator = MissionToolApprovalCoordinator(bus)
    trace = await _pending(coordinator, bus, "fp-A")
    assert await coordinator.deny("m-1", trace, reason="x") is not None


def test_a_decision_made_on_a_screen_that_missed_a_stop_is_refused():
    reset_stop_controller()
    from fastapi import HTTPException

    known = get_stop_controller().generation
    missions_routes._require_known_generation(known)  # same generation: fine
    missions_routes._require_known_generation(None)  # old clients: unchanged
    with pytest.raises(HTTPException) as caught:
        missions_routes._require_known_generation(known + 1)
    assert caught.value.status_code == 409 and caught.value.detail["code"] == "stale_generation"
    reset_stop_controller()
