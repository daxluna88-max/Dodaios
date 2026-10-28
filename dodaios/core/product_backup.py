"""Pre-update backup and restore of ONE product's data directory (ADR-0064).

``relauncher.snapshot_user_state`` saves files of the INSTALL folder. Since ADR-0054 the
user's data of a product lives in its own sibling directory ``<root>-<product>``, which that
snapshot never looked at. This module closes the gap without ever reaching the other product
or the shared directory:

* the backup lives INSIDE the product's own directory (``.pre-update-backup/<stamp>/``), so it
  is removed together with the product by ``--product <name> --uninstall`` and is never
  written into, or read from, anything the other product owns;
* only the top-level regular files of the directory are saved. SQLite stores (``*.sqlite``,
  ``*.db``, ``*.sqlite3``) are copied with SQLite's ONLINE BACKUP API from a read-only source
  connection, so the copy is a coherent database even while it is being written; their
  ``-wal``/``-shm``/``-journal`` sidecars are NEVER copied. Other files are copied normally;
* the manifest records COVERAGE and every OMISSION with its reason (size cap, unreadable, link,
  directory, sidecar, ownership/lock). Nothing is skipped silently. A backup is never described
  as a complete data rollback: sub-directories (wiki, skills, logs, models) are not included;
* a snapshot is "verified" only if every SQLite store was backed up, passed an integrity check
  and every file hash was re-read; any doubt is a FAILURE, which the update path must treat as a
  refusal (see ``relauncher.finalize_pending_update``) unless the owner overrides it explicitly;
* restore refuses unless the directory is that product's canonical one, the marker names it,
  and its launch lock can be taken (the product is not running). It STAGES every input first
  (exclusive-create temp files inside the verified product directory, hashes checked on the
  staged copies), then swaps under a JOURNAL and rolls back on error. The destination's
  ``-wal``/``-shm``/``-journal`` sidecars are moved away together with its database, so a stale
  WAL can never pair with a restored database. Prior copies are kept as ``*.before-restore``
  (numbered, never overwritten). The final message states exactly what changed.

Standard library plus the product profile. Nothing here runs at import. Written statically,
NOT run (docs/adr/0064-desktop-packaging-readiness.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from dodaios.core import product_profile as pp

log = logging.getLogger(__name__)

BACKUP_DIR_NAME = ".pre-update-backup"
MANIFEST_NAME = "manifest.json"
KEEP = 3
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
#: Never saved or restored: ownership and locking belong to the running product.
_SKIP_NAMES = frozenset({pp.MARKER_NAME, ".product.lock", pp.LAUNCH_LOCK_NAME})
#: 0 done; 3 refused BEFORE any change (nothing was touched); 4 the data state is final and good
#: but the journal could not be removed (see the report); 5 failed AFTER files were changed (the
#: report says what was restored / rolled back / left behind).
EXIT_OK, EXIT_REFUSED, EXIT_CLEANUP_PENDING, EXIT_FAILED = 0, 3, 4, 5


#: SQLite stores are backed up with the online backup API; these sidecars are never copied.
_DB_SUFFIXES = (".sqlite", ".db", ".sqlite3")
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
MANIFEST_SCHEMA = 2
RESTORE_JOURNAL_PREFIX = ".restore-journal-"
RESTORE_TEMP_PREFIX = ".restore-"
JOURNAL_SCHEMAS = (1, 2)  # 1: block 17 (no terminal state, read as interrupted); 2: block 18
JOURNAL_STATES = ("swapping", "committed", "rolled_back")
ITEM_STATES = ("planned", "saving", "installing", "installed")
MAX_JOURNAL_BYTES = 1024 * 1024
MAX_JOURNAL_ITEMS = 4096
_JOURNAL_NAME_RE = re.compile(
    r"\.restore-journal-(?P<token>[A-Za-z0-9][A-Za-z0-9._-]{0,127})\.json"
)
#: Environment variable the owner sets (``jarvis update --allow-unbacked-update`` does it) to let
#: an update run although no verified pre-update backup could be made. Always recorded.
ALLOW_UNBACKED_ENV = "DODAIOS_ALLOW_UNBACKED_UPDATE"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_database_name(name: str) -> bool:
    return name.lower().endswith(_DB_SUFFIXES)


def _is_sidecar_name(name: str) -> bool:
    low = name.lower()
    return any(
        low.endswith(suf) and is_database_name(low[: -len(suf)]) for suf in _SIDECAR_SUFFIXES
    )


def _is_link(path: Path) -> bool:
    return path.is_symlink() or pp.is_directory_link(path)


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #
@dataclass
class SnapshotResult:
    """Outcome of a pre-update snapshot.

    ``status``: ``"not_scoped"`` (no product active: nothing to back up here), ``"ok"`` (a
    VERIFIED backup of the covered files, or nothing to back up) or ``"failed"``. ``complete``
    is True only when nothing at all was omitted; sub-directories always make it False, so a
    backup is never a complete data rollback.
    """

    status: str
    path: Path | None = None
    reason: str = ""
    complete: bool = False
    omissions: list[dict[str, str]] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.status == "ok"


def _plan(data_dir: Path) -> tuple[list[tuple[Path, str]], list[dict[str, str]]]:
    """What to save (``(path, "sqlite"|"copy")``) and EVERY omission with its reason."""
    copies: list[tuple[Path, str]] = []
    omitted: list[dict[str, str]] = []
    total = 0

    def skip(name: str, reason: str, *, database: bool = False) -> None:
        item = {"name": name, "reason": reason}
        if database:
            item["database"] = "yes"
        omitted.append(item)

    for entry in sorted(data_dir.iterdir()):
        name = entry.name
        if name == BACKUP_DIR_NAME:
            skip(name, "the backup folder itself")
        elif name in _SKIP_NAMES or name.startswith(".product."):
            skip(name, "ownership/lock file, never saved")
        elif name.startswith(RESTORE_TEMP_PREFIX):
            skip(name, "restore working file, never saved")
        elif _is_link(entry):
            skip(name, "link, never followed", database=is_database_name(name))
        elif entry.is_dir():
            skip(name, "directory: only top-level files are saved")
        elif not entry.is_file():
            skip(name, "not a regular file")
        elif _is_sidecar_name(name):
            skip(name, "SQLite sidecar: covered by the online backup of its database")
        else:
            try:
                size = entry.stat().st_size
            except OSError as exc:
                skip(name, f"unreadable: {exc}", database=is_database_name(name))
                continue
            is_db = is_database_name(name)
            if size > MAX_FILE_BYTES:
                skip(name, f"size cap: file above {MAX_FILE_BYTES} bytes", database=is_db)
            elif total + size > MAX_TOTAL_BYTES:
                skip(name, f"size cap: total above {MAX_TOTAL_BYTES} bytes", database=is_db)
            else:
                total += size
                copies.append((entry, "sqlite" if is_database_name(name) else "copy"))
    return copies, omitted


def _backup_sqlite(src: Path, dst: Path) -> None:
    """Coherent copy of ``src`` into ``dst`` with the online backup API (read-only source)."""
    source = sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        try:
            source.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            log.info("checkpoint of %s skipped (read-only connection)", src.name)
        dest = sqlite3.connect(str(dst))
        try:
            source.backup(dest)
            dest.commit()
            if dest.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError(f"integrity check of the backup of {src.name} failed")
            dest.execute("PRAGMA journal_mode=DELETE")
        finally:
            dest.close()
    finally:
        source.close()
    for suffix in _SIDECAR_SUFFIXES:
        Path(str(dst) + suffix).unlink(missing_ok=True)


def snapshot_product_state_checked(env: dict[str, str] | None = None) -> SnapshotResult:
    """Back up the ACTIVE product's data directory and say whether the backup is verified.

    The product comes from the inherited environment and is validated whole
    (``validate_inherited``). Never raises. ``failed`` also covers a SQLite store that could not
    be backed up (including one omitted by a size cap): recovery depends on it.
    """
    target: Path | None = None
    try:
        source_env = os.environ if env is None else env
        name = pp.active_product(source_env)
        if name is None:
            return SnapshotResult("not_scoped", reason="no product is active")
        paths = pp.validate_inherited(name, source_env)
        base = paths.data_dir / BACKUP_DIR_NAME
        if _is_link(base):
            return SnapshotResult("failed", reason=f"{base} is a link")
        copies, omitted = _plan(paths.data_dir)
        lost = [o["name"] for o in omitted if o.get("database")]
        if lost:
            return SnapshotResult(
                "failed", reason="SQLite store(s) not saved: " + ", ".join(lost), omissions=omitted
            )
        if not copies:
            return SnapshotResult("ok", reason="nothing to back up", omissions=omitted)
        target = base / time.strftime("%Y%m%d-%H%M%S")
        target.mkdir(parents=True, exist_ok=False)
        entries: dict[str, dict[str, object]] = {}
        for src, kind in copies:
            dst = target / src.name
            if kind == "sqlite":
                _backup_sqlite(src, dst)
            else:
                shutil.copy2(src, dst)
            try:
                os.chmod(dst, 0o600)  # credential fallback files; no-op on Windows
            except OSError:
                pass
            entries[src.name] = {"bytes": dst.stat().st_size, "sha256": _sha256(dst), "kind": kind}
        for leftover in target.iterdir():
            if leftover.name not in entries:
                raise OSError(f"unexpected file in the backup: {leftover.name}")
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "product": name,
            "created": target.name,
            "files": entries,
            "omissions": omitted,
            "coverage": {
                "scope": "top-level files of the product directory only",
                "sqlite": "online backup API, no -wal/-shm/-journal copied",
                "complete_data_rollback": False,
                "note": "NOT a full data rollback: sub-directories are not included"
                if any(o["reason"].startswith("directory") for o in omitted)
                else "see omissions",
                "nothing_omitted": not omitted,
            },
        }
        tmp = target / (MANIFEST_NAME + ".tmp")
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(tmp, target / MANIFEST_NAME)
        _prune(base)
        return SnapshotResult("ok", path=target, complete=not omitted, omissions=omitted)
    except Exception as exc:  # noqa: BLE001 - reported as a failed snapshot, never raised
        log.warning("pre-update product backup failed", exc_info=True)
        if target is not None:
            shutil.rmtree(target, ignore_errors=True)  # no half backup is ever listed
        return SnapshotResult("failed", reason=f"{type(exc).__name__}: {exc}")


def snapshot_product_state(env: dict[str, str] | None = None) -> Path | None:
    """Compatibility wrapper: the verified backup folder, else ``None`` (unscoped or failed).

    Callers that must not continue without a backup use ``snapshot_product_state_checked``.
    """
    result = snapshot_product_state_checked(env)
    return result.path if result.verified else None


def _prune(base: Path) -> None:
    try:
        stamps = sorted(
            (p for p in base.iterdir() if p.is_dir() and not p.is_symlink()), reverse=True
        )
        for old in stamps[KEEP:]:
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass


def list_backups(data_dir: Path) -> list[str]:
    base = data_dir / BACKUP_DIR_NAME
    try:
        found = (p.name for p in base.iterdir() if (p / MANIFEST_NAME).is_file())
        return sorted(found, reverse=True)
    except OSError:
        return []


def _product_dir(product: str) -> Path:
    name = pp.validate_product(product)
    for shared in pp._shared_roots():  # noqa: SLF001 - same roots the profile itself uses
        candidate = pp.paths_for(name, shared).data_dir
        if candidate.is_dir():
            return candidate
    raise pp.ProductError(f"no {name} data directory exists: nothing to restore into")


# --------------------------------------------------------------------------- #
# Restore: stage, verify, journalled swap, rollback
# --------------------------------------------------------------------------- #
@dataclass
class RestoreReport:
    source: Path | None = None
    restored: list[str] = field(default_factory=list)
    rolled_back: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)
    kept_prior: list[str] = field(default_factory=list)
    untouched: list[str] = field(default_factory=list)
    rollback_errors: list[str] = field(default_factory=list)
    journal: Path | None = None
    coverage_note: str = ""
    #: ``committed`` (new files in place), ``rolled_back`` (previous files back) or
    #: ``interrupted``/``unknown``; ``journal_left`` + ``cleanup_errors`` say what stayed behind.
    final_state: str = ""
    journal_left: list[str] = field(default_factory=list)
    cleanup_errors: list[str] = field(default_factory=list)

    def describe(self) -> str:
        def one(label: str, items: list[str]) -> str:
            return f"  {label}: {', '.join(items) if items else '(none)'}"

        tail = []
        if self.final_state:
            tail.append(f"  final data state: {self.final_state}")
        if self.journal_left:
            tail.append(f"  journal left behind: {', '.join(self.journal_left)}")
        if self.cleanup_errors:
            tail.append(f"  cleanup errors: {'; '.join(self.cleanup_errors)}")
        if self.rollback_errors:
            tail.append(f"  rollback errors: {'; '.join(self.rollback_errors)}")
        return "\n".join([
            one("restored", self.restored),
            one("rolled back", self.rolled_back),
            one("moved away (sidecars, previous files kept)", self.quarantined + self.kept_prior),
            one("left untouched", self.untouched),
        ] + tail + ([f"  coverage: {self.coverage_note}"] if self.coverage_note else []))


class JournalInvalid(pp.ProductError):
    """A restore journal failed validation; raised BEFORE any recovery mutation."""

    def __init__(self, journal: str, field_name: str, why: str) -> None:
        super().__init__(
            f"restore journal {journal} is invalid at {field_name}: {why}. "
            "Recovery refused; nothing was changed"
        )
        self.journal, self.field = journal, field_name


class RestoreFailed(pp.ProductError):
    """A restore that failed AFTER it started changing files; ``report`` says exactly what."""

    def __init__(self, message: str, report: RestoreReport) -> None:
        super().__init__(message)
        self.report = report


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _unique(data_dir: Path, base: str, reserved: set[str]) -> str:
    """``base`` or ``base.N``: a name that exists nowhere and is not reserved. Never overwrites."""
    candidate, n = base, 0
    while candidate in reserved or _lexists(data_dir / candidate):
        n += 1
        candidate = f"{base}.{n}"
    reserved.add(candidate)
    return candidate


def _fsync_dir(path: Path) -> None:
    """Best effort: make a rename durable (not possible on Windows)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_journal(path: Path, data: dict[str, object]) -> None:
    """Atomic, durable rewrite: exclusive temp file, fsync, rename, fsync of the directory."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _persist_state(path: Path, journal: dict, state: str) -> str | None:
    """Write the journal's terminal state durably. Returns an error text, never raises."""
    journal["state"] = state
    try:
        _write_journal(path, journal)
    except OSError as exc:
        return f"could not record state {state!r} in {path.name}: {exc}"
    return None


