"""Private resumable file storage for the phone companion (no HTTP, no authentication).

Ported from the Jarvis integration repo's ``TransferStore`` with the review fixes: the SHA-256 of a
finishing file is computed outside the database transaction (and under that transfer's own lock,
so a 2 GiB hash never blocks other transfers), the row exists before the blob (a crash leaves an
orphan the sweep removes), the quota counts only live transfers that expire, and the directory is
fsynced after a blob is created.

Ownership and device membership are separate: ``owner`` is the account every file belongs to;
``device_id`` only records which paired device sent it. The adapter decides whether a device is
still a member before every call and passes ``owner`` from its own principal, never from a request.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:  # POSIX advisory file locks; Windows falls back to the in-process lock alone
    import fcntl
except ImportError:  # no fcntl on Windows: the in-process lock alone applies (documented above)
    fcntl = None  # type: ignore[assignment]

DEFAULT_MAX_FILE = 2 * 1024**3
DEFAULT_QUOTA = 10 * 1024**3
DEFAULT_CHUNK = 4 * 1024**2
MAX_TRANSFERS = 1000
#: An unfinished upload nobody touched for this long is deleted (and its reservation released).
INCOMPLETE_TTL_S = 24 * 3600
#: A finished file is kept this long, then deleted.
READY_TTL_S = 14 * 24 * 3600
_HASH_BLOCK = 1024 * 1024
_ID = re.compile(r"[0-9a-f]{32}")
_PUBLIC = ("id", "name", "size", "offset", "ready", "direction", "device_id", "created_at")


class TransferError(ValueError):
    """A refused operation; the message is safe to show the client."""


class Revoked(TransferError):
    """The caller's guard refused at the moment of the mutation (the device was revoked)."""


Guard = Callable[[], bool]


class NotFound(KeyError):
    """No such transfer for this owner."""


