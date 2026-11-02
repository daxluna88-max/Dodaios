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
