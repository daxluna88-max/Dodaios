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
from dodaios.phone.store import NotFound, TransferError, TransferStore

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
        self._swept = 0.0

    def sweep_if_due(self) -> None:
        now = time.monotonic()
        if now - self._swept >= _SWEEP_EVERY_S:
            self._swept = now
            self.store.sweep()


def _hub(request: Request) -> _Hub:
    state = request.app.state
    hub = getattr(state, "phone_hub", None)
    if hub is None:
        cfg = getattr(state, "config", None)
        data_dir = getattr(getattr(cfg, "memory", None), "data_dir", None) or "data"
        hub = state.phone_hub = _Hub(Path(data_dir) / "phone")
    return hub


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
    if request.method not in ("GET", "HEAD"):
        if not _same_origin(request) or not csrf_matches(
            device["csrf"], request.headers.get(CSRF_HEADER)
        ):
            raise HTTPException(status_code=403, detail="request refused")
    return device


def _owner(request: Request) -> str:
    return _hub(request).registry.owner


def _store_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
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
async def me(device: dict[str, Any] = Depends(_device)) -> dict[str, Any]:  # noqa: B008
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


# --------------------------------------------------------------------- chat
class PhoneMessage(BaseModel):
    text: str = Field(default="", max_length=20_000)
    file_ids: list[str] = Field(default_factory=list, max_length=10)
    timezone: str | None = Field(default=None, max_length=100)


class PhoneApproval(BaseModel):
    decision: str


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
    message = routes.MessageBody(text=body.text, attachments=attachments, timezone=body.timezone)
    return await routes.post_message(session_id, message, request)


@router.post("/chats/{session_id}/approvals/{approval_id}")
async def approve(
    session_id: str, approval_id: str, body: PhoneApproval, request: Request,
    _device: dict[str, Any] = Depends(_device),  # noqa: B008
) -> dict[str, Any]:
    routes = _chat()
    return await routes.resolve_approval(
        session_id, approval_id, routes.ApprovalBody(decision=body.decision), request
    )


@router.post("/chats/{session_id}/cancel")
async def cancel(
    session_id: str, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    return await _chat().cancel_turn(session_id, request)


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
        _store_call, hub.store.create, _owner(request), device["id"], body.name, body.size,
        body.sha256.lower(),
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
    _device: dict[str, Any] = Depends(_device),  # noqa: B008
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
    new_offset = await asyncio.to_thread(
        _store_call, hub.store.append, _owner(request), file_id, offset, bytes(data)
    )
    return {"offset": new_offset}


@router.post("/files/{file_id}/finish")
async def finish_file(
    file_id: str, request: Request, _device: dict[str, Any] = Depends(_device)  # noqa: B008
) -> dict[str, Any]:
    hub = _hub(request)
    row = await asyncio.to_thread(_store_call, hub.store.finish, _owner(request), file_id)
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