def _finish_journal(path: Path, report: RestoreReport) -> None:
    """Remove a journal whose data state is final; a failure is REPORTED, never hidden."""
    try:
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".tmp").unlink(missing_ok=True)
    except OSError as exc:
        report.journal_left.append(path.name)
        report.cleanup_errors.append(f"{path.name} could not be removed: {exc}")


# --------------------------------------------------------------------------- #
# Journal validation: a journal is DATA from disk, never trusted instructions
# --------------------------------------------------------------------------- #
def _single_name(journal: str, where: str, value: object) -> str:
    """A canonical single path component: no separator, drive, '..', NUL or control character."""
    if not isinstance(value, str) or not value:
        raise JournalInvalid(journal, where, "must be a non-empty string")
    bad = (
        "/" in value or "\\" in value or ":" in value or "\x00" in value
        or value in (".", "..") or value != value.strip() or Path(value).name != value
        or Path(value).is_absolute() or any(ord(c) < 32 for c in value)
    )
    if bad or len(value) > 200:
        raise JournalInvalid(journal, where, f"{value!r} is not a canonical single file name")
    return value


def _exact_keys(journal: str, where: str, obj: object, keys: set[str]) -> dict:
    if not isinstance(obj, dict):
        raise JournalInvalid(journal, where, "must be an object")
    if set(obj) != keys:
        raise JournalInvalid(journal, where, f"keys must be exactly {sorted(keys)}")
    return obj


