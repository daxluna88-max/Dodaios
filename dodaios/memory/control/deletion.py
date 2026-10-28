"""Real deletion of a vault page, distinct from Archive (ADR-0061).

Two modes, both honest about what they cover:

* **logical** (``logical_delete``): the page file moves to ``<vault>/.trash/<id>/`` with a
  manifest. It disappears from the page list, from text search, from the embedding index and
  from the in-memory caches, so it cannot be recalled. It is recoverable until
  ``restore_until`` (``RETENTION_DAYS``); after that ``purge_expired`` removes it for good.
  The trash folder is dot-prefixed: the page walk, the search indexer and the backup snapshot all
  skip it.
* **permanent** (``permanent_delete``): the same stores are cleared, and the page bytes are
  unlinked from the vault, from ``.trash`` and from any ``_archive`` copy of the same path. There
  is no undo.

What this module does NOT do (and the report says so): rewrite backup archives, recall bytes from
a disk's free space (deleted files are unlinked, not overwritten, and SQLite pages are only
overwritten with ``secure_delete``), recall exports already made, un-send copies given to a
remote engine, or rewrite other pages that quote the page. Those are listed in the result.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import stat as _stat
import tarfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

log = logging.getLogger(__name__)

TRASH_DIR: Final[str] = ".trash"
MANIFEST: Final[str] = "manifest.json"
RETENTION_DAYS: Final[int] = 30
_QUOTE_MIN_CHARS: Final[int] = 40
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")


class DeletionError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class StoreResult:
    store: str
    done: bool
    detail: str = ""


@dataclass
class DeletionReport:
    mode: str
    path: str
    stores: list[StoreResult] = field(default_factory=list)
    derived: list[dict[str, str]] = field(default_factory=list)
    residual: list[str] = field(default_factory=list)
    trash_id: str = ""
    restore_until: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "path": self.path,
            "trash_id": self.trash_id,
            "restore_until": self.restore_until,
            "stores": [vars(s) for s in self.stores],
            "derived": self.derived,
            "residual": self.residual,
        }


# ------------------------------------------------------------------ paths


def _is_linked(path: Path) -> bool:
    """A symlink, a Windows junction or any other reparse point (``lstat``, never followed)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return _stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def canonical_rel(value: Any) -> str:
    """A page path exactly as this module writes it: relative, ``/`` separated, no ``..``, no
    empty part, no drive, no backslash, not in a reserved or dot folder. Raises ``ValueError``."""
    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value:
        raise ValueError("not a path")
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ValueError("absolute or drive path")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p != p.strip() or p.endswith(".") for p in parts):
        raise ValueError("unsafe path part")
    if parts[0] in (TRASH_DIR, "_archive") or any(p.startswith(".") for p in parts):
        raise ValueError("reserved path")
    if not value.lower().endswith(".md"):
        raise ValueError("not a page")
    return value


def _confine(base: Path, rel: str, *, must_exist: bool = False) -> Path:
    """``base / rel`` proven to stay inside ``base``: the base is strictly resolved, every
    existing component below it must be a real (not linked) entry, and the strict resolution of
    the deepest existing part must still be inside ``base``. Raises :class:`DeletionError`."""
    parts = rel.split("/") if rel else []
    try:
        root = base.resolve(strict=True)
    except OSError as exc:
        raise DeletionError("The memory vault is not available.", status=404) from exc
    current = root
    for part in parts:
        current = current / part
        if not os.path.lexists(current):
            break
        if _is_linked(current):
            raise DeletionError("That path goes through a link; refused.", status=422)
    probe = current
    while not os.path.lexists(probe) and probe != root:
        probe = probe.parent
    try:
        probe.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise DeletionError("That path is outside the memory vault.", status=422) from exc
    target = root.joinpath(*parts)
    if must_exist and not (target.is_file() and not _is_linked(target)):
        raise DeletionError("No such page.", status=404)
    return target


def _safe_page(vault_root: Path, rel_path: str) -> tuple[Path, str]:
    rel = (rel_path or "").replace("\\", "/").strip("/")
    if not rel or not rel.lower().endswith(".md"):
        raise DeletionError("Only a memory page (.md) can be deleted.", status=422)
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts) or parts[0] in (TRASH_DIR, "_archive"):
        raise DeletionError("That path is not a live memory page.", status=422)
    target = _confine(vault_root, rel, must_exist=True)
    return target, rel


