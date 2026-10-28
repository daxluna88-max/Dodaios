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
import shutil
import sqlite3
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
EXIT_OK, EXIT_REFUSED = 0, 3


#: SQLite stores are backed up with the online backup API; these sidecars are never copied.
_DB_SUFFIXES = (".sqlite", ".db", ".sqlite3")
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
MANIFEST_SCHEMA = 2
RESTORE_JOURNAL_PREFIX = ".restore-journal-"
RESTORE_TEMP_PREFIX = ".restore-"
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

    def describe(self) -> str:
        def one(label: str, items: list[str]) -> str:
            return f"  {label}: {', '.join(items) if items else '(none)'}"

        return "\n".join([
            one("restored", self.restored),
            one("rolled back", self.rolled_back),
            one("moved away (sidecars, previous files kept)", self.quarantined + self.kept_prior),
            one("left untouched", self.untouched),
        ] + ([f"  coverage: {self.coverage_note}"] if self.coverage_note else []))


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


def _write_journal(path: Path, data: dict[str, object]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


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
        journal: dict = {"schema": 1, "product": name, "stamp": chosen, "items": items}
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
        # 2. journalled swap
        report.journal = journal_path
        _write_journal(journal_path, journal)
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
            report.quarantined.clear()
            report.kept_prior.clear()
            _rollback(data_dir, journal, report)
            _remove_staged(data_dir, journal)
            if report.rollback_errors:
                report.untouched = []
                raise RestoreFailed(
                    f"restore failed ({exc}) AND the rollback is incomplete "
                    f"({'; '.join(report.rollback_errors)}). Journal {journal_path.name} is kept: "
                    f"run 'recover --product {name}'. The data directory may be inconsistent",
                    report,
                ) from exc
            journal_path.unlink(missing_ok=True)
            report.untouched = [i["name"] for i in items if i["name"] not in report.rolled_back]
            raise RestoreFailed(
                f"restore failed ({exc}); every replacement was rolled back "
                f"({len(report.rolled_back)} file(s)), so the data files are as before it started",
                report,
            ) from exc
        journal_path.unlink(missing_ok=True)
        report.untouched = ["everything not in the backup manifest (sub-directories included)"]
        return report
    finally:
        lock.release()


def recover_product_state(product: str) -> RestoreReport:
    """Roll back an unfinished restore from its journal (e.g. after a crash); lock required."""
    name = pp.validate_product(product)
    data_dir, paths = _locked_paths(name)
    lock = pp.acquire_launch_lock(paths)
    try:
        report = RestoreReport()
        for path in _stale_journals(data_dir):
            journal = json.loads(path.read_text(encoding="utf-8"))
            if journal.get("product") != name:
                raise pp.ProductConflict(f"{path.name} belongs to {journal.get('product')!r}")
            _rollback(data_dir, journal, report)
            _remove_staged(data_dir, journal)
            if report.rollback_errors:
                detail = "; ".join(report.rollback_errors)
                raise RestoreFailed(f"recovery incomplete: {detail}", report)
            path.unlink(missing_ok=True)
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
            return EXIT_OK
        report = restore_product_state(args.product, args.stamp)
    except RestoreFailed as exc:
        print(f"FAILED: {exc}.\n{exc.report.describe()}", file=sys.stderr)
        return EXIT_REFUSED
    except (pp.ProductError, OSError, ValueError) as exc:
        # Raised only before any data file was replaced (staging leftovers are removed).
        print(f"refused: {exc}. No data file was replaced.", file=sys.stderr)
        return EXIT_REFUSED
    name = report.source.name if report.source else "?"
    print(f"restored {args.product} from {name}\n{report.describe()}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