def _read_journal_file(data_dir: Path, path: Path) -> object:
    jname = path.name
    if path.parent != data_dir or not _JOURNAL_NAME_RE.fullmatch(jname):
        raise JournalInvalid(jname, "file name", "not a generated journal name in the data dir")
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise JournalInvalid(jname, "file", f"cannot be examined: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or _is_link(path):
        raise JournalInvalid(jname, "file", "must be a regular, non-linked file")
    if info.st_nlink != 1:
        raise JournalInvalid(jname, "file", "has several hard links")
    if info.st_size > MAX_JOURNAL_BYTES:
        raise JournalInvalid(jname, "file", "is too large")
    try:
        fd = _open_nofollow(path)
        with os.fdopen(fd, "rb") as handle:
            after = os.fstat(handle.fileno())
            if not stat.S_ISREG(after.st_mode) or (after.st_ino, after.st_dev) != (
                info.st_ino, info.st_dev
            ):
                raise JournalInvalid(jname, "file", "changed while it was opened")
            raw = handle.read(MAX_JOURNAL_BYTES + 1)
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
        raise JournalInvalid(jname, "file", f"cannot be read as JSON: {exc}") from exc


def _confined(jname: str, where: str, data_dir: Path, real_data: Path, name: str) -> None:
    """``data_dir/name`` must sit directly in the verified data dir, and not be a link."""
    candidate = data_dir / name
    try:
        if _lexists(candidate):
            if _is_link(candidate):
                raise JournalInvalid(jname, where, f"{name!r} is a link")
            resolved = candidate.resolve(strict=True)
            if resolved.parent != real_data or resolved.name != name:
                raise JournalInvalid(jname, where, f"{name!r} resolves outside the data dir")
        elif candidate.parent.resolve(strict=True) != real_data:
            raise JournalInvalid(jname, where, f"{name!r} would be outside the data dir")
    except (OSError, RuntimeError) as exc:
        raise JournalInvalid(jname, where, f"{name!r} cannot be resolved: {exc}") from exc


def _prior_ok(base: str, value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(
        re.escape(base) + r"\.before-restore(\.[1-9][0-9]{0,5})?", value
    ) is not None


def _validate_journal(data_dir: Path, path: Path, product: str) -> dict:
    """Validate ONE journal completely; return it. Raises ``JournalInvalid``, mutates nothing."""
    jname = path.name
    match = _JOURNAL_NAME_RE.fullmatch(jname)
    if match is None:
        raise JournalInvalid(jname, "file name", "not a generated journal name")
    token = match.group("token")
    if _is_link(data_dir):
        raise JournalInvalid(jname, "data dir", "is a link")
    try:
        real_data = data_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise JournalInvalid(jname, "data dir", f"cannot be resolved: {exc}") from exc
    data = _read_journal_file(data_dir, path)
    top = {"schema", "product", "stamp", "items"}
    if isinstance(data, dict) and data.get("schema") == 2:
        top = top | {"state"}
    journal = _exact_keys(jname, "journal", data, top)
    if journal["schema"] not in JOURNAL_SCHEMAS or isinstance(journal["schema"], bool):
        raise JournalInvalid(jname, "schema", f"unsupported {journal['schema']!r}")
    if journal["product"] != product:
        other = journal["product"]
        raise JournalInvalid(jname, "product", f"belongs to {other!r}, not {product!r}")
    stamp = journal["stamp"]
    if not isinstance(stamp, str) or len(stamp) > 64 or "/" in stamp or "\\" in stamp:
        raise JournalInvalid(jname, "stamp", "must be a short string without separators")
    state = journal.get("state", "swapping")
    if state not in JOURNAL_STATES:
        raise JournalInvalid(jname, "state", f"{state!r} is not one of {list(JOURNAL_STATES)}")
    journal["state"] = state
    items = journal["items"]
    if not isinstance(items, list) or len(items) > MAX_JOURNAL_ITEMS:
        raise JournalInvalid(jname, "items", "must be a list of reasonable size")
    seen: set[str] = set()

    def claim(where: str, name: str) -> None:
        key = name.lower()  # case-insensitive file systems: 'A' and 'a' are one file
        if key in seen:
            raise JournalInvalid(jname, where, f"{name!r} is used twice in the journal")
        seen.add(key)

    for idx, raw in enumerate(items):
        where = f"items[{idx}]"
        item = _exact_keys(jname, where, raw, {"name", "staged", "prior", "sidecars", "state"})
        fname = _single_name(jname, f"{where}.name", item["name"])
        unsafe = fname in _SKIP_NAMES or fname.startswith(RESTORE_TEMP_PREFIX)
        if unsafe or fname == BACKUP_DIR_NAME or _is_sidecar_name(fname):
            raise JournalInvalid(jname, f"{where}.name", f"{fname!r} is not a restorable name")
        if item["state"] not in ITEM_STATES:
            raise JournalInvalid(jname, f"{where}.state", f"{item['state']!r} is not allowed")
        staged = _single_name(jname, f"{where}.staged", item["staged"])
        pattern = re.escape(f"{RESTORE_TEMP_PREFIX}{token}-") + r"[0-9]{1,6}\.tmp"
        if not re.fullmatch(pattern, staged):
            why = f"{staged!r} is not a generated temp name"
            raise JournalInvalid(jname, f"{where}.staged", why)
        claim(f"{where}.name", fname)
        claim(f"{where}.staged", staged)
        _confined(jname, f"{where}.name", data_dir, real_data, fname)
        _confined(jname, f"{where}.staged", data_dir, real_data, staged)
        prior = item["prior"]
        if prior is not None:
            _single_name(jname, f"{where}.prior", prior)
            if not _prior_ok(fname, prior):
                raise JournalInvalid(jname, f"{where}.prior", f"{prior!r} is not a generated name")
            claim(f"{where}.prior", prior)
            _confined(jname, f"{where}.prior", data_dir, real_data, prior)
        sides = item["sidecars"]
        if not isinstance(sides, list) or len(sides) > len(_SIDECAR_SUFFIXES):
            raise JournalInvalid(jname, f"{where}.sidecars", "must be a short list")
        if sides and not is_database_name(fname):
            raise JournalInvalid(jname, f"{where}.sidecars", "only a database has sidecars")
        for sidx, rawside in enumerate(sides):
            swhere = f"{where}.sidecars[{sidx}]"
            side = _exact_keys(jname, swhere, rawside, {"name", "prior"})
            sname = _single_name(jname, f"{swhere}.name", side["name"])
            if sname not in {fname + suf for suf in _SIDECAR_SUFFIXES}:
                why = f"{sname!r} is not a sidecar of {fname!r}"
                raise JournalInvalid(jname, f"{swhere}.name", why)
            sprior = _single_name(jname, f"{swhere}.prior", side["prior"])
            if not _prior_ok(sname, sprior):
                why = f"{sprior!r} is not a generated name"
                raise JournalInvalid(jname, f"{swhere}.prior", why)
            claim(f"{swhere}.name", sname)
            claim(f"{swhere}.prior", sprior)
            _confined(jname, f"{swhere}.name", data_dir, real_data, sname)
            _confined(jname, f"{swhere}.prior", data_dir, real_data, sprior)
    return journal


def _rollback(data_dir: Path, journal: dict, report: RestoreReport) -> None:
    """Undo, in reverse, every replacement the journal started. Collects errors, never raises."""
    for item in reversed(journal["items"]):
        fname = item["name"]
        try:
            prior = item.get("prior")
            dst = data_dir / fname
            touched = item["state"] != "planned"
            if prior and _lexists(data_dir / prior):
                os.replace(data_dir / prior, dst)
            elif prior is None and _lexists(dst) and item["state"] in ("installing", "installed"):
                dst.unlink()
            for side in item.get("sidecars", []):
                back, orig = data_dir / side["prior"], data_dir / side["name"]
                if _lexists(back) and not _lexists(orig):
                    os.replace(back, orig)
            if touched:
                report.rolled_back.append(fname)
                if fname in report.restored:
                    report.restored.remove(fname)
        except OSError as exc:
            report.rollback_errors.append(f"{fname}: {exc}")


def _remove_staged(data_dir: Path, journal: dict) -> None:
    for item in journal["items"]:
        try:
            (data_dir / item["staged"]).unlink(missing_ok=True)
        except OSError:
            pass


def _open_nofollow(path: Path) -> int:
    return os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))


