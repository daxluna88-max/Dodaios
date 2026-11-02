"""GET/PUT /api/settings/personal-context — the privacy switch for the model prompt."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dodaios.core import config as cfg_mod
from dodaios.core.bus import EventBus
from dodaios.ui.web.settings_routes import router as settings_router


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text('[brain]\nprimary = "openrouter"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(config_file))
    app = FastAPI()
    app.state.bus = EventBus()
    app.state.config = cfg_mod.load_config()
    app.include_router(settings_router)
    return TestClient(app), config_file, app


def test_default_allows_personal_context(ctx) -> None:
    tc, _, _ = ctx
    assert tc.get("/api/settings/personal-context").json() == {"allowed": True}


def test_turning_it_off_persists_and_applies_live(ctx) -> None:
    tc, config_file, app = ctx
    body = tc.put("/api/settings/personal-context", json={"allowed": False}).json()
    assert body == {"allowed": False, "persisted": True, "applied": True}
    assert "personal_context_to_cloud = false" in config_file.read_text(encoding="utf-8")
    assert app.state.config.brain.personal_context_to_cloud is False
    assert tc.get("/api/settings/personal-context").json() == {"allowed": False}
    assert cfg_mod.load_config().brain.personal_context_to_cloud is False


def test_turning_it_back_on(ctx) -> None:
    tc, config_file, app = ctx
    tc.put("/api/settings/personal-context", json={"allowed": False})
    tc.put("/api/settings/personal-context", json={"allowed": True})
    assert app.state.config.brain.personal_context_to_cloud is True
    assert cfg_mod.load_config().brain.personal_context_to_cloud is True


def test_a_non_boolean_is_rejected(ctx) -> None:
    tc, _, _ = ctx
    assert tc.put("/api/settings/personal-context", json={"allowed": "maybe"}).status_code == 422
