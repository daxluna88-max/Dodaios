"""Block 14 (ADR-0062): phone approvals, STOP, provenance, product isolation. Written, NOT run."""
# ruff: noqa: F811 - the fixtures are reused from test_phone_routes by name

from __future__ import annotations

from pathlib import Path

import pytest

from dodaios.agent_chat.events import make_event
from dodaios.control.stop import get_stop_controller
from dodaios.safety.effects import call_fingerprint
from tests.unit.ui.web.test_phone_routes import app, browser, owner, pair  # noqa: F401

pytestmark = pytest.mark.no_auto_web_auth


def _session(app) -> str:
    made = app.state.agent_chat.store.create_session(
        provider="claude-code", title="t", surface="agent", model="", effort="", cwd="")
    return made.session_id if hasattr(made, "session_id") else made["session_id"]


def _card(app, sid: str, name: str = "Write", args: dict | None = None) -> tuple[str, dict]:
    """A stored approval card plus a pending future, as AgentChatService._ask leaves them."""
    import asyncio

    svc = app.state.agent_chat
    args = args or {"path": "a.txt"}
    approval_id = "card-1"
    loop = asyncio.new_event_loop()
    svc._approvals[approval_id] = loop.create_future()  # noqa: SLF001
    svc._approval_session[approval_id] = sid  # noqa: SLF001
    svc.store.append_event(sid, make_event("approval_required", {
        "turn_id": "t1", "approval_id": approval_id, "call_id": "c", "name": name,
        "input": args, "summary": "s"}))
    return approval_id, {"request_id": approval_id, "tool_name": name,
                         "call_fingerprint": call_fingerprint(name, args),
                         "generation": get_stop_controller().generation}


def test_chat_view_serves_the_identity_a_phone_approval_must_echo(app, owner) -> None:
    phone, _ = pair(app, owner)
    sid = _session(app)
    aid, ident = _card(app, sid)
    view = phone.get(f"/api/phone/chats/{sid}").json()
    payload = next(e["payload"] for e in view["events"] if e["kind"] == "approval_required")
    assert payload["request_id"] == aid
    assert payload["call_fingerprint"] == ident["call_fingerprint"]
    assert view["stop_generation"] == ident["generation"] and view["door_open"] is True


def test_approval_needs_the_four_identity_parts_and_no_always_allow(app, owner) -> None:
    phone, _ = pair(app, owner)
    sid = _session(app)
    aid, ident = _card(app, sid)
    url = f"/api/phone/chats/{sid}/approvals/{aid}"
    assert phone.post(url, json={"decision": "allow"}).status_code == 422
    for missing in ident:
        partial = {k: v for k, v in ident.items() if k != missing}
        assert phone.post(url, json={"decision": "allow", **partial}).status_code == 422
    assert phone.post(url, json={"decision": "allow_always", **ident}).status_code == 422
    assert phone.post(url, json={"decision": "allow", **{**ident, "request_id": "x"}}
                      ).status_code == 400


def test_approval_of_another_call_or_generation_is_refused_unconsumed(app, owner) -> None:
    phone, _ = pair(app, owner)
    sid = _session(app)
    aid, ident = _card(app, sid)
    url = f"/api/phone/chats/{sid}/approvals/{aid}"
    other = phone.post(url, json={"decision": "allow", **{**ident, "call_fingerprint": "z"}})
    assert other.status_code == 409 and other.json()["detail"]["code"] == "identity_mismatch"
    wrong_tool = phone.post(url, json={"decision": "allow", **{**ident, "tool_name": "Bash"}})
    assert wrong_tool.status_code == 409
    stale = phone.post(url, json={"decision": "allow", **{**ident, "generation": 999}})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_generation"
    assert not app.state.agent_chat._approvals[aid].done()  # noqa: SLF001
    ok = phone.post(url, json={"decision": "allow", **ident})
    assert ok.status_code == 200
    assert app.state.agent_chat._approvals[aid].result() == "allow"  # noqa: SLF001