def _stage(source: Path, data_dir: Path, fname: str, meta: dict, staged_name: str) -> None:
    """Copy one backup file into an exclusive temp file and verify its hash on the COPY."""
    src = source / fname
    if src.resolve().parent != source.resolve():
        raise pp.ProductError(f"backup file {fname} resolves outside the backup folder")
    digest = hashlib.sha256()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    out_fd = os.open(data_dir / staged_name, flags, 0o600)  # O_EXCL: also refuses a planted link
    with os.fdopen(out_fd, "wb") as out, os.fdopen(_open_nofollow(src), "rb") as inp:
        for chunk in iter(lambda: inp.read(1024 * 1024), b""):
            digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    if digest.hexdigest() != meta.get("sha256"):
        raise pp.ProductError(f"backup file {fname} is altered (hash mismatch): nothing restored")
    staged = data_dir / staged_name
    if _sha256(staged) != meta.get("sha256"):
        raise pp.ProductError(f"staged copy of {fname} does not match its hash: nothing restored")


def _stale_journals(data_dir: Path) -> list[Path]:
    return sorted(p for p in data_dir.glob(RESTORE_JOURNAL_PREFIX + "*.json"))


def _locked_paths(name: str) -> tuple[Path, pp.ProductPaths]:
    data_dir = _product_dir(name)
    pp.check_product_root(name, data_dir, canonical=True)
    if pp._read_owner(data_dir / pp.MARKER_NAME) != name:  # noqa: SLF001
        raise pp.ProductError(f"{data_dir} is not claimed by the {name} product")
    paths = pp.ProductPaths(
        name, data_dir, data_dir / pp.CONFIG_FILE_NAME, f"{pp.SERVICE_PREFIX}{name}"
    )
    return data_dir, paths