#: One lock per blob for the whole process, shared by every store opened on the same folder.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def _flocked(handle: Any) -> Iterator[None]:
    """An OS-level exclusive lock on an open blob, so a second PROCESS cannot interleave."""
    if fcntl is None:
        yield
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class TransferStore:
    def __init__(
        self,
        root: Path,
        *,
        max_file: int = DEFAULT_MAX_FILE,
        quota: int = DEFAULT_QUOTA,
        chunk_size: int = DEFAULT_CHUNK,
        incomplete_ttl_s: float = INCOMPLETE_TTL_S,
        ready_ttl_s: float = READY_TTL_S,
    ) -> None:
        if min(max_file, quota, chunk_size) <= 0:
            raise ValueError("limits must be positive")
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.max_file, self.quota, self.chunk_size = max_file, quota, chunk_size
        self.incomplete_ttl_s, self.ready_ttl_s = incomplete_ttl_s, ready_ttl_s
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS transfers (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, device_id TEXT NOT NULL,
                direction TEXT NOT NULL, name TEXT NOT NULL, size INTEGER NOT NULL,
                digest TEXT NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
                ready INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                updated_at REAL NOT NULL)"""
            )

    # ------------------------------------------------------------ plumbing
    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.root / "transfers.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _lock(self, transfer_id: str) -> threading.Lock:
        with _LOCKS_GUARD:
            return _LOCKS.setdefault(f"{self.root}/{transfer_id}", threading.Lock())

    def _path(self, transfer_id: str) -> Path:
        if not _ID.fullmatch(transfer_id):
            raise TransferError("invalid transfer id")
        return self.root / (transfer_id + ".blob")

    @staticmethod
    def _row(db: sqlite3.Connection, owner: str, transfer_id: str) -> sqlite3.Row:
        if not _ID.fullmatch(str(transfer_id)):
            raise TransferError("invalid transfer id")
        row = db.execute(
            "SELECT * FROM transfers WHERE id=? AND owner=?", (transfer_id, owner)
        ).fetchone()
        if row is None:
            raise NotFound("transfer not found")
        return row

    def _fsync_dir(self) -> None:
        try:
            fd = os.open(self.root, os.O_RDONLY)
        except OSError:  # directories cannot be opened on Windows: nothing to sync there
            return
        try:
            os.fsync(fd)
        except OSError:  # a filesystem without directory sync: the blob fsync still holds
            pass
        finally:
            os.close(fd)

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        view = {key: row[key] for key in _PUBLIC}
        view["ready"] = bool(view["ready"])
        return view

    # ------------------------------------------------------------- uploads
    def create(
        self, owner: str, device_id: str, name: str, size: int, sha256: str,
        *, direction: str = "in", guard: Guard | None = None,
    ) -> str:
        if not owner or not device_id:
            raise TransferError("owner and device required")
        if direction not in ("in", "out"):
            raise TransferError("invalid direction")
        if not isinstance(name, str) or not name or len(name) > 255:
            raise TransferError("a file name of 1-255 characters is required")
        if any(ord(c) < 32 or c in "/\\" for c in name) or name in (".", ".."):
            raise TransferError("invalid file name")
        if type(size) is not int or not 0 <= size <= self.max_file:
            raise TransferError("file exceeds the size limit")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise TransferError("SHA-256 required")
        now = time.time()
        transfer_id = secrets.token_hex(16)
        with self._db() as db:
            if guard is not None and not guard():
                raise Revoked("device revoked")
            used = db.execute("SELECT COALESCE(SUM(size),0) FROM transfers").fetchone()[0]
            count = db.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
            if used + size > self.quota or count >= MAX_TRANSFERS:
                raise TransferError("storage quota exceeded")
            db.execute(
                "INSERT INTO transfers(id,owner,device_id,direction,name,size,digest,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (transfer_id, owner, device_id, direction, name, size, sha256, now, now),
            )
        try:
            fd = os.open(self._path(transfer_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            self._fsync_dir()
        except BaseException:
            with self._db() as db:
                db.execute("DELETE FROM transfers WHERE id=?", (transfer_id,))
            raise
        return transfer_id

    def status(self, owner: str, transfer_id: str) -> dict[str, Any]:
        with self._db() as db:
            return self._public(self._row(db, owner, transfer_id))

    def append(
        self, owner: str, transfer_id: str, offset: int, data: bytes, *, guard: Guard | None = None
    ) -> int:
        if type(offset) is not int or offset < 0:
            raise TransferError("invalid offset")
        if not isinstance(data, bytes) or not 0 < len(data) <= self.chunk_size:
            raise TransferError("invalid chunk")
        with self._lock(transfer_id):
            with self._db() as db:
                self._row(db, owner, transfer_id)  # fail early: unknown id or owner
            try:
                handle = self._path(transfer_id).open("r+b")
            except FileNotFoundError as exc:
                raise TransferError("stored file is missing") from exc
            with handle, _flocked(handle):
                with self._db() as db:
                    row = self._row(db, owner, transfer_id)
                if row["ready"] or offset != row["offset"] or offset + len(data) > row["size"]:
                    raise TransferError(
                        "offset conflict or completed transfer; read the status before retrying"
                    )
                if guard is not None and not guard():  # last look before the bytes land
                    raise Revoked("device revoked")
                # Repair bytes written before a crash that never reached the database.
                if handle.seek(0, 2) < offset:
                    raise TransferError("stored file is truncated")
                handle.truncate(offset)
                handle.seek(offset)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                with self._db() as db:
                    db.execute(
                        "UPDATE transfers SET offset=?, updated_at=? WHERE id=?",
                        (offset + len(data), time.time(), transfer_id),
                    )
        return offset + len(data)

    def finish(
        self, owner: str, transfer_id: str, *, guard: Guard | None = None
    ) -> dict[str, Any]:
        """Verify the digest, then make the file downloadable. Hashing holds only this
        transfer's lock, never the database."""
        with self._lock(transfer_id):
            with self._db() as db:
                row = self._row(db, owner, transfer_id)
            if row["ready"]:
                return self._public(row)
            if row["offset"] != row["size"]:
                raise TransferError("incomplete transfer")
            digest = hashlib.sha256()
            try:
                with self._path(transfer_id).open("rb") as handle:
                    if os.fstat(handle.fileno()).st_size != row["size"]:
                        raise TransferError("stored size mismatch")
                    while block := handle.read(_HASH_BLOCK):
                        digest.update(block)
            except FileNotFoundError as exc:
                raise TransferError("stored file is missing") from exc
            if digest.hexdigest() != row["digest"]:
                raise TransferError("checksum mismatch")
            with self._db() as db:
                if guard is not None and not guard():  # revoked while the file was hashed
                    raise Revoked("device revoked")
                db.execute(
                    "UPDATE transfers SET ready=1, updated_at=? WHERE id=?",
                    (time.time(), transfer_id),
                )
        return self.status(owner, transfer_id)

    # ----------------------------------------------------------- downloads
    def open_ready(self, owner: str, transfer_id: str) -> tuple[dict[str, Any], Path]:
        with self._db() as db:
            row = self._row(db, owner, transfer_id)
        if not row["ready"]:
            raise TransferError("file unavailable")
        return self._public(row), self._path(transfer_id)

    def read(
        self, owner: str, transfer_id: str, offset: int = 0, length: int | None = None
    ) -> bytes:
        meta, path = self.open_ready(owner, transfer_id)
        if type(offset) is not int or not 0 <= offset <= meta["size"]:
            raise TransferError("invalid offset")
        wanted = min(length if length is not None else self.chunk_size, self.chunk_size)
        with path.open("rb") as handle:
            handle.seek(offset)
            return handle.read(max(0, wanted))

    # ------------------------------------------------------------ listing
    def list(self, owner: str, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM transfers WHERE owner=? ORDER BY created_at DESC LIMIT ?",
                (owner, max(1, min(limit, 500))),
            ).fetchall()
        return [self._public(row) for row in rows]

    def delete(self, owner: str, transfer_id: str) -> None:
        with self._lock(transfer_id):
            with self._db() as db:
                self._row(db, owner, transfer_id)
                # The reservation is released only after the blob is gone.
                self._path(transfer_id).unlink(missing_ok=True)
                db.execute("DELETE FROM transfers WHERE id=?", (transfer_id,))

    def delete_for_device(self, device_id: str) -> int:
        """Drop a revoked device's unfinished uploads; finished files stay with the owner."""
        with self._db() as db:
            rows = db.execute(
                "SELECT id, owner FROM transfers WHERE device_id=? AND ready=0 AND direction='in'",
                (device_id,),
            ).fetchall()
        dropped = 0
        for row in rows:
            try:
                self.delete(row["owner"], row["id"])
                dropped += 1
            except (NotFound, TransferError):  # already gone: the goal (no blob, no row) is met
                continue
        return dropped

    # --------------------------------------------------------------- sweep
    def sweep(self, now: float | None = None) -> dict[str, int]:
        """Delete expired transfers and reconcile blobs with rows. Safe to call at any time."""
        now = time.time() if now is None else now
        removed = {"expired": 0, "orphan_blobs": 0, "missing_blobs": 0}
        with self._db() as db:
            rows = db.execute(
                "SELECT id, ready, updated_at, created_at, owner FROM transfers"
            ).fetchall()
        known = {row["id"] for row in rows}
        for row in rows:
            ttl = self.ready_ttl_s if row["ready"] else self.incomplete_ttl_s
            blob = self._path(row["id"])
            missing = not blob.exists() and now - row["created_at"] > 60
            if now - row["updated_at"] > ttl or missing:
                try:
                    self.delete(row["owner"], row["id"])
                except (NotFound, TransferError):  # deleted by a caller meanwhile: nothing to do
                    continue
                removed["missing_blobs" if missing else "expired"] += 1
        for blob in self.root.glob("*.blob"):
            if blob.stem in known:
                continue
            try:
                # A young blob may belong to a create() that has not committed its row yet.
                if now - blob.stat().st_mtime > 300:
                    blob.unlink()
                    removed["orphan_blobs"] += 1
            except OSError:  # the blob vanished or is unreadable: the next sweep looks again
                continue
        return removed
