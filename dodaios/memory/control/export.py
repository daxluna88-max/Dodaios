"""Consistent export of THIS product's memory vault as a zip (ADR-0061).

Own product only: the manifest names the product, and a vault that sits inside the other
product's data directory is refused. What is in: live Markdown pages and the learned notebooks
that live in the vault. What is out, and listed as such in the manifest: ``_archive``,
``attachments``, the trash, dot-folders and dot-files (ledgers, locks), search and embedding
indexes, caches, backups, credentials. Each file is hashed; a file that changed while it was
being read is read again once and otherwise named in ``changed_during_export``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Final

MAX_EXPORT_BYTES: Final[int] = 200 * 1024 * 1024
_SKIP_TOP: Final[frozenset[str]] = frozenset({"_archive", "attachments", ".trash"})


class ExportError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def check_own_product(vault_root: Path, product: str | None) -> None:
    """Refuse a vault that lives inside the OTHER product's data directory."""
    if product not in ("work", "consumer"):
        return
    other = "consumer" if product == "work" else "work"
    for part in vault_root.resolve().parts:
        if part.endswith(f"-{other}"):
            raise ExportError(
                f"This vault belongs to the {other} product; it is not exported from {product}.",
                status=403,
            )


def _read_stable(path: Path) -> tuple[bytes, bool]:
    for _ in range(2):
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size):
            return data, True
    return data, False


def build_export(
    vault_root: Path, *, product: str | None, backups: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    check_own_product(vault_root, product)
    root = vault_root.resolve()
    if not root.is_dir():
        raise ExportError("The memory vault is not set up yet.", status=404)
    files: list[dict[str, Any]] = []
    changed: list[str] = []
    total = 0
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for current, dirs, names in os.walk(root):
            here = Path(current)
            dirs[:] = sorted(
                d for d in dirs
                if not d.startswith(".") and not (here == root and d in _SKIP_TOP)
            )
            for name in sorted(names):
                path = here / name
                if name.startswith(".") or not name.lower().endswith(".md"):
                    continue
                if path.is_symlink() or not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                try:
                    data, stable = _read_stable(path)
                except OSError:
                    continue
                total += len(data)
                if total > MAX_EXPORT_BYTES:
                    raise ExportError("The memory is too large to export in one file.", status=413)
                if not stable:
                    changed.append(rel)
                archive.writestr(f"memory/{rel}", data)
                files.append({"path": rel, "bytes": len(data),
                              "sha256": hashlib.sha256(data).hexdigest()})
        manifest: dict[str, Any] = {
            "format": "dodaios-memory-export-1",
            "product": product or "shared",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "files": files,
            "changed_during_export": changed,
            "not_included": [
                "archived pages (_archive)", "attachments", "trash", "dot files and folders",
                "search and embedding indexes", "caches", "backups", "credentials",
            ],
            "backups": backups or {},
            "note": (
                "This file is a copy. Deleting memory later does not change this export; "
                "keep or delete it yourself."
            ),
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buffer.getvalue(), manifest
