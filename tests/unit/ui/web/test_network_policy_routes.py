"""Settings > General > Network (ADR-0053): writes the config file, reports enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dodaios.core import config as cfg_mod
from dodaios.core import config_writer, egress_guard
from dodaios.ui.web import network_policy_routes as routes


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text('[brain]\nprimary = "ollama"\n', encoding="utf-8")
    original = config_writer.set_network_policy
    monkeypatch.setattr(
        config_writer, "set_network_policy", lambda policy: original(policy, path=config_file)
    )
    monkeypatch.setattr(
        cfg_mod,
        "load_config",
        lambda *a, **k: cfg_mod.JarvisConfig.model_validate(_read(config_file)),
    )
    monkeypatch.delenv(egress_guard.POLICY_ENV, raising=False)
    egress_guard.uninstall()
    app = FastAPI()
    app.include_router(routes.router)
    yield TestClient(app), config_file
    egress_guard.uninstall()


def _read(path: Path) -> dict:
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_default_is_off_and_nothing_is_enforced(client):
    web, _ = client
    body = web.get("/api/network-policy").json()
    assert body == {
        "configured": "off",
        "env_override": None,
        "active": False,
        "source": "none",
        "blocked": 0,
        "recent": [],
        "restart_required": False,
    }


def test_choosing_local_only_writes_the_file_and_asks_for_a_restart(client):
    web, config_file = client
    body = web.put("/api/network-policy", json={"policy": "local-only"}).json()
    assert body["configured"] == "local-only" and body["active"] is False
    assert body["restart_required"] is True  # the guard is installed at process start
    assert 'policy = "local-only"' in config_file.read_text(encoding="utf-8")
    back = web.put("/api/network-policy", json={"policy": "off"}).json()
    assert back["configured"] == "off" and back["restart_required"] is False


def test_the_running_guard_is_reported_and_no_restart_is_needed_once_it_matches(client):
    web, config_file = client
    web.put("/api/network-policy", json={"policy": "local-only"})
    assert egress_guard.install_from_settings(config_file, {}) == "config"
    body = web.get("/api/network-policy").json()
    assert (body["active"], body["source"], body["restart_required"]) == (True, "config", False)


def test_an_environment_override_is_reported_as_the_reason(client, monkeypatch):
    web, _ = client
    monkeypatch.setenv(egress_guard.POLICY_ENV, "local-only")
    body = web.get("/api/network-policy").json()
    assert body["env_override"] == "local-only" and body["restart_required"] is True


@pytest.mark.parametrize("value", ["mostly", "", "LOCAL"])
def test_unknown_values_are_rejected_and_nothing_is_written(client, value):
    web, config_file = client
    assert web.put("/api/network-policy", json={"policy": value}).status_code == 400
    assert "network" not in config_file.read_text(encoding="utf-8")
