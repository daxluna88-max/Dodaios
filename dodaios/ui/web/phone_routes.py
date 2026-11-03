"""Routes behind the phone companion (``/m/``).

Two surfaces, kept apart on purpose:

* ``/api/phone-admin/*`` — what the OWNER does on the computer (mint a pairing code, list and
  revoke devices, publish a file to the phone). It sits behind the normal Jarvis guard
  (session cookie, loopback open access, or the Control Key); no phone credential reaches it.
* ``/api/phone/*`` — what a PAIRED PHONE may do: chat, approvals, stop, and its own file
  transfers. The outer guard lets these through (:func:`_http_auth_exception`), so every handler
  authenticates the device itself, re-reading the device row on every request: revocation is
  immediate for sessions, uploads and downloads in flight.

The phone's credential is a random token in an HttpOnly, SameSite=Strict cookie scoped to
``/api/phone``; unsafe methods also need the device's CSRF token in ``X-Phone-CSRF`` and an Origin
that matches the Host. Chat calls reuse the desktop chat routes' own functions, so approvals,
cancellation and queuing behave exactly as they do there.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dodaios.agent_chat import attachments as chat_attachments
from dodaios.phone.devices import DeviceRegistry, PairingError, csrf_matches
from dodaios.phone.store import NotFound, Revoked, TransferError, TransferStore
from dodaios.ui.web import phone_auto, phone_push, phone_todo

router = APIRouter(prefix="/api/phone", tags=["phone"])
admin_router = APIRouter(prefix="/api/phone-admin", tags=["phone"])

COOKIE = "jphone"
CSRF_HEADER = "x-phone-csrf"
COOKIE_MAX_AGE = 90 * 24 * 3600
_SWEEP_EVERY_S = 600.0
#: Types a phone may preview inline (in a sandboxed response); everything else downloads.
_INLINE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
}


class _Hub:
    """The registry and the store of one app, built on first use."""

    def __init__(self, root: Path) -> None:
        self.registry = DeviceRegistry(root)
        self.store = TransferStore(root / "files")
        self.push = phone_push.PushStore(root)
        self.auto = phone_auto.AutoGrants(root)
        #: Last outcome of the voice calls: key -> (ok, detail, unix time). Memory only.
        self.notes: dict[str, tuple[bool, str, float]] = {}
        self._swept = 0.0

    def sweep_if_due(self) -> None:
        now = time.monotonic()
        if now - self._swept >= _SWEEP_EVERY_S:
            self._swept = now
            self.store.sweep()


def hub_for_state(state: Any) -> _Hub:
    hub = getattr(state, "phone_hub", None)
    if hub is None:
        cfg = getattr(state, "config", None)
        data_dir = getattr(getattr(cfg, "memory", None), "data_dir", None) or "data"
        hub = state.phone_hub = _Hub(Path(data_dir) / "phone")
    return hub


def attach_push(state: Any, service: Any) -> None:
    """Called once when the chat service is built: lets a finished turn reach a closed phone app."""
    try:
        phone_push.attach(service, hub_for_state(state))
    except Exception:  # noqa: BLE001 — notifications are optional
        pass
    try:  # chats whose "approve always" ran out while the server was off go back to asking
        loop = asyncio.get_running_loop()
        loop.create_task(hub_for_state(state).auto.restore(service))
    except RuntimeError:
        pass


def _hub(request: Request) -> _Hub:
    return hub_for_state(request.app.state)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    host = request.headers.get("host", "").lower()
    if not origin or not host:
        return False
    return urlsplit(origin).netloc.lower() == host


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _device(request: Request) -> dict[str, Any]:
    """The live paired device behind this request, else 401. Also enforces the CSRF rules."""
    hub = _hub(request)
    device = hub.registry.authenticate(request.cookies.get(COOKIE))
    if device is None:
        raise HTTPException(status_code=401, detail="not paired")
    hub.push.seen(device["id"])
    if request.method not in ("GET", "HEAD"):
        if not _same_origin(request) or not csrf_matches(
            device["csrf"], request.headers.get(CSRF_HEADER)
        ):
            raise HTTPException(status_code=403, detail="request refused")
    return device


def _owner(request: Request) -> str:
    return _hub(request).registry.owner


def _guard(request: Request, device: dict[str, Any]) -> Any:
    """A callable answering "is this device still paired?" for a mutation's last look."""
    registry = _hub(request).registry
    return lambda: registry.is_active(device["id"])


