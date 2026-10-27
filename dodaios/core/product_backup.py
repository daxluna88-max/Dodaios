"""Pre-update backup and restore of ONE product's data directory (ADR-0064).

``relauncher.snapshot_user_state`` saves files of the INSTALL folder. Since ADR-0054 the
user's data of a product lives in its own sibling directory ``<root>-<product>``, which that
snapshot never looked at. This module closes the gap without ever reaching the other product
or the shared directory:

* the backup lives INSIDE the product's own directory (``.pre-update-backup/<stamp>/``), so it
  is removed together with the product by ``--product <name> --uninstall`` and is never
  written into, or read from, anything the other product owns;
* only the top-level regular files of the directory are saved (config, credential fallback
  file, SQLite stores), each below a size cap, never links, never the ownership marker or the
  lock files; sub-directories (wiki, skills, logs, models) are NOT included;
* a manifest records product, file sizes and SHA-256; restore verifies all of it first;
* restore refuses unless the directory is that product's canonical one, the marker names it,
  and its launch lock can be taken (the product is not running).

Standard library plus the product profile. Nothing here runs at import. Written statically,
NOT run (docs/adr/0064-desktop-packaging-readiness.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import time
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidates(data_dir: Path) -> list[Path]:
    out: list[Path] = []
    total = 0
    for entry in sorted(data_dir.iterdir()):
        if entry.name in _SKIP_NAMES or entry.name.startswith(".product."):
            continue
        if entry.is_symlink() or not entry.is_file():
            continue
        size = entry.stat().st_size
        if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
            log.warning("pre-update backup skips %s (size cap)", entry.name)
            continue
        total += size
        out.append(entry)
    return out


def snapshot_product_state(env: dict[str, str] | None = None) -> Path | None:
    """Back up the ACTIVE product's data directory; ``None`` when unscoped or on any doubt.

    The product comes from the inherited environment and is validated whole
    (``validate_inherited``). Never raises: an update must proceed (and say so) without it.
    """
    try:
        source = os.environ if env is None else env
        name = pp.active_product(source)
        if name is None:
            return None
        paths = pp.validate_inherited(name, source)
        target = paths.data_dir / BACKUP_DIR_NAME / time.strftime("%Y%m%d-%H%M%S")
        files = _candidates(paths.data_dir)
        if not files:
            return None
        target.mkdir(parents=True, exist_ok=True)
        entries: dict[str, dict[str, object]] = {}
        for src in files:
            dst = target / src.name
            shutil.copy2(src, dst)
            try:
                os.chmod(dst, 0o600)  # credential fallback files; no-op on Windows
            except OSError:
                pass
            entries[src.name] = {"bytes": dst.stat().st_size, "sha256": _sha256(dst)}
        manifest = {"product": name, "created": target.name, "files": entries}
        (target / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        _prune(paths.data_dir / BACKUP_DIR_NAME)
        return target
    except Exception:  # noqa: BLE001 - best effort by design, see the docstring
        log.warning("pre-update product backup failed", exc_info=True)
        return None


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


def restore_product_state(product: str, stamp: str | None = None) -> Path:
    """Restore ``stamp`` (default: the newest) into ``product``'s directory, or raise.

    Refuses (``ProductError``) on: a non-canonical/linked directory, a marker that does not
    name this product, a running product (launch lock held), a manifest of another product,
    a missing or altered backup file. Files replaced are first kept as ``<name>.before-restore``.
    Only files named in the manifest are written, only inside this product's directory.
    """
    name = pp.validate_product(product)
    data_dir = _product_dir(name)
    pp.check_product_root(name, data_dir, canonical=True)
    if pp._read_owner(data_dir / pp.MARKER_NAME) != name:  # noqa: SLF001
        raise pp.ProductError(f"{data_dir} is not claimed by the {name} product")
    chosen = stamp or next(iter(list_backups(data_dir)), None)
    if not chosen or "/" in chosen or "\\" in chosen or chosen.startswith("."):
        raise pp.ProductError(f"no usable backup in {data_dir / BACKUP_DIR_NAME}")
    source = data_dir / BACKUP_DIR_NAME / chosen
    if source.is_symlink() or not source.is_dir():
        raise pp.ProductError(f"{source} is not a backup directory")
    manifest = json.loads((source / MANIFEST_NAME).read_text(encoding="utf-8"))
    if manifest.get("product") != name:
        raise pp.ProductConflict(f"backup {chosen} belongs to {manifest.get('product')!r}")
    files = manifest.get("files") or {}
    for fname, meta in files.items():
        if fname in _SKIP_NAMES or Path(fname).name != fname:
            raise pp.ProductError(f"backup lists an unsafe file name {fname!r}")
        item = source / fname
        if item.is_symlink() or not item.is_file() or _sha256(item) != meta.get("sha256"):
            raise pp.ProductError(f"backup file {fname} is missing or altered: nothing restored")
    paths = pp.ProductPaths(
        name, data_dir, data_dir / pp.CONFIG_FILE_NAME, f"{pp.SERVICE_PREFIX}{name}"
    )
    lock = pp.acquire_launch_lock(paths)  # raises AlreadyRunning: the product must be closed
    try:
        for fname in files:
            dst = data_dir / fname
            if dst.exists() and not dst.is_symlink():
                os.replace(dst, data_dir / (fname + ".before-restore"))
            tmp = data_dir / (fname + ".restoring")
            shutil.copy2(source / fname, tmp)
            os.replace(tmp, dst)
    finally:
        lock.release()
    return source


def main(argv: list[str] | None = None) -> int:
    """``python -m dodaios.core.product_backup list|restore --product work|consumer [--stamp S]``"""
    import argparse

    parser = argparse.ArgumentParser(prog="product_backup")
    parser.add_argument("action", choices=("list", "restore"))
    parser.add_argument("--product", required=True)
    parser.add_argument("--stamp")
    args = parser.parse_args(argv)
    try:
        if args.action == "list":
            for stamp in list_backups(_product_dir(args.product)):
                print(stamp)
            return EXIT_OK
        used = restore_product_state(args.product, args.stamp)
    except (pp.ProductError, OSError, ValueError) as exc:
        print(f"refused: {exc}. Nothing was changed.", file=sys.stderr)
        return EXIT_REFUSED
    print(f"restored {args.product} from {used.name}; previous files kept as *.before-restore")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