def restore_product_state(product: str, stamp: str | None = None) -> RestoreReport:
    """Restore ``stamp`` (default: the newest) into ``product``'s directory, or raise.

    ``ProductError`` before anything is changed (non-canonical/linked directory or backup
    parent, marker mismatch, running product, other product's manifest, missing or altered
    file, linked temp, unfinished earlier restore). ``RestoreFailed`` (with a report) if the
    swap started and failed: earlier replacements are rolled back and the report lists what was
    restored, rolled back and left untouched. Only manifest files are written, only inside
    this product's directory; the other product and the shared directory are never opened.
    """
    name = pp.validate_product(product)
    data_dir, paths = _locked_paths(name)
    chosen = stamp or next(iter(list_backups(data_dir)), None)
    if not chosen or "/" in chosen or "\\" in chosen or chosen.startswith("."):
        raise pp.ProductError(f"no usable backup in {data_dir / BACKUP_DIR_NAME}")
    base = data_dir / BACKUP_DIR_NAME
    source = base / chosen
    if _is_link(base) or _is_link(source) or not source.is_dir():
        raise pp.ProductError(f"{source} is not a backup directory (or a parent is a link)")
    real_data = data_dir.resolve()
    if source.resolve().parent.parent != real_data:
        raise pp.ProductError(f"{source} does not resolve inside {data_dir}")
    manifest = json.loads((source / MANIFEST_NAME).read_text(encoding="utf-8"))
    if manifest.get("product") != name:
        raise pp.ProductConflict(f"backup {chosen} belongs to {manifest.get('product')!r}")
    files = manifest.get("files") or {}
    for fname, meta in files.items():
        unsafe = fname in _SKIP_NAMES or Path(fname).name != fname
        unsafe = unsafe or fname.startswith(RESTORE_TEMP_PREFIX) or fname == BACKUP_DIR_NAME
        if unsafe or _is_sidecar_name(fname):
            raise pp.ProductError(f"backup lists an unsafe file name {fname!r}")
        item = source / fname
        if _is_link(item) or not item.is_file() or _sha256(item) != meta.get("sha256"):
            raise pp.ProductError(f"backup file {fname} is missing or altered: nothing restored")
        dst = data_dir / fname
        if _lexists(dst) and (_is_link(dst) or not dst.is_file()):
            raise pp.ProductError(
                f"destination {fname} is a link or not a regular file: nothing restored"
            )
    coverage = manifest.get("coverage") or {}
    report = RestoreReport(
        source=source,
        coverage_note=str(coverage.get("note", "")) or "coverage unknown (older manifest)",
    )
    lock = pp.acquire_launch_lock(paths)  # raises AlreadyRunning: the product must be closed
    try:
        stale = _stale_journals(data_dir)
        if stale:
            raise pp.ProductError(
                f"an earlier restore did not finish ({stale[0].name}); run "
                f"'recover --product {name}' first. Nothing was changed."
            )
        token = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{os.urandom(4).hex()}"
        journal_path = data_dir / f"{RESTORE_JOURNAL_PREFIX}{token}.json"
        reserved: set[str] = set()
        items: list[dict] = []
        for idx, fname in enumerate(files):
            staged_name = f"{RESTORE_TEMP_PREFIX}{token}-{idx}.tmp"
            if _lexists(data_dir / staged_name):
                raise pp.ProductError(f"temp name {staged_name} already exists: nothing restored")
            reserved.add(staged_name)
            prior = None
            if _lexists(data_dir / fname):
                prior = _unique(data_dir, fname + ".before-restore", reserved)
            sidecars = []
            if is_database_name(fname):
                for suffix in _SIDECAR_SUFFIXES:
                    if _lexists(data_dir / (fname + suffix)):
                        sidecars.append({
                            "name": fname + suffix,
                            "prior": _unique(
                                data_dir, fname + suffix + ".before-restore", reserved
                            ),
                        })
            items.append({"name": fname, "staged": staged_name, "prior": prior,
                          "sidecars": sidecars, "state": "planned"})
        journal: dict = {
            "schema": 2, "product": name, "stamp": chosen, "state": "swapping", "items": items,
        }
        # 1. stage + verify everything; any failure leaves only removable temp files behind
        try:
            for item in items:
                _stage(source, data_dir, item["name"], files[item["name"]], item["staged"])
        except (OSError, pp.ProductError) as exc:
            _remove_staged(data_dir, journal)
            if isinstance(exc, pp.ProductError):
                raise
            raise pp.ProductError(
                f"could not stage the backup ({exc}); temp files removed"
            ) from exc
        # 2. journalled swap. Writing the first journal is still BEFORE any data change.
        report.journal = journal_path
        try:
            _write_journal(journal_path, journal)
        except OSError as exc:
            _remove_staged(data_dir, journal)
            for leftover in (journal_path, journal_path.with_name(journal_path.name + ".tmp")):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass
            raise pp.ProductError(
                f"could not write the restore journal ({exc}); temp files removed"
            ) from exc

        def undo(cause: str, exc: BaseException) -> RestoreFailed:
            """Roll back, record the terminal state, clean up; the report says what is left."""
            report.quarantined.clear()
            report.kept_prior.clear()
            _rollback(data_dir, journal, report)
            _remove_staged(data_dir, journal)
            if report.rollback_errors:
                report.untouched = []
                report.final_state = "interrupted: rollback incomplete, data may be inconsistent"
                report.journal_left.append(journal_path.name)
                return RestoreFailed(
                    f"restore failed ({cause}) AND the rollback is incomplete "
                    f"({'; '.join(report.rollback_errors)}). Journal {journal_path.name} is kept: "
                    f"run 'recover --product {name}'. The data directory may be inconsistent",
                    report,
                )
            report.final_state = "rolled_back"
            problem = _persist_state(journal_path, journal, "rolled_back")
            if problem:
                report.cleanup_errors.append(problem)
            _finish_journal(journal_path, report)
            report.untouched = [i["name"] for i in items if i["name"] not in report.rolled_back]
            left = (
                f"; journal {journal_path.name} could not be removed (recover only cleans it)"
                if report.journal_left else ""
            )
            return RestoreFailed(
                f"restore failed ({cause}); every replacement was rolled back "
                f"({len(report.rolled_back)} file(s)), so the data files are as before it "
                f"started{left}",
                report,
            )

        try:
            for item in items:
                fname = item["name"]
                item["state"] = "saving"
                _write_journal(journal_path, journal)
                for side in item["sidecars"]:
                    os.replace(data_dir / side["name"], data_dir / side["prior"])
                    report.quarantined.append(f"{side['name']} -> {side['prior']}")
                if item["prior"]:
                    os.replace(data_dir / fname, data_dir / item["prior"])
                    report.kept_prior.append(f"{fname} -> {item['prior']}")
                item["state"] = "installing"
                _write_journal(journal_path, journal)
                os.replace(data_dir / item["staged"], data_dir / fname)
                item["state"] = "installed"
                report.restored.append(fname)
                _write_journal(journal_path, journal)
        except OSError as exc:
            raise undo(str(exc), exc) from exc
        # 3. every file is installed: make the outcome durable BEFORE any cleanup, so a later
        #    cleanup failure can never make `recover` roll back a finished restore.
        problem = _persist_state(journal_path, journal, "committed")
        if problem:
            # Cannot prove the commit on disk: undo it so journal and data agree.
            raise undo(problem, OSError(problem))
        report.final_state = "committed"
        _remove_staged(data_dir, journal)
        _finish_journal(journal_path, report)
        report.untouched = ["everything not in the backup manifest (sub-directories included)"]
        return report
    finally:
        lock.release()