def _ensure_active(request: Request, device: dict[str, Any]) -> None:
    """Re-check membership after an await that may have outlived the request's entry check."""
    if not _hub(request).registry.is_active(device["id"]):
        raise HTTPException(status_code=401, detail="not paired")


def _store_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Revoked as exc:
        raise HTTPException(status_code=401, detail="not paired") from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    except TransferError as exc:
        status = 409 if "offset" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


# ------------------------------------------------------------------ pairing
class RedeemBody(BaseModel):
    code: str = Field(max_length=32)
    name: str = Field(default="", max_length=80)


@router.post("/pair/redeem")
async def redeem(body: RedeemBody, request: Request, response: Response) -> dict[str, Any]:
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="request refused")
    hub = _hub(request)
    try:
        device, token = await asyncio.to_thread(hub.registry.redeem, body.code, body.name)
    except PairingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.set_cookie(
        COOKIE, token, max_age=COOKIE_MAX_AGE, path="/api/phone", httponly=True,
        samesite="strict", secure=_secure(request),
    )
    return {"device": {k: v for k, v in device.items() if k != "csrf"}, "csrf": device["csrf"]}


@router.get("/me")
def me(device: dict[str, Any] = Depends(_device)) -> dict[str, Any]:  # noqa: B008
    return {"device": {k: v for k, v in device.items() if k != "csrf"}, "csrf": device["csrf"]}


@router.post("/logout")
async def logout(
    request: Request, response: Response, device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, bool]:
    hub = _hub(request)
    await asyncio.to_thread(hub.registry.revoke, device["id"])
    await asyncio.to_thread(hub.store.delete_for_device, device["id"])
    response.delete_cookie(COOKIE, path="/api/phone")
    return {"ok": True}


# ------------------------------------------------------------------- status
_REASONS = {
    "bad_key": "chiave rifiutata", "no_credits": "crediti finiti", "not_configured": "non configurato",
    "timeout": "nessuna risposta in tempo", "check_failed": "controllo non riuscito",
}
_RANK = {"ok": 0, "unknown": 0, "warn": 1, "bad": 2}


