"""The phone panel (``/m/``): static files reachable without a credential, API still locked."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig
from dodaios.ui.web.server import MOBILE_DIR, WebServer

pytestmark = pytest.mark.no_auto_web_auth

KEY = "phone-panel-test-key-123"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_CONTROL_API_KEY", KEY)
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True  # the panel is mounted in dev mode too
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    with TestClient(srv.app, base_url="http://localhost") as web:
        yield web


def test_panel_files_are_served(client: TestClient) -> None:
    index = client.get("/m/")
    assert index.status_code == 200 and "Jarvis" in index.text
    assert '/m/app.js' in index.text and 'rel="manifest"' in index.text
    manifest = client.get("/m/manifest.webmanifest").json()
    assert manifest["start_url"] == "/m/" and manifest["display"] == "standalone"
    assert client.get("/m/sw.js").status_code == 200
    assert client.get("/m/icon-256.png").headers["content-type"] == "image/png"


def test_the_panel_ships_no_secret_and_the_api_stays_locked(client: TestClient) -> None:
    for name in ("index.html", "app.js", "sw.js", "manifest.webmanifest"):
        text = (MOBILE_DIR / name).read_text(encoding="utf-8")
        assert "jctl_" not in text.replace('placeholder = "jctl_…"', "")
    # A remote caller (non-loopback peer) without the key is refused on the API.
    remote = TestClient(client.app, base_url="http://100.64.0.1", client=("100.64.0.2", 5000))
    assert remote.get("/m/").status_code == 200
    assert remote.get("/api/agent-chat/sessions").status_code in {401, 403}
    wrong = {"Authorization": "Bearer wrong-key-000000"}
    assert remote.get("/api/agent-chat/sessions", headers=wrong).status_code in {401, 403}
    ok = remote.get("/api/control/auth/probe", headers={"Authorization": f"Bearer {KEY}"})
    assert ok.status_code == 200


def test_the_service_worker_never_caches_the_api() -> None:
    sw = (MOBILE_DIR / "sw.js").read_text(encoding="utf-8")
    assert 'pathname.startsWith("/m/")' in sw and 'startsWith("/api' not in sw
