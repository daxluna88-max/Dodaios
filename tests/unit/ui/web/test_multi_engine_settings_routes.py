"""The chat's "ask several engines" settings (ADR-0055): writes the config file, reads it back."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dodaios.core import config as cfg_mod
from dodaios.core import config_writer
from dodaios.ui.web import consumer_fanout_routes as routes


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text('[brain]\nprimary = "ollama"\n', encoding="utf-8")
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    original = config_writer.set_multi_engine
    monkeypatch.setattr(
        config_writer, "set_multi_engine", lambda data: original(data, path=config_file)
    )
    monkeypatch.setattr(
        cfg_mod, "load_config",
        lambda *a, **k: cfg_mod.JarvisConfig.model_validate(
            tomllib.loads(config_file.read_text(encoding="utf-8"))),
    )
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), config_file


BODY = {
    "enabled": True,
    "engines": [{"provider": "claude-api", "est_cost": 0.5},
                {"provider": "ollama", "est_cost": 0.0, "timeout_s": 90}],
    "synthesizer": {"provider": "claude-api", "est_cost": 0.2},
    "budget": 2.0,
    "allow_unknown_cost": False,
    "max_concurrent": 2,
}


def test_defaults_are_off_and_empty(env):
    client, _ = env
    got = client.get("/api/consumer/multi-engine").json()
    assert got["available"] is True and got["enabled"] is False
    assert got["engines"] == [] and got["budget"] == 0.0


def test_save_round_trips_through_the_config_file(env):
    client, config_file = env
    saved = client.put("/api/consumer/multi-engine", json=BODY)
    assert saved.status_code == 200
    again = client.get("/api/consumer/multi-engine").json()
    assert again["enabled"] is True and again["budget"] == 2.0
    assert [e["provider"] for e in again["engines"]] == ["claude-api", "ollama"]
    assert again["synthesizer"]["provider"] == "claude-api"
    assert again["engines"][1]["timeout_s"] == 90
    # the rest of the file survives, and the block really is on disk
    on_disk = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert on_disk["brain"]["primary"] == "ollama"
    assert on_disk["brain"]["multi_engine"]["max_concurrent"] == 2


def test_saving_again_replaces_the_list_not_appends(env):
    client, _ = env
    client.put("/api/consumer/multi-engine", json=BODY)
    client.put("/api/consumer/multi-engine", json={**BODY, "engines": [BODY["engines"][0]],
                                                    "synthesizer": None})
    got = client.get("/api/consumer/multi-engine").json()
    assert len(got["engines"]) == 1 and "synthesizer" not in got


@pytest.mark.parametrize("patch", [
    {"budget": -1}, {"max_concurrent": 0}, {"max_concurrent": 9},
    {"engines": [{"provider": "x", "timeout_s": 0}]},
    {"engines": [{"provider": "x"}] * 7},
])
def test_bad_values_are_refused_and_nothing_is_written(env, patch):
    client, config_file = env
    before = config_file.read_text(encoding="utf-8")
    assert client.put("/api/consumer/multi-engine", json={**BODY, **patch}).status_code == 422
    assert config_file.read_text(encoding="utf-8") == before


def test_work_product_reads_unavailable_and_refuses_to_save(env, monkeypatch):
    client, config_file = env
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    got = client.get("/api/consumer/multi-engine").json()
    assert got == {"available": False, "reason": "work_local_only"}
    before = config_file.read_text(encoding="utf-8")
    assert client.put("/api/consumer/multi-engine", json=BODY).status_code == 403
    assert config_file.read_text(encoding="utf-8") == before