@router.get("/status")
async def status(
    request: Request, session_id: str = "", device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    """One honest line about whether Jarvis can work: the engine, the voice calls, notifications."""
    routes = _chat()
    svc = await routes._async_service(request)  # noqa: SLF001
    hub = _hub(request)
    session = svc.store.get_session(session_id) if session_id else None
    if session is None:
        found = await asyncio.to_thread(svc.store.list_sessions, limit=1, surface=None)
        session = found[0] if found else None
    checks: list[dict[str, str]] = []
    if session is not None:
        state, detail = "unknown", "non ancora verificato"
        try:
            health = await routes.get_provider_health(request, surface=session.surface)
            row = next((r for r in health.providers if r.provider == session.provider), None)
            if row is not None and row.status == "ok":
                state, detail = "ok", "risponde"
            elif row is not None and row.status in ("needs_setup", "error"):
                state, detail = "bad", _REASONS.get(row.reason, "problema con questo motore")
        except Exception:  # noqa: BLE001 - a failed check is "unknown", never an error page
            state, detail = "unknown", "controllo non disponibile"
        checks.append({"id": "brain", "label": "Cervello · " + session.provider, "state": state, "detail": detail})
    for key, label in (("listen", "Dettatura"), ("speak", "Voce di Jarvis")):
        note = hub.notes.get(key)
        if note is None:
            checks.append({"id": key, "label": label, "state": "unknown", "detail": "non ancora usata"})
        elif note[0]:
            checks.append({"id": key, "label": label, "state": "ok", "detail": "ha funzionato"})
        else:
            checks.append({"id": key, "label": label, "state": "bad", "detail": "ultimo tentativo non riuscito"})
    worst = max((c["state"] for c in checks), key=lambda x: _RANK[x], default="ok")
    return {
        "overall": worst, "checks": checks,
        "push": hub.push.subscription(device["id"]) is not None,
    }


class AutoWindow(BaseModel):
    window: str


@router.post("/chats/{session_id}/auto")
async def auto_grant(
    session_id: str, body: AutoWindow, request: Request,
    _device: dict[str, Any] = Depends(_device),  # noqa: B008
) -> dict[str, Any]:
    """Switch a chat to automatic approvals for a window, without waiting for an approval card."""
    if body.window not in phone_auto.WINDOWS:
        raise HTTPException(status_code=400, detail="invalid window")
    svc = await _chat()._async_service(request)  # noqa: SLF001
    try:
        return {"auto": await _hub(request).auto.grant(svc, session_id, body.window)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


# --------------------------------------------------------------------- to do
def _vault(request: Request) -> Path:
    from dodaios.memory.wiki.vault_root import resolve_vault_root

    wiki_cfg = getattr(getattr(request.app.state, "config", None), "wiki_integration", None)
    raw = getattr(wiki_cfg, "vault_root", None)
    if raw is not None and str(raw).strip() == "":
        raw = None
    return resolve_vault_root(raw).path


class TodoTick(BaseModel):
    line: int = Field(ge=0, le=100000)
    tag: str = Field(max_length=16)
    done: bool


@router.get("/todo")
async def todo(request: Request, _device: dict[str, Any] = Depends(_device)) -> dict[str, Any]:  # noqa: B008
    return await asyncio.to_thread(phone_todo.list_items, _vault(request))


@router.post("/todo/tick")
async def todo_tick(
    body: TodoTick, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    try:
        await asyncio.to_thread(phone_todo.set_done, _vault(request), body.line, body.tag, body.done)
    except phone_todo.Stale as exc:
        raise HTTPException(status_code=409, detail="the list changed: reload it") from exc
    return await asyncio.to_thread(phone_todo.list_items, _vault(request))


# ------------------------------------------------------------- notifications
class PushSub(BaseModel):
    endpoint: str = Field(max_length=2048)


@router.get("/push/key")
def push_key(request: Request, device: dict[str, Any] = Depends(_device)) -> dict[str, Any]:  # noqa: B008
    push = _hub(request).push
    return {"key": push.public_key(), "subscribed": push.subscription(device["id"]) is not None}


@router.post("/push/subscribe")
async def push_subscribe(
    body: PushSub, request: Request, device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, bool]:
    if not phone_push.allowed_endpoint(body.endpoint):
        raise HTTPException(status_code=400, detail="unsupported push service")
    host = request.headers.get("host", "")
    subject = f"https://{host}" if host else "mailto:jarvis@example.com"
    await asyncio.to_thread(_hub(request).push.subscribe, device["id"], body.endpoint, subject)
    return {"ok": True}


@router.delete("/push/subscribe")
async def push_unsubscribe(request: Request, device: dict[str, Any] = Depends(_device)) -> dict[str, bool]:  # noqa: B008
    await asyncio.to_thread(_hub(request).push.unsubscribe, device["id"])
    return {"ok": True}


@router.get("/push/last")
def push_last(request: Request, device: dict[str, Any] = Depends(_device)) -> dict[str, Any]:  # noqa: B008
    notice = _hub(request).push.last(device["id"])
    return {"notice": notice}


# --------------------------------------------------------------------- chat
class PhoneMessage(BaseModel):
    text: str = Field(default="", max_length=20_000)
    file_ids: list[str] = Field(default_factory=list, max_length=10)
    timezone: str | None = Field(default=None, max_length=100)


class PhoneApproval(BaseModel):
    decision: str
    #: "approve always" for this chat: "30m", "1h" or "chat" (until the chat is over).
    auto: str = ""


class PhoneNewChat(BaseModel):
    provider: str = ""


def _chat() -> Any:
    from dodaios.ui.web import agent_chat_routes

    return agent_chat_routes


@router.get("/chats")
async def chats(request: Request, _device: dict[str, Any] = Depends(_device)) -> dict[str, Any]:  # noqa: B008
    return await asyncio.to_thread(_chat().list_sessions, request, 40, None)


@router.post("/chats", status_code=201)
async def new_chat(
    body: PhoneNewChat, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    routes = _chat()
    svc = await routes._async_service(request)  # noqa: SLF001 - the chat routes' own accessor
    latest = (await asyncio.to_thread(svc.store.list_sessions, limit=1, surface=None) or [None])[0]
    provider = body.provider or (latest.provider if latest else "")
    if not provider:
        raise HTTPException(status_code=409, detail="create the first chat on the computer")
    created = routes.CreateSessionBody(
        provider=provider, model=getattr(latest, "model", "") or "",
        surface=getattr(latest, "surface", "agent") or "agent",
    )
    return await asyncio.to_thread(routes.create_session, created, request)


@router.get("/chats/{session_id}")
async def chat(
    session_id: str, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    return await _chat().get_session(session_id, request, tail=120)


@router.post("/chats/{session_id}/messages", status_code=202)
async def send_message(
    session_id: str, body: PhoneMessage, request: Request,
    device: dict[str, Any] = Depends(_device),  # noqa: B008
) -> dict[str, Any]:
    routes = _chat()
    attachments: list[dict[str, Any]] = []
    if body.file_ids:
        svc = await routes._async_service(request)  # noqa: SLF001
        session = svc.store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        folder = session.cwd or svc.default_cwd(session.surface)
        hub = _hub(request)
        handles = []
        try:
            uploads = []
            for file_id in body.file_ids:
                meta, path = _store_call(hub.store.open_ready, _owner(request), file_id)
                if meta["direction"] != "in":
                    raise HTTPException(status_code=400, detail="not an uploaded file")
                handle = path.open("rb")
                handles.append(handle)
                uploads.append((meta["name"], handle))
            try:
                found = await chat_attachments.ingest(
                    folder, uploads=uploads, provider=session.provider
                )
            except chat_attachments.AttachmentError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            attachments = [item.to_dict() for item in found]
        finally:
            for handle in handles:
                handle.close()
    if not body.text.strip() and not attachments:
        raise HTTPException(status_code=400, detail="write a message or attach a file")
    # Preparing the attachments awaited; a device revoked meanwhile must not reach the agent.
    _ensure_active(request, device)
    message = routes.MessageBody(text=body.text, attachments=attachments, timezone=body.timezone)
    _hub(request).push.watch(session_id)
    return await routes.post_message(session_id, message, request)


@router.post("/chats/{session_id}/approvals/{approval_id}")
async def approve(
    session_id: str, approval_id: str, body: PhoneApproval, request: Request,
    _device: dict[str, Any] = Depends(_device),  # noqa: B008
) -> dict[str, Any]:
    routes = _chat()
    if body.auto and (body.decision != "allow" or body.auto not in phone_auto.WINDOWS):
        raise HTTPException(status_code=400, detail="invalid auto-approval request")
    result = await routes.resolve_approval(
        session_id, approval_id, routes.ApprovalBody(decision=body.decision), request
    )
    if body.auto:
        svc = await routes._async_service(request)  # noqa: SLF001
        try:
            result["auto"] = await _hub(request).auto.grant(svc, session_id, body.auto)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
    return result


@router.get("/chats/{session_id}/auto")
async def auto_state(
    session_id: str, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    return {"auto": _hub(request).auto.state(session_id)}


@router.post("/chats/{session_id}/auto/off")
async def auto_off(
    session_id: str, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    svc = await _chat()._async_service(request)  # noqa: SLF001
    await _hub(request).auto.revoke(svc, session_id)
    return {"auto": None}


@router.post("/chats/{session_id}/cancel", openapi_extra={"x-jarvis-dangerous": True})
async def cancel(
    session_id: str, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    return await _chat().cancel_turn(session_id, request)


# -------------------------------------------------------------------- brain
class PhoneBrain(BaseModel):
    """Which engine answers in this chat. Nothing else about the chat can be changed from a phone."""

    provider: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)


def _brain_rows(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in catalog.get("providers", []):
        models = [
            {"id": str(m.get("id", "")), "label": str(m.get("label") or m.get("id") or "")}
            for m in (row.get("curated_models") or [])
            if isinstance(m, dict) and m.get("id")
        ]
        rows.append({
            "id": str(row.get("id", "")), "label": str(row.get("label") or row.get("id") or ""),
            "models": models, "default_model": str(row.get("default_model") or ""),
            "keyless": bool(row.get("keyless")), "free_model": row.get("models_source") == "live",
        })
    return rows


@router.get("/chats/{session_id}/brain")
async def get_brain(
    session_id: str, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    """The engines this chat may use and the one it uses now (read-only projection of the catalog)."""
    routes = _chat()
    svc = await routes._async_service(request)  # noqa: SLF001
    session = svc.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    catalog = await routes.get_catalog(request, surface=session.surface, session_id=session_id)
    return {
        "current": {"provider": session.provider, "model": getattr(session, "model", "") or ""},
        "running": bool(svc.is_running(session_id)),
        "providers": _brain_rows(catalog),
    }


@router.patch("/chats/{session_id}/brain")
async def set_brain(
    session_id: str, body: PhoneBrain, request: Request,
    device: dict[str, Any] = Depends(_device),  # noqa: B008
) -> dict[str, Any]:
    """Switch provider and/or model. Same rules as the desktop picker; not while a turn is running."""
    if body.provider is None and body.model is None:
        raise HTTPException(status_code=400, detail="nothing to change")
    routes = _chat()
    svc = await routes._async_service(request)  # noqa: SLF001
    if svc.store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    if svc.is_running(session_id):
        raise HTTPException(status_code=409, detail="Jarvis is working: wait or stop first")
    _ensure_active(request, device)
    patch = routes.PatchSessionBody(provider=body.provider, model=body.model)
    await routes.patch_session(session_id, patch, request)
    return await get_brain(session_id, request, device)


@router.get("/brain/models")
async def brain_models(
    request: Request, provider: str, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    """The model catalog of one provider (live for OpenRouter and the like), trimmed for a phone."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,60}", provider):
        raise HTTPException(status_code=400, detail="bad provider")
    from dodaios.ui.web import provider_routes

    try:
        out = await provider_routes.list_brain_models(provider, request)
    except HTTPException as exc:
        if exc.status_code == 404:
            return {"provider": provider, "source": "none", "models": []}
        raise
    models = [
        {"id": m.id, "label": m.label, "free": m.free, "frontier": m.frontier, "value": m.value,
         "starred": m.starred, "vision": m.vision}
        for m in out.models[:1500]
    ]
    return {"provider": provider, "source": out.source, "models": models}


# --------------------------------------------------------------- dictation
#: A minute of 16 kHz mono int16 is ~1.9 MB; a little headroom, nothing more.
_MAX_PCM_BYTES = 3 * 1024 * 1024
_transcribing: set[str] = set()


async def _transcribe_impl(
    request: Request, rate: int, lang: str, device: dict[str, Any]
) -> dict[str, Any]:
    """Raw int16 mono PCM from the phone's microphone -> text, with Jarvis' configured STT engine.

    The audio is not stored. Which engine hears it (local or a cloud one) is the Jarvis STT setting;
    the answer names it so the phone can say so.
    """
    if rate not in (8000, 16000, 24000, 44100, 48000) or not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,4})?", lang):
        raise HTTPException(status_code=400, detail="bad audio parameters")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_PCM_BYTES:
        raise HTTPException(status_code=413, detail="recording too long")
    data = await request.body()
    if len(data) > _MAX_PCM_BYTES:
        raise HTTPException(status_code=413, detail="recording too long")
    if len(data) < rate // 10 * 2 or len(data) % 2:
        raise HTTPException(status_code=400, detail="recording too short")
    if device["id"] in _transcribing:
        raise HTTPException(status_code=409, detail="a recording is already being transcribed")
    _transcribing.add(device["id"])
    try:
        from dodaios.plugins.stt import build_stt_from_config

        cfg = getattr(request.app.state, "config", None)
        try:
            stt = await asyncio.to_thread(build_stt_from_config, cfg.stt)
        except Exception as exc:  # noqa: BLE001 — a missing key or engine is the owner's to fix
            raise HTTPException(status_code=503, detail="speech-to-text is not available") from exc
        try:
            result = await asyncio.wait_for(
                stt.transcribe_pcm(data, sample_rate=rate, language=lang), timeout=45
            )
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="transcription took too long") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail="transcription failed") from exc
        _ensure_active(request, device)
        text = str(getattr(result, "text", result) or "").strip()
        return {"text": text, "engine": str(getattr(cfg.stt, "provider", "") or "")}
    finally:
        _transcribing.discard(device["id"])


# ------------------------------------------------------------------- speech
class PhoneSpeak(BaseModel):
    text: str = Field(max_length=1500)
    lang: str = Field(default="it", pattern=r"^(it|fr|en|de|es|pt)$")


_SPEECH_LANG = {"it": "it-IT", "fr": "fr-FR", "en": "en-US", "de": "de-DE", "es": "es-ES", "pt": "pt-PT"}


_speaking: set[str] = set()
_MAX_WAV_BYTES = 8 * 1024 * 1024


def _wav(pcm: bytes, rate: int) -> bytes:
    import struct

    header = (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(pcm))
    )
    return header + pcm


async def _speak_impl(body: PhoneSpeak, request: Request, device: dict[str, Any]) -> Response:
    """Text -> speech with Jarvis' configured TTS voice, as a WAV the phone can play. Not stored."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="nothing to say")
    if device["id"] in _speaking:
        raise HTTPException(status_code=409, detail="a reply is already being voiced")
    _speaking.add(device["id"])
    try:
        from dodaios.brain.output_filter import scrub_for_voice
        from dodaios.plugins.tts import build_tts_from_config

        cfg = getattr(request.app.state, "config", None)
        try:
            tts = await asyncio.to_thread(build_tts_from_config, cfg.tts)
        except Exception as exc:  # noqa: BLE001 — a missing key or voice is the owner's to fix
            raise HTTPException(status_code=503, detail="text-to-speech is not available") from exc
        scrubbed = scrub_for_voice(text)
        spoken = str(getattr(scrubbed, "cleaned", scrubbed) or text)
        pcm = bytearray()
        rate = 24000

        async def collect() -> None:
            nonlocal rate
            async for chunk in tts.synthesize(spoken, language_code=_SPEECH_LANG[body.lang]):
                rate = int(getattr(chunk, "sample_rate", rate) or rate)
                pcm.extend(bytes(chunk.pcm))
                if len(pcm) > _MAX_WAV_BYTES:
                    raise HTTPException(status_code=413, detail="reply too long to voice")

        try:
            await asyncio.wait_for(collect(), timeout=45)
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="speech took too long") from exc
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail="speech failed") from exc
        if not pcm:
            raise HTTPException(status_code=502, detail="speech failed")
        _ensure_active(request, device)
        return Response(_wav(bytes(pcm), rate), media_type="audio/wav",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    finally:
        _speaking.discard(device["id"])


async def _recorded(request: Request, key: str, work: Any) -> Any:
    """Run a voice call and remember whether it worked, for the status line."""
    hub = _hub(request)
    try:
        out = await work
    except HTTPException as exc:
        if exc.status_code >= 500:
            hub.notes[key] = (False, str(exc.detail), time.time())
        raise
    hub.notes[key] = (True, "", time.time())
    return out


@router.post("/transcribe")
async def transcribe(
    request: Request, rate: int = 16000, lang: str = "it",
    device: dict[str, Any] = Depends(_device),  # noqa: B008
) -> dict[str, Any]:
    return await _recorded(request, "listen", _transcribe_impl(request, rate, lang, device))


@router.post("/speak")
async def speak(
    body: PhoneSpeak, request: Request, device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> Response:
    return await _recorded(request, "speak", _speak_impl(body, request, device))


# ------------------------------------------------------------------- drops
_DROP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}")


@router.get("/chats/{session_id}/drops/{name}")
async def drop_file(
    session_id: str, name: str, request: Request,
    _device: dict[str, Any] = Depends(_device),  # noqa: B008
) -> Response:
    """A picture/video/audio file in THIS chat's drops folder, so the phone can show what Jarvis refers to.

    Only plain files directly inside ``<chat folder>/.jarvis/drops``, only the inline-safe media types,
    served sandboxed. No path ever comes from the phone: a name, looked up in one folder.
    """
    if not _DROP_NAME.fullmatch(name) or ".." in name:
        raise HTTPException(status_code=400, detail="bad name")
    media = _INLINE_TYPES.get(Path(name).suffix.lower())
    if media is None:
        raise HTTPException(status_code=415, detail="not a viewable type")
    routes = _chat()
    svc = await routes._async_service(request)  # noqa: SLF001
    session = svc.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    from dodaios.agentic_ide import drops

    folder = Path(session.cwd or svc.default_cwd(session.surface))
    root = (folder / drops.DROP_DIRNAME).resolve()
    candidate = root / name
    path = candidate.resolve()
    if candidate.is_symlink() or path.parent != root or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if path.stat().st_size > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large to preview")
    return Response(
        await asyncio.to_thread(path.read_bytes),
        media_type=media,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
                 "Content-Security-Policy": "sandbox; default-src 'none'",
                 "Content-Disposition": "inline"},
    )


# -------------------------------------------------------------------- files
class NewFile(BaseModel):
    name: str = Field(max_length=300)
    size: int
    sha256: str = Field(max_length=64)


def _file_view(hub: _Hub, row: dict[str, Any]) -> dict[str, Any]:
    view = dict(row)
    view["inline"] = Path(row["name"]).suffix.lower() in _INLINE_TYPES
    return view


@router.get("/files")
async def files(request: Request, _device: dict[str, Any] = Depends(_device)) -> dict[str, Any]:  # noqa: B008
    hub = _hub(request)
    await asyncio.to_thread(hub.sweep_if_due)
    rows = await asyncio.to_thread(hub.store.list, _owner(request))
    return {"files": [_file_view(hub, r) for r in rows], "chunk_size": hub.store.chunk_size,
            "max_file": hub.store.max_file}


@router.post("/files", status_code=201)
async def create_file(
    body: NewFile, request: Request, device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    hub = _hub(request)
    transfer_id = await asyncio.to_thread(
        lambda: _store_call(
            hub.store.create, _owner(request), device["id"], body.name, body.size,
            body.sha256.lower(), guard=_guard(request, device),
        )
    )
    return {"id": transfer_id, "offset": 0, "chunk_size": hub.store.chunk_size}


@router.get("/files/{file_id}")
async def file_status(
    file_id: str, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    hub = _hub(request)
    row = await asyncio.to_thread(_store_call, hub.store.status, _owner(request), file_id)
    return _file_view(hub, row)


@router.put("/files/{file_id}/chunk")
async def put_chunk(
    file_id: str, offset: int, request: Request,
    device: dict[str, Any] = Depends(_device),  # noqa: B008
) -> dict[str, int]:
    hub = _hub(request)
    limit = hub.store.chunk_size
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="chunk too large")
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail="chunk too large")
    _ensure_active(request, device)  # the body took time to arrive
    new_offset = await asyncio.to_thread(
        lambda: _store_call(
            hub.store.append, _owner(request), file_id, offset, bytes(data),
            guard=_guard(request, device),
        )
    )
    return {"offset": new_offset}


@router.post("/files/{file_id}/finish")
async def finish_file(
    file_id: str, request: Request, device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    hub = _hub(request)
    row = await asyncio.to_thread(
        lambda: _store_call(
            hub.store.finish, _owner(request), file_id, guard=_guard(request, device)
        )
    )
    return _file_view(hub, row)


@router.delete("/files/{file_id}")
async def delete_file(
    file_id: str, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, bool]:
    hub = _hub(request)
    await asyncio.to_thread(_store_call, hub.store.delete, _owner(request), file_id)
    return {"ok": True}


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """A single ``bytes=a-b`` range as ``(start, end_inclusive)``; ``None`` = whole file."""
    if not header:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or (not match.group(1) and not match.group(2)):
        raise HTTPException(status_code=416, detail="bad range")
    if not match.group(1):
        length = min(int(match.group(2)), size)
        start, end = size - length, size - 1
    else:
        start = int(match.group(1))
        end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
    if start > end or start >= size:
        raise HTTPException(status_code=416, detail="range not satisfiable")
    return start, end


def _stream(hub: _Hub, path: Path, device_id: str, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            # A revoked device stops receiving bytes mid-download.
            if not hub.registry.is_active(device_id):
                return
            block = handle.read(min(hub.store.chunk_size, remaining))
            if not block:
                return
            remaining -= len(block)
            yield block


@router.get("/files/{file_id}/download")
async def download(
    file_id: str, request: Request, inline: bool = False,
    device: dict[str, Any] = Depends(_device),  # noqa: B008
) -> Response:
    hub = _hub(request)
    meta, path = await asyncio.to_thread(
        _store_call, hub.store.open_ready, _owner(request), file_id
    )
    size = meta["size"]
    span = _parse_range(request.headers.get("range"), size) if size else None
    start, end = span if span else (0, size - 1)
    media = _INLINE_TYPES.get(Path(meta["name"]).suffix.lower())
    headers = {
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        "Accept-Ranges": "bytes", "Content-Length": str(max(0, end - start + 1)),
        "Content-Security-Policy": "sandbox; default-src 'none'",
    }
    if inline and media:
        disposition = "inline"
    else:
        disposition, media = "attachment", "application/octet-stream"
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", meta["name"]) or "file"
    headers["Content-Disposition"] = (
        f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(meta['name'])}"
    )
    status = 200
    if span:
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if size == 0:
        return Response(b"", media_type=media, headers=headers)
    return StreamingResponse(
        _stream(hub, path, device["id"], start, end), status_code=status, media_type=media,
        headers=headers,
    )


# -------------------------------------------------------------------- admin
@admin_router.post("/pairing")
async def mint_pairing(request: Request) -> dict[str, Any]:
    try:
        code, ttl = await asyncio.to_thread(_hub(request).registry.new_code)
    except PairingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"code": code, "expires_in": int(ttl)}


@admin_router.get("/devices")
async def list_devices(request: Request) -> dict[str, Any]:
    return {"devices": await asyncio.to_thread(_hub(request).registry.list)}


@admin_router.delete("/devices/{device_id}")
async def revoke_device(device_id: str, request: Request) -> dict[str, Any]:
    hub = _hub(request)
    revoked = await asyncio.to_thread(hub.registry.revoke, device_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="no such active device")
    dropped = await asyncio.to_thread(hub.store.delete_for_device, device_id)
    return {"ok": True, "unfinished_uploads_removed": dropped}


class OutboxBody(BaseModel):
    path: str = Field(max_length=4096)
    name: str = Field(default="", max_length=255)


def _publish(hub: _Hub, owner: str, path: str, name: str) -> dict[str, Any]:
    source = Path(os.path.realpath(os.path.expanduser(path)))
    if not source.is_file():
        raise HTTPException(status_code=400, detail="not a file")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    size = source.stat().st_size
    transfer_id = _store_call(
        hub.store.create, owner, "owner", name or source.name, size, digest.hexdigest(),
        direction="out",
    )
    try:
        offset = 0
        with source.open("rb") as handle:
            while block := handle.read(hub.store.chunk_size):
                offset = hub.store.append(owner, transfer_id, offset, block)
        if offset != size:
            raise HTTPException(status_code=409, detail="the file changed while it was copied")
        return _store_call(hub.store.finish, owner, transfer_id)
    except BaseException:
        hub.store.delete(owner, transfer_id)
        raise


@admin_router.post("/outbox", status_code=201)
async def publish_to_phone(body: OutboxBody, request: Request) -> dict[str, Any]:
    """Make a file on this computer downloadable by the paired phones (owner action)."""
    hub = _hub(request)
    return await asyncio.to_thread(_publish, hub, _owner(request), body.path, body.name)
