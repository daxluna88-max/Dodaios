"""The phone companion's HTTP boundary: pairing, CSRF, revocation, transfers, chat reuse."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dodaios.agent_chat.service import AgentChatService
from dodaios.agent_chat.store import AgentChatStore
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig
from dodaios.ui.web import phone_routes
from dodaios.ui.web.server import WebServer

pytestmark = pytest.mark.no_auto_web_auth

# The Control Key is cached for the life of the process, so every phone test module shares one.
KEY = "phone-surface-test-key-123"
ORIGIN = "http://localhost"
OWNER_HEADERS = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_CONTROL_API_KEY", KEY)
    cfg = JarvisConfig()
    cfg.memory.data_dir = str(tmp_path / "data")
    cfg.ui.dev_mode = True
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    srv.app.state.agent_chat = AgentChatService(AgentChatStore(":memory:"))
    return srv.app


def browser(app, **kwargs: Any) -> TestClient:
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}, **kwargs)


@pytest.fixture
def owner(app):
    with browser(app) as client:
        client.headers.update(OWNER_HEADERS)
        yield client


def pair(app, owner: TestClient, name: str = "iPhone") -> tuple[TestClient, str]:
    code = owner.post("/api/phone-admin/pairing").json()["code"]
    phone = browser(app)
    phone.__enter__()
    res = phone.post("/api/phone/pair/redeem", json={"code": code, "name": name})
    assert res.status_code == 200, res.text
    csrf = res.json()["csrf"]
    phone.headers["X-Phone-CSRF"] = csrf
    return phone, csrf


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def upload(phone: TestClient, name: str, data: bytes, chunk: int = 4) -> dict[str, Any]:
    made = phone.post("/api/phone/files", json={"name": name, "size": len(data),
                                                "sha256": sha(data)})
    assert made.status_code == 201, made.text
    fid = made.json()["id"]
    for offset in range(0, len(data), chunk):
        res = phone.put(f"/api/phone/files/{fid}/chunk", params={"offset": offset},
                        content=data[offset:offset + chunk])
        assert res.status_code == 200, res.text
    done = phone.post(f"/api/phone/files/{fid}/finish")
    assert done.status_code == 200, done.text
    return done.json()


# ------------------------------------------------------------- boundary
def test_anonymous_and_unpaired_callers_get_nothing(app) -> None:
    with browser(app) as anon:
        for method, path in (("GET", "/api/phone/me"), ("GET", "/api/phone/files"),
                             ("GET", "/api/phone/chats"), ("POST", "/api/phone/files")):
            assert anon.request(method, path).status_code == 401
        assert anon.post("/api/phone-admin/pairing").status_code in {401, 403}
        assert anon.get("/api/phone-admin/devices").status_code in {401, 403}
        bogus = browser(app, cookies={"jphone": "x" * 40})
        assert bogus.get("/api/phone/me").status_code == 401


def test_pairing_sets_a_locked_down_cookie_and_works_once(app, owner) -> None:
    code = owner.post("/api/phone-admin/pairing").json()["code"]
    with browser(app) as phone:
        res = phone.post("/api/phone/pair/redeem", json={"code": code, "name": "iPhone"})
        cookie = res.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api/phone" in cookie
        assert phone.get("/api/phone/me").json()["device"]["name"] == "iPhone"
        again = browser(app).post("/api/phone/pair/redeem", json={"code": code})
        assert again.status_code == 400
    # Behind an HTTPS proxy the cookie is also Secure.
    code = owner.post("/api/phone-admin/pairing").json()["code"]
    secure = browser(app).post("/api/phone/pair/redeem", json={"code": code},
                               headers={"X-Forwarded-Proto": "https"})
    assert "secure" in secure.headers["set-cookie"].lower()


def test_redeem_needs_a_same_origin_browser_request(app, owner) -> None:
    code = owner.post("/api/phone-admin/pairing").json()["code"]
    foreign = TestClient(app, base_url=ORIGIN)
    assert foreign.post("/api/phone/pair/redeem", json={"code": code}).status_code == 403
    cross = TestClient(app, base_url=ORIGIN, headers={"Origin": "http://evil.example"})
    assert cross.post("/api/phone/pair/redeem", json={"code": code}).status_code in {400, 403}


def test_unsafe_requests_need_the_csrf_token_and_a_matching_origin(app, owner) -> None:
    phone, _csrf = pair(app, owner)
    body = {"name": "a.txt", "size": 1, "sha256": sha(b"a")}
    del phone.headers["X-Phone-CSRF"]
    assert phone.post("/api/phone/files", json=body).status_code == 403
    phone.headers["X-Phone-CSRF"] = "wrong"
    assert phone.post("/api/phone/files", json=body).status_code == 403
    phone.headers["X-Phone-CSRF"] = _csrf
    phone.headers["Origin"] = "http://localhost:9999"
    assert phone.post("/api/phone/files", json=body).status_code in {403}
    phone.headers["Origin"] = ORIGIN
    assert phone.post("/api/phone/files", json=body).status_code == 201


def test_the_phone_cookie_does_not_open_the_owner_surface(app, owner) -> None:
    phone, _ = pair(app, owner)
    assert phone.post("/api/phone-admin/pairing").status_code in {401, 403}
    assert phone.get("/api/phone-admin/devices").status_code in {401, 403}
    assert phone.get("/api/agent-chat/sessions").status_code in {401, 403}
    assert phone.get("/api/control/config?path=ui.theme").status_code in {401, 403}


# ----------------------------------------------------------- revocation
def test_revoking_a_device_ends_everything_at_once(app, owner) -> None:
    phone, _ = pair(app, owner)
    sent = upload(phone, "foto.jpg", b"0123456789")
    half = phone.post("/api/phone/files",
                      json={"name": "v.mp4", "size": 8, "sha256": sha(b"x" * 8)})
    device_id = phone.get("/api/phone/me").json()["device"]["id"]
    assert owner.delete(f"/api/phone-admin/devices/{device_id}").json()[
        "unfinished_uploads_removed"] == 1
    assert phone.get("/api/phone/me").status_code == 401
    assert phone.get("/api/phone/files").status_code == 401
    assert phone.get(f"/api/phone/files/{sent['id']}/download").status_code == 401
    assert half.status_code == 201
    assert owner.delete(f"/api/phone-admin/devices/{device_id}").status_code == 404
    # The finished file stays with the owner and a newly paired phone can fetch it.
    other, _ = pair(app, owner, "iPad")
    assert other.get(f"/api/phone/files/{sent['id']}/download").content == b"0123456789"


def test_a_device_can_sign_itself_out(app, owner) -> None:
    phone, _ = pair(app, owner)
    assert phone.post("/api/phone/logout").json() == {"ok": True}
    assert phone.get("/api/phone/me").status_code == 401
    assert owner.get("/api/phone-admin/devices").json()["devices"][0]["revoked"] is True


# --------------------------------------------------------------- files
def test_upload_resume_download_and_list(app, owner) -> None:
    phone, _ = pair(app, owner)
    data = bytes(range(256)) * 3
    fid = phone.post("/api/phone/files", json={"name": "doc.pdf", "size": len(data),
                                               "sha256": sha(data)}).json()["id"]
    assert phone.put(f"/api/phone/files/{fid}/chunk", params={"offset": 0},
                     content=data[:300]).json() == {"offset": 300}
    # A lost response: the client asks where it is, and a stale retry is a conflict.
    assert phone.get(f"/api/phone/files/{fid}").json()["offset"] == 300
    assert phone.put(f"/api/phone/files/{fid}/chunk", params={"offset": 0},
                     content=data[:300]).status_code == 409
    assert phone.get(f"/api/phone/files/{fid}/download").status_code == 400  # not ready yet
    phone.put(f"/api/phone/files/{fid}/chunk", params={"offset": 300}, content=data[300:])
    assert phone.post(f"/api/phone/files/{fid}/finish").json()["ready"] is True
    res = phone.get(f"/api/phone/files/{fid}/download")
    assert res.content == data
    assert res.headers["content-disposition"].startswith("attachment")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["content-type"] == "application/octet-stream"
    ranged = phone.get(f"/api/phone/files/{fid}/download", headers={"Range": "bytes=10-19"})
    assert ranged.status_code == 206 and ranged.content == data[10:20]
    assert ranged.headers["content-range"] == f"bytes 10-19/{len(data)}"
    assert phone.get(f"/api/phone/files/{fid}/download",
                     headers={"Range": "bytes=9999-"}).status_code == 416
    listing = phone.get("/api/phone/files").json()
    assert [f["name"] for f in listing["files"]] == ["doc.pdf"]


def test_bad_uploads_are_refused(app, owner) -> None:
    phone, _ = pair(app, owner)
    data = b"abcdefgh"
    for bad in ({"name": "../x", "size": 1, "sha256": sha(b"a")},
                {"name": "x", "size": -1, "sha256": sha(b"a")},
                {"name": "x", "size": 1, "sha256": "zz"},
                {"name": "x", "size": 10**13, "sha256": sha(b"a")}):
        assert phone.post("/api/phone/files", json=bad).status_code in {400, 422}
    fid = phone.post("/api/phone/files", json={"name": "x", "size": 8, "sha256": sha(data)}
                     ).json()["id"]
    hub = app.state.phone_hub
    big = b"z" * (hub.store.chunk_size + 1)
    assert phone.put(f"/api/phone/files/{fid}/chunk", params={"offset": 0},
                     content=big).status_code == 413
    phone.put(f"/api/phone/files/{fid}/chunk", params={"offset": 0}, content=b"WRONGBYT")
    assert phone.post(f"/api/phone/files/{fid}/finish").status_code == 400  # checksum
    assert phone.get("/api/phone/files/" + "0" * 32).status_code == 404
    assert phone.get("/api/phone/files/not-an-id").status_code in {400, 404}
    assert phone.get(f"/api/phone/files/{fid}/download").status_code == 400


def test_inline_preview_is_allowlisted_and_sandboxed(app, owner) -> None:
    phone, _ = pair(app, owner)
    png = upload(phone, "pic.png", b"\x89PNG....")
    html = upload(phone, "page.html", b"<script>alert(1)</script>")
    shown = phone.get(f"/api/phone/files/{png['id']}/download", params={"inline": "1"})
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["content-disposition"].startswith("inline")
    assert "sandbox" in shown.headers["content-security-policy"]
    risky = phone.get(f"/api/phone/files/{html['id']}/download", params={"inline": "1"})
    assert risky.headers["content-disposition"].startswith("attachment")
    assert risky.headers["content-type"] == "application/octet-stream"


def test_the_owner_can_publish_a_file_to_the_phone(app, owner, tmp_path: Path) -> None:
    phone, _ = pair(app, owner)
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.7 hello")
    made = owner.post("/api/phone-admin/outbox", json={"path": str(report)})
    assert made.status_code == 201 and made.json()["direction"] == "out"
    names = {f["name"]: f for f in phone.get("/api/phone/files").json()["files"]}
    assert phone.get(f"/api/phone/files/{names['report.pdf']['id']}/download").content == (
        b"%PDF-1.7 hello")
    assert owner.post("/api/phone-admin/outbox", json={"path": str(tmp_path)}).status_code == 400


def test_a_download_stops_when_the_device_is_revoked_mid_stream(app, owner) -> None:
    phone, _ = pair(app, owner)
    data = b"q" * 100
    meta = upload(phone, "big.bin", data)
    hub = app.state.phone_hub
    device_id = phone.get("/api/phone/me").json()["device"]["id"]
    _, path = hub.store.open_ready("owner", meta["id"])
    stream = phone_routes._stream(hub, path, device_id, 0, 99)  # noqa: SLF001
    first = next(stream)
    assert first
    hub.registry.revoke(device_id)
    assert list(stream) == []


# ---------------------------------------------------------------- chat
def test_chat_calls_reuse_the_desktop_routes(app, owner, monkeypatch: pytest.MonkeyPatch) -> None:
    phone, _ = pair(app, owner)
    svc = app.state.agent_chat
    first = svc.store.create_session(provider="claude-code", title="Prova", surface="agent",
                                     model="", effort="", cwd="")
    sid = first.session_id if hasattr(first, "session_id") else first["session_id"]
    assert [s["session_id"] for s in phone.get("/api/phone/chats").json()["sessions"]] == [sid]
    assert phone.get(f"/api/phone/chats/{sid}").json()["session"]["title"] == "Prova"
    assert phone.post(f"/api/phone/chats/{sid}/approvals/nope",
                      json={"decision": "allow"}).status_code in {404, 410}
    assert phone.post(f"/api/phone/chats/{sid}/approvals/nope",
                      json={"decision": "sudo"}).status_code == 400
    assert phone.post(f"/api/phone/chats/{sid}/cancel").status_code == 200
    assert phone.post(f"/api/phone/chats/{sid}/messages", json={"text": ""}).status_code == 400
    assert phone.get("/api/phone/chats/does-not-exist").status_code == 404


def test_a_message_carries_uploaded_files_through_the_chat_pipeline(
    app, owner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    phone, _ = pair(app, owner)
    svc = app.state.agent_chat
    session = svc.store.create_session(provider="claude-code", title="x", surface="agent",
                                       model="", effort="", cwd=str(tmp_path))
    sid = session.session_id if hasattr(session, "session_id") else session["session_id"]
    sent = upload(phone, "nota.txt", b"ciao")
    seen: dict[str, Any] = {}

    class Found:
        def to_dict(self) -> dict[str, Any]:
            return {"name": "nota.txt"}

    async def fake_ingest(folder, *, uploads=None, **_kw):
        seen["folder"], seen["name"] = folder, uploads[0][0]
        seen["bytes"] = uploads[0][1].read()
        return [Found()]

    async def fake_post(session_id, body, request):
        seen["body"] = body
        return {"turn_id": "t1", "session_id": session_id}

    monkeypatch.setattr(phone_routes.chat_attachments, "ingest", fake_ingest)
    monkeypatch.setattr("dodaios.ui.web.agent_chat_routes.post_message", fake_post)
    res = phone.post(f"/api/phone/chats/{sid}/messages",
                     json={"text": "leggi", "file_ids": [sent["id"]]})
    assert res.status_code == 202 and res.json()["turn_id"] == "t1"
    assert seen["bytes"] == b"ciao" and seen["name"] == "nota.txt"
    assert seen["body"].attachments == [{"name": "nota.txt"}] and seen["body"].text == "leggi"
    # A file that is not ready, not an upload or not there is refused, and nothing is sent.
    seen.clear()
    assert phone.post(f"/api/phone/chats/{sid}/messages",
                      json={"text": "x", "file_ids": ["0" * 32]}).status_code == 404
    assert "body" not in seen


# ------------------------------------------- revocation during long operations
def test_a_device_revoked_while_attachments_are_prepared_dispatches_nothing(
    app, owner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    phone, _ = pair(app, owner)
    svc = app.state.agent_chat
    session = svc.store.create_session(provider="claude-code", title="x", surface="agent",
                                       model="", effort="", cwd=str(tmp_path))
    sent = upload(phone, "nota.txt", b"ciao")
    device_id = phone.get("/api/phone/me").json()["device"]["id"]
    dispatched: list[object] = []

    class Found:
        def to_dict(self) -> dict[str, Any]:
            return {"name": "nota.txt"}

    async def revoking_ingest(folder, **_kw):
        # The owner revokes the phone while the attachment is being read and analysed.
        app.state.phone_hub.registry.revoke(device_id)
        return [Found()]

    async def fake_post(session_id, body, request):
        dispatched.append(body)
        return {"turn_id": "t"}

    monkeypatch.setattr(phone_routes.chat_attachments, "ingest", revoking_ingest)
    monkeypatch.setattr("dodaios.ui.web.agent_chat_routes.post_message", fake_post)
    res = phone.post(f"/api/phone/chats/{session.session_id}/messages",
                     json={"text": "leggi", "file_ids": [sent["id"]]})
    assert res.status_code == 401 and dispatched == []


def test_a_device_revoked_during_the_chunk_stream_or_finish_changes_nothing(
    app, owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    phone, _ = pair(app, owner)
    data = b"abcdefgh"
    fid = phone.post("/api/phone/files", json={"name": "v.bin", "size": 8,
                                               "sha256": sha(data)}).json()["id"]
    phone.put(f"/api/phone/files/{fid}/chunk", params={"offset": 0}, content=data[:4])
    registry = app.state.phone_hub.registry
    # After the request's entry check passes, membership is gone: nothing may be written.
    monkeypatch.setattr(registry, "is_active", lambda _id: False)
    refused = phone.put(f"/api/phone/files/{fid}/chunk", params={"offset": 4}, content=data[4:])
    assert refused.status_code == 401
    monkeypatch.undo()
    assert phone.get(f"/api/phone/files/{fid}").json()["offset"] == 4
    phone.put(f"/api/phone/files/{fid}/chunk", params={"offset": 4}, content=data[4:])
    monkeypatch.setattr(registry, "is_active", lambda _id: False)
    assert phone.post(f"/api/phone/files/{fid}/finish").status_code == 401
    monkeypatch.undo()
    assert phone.get(f"/api/phone/files/{fid}").json()["ready"] is False
    assert phone.post(f"/api/phone/files/{fid}/finish").json()["ready"] is True


def _chat_id(app) -> str:
    made = app.state.agent_chat.store.create_session(
        provider="claude-api", title="x", surface="agent", model="", effort="", cwd="")
    return made.session_id if hasattr(made, "session_id") else made["session_id"]


def test_a_phone_reads_and_switches_the_brain_of_its_chat(app, owner) -> None:
    phone, _csrf = pair(app, owner)
    svc = app.state.agent_chat
    sid = _chat_id(app)
    seen = phone.get(f"/api/phone/chats/{sid}/brain")
    assert seen.status_code == 200, seen.text
    body = seen.json()
    assert body["current"]["provider"] == "claude-api"
    ids = {row["id"] for row in body["providers"]}
    assert {"claude-api", "openrouter"} <= ids
    assert all(set(row) == {"id", "label", "models", "default_model", "keyless", "free_model"}
               for row in body["providers"])
    # An unknown provider is refused by the desktop picker's own rule.
    assert phone.patch(f"/api/phone/chats/{sid}/brain", json={"provider": "nope"}).status_code == 400
    moved = phone.patch(f"/api/phone/chats/{sid}/brain",
                        json={"provider": "openrouter", "model": "some/model"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["current"] == {"provider": "openrouter", "model": "some/model"}
    assert svc.store.get_session(sid).provider == "openrouter"
    assert phone.patch(f"/api/phone/chats/{sid}/brain", json={}).status_code == 400
    assert phone.get("/api/phone/chats/missing/brain").status_code == 404


def test_the_brain_routes_need_a_paired_device_and_csrf(app, owner) -> None:
    phone, csrf = pair(app, owner)
    sid = _chat_id(app)
    with browser(app) as stranger:
        assert stranger.get(f"/api/phone/chats/{sid}/brain").status_code == 401
        assert stranger.patch(f"/api/phone/chats/{sid}/brain", json={"model": "m"}).status_code == 401
    del phone.headers["X-Phone-CSRF"]
    assert phone.patch(f"/api/phone/chats/{sid}/brain", json={"model": "m"}).status_code == 403
    phone.headers["X-Phone-CSRF"] = csrf
    assert phone.patch(f"/api/phone/chats/{sid}/brain", json={"model": "m"}).status_code == 200


def test_the_brain_cannot_change_while_a_turn_runs(app, owner, monkeypatch: pytest.MonkeyPatch) -> None:
    phone, _csrf = pair(app, owner)
    svc = app.state.agent_chat
    sid = _chat_id(app)
    monkeypatch.setattr(svc, "is_running", lambda _sid: True)
    res = phone.patch(f"/api/phone/chats/{sid}/brain", json={"provider": "openrouter"})
    assert res.status_code == 409
    assert svc.store.get_session(sid).provider == "claude-api"


def test_an_extra_static_app_is_served_only_when_its_folder_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<p>extra</p>")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_CONTROL_API_KEY", KEY)
    for value, expected in ((str(tmp_path / "web"), 200), ("", 404), (str(tmp_path / "nope"), 404)):
        monkeypatch.setenv("JARVIS_EXTRA_STATIC_DIR", value)
        cfg = JarvisConfig()
        cfg.memory.data_dir = str(tmp_path / "data")
        cfg.ui.dev_mode = True
        srv = WebServer(cfg, bus=EventBus())
        with browser(srv.app) as client:
            assert client.get("/app-extra/").status_code == expected


class _FakeStt:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str | None]] = []

    async def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000, language: str | None = None):
        self.calls.append((len(pcm), sample_rate, language))
        return type("T", (), {"text": " ciao Jarvis "})()


def test_dictation_turns_phone_audio_into_text(app, owner, monkeypatch: pytest.MonkeyPatch) -> None:
    phone, csrf = pair(app, owner)
    fake = _FakeStt()
    monkeypatch.setattr("dodaios.plugins.stt.build_stt_from_config", lambda _cfg: fake)
    pcm = b"\x01\x00" * 16000
    ok = phone.post("/api/phone/transcribe", content=pcm)
    assert ok.status_code == 200, ok.text
    assert ok.json()["text"] == "ciao Jarvis"
    assert fake.calls == [(len(pcm), 16000, "it")]
    assert phone.post("/api/phone/transcribe", content=b"\x00\x00").status_code == 400  # too short
    assert phone.post("/api/phone/transcribe", content=b"\x01\x00" * 3 * 1024 * 1024).status_code == 413
    assert phone.post("/api/phone/transcribe", params={"rate": 123}, content=pcm).status_code == 400
    assert phone.post("/api/phone/transcribe", params={"lang": "../x"}, content=pcm).status_code == 400
    del phone.headers["X-Phone-CSRF"]
    assert phone.post("/api/phone/transcribe", content=pcm).status_code == 403
    with browser(app) as stranger:
        assert stranger.post("/api/phone/transcribe", content=pcm).status_code == 401


def test_dictation_reports_an_engine_that_cannot_start(app, owner, monkeypatch: pytest.MonkeyPatch) -> None:
    phone, _csrf = pair(app, owner)

    def boom(_cfg):
        raise RuntimeError("no key")

    monkeypatch.setattr("dodaios.plugins.stt.build_stt_from_config", boom)
    res = phone.post("/api/phone/transcribe", content=b"\x01\x00" * 16000)
    assert res.status_code == 503
    assert "key" not in res.text  # the owner's problem is not spelled out to the phone


def test_a_phone_can_view_only_files_in_its_chats_drops_folder(app, owner, tmp_path: Path) -> None:
    phone, _csrf = pair(app, owner)
    svc = app.state.agent_chat
    folder = tmp_path / "chat"
    drops_dir = folder / ".jarvis" / "drops"
    drops_dir.mkdir(parents=True)
    (drops_dir / "20261103-foto.jpg").write_bytes(b"\xff\xd8jpegbytes")
    (drops_dir / "notes.txt").write_text("secret")
    (tmp_path / "outside.jpg").write_bytes(b"outside")
    (drops_dir / "link.jpg").symlink_to(tmp_path / "outside.jpg")
    made = svc.store.create_session(provider="claude-api", title="x", surface="agent",
                                    model="", effort="", cwd=str(folder))
    sid = made.session_id if hasattr(made, "session_id") else made["session_id"]
    ok = phone.get(f"/api/phone/chats/{sid}/drops/20261103-foto.jpg")
    assert ok.status_code == 200 and ok.content == b"\xff\xd8jpegbytes"
    assert ok.headers["content-type"] == "image/jpeg"
    assert "sandbox" in ok.headers["content-security-policy"]
    assert phone.get(f"/api/phone/chats/{sid}/drops/notes.txt").status_code == 415
    assert phone.get(f"/api/phone/chats/{sid}/drops/link.jpg").status_code == 404
    assert phone.get(f"/api/phone/chats/{sid}/drops/missing.jpg").status_code == 404
    assert phone.get(f"/api/phone/chats/{sid}/drops/..%2Foutside.jpg").status_code in (400, 404)
    assert phone.get("/api/phone/chats/nope/drops/20261103-foto.jpg").status_code == 404
    with browser(app) as stranger:
        assert stranger.get(f"/api/phone/chats/{sid}/drops/20261103-foto.jpg").status_code == 401


def test_the_phone_reads_a_providers_model_list(app, owner, monkeypatch: pytest.MonkeyPatch) -> None:
    phone, _csrf = pair(app, owner)
    from dodaios.ui.web import provider_routes

    async def fake(provider_id: str, request, refresh: bool = False):
        info = provider_routes.BrainModelInfo(id="a/b", label="A B", free=True, starred=True)
        return provider_routes.BrainModelsResponse(provider=provider_id, current_model="", models=[info],
                                                   source="live")

    monkeypatch.setattr(provider_routes, "list_brain_models", fake)
    res = phone.get("/api/phone/brain/models", params={"provider": "openrouter"})
    assert res.status_code == 200, res.text
    assert res.json()["models"][0] == {"id": "a/b", "label": "A B", "free": True, "frontier": False,
                                       "value": False, "starred": True, "vision": None}
    assert phone.get("/api/phone/brain/models", params={"provider": "../x"}).status_code == 400


class _Chunk:
    def __init__(self, pcm: bytes, sample_rate: int = 24000) -> None:
        self.pcm, self.sample_rate = pcm, sample_rate


class _FakeTts:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def synthesize(self, text: str, language_code: str | None = None):
        self.calls.append((text, language_code))
        yield _Chunk(b"\x01\x00" * 100)
        yield _Chunk(b"\x02\x00" * 100)


def test_the_phone_can_have_a_reply_spoken(app, owner, monkeypatch: pytest.MonkeyPatch) -> None:
    phone, csrf = pair(app, owner)
    fake = _FakeTts()
    monkeypatch.setattr("dodaios.plugins.tts.build_tts_from_config", lambda _cfg: fake)
    res = phone.post("/api/phone/speak", json={"text": "Ciao **Davide**"})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "audio/wav"
    assert res.content[:4] == b"RIFF" and res.content[8:12] == b"WAVE"
    assert len(res.content) == 44 + 400
    assert fake.calls and fake.calls[0][1] == "it-IT" and "**" not in fake.calls[0][0]
    assert phone.post("/api/phone/speak", json={"text": "  "}).status_code == 400
    assert phone.post("/api/phone/speak", json={"text": "x" * 1600}).status_code == 422
    del phone.headers["X-Phone-CSRF"]
    assert phone.post("/api/phone/speak", json={"text": "ciao"}).status_code == 403
    with browser(app) as stranger:
        assert stranger.post("/api/phone/speak", json={"text": "ciao"}).status_code == 401


def test_speech_reports_a_voice_that_cannot_start(app, owner, monkeypatch: pytest.MonkeyPatch) -> None:
    phone, _csrf = pair(app, owner)

    def boom(_cfg):
        raise RuntimeError("no key")

    monkeypatch.setattr("dodaios.plugins.tts.build_tts_from_config", boom)
    res = phone.post("/api/phone/speak", json={"text": "ciao"})
    assert res.status_code == 503 and "key" not in res.text


# ----------------------------------------------------------------- push notifications
GOOD_ENDPOINT = "https://web.push.apple.com/abc123"


def test_vapid_header_is_a_valid_es256_token_for_the_servers_public_key(tmp_path: Path) -> None:
    import base64
    import json

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    from dodaios.ui.web.phone_push import PushStore

    store = PushStore(tmp_path)
    auth = store.vapid_headers(GOOD_ENDPOINT, "https://example.test")["Authorization"]
    token, key = auth.removeprefix("vapid t=").split(", k=")
    assert key == store.public_key()
    head, body, sig = token.split(".")
    pad = lambda x: x + "=" * (-len(x) % 4)  # noqa: E731
    claims = json.loads(base64.urlsafe_b64decode(pad(body)))
    assert claims["aud"] == "https://web.push.apple.com" and claims["sub"] == "https://example.test"
    raw = base64.urlsafe_b64decode(pad(sig))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.urlsafe_b64decode(pad(key)))
    pub.verify(
        encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
        f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()),
    )  # raises if wrong
    assert PushStore(tmp_path).public_key() == store.public_key()  # persisted, not regenerated
    assert (tmp_path / "push-vapid.pem").stat().st_mode & 0o077 == 0


def test_only_real_push_services_are_accepted() -> None:
    from dodaios.ui.web.phone_push import allowed_endpoint

    assert allowed_endpoint("https://web.push.apple.com/x")
    assert allowed_endpoint("https://fcm.googleapis.com/fcm/send/x")
    for bad in ("http://web.push.apple.com/x", "https://127.0.0.1/x", "https://evil.test/x",
                "https://web.push.apple.com.evil.test/x", "https://u:p@web.push.apple.com/x"):
        assert not allowed_endpoint(bad), bad


def test_push_routes_need_a_paired_device_and_refuse_unknown_services(app, owner) -> None:
    anon = browser(app)
    with anon:
        assert anon.get("/api/phone/push/key").status_code == 401
        assert anon.get("/api/phone/push/last").status_code == 401
    phone, _ = pair(app, owner)
    with phone:
        key = phone.get("/api/phone/push/key").json()
        assert key["key"] and key["subscribed"] is False
        assert phone.post("/api/phone/push/subscribe", json={"endpoint": "https://evil.test/x"}).status_code == 400
        bare = browser(app)
        bare.cookies.update(phone.cookies)
        with bare:
            assert bare.post("/api/phone/push/subscribe", json={"endpoint": GOOD_ENDPOINT}).status_code == 403
        assert phone.post("/api/phone/push/subscribe", json={"endpoint": GOOD_ENDPOINT}).status_code == 200
        assert phone.get("/api/phone/push/key").json()["subscribed"] is True
        assert phone.delete("/api/phone/push/subscribe").status_code == 200
        assert phone.get("/api/phone/push/key").json()["subscribed"] is False


def test_a_finished_turn_of_a_chat_the_phone_wrote_to_sends_one_push(app, owner, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from dodaios.ui.web import phone_push

    sent: list[str] = []
    monkeypatch.setattr(phone_push, "_deliver", lambda store, dev, active: sent.append(dev))
    svc = app.state.agent_chat
    phone_routes.attach_push(app.state, svc)
    phone, _ = pair(app, owner)
    with phone:
        phone.post("/api/phone/push/subscribe", json={"endpoint": GOOD_ENDPOINT})
        push = phone_routes.hub_for_state(app.state).push
        device_id = push.device_ids()[0]
        # a chat the phone never wrote to stays silent
        svc._event_listeners[-1]("other-chat", {"kind": "turn_finished"})
        push.watch("mine")
        svc._event_listeners[-1]("mine", {"kind": "assistant_text"})  # not a notifying kind
        svc._event_listeners[-1]("mine", {"kind": "turn_finished"})
        svc._event_listeners[-1]("mine", {"kind": "turn_finished"})  # inside the gap: one push only
        for _ in range(50):
            if sent:
                break
            time.sleep(0.02)
        assert sent == [device_id]
        notice = phone.get("/api/phone/push/last").json()["notice"]
        assert notice["title"] == "Jarvis ha finito" and notice["session"] == "mine"
