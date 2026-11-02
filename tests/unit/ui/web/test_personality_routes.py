"""Personality settings API (issue 17): save, reset, preview, owner mood requests.

Written for the PC checklist (P01, P02, P04, P10, P11); not run when it was written.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dodaios.brain.manager import BrainManager
from dodaios.core import config as cfg_mod
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig
from dodaios.ui.web import personality_routes as routes


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text('[brain]\nprimary = "ollama"\n', encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "resolve_config_path", lambda: config_file)
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    app = FastAPI()
    app.state.brain = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    app.include_router(routes.router)
    return TestClient(app), config_file, app


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_defaults_say_the_mood_is_simulated_and_neutral(env):
    client, _, _ = env
    got = client.get("/api/personality").json()
    assert got["simulated"] is True and got["status"] == "ok"
    assert got["mood"]["mood"] == "neutral" and got["mood"]["adaptation_on"] is True
    assert got["profile"]["mode"] == "mixed" and got["effective"]["preset"] == "friendly"
    assert set(got["presets"]) == {"professional", "friendly", "concise"}


def test_work_product_defaults_to_professional(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    got = client.get("/api/personality").json()
    assert got["effective"]["preset"] == "professional" and got["product"] == "work"


def test_save_writes_the_file_and_applies_at_once(env):
    client, config_file, app = env
    saved = client.put("/api/personality", json={"mode": "manual", "preset": "concise", "humor": 0})
    assert saved.status_code == 200 and saved.json()["profile"]["mode"] == "manual"
    block = _toml(config_file)["brain"]["personality"]
    assert block["mode"] == "manual" and _toml(config_file)["brain"]["primary"] == "ollama"
    assert app.state.brain._config.brain.personality.mode == "manual"


def test_a_bad_profile_is_refused_and_nothing_is_written(env):
    client, config_file, _ = env
    before = config_file.read_text(encoding="utf-8")
    assert client.put("/api/personality", json={"humor": 9}).status_code == 422
    assert client.put("/api/personality", json={"tone": "furious"}).status_code == 422
    assert config_file.read_text(encoding="utf-8") == before


def test_reset_removes_the_block_and_restores_the_defaults(env):
    client, config_file, _ = env
    client.put("/api/personality", json={"mode": "manual", "humor": 0})
    got = client.delete("/api/personality").json()
    assert got["profile"]["mode"] == "mixed" and "personality" not in _toml(config_file)["brain"]


def test_a_damaged_block_on_disk_is_reported_not_fatal(env):
    client, config_file, _ = env
    config_file.write_text('[brain.personality]\nhumor = 99\n', encoding="utf-8")
    got = client.get("/api/personality").json()
    assert got["status"] == "defaults" and "humor" in got["reason"]


def test_preview_returns_text_and_saves_nothing(env):
    client, config_file, _ = env
    before = config_file.read_text(encoding="utf-8")
    out = client.post("/api/personality/preview", json={"preset": "professional"}).json()
    assert "Speaking style" in out["instruction"] and "formal" in out["instruction"]
    assert config_file.read_text(encoding="utf-8") == before


def test_owner_mood_requests_change_this_session_and_never_the_file(env):
    client, config_file, _ = env
    before = config_file.read_text(encoding="utf-8")
    got = client.post("/api/personality/mood", json={"action": "set", "mood": "serious"}).json()
    assert got["mood"]["mood"] == "serious" and got["mood"]["origin"] == "owner"
    assert got["mood"]["reason"] == "owner_request"
    held = client.post("/api/personality/mood", json={"action": "hold"}).json()
    assert held["mood"]["expires_at"] is None
    back = client.post("/api/personality/mood", json={"action": "neutral"}).json()
    assert back["mood"]["mood"] == "neutral"
    off = client.post("/api/personality/mood", json={"action": "adaptation", "enabled": False})
    assert off.json()["mood"]["adaptation_on"] is False
    assert config_file.read_text(encoding="utf-8") == before


def test_refused_mood_requests_say_why(env):
    client, _, _ = env
    assert client.post("/api/personality/mood", json={"action": "set"}).status_code == 400
    assert client.post("/api/personality/mood",
                       json={"action": "set", "mood": "angry"}).status_code == 409
    assert client.post("/api/personality/mood", json={"action": "hold"}).status_code == 409


def test_before_the_assistant_is_ready_the_mood_endpoint_waits(env):
    client, _, app = env
    app.state.brain = None
    assert client.get("/api/personality").json()["mood_available"] is False
    assert client.post("/api/personality/mood", json={"action": "neutral"}).status_code == 503