def recover_product_state(product: str) -> RestoreReport:
    """Settle every journal left by an unfinished restore; lock required.

    PHASE 1 validates ALL journals and EVERY path in them (schema, names, generated patterns,
    states, duplicates, confinement, links) and mutates nothing: one invalid journal refuses the
    whole recovery (``JournalInvalid``). PHASE 2 acts per terminal state: ``committed`` only
    removes the journal (never rolls back), ``rolled_back`` only cleans, ``swapping`` rolls back.
    Any error in phase 2 is a ``RestoreFailed`` whose report states the real data state.
    """
    name = pp.validate_product(product)
    data_dir, paths = _locked_paths(name)
    lock = pp.acquire_launch_lock(paths)
    try:
        report = RestoreReport()
        plans = [
            (path, _validate_journal(data_dir, path, name))
            for path in _stale_journals(data_dir)
        ]
        try:
            for path, journal in plans:
                state = journal["state"]
                if state == "swapping":
                    _rollback(data_dir, journal, report)
                    _remove_staged(data_dir, journal)
                    if report.rollback_errors:
                        report.final_state = "interrupted: rollback incomplete"
                        report.journal_left.append(path.name)
                        raise RestoreFailed(
                            f"recovery incomplete: {'; '.join(report.rollback_errors)}. "
                            f"Journal {path.name} is kept; the data directory may be inconsistent",
                            report,
                        )
                    problem = _persist_state(path, journal, "rolled_back")
                    if problem:
                        report.cleanup_errors.append(problem)
                    report.final_state = "rolled_back"
                else:
                    _remove_staged(data_dir, journal)
                    report.final_state = state
                _finish_journal(path, report)
        except RestoreFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - after the first mutation: never a bare refusal
            report.final_state = report.final_state or "unknown: recovery stopped midway"
            raise RestoreFailed(
                f"recovery stopped by {type(exc).__name__}: {exc}. Some files may already have "
                "been moved back; see the report", report,
            ) from exc
        return report
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    """``python -m dodaios.core.product_backup list|restore|recover --product P [--stamp S]``"""
    import argparse

    parser = argparse.ArgumentParser(prog="product_backup")
    parser.add_argument("action", choices=("list", "restore", "recover"))
    parser.add_argument("--product", required=True)
    parser.add_argument("--stamp")
    args = parser.parse_args(argv)
    try:
        if args.action == "list":
            for stamp in list_backups(_product_dir(args.product)):
                print(stamp)
            return EXIT_OK
        if args.action == "recover":
            report = recover_product_state(args.product)
            print(f"recovery of {args.product} finished\n{report.describe()}")
            if report.journal_left:
                print("WARNING: data state is final but a journal could not be removed",
                      file=sys.stderr)
                return EXIT_CLEANUP_PENDING
            return EXIT_OK
        report = restore_product_state(args.product, args.stamp)
    except RestoreFailed as exc:
        # Raised only AFTER files may have changed: the report is the truth, never "unchanged".
        print(f"FAILED after changes started: {exc}.\n{exc.report.describe()}", file=sys.stderr)
        return EXIT_FAILED
    except (pp.ProductError, OSError, ValueError) as exc:
        # Only reachable from checks that run before any data file or journal is touched.
        print(f"refused before any change: {exc}. No data file was replaced.", file=sys.stderr)
        return EXIT_REFUSED
    name = report.source.name if report.source else "?"
    print(f"restored {args.product} from {name}\n{report.describe()}")
    if report.journal_left:
        print(
            f"WARNING: the restore is COMPLETE and committed, but {', '.join(report.journal_left)} "
            "could not be removed. Run 'recover' to clean it; it will NOT roll anything back.",
            file=sys.stderr,
        )
        return EXIT_CLEANUP_PENDING
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