def _trash_root(vault_root: Path) -> Path:
    return vault_root.resolve() / TRASH_DIR


def _checked_trash_root(vault_root: Path) -> Path | None:
    """The trash folder when it exists, is a real directory and is not linked; else None."""
    root = _trash_root(vault_root)
    if not os.path.lexists(root):
        return None
    if _is_linked(root) or not root.is_dir():
        raise DeletionError("The trash folder is not a plain folder; refused.", status=422)
    return root


def _body_hash(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _current_product() -> str:
    from dodaios.memory.control.proposals import current_product_name

    try:
        return current_product_name()
    except Exception:  # noqa: BLE001 - an unreadable product means "do not vouch"
        return ""


def _validate_manifest(vault_root: Path, folder: Path, trash_id: str, data: Any) -> dict[str, Any]:
    """The manifest as a trusted record, or :class:`DeletionError` (404, nothing leaked).

    Checks: schema and types, ``id`` equals the directory name and the requested id, a canonical
    relative page path, the product that deleted it (when recorded) is the current one, finite
    timestamps, and the trash folder itself is a real folder inside this vault's trash."""
    reason = ""
    if not isinstance(data, dict):
        reason = "manifest is not an object"
    elif data.get("id") != trash_id or folder.name != trash_id:
        reason = "manifest id does not match its folder"
    else:
        for key in ("deleted_at", "restore_until"):
            value = data.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(
                value
            ):
                reason = f"bad {key}"
        sha = data.get("sha256")
        if sha is not None and not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
            reason = "bad sha256"
        product = data.get("product")
        if not reason and product is not None and product != _current_product():
            reason = "entry belongs to another product"
        if not reason:
            try:
                canonical_rel(data.get("path"))
            except ValueError as exc:
                reason = f"bad path ({exc})"
    if not reason:
        try:
            trash = _checked_trash_root(vault_root)
            if trash is None or _is_linked(folder) or not folder.is_dir():
                reason = "trash folder is missing or linked"
            else:
                folder.resolve(strict=True).relative_to(trash.resolve(strict=True))
        except (DeletionError, OSError, ValueError):
            reason = "trash folder is not inside this vault's trash"
    if reason:
        log.warning("memory delete: trash entry %s refused (%s)", trash_id, reason)
        raise DeletionError("Unknown trash entry.", status=404)
    return data


# ------------------------------------------------------------------ stores


def _clear_indexes(db_path: Path | None, vault_root: Path, rel: str) -> list[StoreResult]:
    """Search (FTS5), embedding rows and the candidate journal, with ``secure_delete`` on."""
    if db_path is None or not Path(db_path).is_file():
        return [
            StoreResult("search index", False, "no index database found; nothing to clear"),
            StoreResult("embedding index", False, "no index database found; nothing to clear"),
        ]
    from dodaios.memory.wiki import fts_index, semantic_index

    out: list[StoreResult] = []
    root = vault_root.resolve()
    abs_path = root / rel
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        conn.execute("PRAGMA secure_delete = ON")
        for name, call in (
            ("search index", lambda: fts_index.remove_page(conn, root, abs_path)),
            ("embedding index", lambda: semantic_index.remove_page(conn, root, abs_path)),
        ):
            try:
                call()
                out.append(StoreResult(name, True, "rows for this page deleted (secure_delete on)"))
            except sqlite3.Error as exc:
                out.append(StoreResult(name, False, f"failed: {exc}"))
        try:
            cur = conn.execute("DELETE FROM wiki_candidate_journal WHERE target_path = ?", (rel,))
            conn.commit()
            out.append(StoreResult("candidate journal", True, f"{cur.rowcount} row(s) deleted"))
        except sqlite3.OperationalError:
            out.append(StoreResult("candidate journal", True, "table not present"))
        try:
            conn.execute("INSERT INTO wiki_fts(wiki_fts) VALUES('optimize')")
            conn.commit()
        except sqlite3.Error:
            log.debug("memory delete: fts optimize not available")
    finally:
        conn.close()
    return out


def _clear_caches(vault_root: Path) -> StoreResult:
    """Every in-process cache that can hold the page text. Failures are reported, not hidden."""
    cleared: list[str] = []
    failed: list[str] = []
    for name, fn in (
        ("page parse cache", _drop_parse_cache),
        ("vault model cache", _drop_model_cache),
    ):
        try:
            fn(vault_root)
            cleared.append(name)
        except Exception as exc:  # noqa: BLE001 - reported below
            failed.append(f"{name}: {exc}")
    try:
        from dodaios.memory.learning import notebook as nb

        active = nb.active()
        if active is not None:
            active.invalidate()
            cleared.append("notebook prompt cache")
    except Exception as exc:  # noqa: BLE001
        failed.append(f"notebook prompt cache: {exc}")
    detail = "cleared: " + ", ".join(cleared)
    if failed:
        detail += "; NOT cleared: " + "; ".join(failed)
    return StoreResult("in-memory caches", not failed, detail)


def _drop_parse_cache(vault_root: Path) -> None:
    from dodaios.ui.web.wiki_routes import invalidate_visible_page_cache

    invalidate_visible_page_cache(vault_root)


def _drop_model_cache(vault_root: Path) -> None:
    from dodaios.ui.web import memory_vault_routes

    with memory_vault_routes._MODEL_LOCK:
        memory_vault_routes._MODEL_CACHE.pop(str(vault_root.resolve()), None)
        memory_vault_routes._MODEL_CACHE.pop(str(vault_root), None)


# --------------------------------------------------------------- derived


def _norm_lines(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        flat = " ".join(line.lower().split())
        if len(flat) >= _QUOTE_MIN_CHARS and not flat.startswith(("---", "title:", "slug:")):
            out.add(flat)
    return out


def find_derived(vault_root: Path, rel: str, body: str) -> list[dict[str, str]]:
    """Pages that link to this page or repeat one of its lines verbatim (40+ characters).

    A report, not an action: another page may be the owner's own writing, so it is listed for
    the owner to open, never rewritten or deleted here. A paraphrase is not found.
    """
    root = vault_root.resolve()
    stem = Path(rel).stem.casefold()
    no_ext = rel[:-3].casefold()
    lines = _norm_lines(body)
    found: list[dict[str, str]] = []
    for path in root.rglob("*.md"):
        try:
            sub = path.relative_to(root)
        except ValueError:
            continue
        if sub.parts[0] in (TRASH_DIR, "_archive") or any(p.startswith(".") for p in sub.parts):
            continue
        other = sub.as_posix()
        if other == rel or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        reasons = []
        for link in _WIKILINK.findall(text):
            if link.strip().casefold() in (stem, no_ext):
                reasons.append("links to it")
                break
        if lines and lines & _norm_lines(text):
            reasons.append("repeats a line of it")
        if reasons:
            found.append({"path": other, "why": " and ".join(reasons)})
    return sorted(found, key=lambda d: d["path"])


# --------------------------------------------------------------- backups


def backup_facts(backup_dir: Path | None, rel: str) -> dict[str, Any]:
    """How many backup archives still contain the page, and the rotation rule. Read only."""
    from dodaios.memory.wiki.backup import DEFAULT_MAX_BACKUPS

    facts: dict[str, Any] = {
        "directory_known": backup_dir is not None and Path(backup_dir).is_dir(),
        "archives": 0,
        "archives_with_page": 0,
        "keeps_newest": DEFAULT_MAX_BACKUPS,
        "rotation": (
            "A snapshot is taken before each memory write; only the newest "
            f"{DEFAULT_MAX_BACKUPS} are kept. Rotation is by count, not by age: with few writes "
            "an old copy can stay for a long time."
        ),
        "excluded_from_snapshots": ["_archive", "attachments", ".trash", "dot folders"],
    }
    if not facts["directory_known"]:
        return facts
    for archive in sorted(Path(backup_dir).glob("wiki-*.tar.gz")):
        facts["archives"] += 1
        try:
            with tarfile.open(archive, "r:gz") as tar:
                if rel in tar.getnames():
                    facts["archives_with_page"] += 1
        except (tarfile.TarError, OSError):
            log.info("memory delete: could not read backup %s", archive.name)
    return facts


def _residual(rel: str, backups: dict[str, Any], mode: str) -> list[str]:
    items = []
    if backups["archives_with_page"]:
        items.append(
            f"{backups['archives_with_page']} backup archive(s) still contain this page "
            "until they rotate out. They are not rewritten."
        )
    elif backups["directory_known"]:
        items.append("No backup archive listed this page when checked (a newer one may be made).")
    else:
        items.append("The backup folder could not be found, so backups were not checked.")
    items += [
        "Exports you already made keep their copy.",
        "Copies already sent to a remote engine in earlier conversations cannot be recalled.",
        "log.md keeps the line that says the page was written (title and summary), not rewritten.",
        "Other pages that link to or repeat it are listed above and are not changed.",
        "The disk may still hold the old bytes in free space: files are unlinked, not wiped.",
        "A running voice session keeps the instructions it started with until it is renewed.",
    ]
    if mode == "logical":
        items.insert(0, "The page is kept in the trash until the restore date.")
    return items


# ------------------------------------------------------------------ modes


def logical_delete(
    vault_root: Path, rel_path: str, *, db_path: Path | None = None,
    backup_dir: Path | None = None, now: float | None = None,
) -> DeletionReport:
    target, rel = _safe_page(vault_root, rel_path)
    stamp = now if now is not None else time.time()
    body = target.read_text(encoding="utf-8", errors="replace")
    derived = find_derived(vault_root, rel, body)
    try:
        canonical_rel(rel)
    except ValueError as exc:
        raise DeletionError("That path is not a live memory page.", status=422) from exc
    trash_id = uuid.uuid4().hex[:12]
    trash = _trash_root(vault_root)
    if _checked_trash_root(vault_root) is None:
        trash.mkdir(parents=True, exist_ok=True)
        _checked_trash_root(vault_root)
    folder = trash / trash_id
    dest = _confine(trash, f"{trash_id}/{rel}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _confine(trash, f"{trash_id}/{rel}")  # re-check after creating the folders
    restore_until = stamp + RETENTION_DAYS * 86400
    manifest = {
        "id": trash_id,
        "path": rel,
        "deleted_at": stamp,
        "restore_until": restore_until,
        "sha256": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest(),
        "product": _current_product(),
    }
    # Manifest first: a page in the trash must never exist without its record.
    (folder / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    try:
        os.replace(target, dest)
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    report = DeletionReport("logical", rel, trash_id=trash_id, restore_until=restore_until)
    moved = f"moved to the trash, restorable until {_fmt(restore_until)}"
    report.stores.append(StoreResult("page file", True, moved))
    report.stores += _clear_indexes(db_path, vault_root, rel)
    report.stores.append(_clear_caches(vault_root))
    report.derived = derived
    report.residual = _residual(rel, backup_facts(backup_dir, rel), "logical")
    return report


def list_trash(vault_root: Path) -> list[dict[str, Any]]:
    """Trash entries whose manifest passes :func:`_validate_manifest`. A corrupted, edited or
    foreign entry is NOT listed (and so can be neither restored nor purged through its id)."""
    out = []
    try:
        root = _checked_trash_root(vault_root)
    except DeletionError:
        return out
    if root is None:
        return out
    for folder in sorted(root.iterdir()):
        if _is_linked(folder) or not folder.is_dir():
            continue
        if not re.fullmatch(r"[0-9a-f]{12}", folder.name):
            continue
        try:
            data = _validate_manifest(
                vault_root, folder, folder.name,
                json.loads((folder / MANIFEST).read_text(encoding="utf-8")),
            )
        except (OSError, ValueError, DeletionError):
            continue
        out.append({k: data.get(k) for k in ("id", "path", "deleted_at", "restore_until")})
    return sorted(out, key=lambda d: -(d.get("deleted_at") or 0))


def _trash_entry(vault_root: Path, trash_id: str) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"[0-9a-f]{12}", trash_id or ""):
        raise DeletionError("Unknown trash entry.", status=404)
    folder = _trash_root(vault_root) / trash_id
    if _is_linked(folder):
        log.warning("memory delete: trash entry %s is a link, refused", trash_id)
        raise DeletionError("Unknown trash entry.", status=404)
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeletionError("Unknown trash entry.", status=404) from exc
    return folder, _validate_manifest(vault_root, folder, trash_id, data)


def restore(vault_root: Path, trash_id: str, *, now: float | None = None) -> str:
    """Put a logically deleted page back, if its window is open and the path is free.

    Everything is confined first: the manifest is validated, the trash copy must be a real file
    inside ``.trash/<id>`` and match the recorded hash, and the destination must lie inside this
    vault with no linked ancestor."""
    folder, data = _trash_entry(vault_root, trash_id)
    if (now if now is not None else time.time()) > float(data["restore_until"]):
        raise DeletionError("The restore window has ended.", status=410)
    rel = data["path"]
    source = _confine(_trash_root(vault_root), f"{trash_id}/{rel}", must_exist=True)
    expected = data.get("sha256")
    if expected and _body_hash(source) != expected:
        raise DeletionError(
            "The trashed copy changed after it was deleted; not restored.", status=409
        )
    target = _confine(vault_root, rel)
    if os.path.lexists(target):
        raise DeletionError("A page with that name exists now. Rename or delete it.", status=409)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _confine(vault_root, rel)  # re-check after creating the folders
    if os.path.lexists(target):
        raise DeletionError("A page with that name exists now. Rename or delete it.", status=409)
    os.replace(source, target)
    shutil.rmtree(folder, ignore_errors=True)
    _clear_caches(vault_root)
    return rel


def _remove_trash_folder(vault_root: Path, trash_id: str) -> bool:
    """Remove ONE validated trash folder; False if it is not a valid entry or could not go."""
    try:
        folder, _data = _trash_entry(vault_root, trash_id)
    except DeletionError:
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not os.path.lexists(folder)


def purge_expired(vault_root: Path, *, now: float | None = None) -> int:
    """Remove trash entries whose window ended. Returns how many were removed.

    Only entries that pass manifest validation are touched; an invalid one is left alone (and is
    not listed), never deleted on the strength of its own say-so."""
    stamp = now if now is not None else time.time()
    removed = 0
    for entry in list_trash(vault_root):
        if stamp > float(entry["restore_until"] or 0) and _remove_trash_folder(
            vault_root, str(entry["id"])
        ):
            removed += 1
    return removed


def permanent_delete(
    vault_root: Path, *, rel_path: str | None = None, trash_id: str | None = None,
    db_path: Path | None = None, backup_dir: Path | None = None,
) -> DeletionReport:
    """Remove a page (live, or already in the trash) from every store this code covers."""
    root = vault_root.resolve()
    body = ""
    if trash_id:
        folder, data = _trash_entry(vault_root, trash_id)
        rel = data["path"]
        try:
            copy = _confine(_trash_root(vault_root), f"{trash_id}/{rel}", must_exist=True)
            body = copy.read_text(encoding="utf-8", errors="replace")
        except DeletionError:
            log.info("memory delete: the trash entry had no readable page copy")
        shutil.rmtree(folder)
        file_detail = "trash copy unlinked"
    else:
        target, rel = _safe_page(vault_root, rel_path or "")
        body = target.read_text(encoding="utf-8", errors="replace")
        target.unlink()
        file_detail = "file unlinked from the vault"
    report = DeletionReport("permanent", rel)
    # Same path in older trash entries or in the archive: the archive is not deletion, so
    # a permanent delete must take those copies too.
    extra = 0
    for entry in list_trash(vault_root):
        if entry["path"] == rel and _remove_trash_folder(vault_root, str(entry["id"])):
            extra += 1
    archived_hits = 0
    try:
        archive_dir = root / "_archive"
        if os.path.lexists(archive_dir):
            if _is_linked(archive_dir) or not archive_dir.is_dir():
                raise DeletionError("The archive folder is not a plain folder.", status=422)
            archived = _confine(archive_dir, rel, must_exist=True)
            archived.unlink()
            archived_hits = 1
    except DeletionError:
        log.info("memory delete: no confined archived copy of %s removed", rel)
    except OSError:
        log.info("memory delete: could not remove the archived copy of %s", rel)
    if extra:
        file_detail += f"; {extra} older trash copy(ies) unlinked"
    if archived_hits:
        file_detail += f"; {archived_hits} archived copy(ies) unlinked"
    report.stores.append(StoreResult("page file", True, file_detail))
    report.stores += _clear_indexes(db_path, vault_root, rel)
    report.stores.append(_clear_caches(vault_root))
    report.derived = find_derived(vault_root, rel, body)
    report.residual = _residual(rel, backup_facts(backup_dir, rel), "permanent")
    return report


# -------------------------------------------------------- notebook entries


def delete_notebook_entry(notebook: Any, target: str, entry_id: str) -> DeletionReport:
    """Permanently remove one learned note (USER.md / MEMORY.md / SOUL.md) from the stores below.

    The note leaves the notebook first (if that fails nothing was erased and the error is
    raised). Then each other store is cleared on its own and reported on its own: the learning
    ledger (it keeps old/new text of every change, so its lines for this note are removed from
    the file and its rotated copy) and the proposal store (``.memory-proposals.json``: text,
    evidence and before-text of every matching record, decided ones included; a matching OPEN
    proposal is dismissed, keeping only its hash). A store that failed or was not touched is
    reported ``done=False`` and the residual list says the note may still be there; this function
    never states the note is erased from a store it did not clear.
    """
    entries = notebook.entries()
    match = [e for e in entries.get(target, []) if e.id == entry_id]
    if len(match) != 1:
        raise DeletionError("No such note.", status=404)
    text = match[0].text
    report = DeletionReport("permanent", f"{target}:{entry_id}")
    notebook.apply(target=target, operation="remove", entry_id=entry_id, expected=text,
                   source="deleted by owner", enforce_budget=False)
    report.stores.append(StoreResult("notebook entry", True, "removed from the notebook file"))
    failed: list[str] = []
    try:
        scrubbed = _scrub_ledger(notebook, entry_id, text)
        note = f"{scrubbed} line(s) removed (including the deletion's own)"
        report.stores.append(StoreResult("learning ledger", True, note))
    except (OSError, ValueError) as exc:
        report.stores.append(StoreResult("learning ledger", False, f"NOT cleared: {exc}"))
        failed.append("learning ledger")
    try:
        from dodaios.memory.control.proposals import ProposalError, ProposalStore

        try:
            count, dismissed = ProposalStore(notebook.folder).scrub_note(target, entry_id, text)
        except ProposalError as exc:
            raise OSError(str(exc)) from exc
        detail = f"{count} record(s) scrubbed (text, evidence, before-text and hash)"
        if dismissed:
            detail += f"; {dismissed} open proposal(s) of the same note dismissed"
        report.stores.append(StoreResult("proposal records", True, detail))
    except (OSError, ValueError) as exc:
        report.stores.append(StoreResult("proposal records", False, f"NOT cleared: {exc}"))
        failed.append("proposal records")
    try:
        notebook.invalidate()
        report.stores.append(StoreResult("in-memory caches", True, "notebook prompt cache cleared"))
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        report.stores.append(StoreResult("in-memory caches", False, f"NOT cleared: {exc}"))
        failed.append("in-memory caches")
    report.residual = [
        "Backups of the notebook folder, if any, keep their copy until they rotate.",
        "Exports you already made keep their copy.",
        "Copies already sent to a remote engine in earlier prompts cannot be recalled.",
        "A running voice session keeps the instructions it started with until it is renewed.",
        "The disk may still hold the old bytes in free space.",
    ]
    if failed:
        report.residual.insert(
            0,
            "The note may STILL be present in: " + ", ".join(failed)
            + ". It was removed from the notebook only.",
        )
    return report


def _scrub_ledger(notebook: Any, entry_id: str, text: str) -> int:
    from dodaios.memory.learning.notebook import LEDGER_NAME

    removed = 0
    for name in (LEDGER_NAME, LEDGER_NAME + ".1"):
        path = notebook.folder / name
        if not path.is_file():
            continue
        keep, dropped = [], 0
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                keep.append(line)
                continue
            if rec.get("entry_id") == entry_id or text in (rec.get("before"), rec.get("after")):
                dropped += 1
            else:
                keep.append(line)
        if dropped:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        removed += dropped
    return removed


def _fmt(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
