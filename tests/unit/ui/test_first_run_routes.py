"""Guided first run API (issue 18): progress, apply from the UI only, honest summary.

Written for the PC checklist (O01, O06, O07, O09); not run when it was written.
"""

from __future__ import annotations

import tomllib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dodaios.core import config as cfg_mod
from dodaios.core import config_writer
from dodaios.ui.web import onboarding_routes


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "jarvis.toml"
    cfg_file.write_text('[brain]\nprimary = "ollama"\n', encoding="utf-8")
    monkeypatch.setattr(onboarding_routes, "_STATE_PATH_OVERRIDE", tmp_path / "s.json")
    monkeypatch.setattr(cfg_mod, "resolve_config_path", lambda: cfg_file)
    monkeypatch.setattr(cfg_mod, "load_config", lambda *a, **k: cfg_mod.JarvisConfig.model_validate(
        tomllib.loads(cfg_file.read_text(encoding="utf-8"))))
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    monkeypatch.delenv("JARVIS_FORCE_ONBOARDING", raising=False)
    app = FastAPI()
    app.include_router(onboarding_routes.router)
    return TestClient(app, headers={"Origin": "http://testserver"}), cfg_file, app


def test_first_run_starts_with_safe_defaults_and_the_products_presets(env):
    client, _, _ = env
    got = client.get("/api/onboarding/first-run").json()
    assert got["product"] == "consumer" and got["applied"] is False
    assert got["choices"]["interaction"] == "text" and got["choices"]["memory"] == "off"
    assert got["presets"] == ["friendly", "ironic", "essential"]
    assert got["default_preset"] == "friendly"


def test_work_gets_its_own_flow(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    got = client.get("/api/onboarding/first-run").json()
    assert got["product"] == "work" and got["default_preset"] == "professional"
    assert got["presets"] == ["professional", "collaborative", "essential"]


def test_saving_progress_writes_no_configuration(env):
    client, cfg_file, _ = env
    before = cfg_file.read_text(encoding="utf-8")
    body = {"owner_name": "Dax", "interaction": "voice"}
    got = client.put("/api/onboarding/first-run", json=body)
    assert got.status_code == 200 and got.json()["choices"]["owner_name"] == "Dax"
    assert cfg_file.read_text(encoding="utf-8") == before
    assert client.get("/api/onboarding/first-run").json()["choices"]["interaction"] == "voice"


def test_invalid_choices_are_refused(env):
    client, _, _ = env
    assert client.put("/api/onboarding/first-run", json={"interaction": "video"}).status_code == 422
    assert client.put("/api/onboarding/first-run", json={"owner_name": "x" * 99}).status_code == 422


def test_apply_writes_the_configuration_and_the_summary_reads_it_back(env):
    client, cfg_file, _ = env
    client.put("/api/onboarding/first-run", json={"personality_preset": "essential"})
    got = client.post("/api/onboarding/first-run/apply")
    assert got.status_code == 200 and got.json()["applied"] is True
    data = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    assert data["safety"]["approval_mode"] == "ask"
    assert data["memory"]["learning"]["enabled"] is False
    summary = got.json()["summary"]
    assert summary["actions"]["approval_mode"] == "ask"
    assert summary["memory"]["learning_enabled"] is False
    assert summary["personality"]["preset"] == "concise"


def test_apply_needs_a_click_in_the_ui_not_a_bearer_or_a_bare_request(env):
    client, cfg_file, app = env
    before = cfg_file.read_text(encoding="utf-8")
    bare = TestClient(app)  # no Origin
    assert bare.post("/api/onboarding/first-run/apply").status_code == 403
    bearer = client.post("/api/onboarding/first-run/apply", headers={"Authorization": "Bearer x"})
    assert bearer.status_code == 403
    assert cfg_file.read_text(encoding="utf-8") == before


def test_a_failed_write_is_an_error_and_the_run_stays_unapplied(env, monkeypatch):
    client, _, _ = env

    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(config_writer, "set_safety_approval_mode", boom)
    got = client.post("/api/onboarding/first-run/apply")
    assert got.status_code == 500 and got.json()["detail"]["step"] == "approval"
    assert client.get("/api/onboarding/first-run").json()["applied"] is False


def test_a_personality_of_the_other_product_is_refused(env, monkeypatch):
    client, cfg_file, _ = env
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    client.put("/api/onboarding/first-run", json={"personality_preset": "ironic"})
    before = cfg_file.read_text(encoding="utf-8")
    assert client.post("/api/onboarding/first-run/apply").status_code == 400
    assert cfg_file.read_text(encoding="utf-8") == before


def test_reset_forgets_the_choices_and_asks_for_a_click(env):
    client, _, app = env
    client.put("/api/onboarding/first-run", json={"owner_name": "Dax"})
    assert TestClient(app).post("/api/onboarding/first-run/reset").status_code == 403
    got = client.post("/api/onboarding/first-run/reset").json()
    assert got["choices"]["owner_name"] is None and got["applied"] is False


def test_completing_onboarding_is_not_done_by_apply(env):
    client, _, _ = env
    client.post("/api/onboarding/first-run/apply")
    assert client.get("/api/onboarding/state").json()["completed"] is False


def test_an_installed_app_is_never_changed_by_replaying_the_guide(env):
    from dodaios.setup import state as setup_state

    client, cfg_file, _ = env
    setup_state.mark_onboarding_complete(onboarding_routes._STATE_PATH_OVERRIDE)
    before = cfg_file.read_text(encoding="utf-8")
    assert client.post("/api/onboarding/first-run/apply").status_code == 409
    assert cfg_file.read_text(encoding="utf-8") == before
