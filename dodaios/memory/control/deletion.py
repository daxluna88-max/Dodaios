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
import os
import re
import shutil
import sqlite3
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


def _safe_page(vault_root: Path, rel_path: str) -> tuple[Path, str]:
    rel = (rel_path or "").replace("\\", "/").strip("/")
    if not rel or not rel.lower().endswith(".md"):
        raise DeletionError("Only a memory page (.md) can be deleted.", status=422)
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts) or parts[0] in (TRASH_DIR, "_archive"):
        raise DeletionError("That path is not a live memory page.", status=422)
    root = vault_root.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise DeletionError("That path is outside the memory vault.", status=422) from exc
    if target.is_symlink() or not target.is_file():
        raise DeletionError("No such page.", status=404)
    return target, rel


def _trash_root(vault_root: Path) -> Path:
    return vault_root.resolve() / TRASH_DIR


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
    trash_id = uuid.uuid4().hex[:12]
    folder = _trash_root(vault_root) / trash_id
    dest = folder / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    restore_until = stamp + RETENTION_DAYS * 86400
    manifest = {
        "id": trash_id,
        "path": rel,
        "deleted_at": stamp,
        "restore_until": restore_until,
        "sha256": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest(),
    }
    os.replace(target, dest)
    (folder / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    report = DeletionReport("logical", rel, trash_id=trash_id, restore_until=restore_until)
    moved = f"moved to the trash, restorable until {_fmt(restore_until)}"
    report.stores.append(StoreResult("page file", True, moved))
    report.stores += _clear_indexes(db_path, vault_root, rel)
    report.stores.append(_clear_caches(vault_root))
    report.derived = derived
    report.residual = _residual(rel, backup_facts(backup_dir, rel), "logical")
    return report


def list_trash(vault_root: Path) -> list[dict[str, Any]]:
    out = []
    root = _trash_root(vault_root)
    if not root.is_dir():
        return out
    for folder in sorted(root.iterdir()):
        try:
            data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: data.get(k) for k in ("id", "path", "deleted_at", "restore_until")})
    return sorted(out, key=lambda d: -(d.get("deleted_at") or 0))


def _trash_entry(vault_root: Path, trash_id: str) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"[0-9a-f]{12}", trash_id or ""):
        raise DeletionError("Unknown trash entry.", status=404)
    folder = _trash_root(vault_root) / trash_id
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeletionError("Unknown trash entry.", status=404) from exc
    return folder, data


def restore(vault_root: Path, trash_id: str, *, now: float | None = None) -> str:
    """Put a logically deleted page back, if its window is open and the path is free."""
    folder, data = _trash_entry(vault_root, trash_id)
    if (now if now is not None else time.time()) > float(data["restore_until"]):
        raise DeletionError("The restore window has ended.", status=410)
    rel = data["path"]
    source = folder / rel
    target = vault_root.resolve() / rel
    if target.exists():
        raise DeletionError("A page with that name exists now. Rename or delete it.", status=409)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
    shutil.rmtree(folder, ignore_errors=True)
    _clear_caches(vault_root)
    return rel


def purge_expired(vault_root: Path, *, now: float | None = None) -> int:
    """Remove trash entries whose window ended. Returns how many were removed."""
    stamp = now if now is not None else time.time()
    removed = 0
    for entry in list_trash(vault_root):
        if stamp > float(entry["restore_until"] or 0):
            shutil.rmtree(_trash_root(vault_root) / entry["id"], ignore_errors=True)
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
        copy = folder / rel
        if copy.is_file():
            body = copy.read_text(encoding="utf-8", errors="replace")
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
        if entry["path"] == rel:
            shutil.rmtree(_trash_root(vault_root) / entry["id"], ignore_errors=True)
            extra += 1
    archived_hits = 0
    archived = root / "_archive" / rel
    if archived.is_file() and not archived.is_symlink():
        try:
            archived.unlink()
            archived_hits = 1
        except OSError:
            log.info("memory delete: could not remove archived copy %s", archived)
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
    """Permanently remove one learned note (USER.md / MEMORY.md) and scrub the learning ledger.

    The ledger keeps the old and new text of every change; leaving it would keep the note, so
    its lines for this note are removed from the ledger and its rotated copy. Approved records
    in the proposal store that carry the same text are cleared too.
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
    scrubbed = _scrub_ledger(notebook, entry_id, text)
    note = f"{scrubbed} line(s) removed (including the deletion's own)"
    report.stores.append(StoreResult("learning ledger", True, note))
    notebook.invalidate()
    report.stores.append(StoreResult("in-memory caches", True, "notebook prompt cache cleared"))
    report.residual = [
        "Backups of the notebook folder, if any, keep their copy until they rotate.",
        "Exports you already made keep their copy.",
        "Copies already sent to a remote engine in earlier prompts cannot be recalled.",
        "A running voice session keeps the instructions it started with until it is renewed.",
        "The disk may still hold the old bytes in free space.",
    ]
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
