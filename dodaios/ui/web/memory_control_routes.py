"""Controllable memory: proposals, deletion, export (ADR-0061).

* ``GET  /api/memory-control/status``           switches, counts, retention and backup facts.
* ``GET  /api/memory-control/proposals``        notes waiting for the owner (this product only).
* ``POST /api/memory-control/proposals/{id}/approve``  one note; optional edited ``text``.
* ``POST /api/memory-control/proposals/{id}/reject``   one note; its hash is remembered.
* ``POST /api/memory-control/pages/delete``     ``mode`` logical (30-day undo) or permanent.
* ``GET  /api/memory-control/trash``            logically deleted pages and their restore date.
* ``POST /api/memory-control/trash/restore``    bring one back inside its window.
* ``POST /api/memory-control/trash/delete``     permanently remove one trash entry.
* ``POST /api/memory-control/notes/delete``     permanently remove one learned note.
* ``GET  /api/memory-control/export``           zip of this product's memory.

There is no bulk approve. Authentication, Host/Origin (CSRF) checks and the credential guard are
the global :mod:`dodaios.ui.web.surface_security` layer, like every other ``/api`` route; writes
carry a risk tier and permanent removal is ``ask``. Nothing here calls a model.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from dodaios.memory.control import deletion
from dodaios.memory.control.export import ExportError, build_export
from dodaios.memory.control.proposals import ProposalError, ProposalStore, current_product_name
from dodaios.ui.web.wiki_routes import _resolve_vault_root

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory-control", tags=["memory-control"])


class ApproveBody(BaseModel):
    text: str | None = Field(default=None, max_length=2000)


class DeletePageBody(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    mode: str = Field(pattern=r"^(logical|permanent)$")
    #: Permanent removal has no undo: the caller must say it understood.
    confirm_permanent: bool = False


class TrashBody(BaseModel):
    id: str = Field(min_length=12, max_length=12)


class DeleteNoteBody(BaseModel):
    target: str = Field(pattern=r"^(user|memory)$")
    entry_id: str = Field(min_length=1, max_length=64)


def _config(request: Request) -> Any:
    return getattr(request.app.state, "config", None)


def _notebook(request: Request) -> Any:
    from dodaios.memory.learning import notebook as nb

    active = nb.active()
    if active is not None:
        return active
    config = _config(request)
    if config is None:
        raise HTTPException(503, "Memory is not configured.")
    return nb.notebook_from_config(config)


def _store(request: Request) -> ProposalStore:
    return ProposalStore(_notebook(request).folder)


def _vault(request: Request) -> Path:
    root = _resolve_vault_root(request)
    if root is None or not root.is_dir():
        raise HTTPException(404, "The memory vault is not set up yet.")
    return root


def _db_path(request: Request) -> Path | None:
    from dodaios.memory.wiki.db_path import resolve_wiki_db_path

    config = _config(request)
    data_dir = getattr(getattr(config, "memory", None), "data_dir", "./data")
    return resolve_wiki_db_path(data_dir)


def _backup_dir(vault: Path) -> Path:
    return vault.parent / "wiki-backups"


def _fail(exc: Exception) -> HTTPException:
    status = getattr(exc, "status", 400)
    return HTTPException(status, str(exc))


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    config = _config(request)
    learning = getattr(getattr(config, "memory", None), "learning", None)
    enabled = getattr(learning, "enabled", None)
    store = _store(request)
    vault = _resolve_vault_root(request)
    trash = deletion.list_trash(vault) if vault is not None and vault.is_dir() else []
    return {
        "ok": True,
        "product": current_product_name(),
        "learning_enabled": enabled if isinstance(enabled, bool) else None,
        "require_approval": bool(getattr(learning, "require_approval", True)),
        "pending": len(store.items()),
        "trash": len(trash),
        "retention_days": deletion.RETENTION_DAYS,
        "backups": deletion.backup_facts(_backup_dir(vault) if vault else None, ""),
    }


@router.get("/proposals")
async def list_proposals(request: Request) -> dict[str, Any]:
    items = await asyncio.to_thread(_store(request).items)
    return {"ok": True, "product": current_product_name(), "proposals": [p.public() for p in items]}


@router.post("/proposals/{proposal_id}/approve", openapi_extra={"x-jarvis-risk-tier": "monitor"})
async def approve(proposal_id: str, payload: ApproveBody, request: Request) -> dict[str, Any]:
    notebook = _notebook(request)
    store = ProposalStore(notebook.folder)
    try:
        item = await asyncio.to_thread(
            store.approve, proposal_id, notebook.apply, text=payload.text
        )
    except ProposalError as exc:
        raise _fail(exc) from exc
    return {"ok": True, "proposal": item.public()}


@router.post("/proposals/{proposal_id}/reject", openapi_extra={"x-jarvis-risk-tier": "monitor"})
async def reject(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        item = await asyncio.to_thread(_store(request).reject, proposal_id)
    except ProposalError as exc:
        raise _fail(exc) from exc
    return {"ok": True, "proposal": item.public()}


@router.post("/pages/delete", openapi_extra={"x-jarvis-risk-tier": "ask"})
async def delete_page(payload: DeletePageBody, request: Request) -> dict[str, Any]:
    from dodaios.ui.web.memory_vault_routes import _EDITABLE_GROUPS, _model

    vault = _vault(request)
    _, model = await _model(request)
    record = model.pages.get(payload.path)
    if record is None:
        raise HTTPException(404, "No page at that path.")
    if record.group not in _EDITABLE_GROUPS:
        raise HTTPException(
            403,
            "Only knowledge and journal pages are deleted here. "
            "Learned notes have their own delete.",
        )
    db, backups = _db_path(request), _backup_dir(vault)
    try:
        if payload.mode == "logical":
            report = await asyncio.to_thread(
                deletion.logical_delete, vault, payload.path, db_path=db, backup_dir=backups
            )
        else:
            if not payload.confirm_permanent:
                raise HTTPException(422, "Permanent removal needs confirm_permanent=true.")
            report = await asyncio.to_thread(
                deletion.permanent_delete, vault, rel_path=payload.path, db_path=db,
                backup_dir=backups,
            )
    except deletion.DeletionError as exc:
        raise _fail(exc) from exc
    return {"ok": True, **report.public()}


@router.get("/trash")
async def get_trash(request: Request) -> dict[str, Any]:
    vault = _vault(request)
    await asyncio.to_thread(deletion.purge_expired, vault)
    return {"ok": True, "items": await asyncio.to_thread(deletion.list_trash, vault)}


@router.post("/trash/restore", openapi_extra={"x-jarvis-risk-tier": "monitor"})
async def restore_trash(payload: TrashBody, request: Request) -> dict[str, Any]:
    try:
        rel = await asyncio.to_thread(deletion.restore, _vault(request), payload.id)
    except deletion.DeletionError as exc:
        raise _fail(exc) from exc
    note = "Restored. Search may not list it until the index is rebuilt (not verified)."
    return {"ok": True, "path": rel, "note": note}


@router.post("/trash/delete", openapi_extra={"x-jarvis-risk-tier": "ask"})
async def delete_trash(payload: TrashBody, request: Request) -> dict[str, Any]:
    vault = _vault(request)
    try:
        report = await asyncio.to_thread(
            deletion.permanent_delete, vault, trash_id=payload.id,
            db_path=_db_path(request), backup_dir=_backup_dir(vault),
        )
    except deletion.DeletionError as exc:
        raise _fail(exc) from exc
    return {"ok": True, **report.public()}


@router.post("/notes/delete", openapi_extra={"x-jarvis-risk-tier": "ask"})
async def delete_note(payload: DeleteNoteBody, request: Request) -> dict[str, Any]:
    notebook = _notebook(request)
    try:
        report = await asyncio.to_thread(
            deletion.delete_notebook_entry, notebook, payload.target, payload.entry_id
        )
    except (deletion.DeletionError, ValueError) as exc:
        raise _fail(exc) from exc
    return {"ok": True, **report.public()}


@router.get("/export")
async def export_memory(request: Request) -> Response:
    vault = _vault(request)
    from dodaios.brain.engine_policy import current_product

    backups = deletion.backup_facts(_backup_dir(vault), "")
    try:
        data, manifest = await asyncio.to_thread(
            build_export, vault, product=current_product(), backups=backups
        )
    except ExportError as exc:
        raise _fail(exc) from exc
    name = f"dodaios-memory-{manifest['product']}-{manifest['created_at'][:10]}.zip"
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


__all__ = ["router"]