def test_phone_approval_after_stop_is_refused_but_deny_still_works(app, owner) -> None:
    phone, _ = pair(app, owner)
    sid = _session(app)
    aid, ident = _card(app, sid)
    controller = get_stop_controller()
    url = f"/api/phone/chats/{sid}/approvals/{aid}"
    try:
        assert phone.post("/api/phone/stop?client_seq=1").status_code == 200
        refused = phone.post(url, json={"decision": "allow", **ident})
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "stopped"
        assert not app.state.agent_chat._approvals[aid].done()  # noqa: SLF001
        assert phone.post(url, json={"decision": "deny", **ident}).status_code == 200
    finally:
        controller.resume()


def test_revoked_device_decides_nothing(app, owner) -> None:
    phone, _ = pair(app, owner)
    sid = _session(app)
    aid, ident = _card(app, sid)
    device_id = owner.get("/api/phone-admin/devices").json()["devices"][0]["id"]
    assert owner.delete(f"/api/phone-admin/devices/{device_id}").status_code == 200
    url = f"/api/phone/chats/{sid}/approvals/{aid}"
    assert phone.post(url, json={"decision": "allow", **ident}).status_code == 401
    assert phone.post("/api/phone/stop").status_code == 401
    assert phone.get("/api/phone/control-strip").status_code == 401
    assert not app.state.agent_chat._approvals[aid].done()  # noqa: SLF001


def test_stop_goes_through_the_stop_controller_and_echoes_client_seq(app, owner) -> None:
    phone, _ = pair(app, owner)
    controller = get_stop_controller()
    try:
        body = phone.post("/api/phone/stop?client_seq=7").json()
        assert body["client_seq"] == 7
        assert body["stop"]["state"] in {"stopped", "partly_stopped"}
        assert all(r["outcome"] in {"requested", "confirmed", "still_active", "unknown"}
                   for r in body["stop"]["results"])
        assert controller.door_open is False
        strip = phone.get("/api/phone/control-strip?client_seq=8").json()
        assert strip["client_seq"] == 8 and {"boot_id", "seq", "generation"} <= set(strip)
    finally:
        controller.resume()


def test_phone_stop_needs_csrf_and_has_no_resume(app, owner) -> None:
    phone, _ = pair(app, owner)
    del phone.headers["X-Phone-CSRF"]
    assert phone.post("/api/phone/stop").status_code == 403
    assert phone.post("/api/phone/stop/resume").status_code in {404, 405}


def test_phone_cookie_opens_no_other_api(app, owner) -> None:
    phone, _ = pair(app, owner)
    for path in ("/api/stop", "/api/control-strip", "/api/phone-admin/devices"):
        assert phone.get(path).status_code in {401, 403, 404, 405}
    assert phone.post("/api/stop").status_code in {401, 403}


def test_phone_root_is_the_product_data_dir_and_never_relative(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=None)))
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("JARVIS_PRODUCT_ACTIVE", raising=False)
    from dodaios.ui.web import phone_routes

    with pytest.raises(HTTPException) as err:
        phone_routes._phone_root(request)  # noqa: SLF001
    assert err.value.status_code == 503
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "d"))
    assert phone_routes._phone_root(request) == tmp_path / "d" / "phone"  # noqa: SLF001


def test_phone_module_never_declares_a_turn_unrestricted() -> None:
    source = Path("dodaios/ui/web/phone_routes.py").read_text(encoding="utf-8")
    assert "attach_unrestricted" not in source
    assert "Provenance.UNAVAILABLE" in source


def test_panel_ships_the_strip_ordering_and_an_always_available_stop() -> None:
    mobile = Path("dodaios/ui/web/mobile")
    order = (mobile / "order.js").read_text(encoding="utf-8")
    for rule in ("boot_id", "seq <= this.lastSeq", "next.generation < this.lastGeneration",
                 "epochFloor", "beginWriteRequest", "isAfterBarrier"):
        assert rule in order
    index = (mobile / "index.html").read_text(encoding="utf-8")
    assert "/m/order.js" in index and 'id="stopall"' in index
    assert "/m/order.js" in (mobile / "sw.js").read_text(encoding="utf-8")
    app = (mobile / "app.js").read_text(encoding="utf-8")
    assert "/api/phone/stop?client_seq=" in app and "allow_always" not in app
    # the STOP button is toggled with the view only, never with the running state
    assert '$("stopall").classList.toggle("hide", view === "pair")' in app
