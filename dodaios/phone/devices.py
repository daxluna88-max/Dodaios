"""Paired phones: one-use pairing codes, device credentials and revocation (no HTTP).

The owner mints a short pairing code on the computer (an authenticated action). The phone redeems it
once and receives a random credential that only its hash is stored for. Every request re-reads the
device row, so revoking a device invalidates its sessions, downloads and uploads at once.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

CODE_TTL_S = 300.0
#: Unambiguous characters (no 0/O, 1/I/L).
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
#: Failed redeems tolerated per window before every redeem is refused until it passes.
MAX_FAILURES = 8
FAILURE_WINDOW_S = 300.0
MAX_DEVICES = 20
_TOUCH_EVERY_S = 60.0


class PairingError(ValueError):
    """The code is wrong, expired, used, or redeeming is rate limited."""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def format_code(code: str) -> str:
    return f"{code[: CODE_LENGTH // 2]}-{code[CODE_LENGTH // 2 :]}"


class DeviceRegistry:
    def __init__(self, root: Path, owner: str = "owner") -> None:
        self.owner = owner
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._failures: list[float] = []
        self._guard = threading.Lock()
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS pairing (
                code_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                csrf TEXT NOT NULL, created_at REAL NOT NULL, last_seen REAL NOT NULL,
                revoked_at REAL)"""
            )

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.root / "devices.sqlite3", timeout=30)
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

    # ------------------------------------------------------------ pairing
    def new_code(self, now: float | None = None) -> tuple[str, float]:
        """A fresh one-use code; any unused earlier code stops working."""
        now = time.time() if now is None else now
        code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
        with self._db() as db:
            active = db.execute(
                "SELECT COUNT(*) FROM devices WHERE revoked_at IS NULL"
            ).fetchone()[0]
            if active >= MAX_DEVICES:
                raise PairingError("too many paired devices; revoke one first")
            db.execute("DELETE FROM pairing")
            db.execute(
                "INSERT INTO pairing(code_hash, expires_at) VALUES(?,?)",
                (_hash(code), now + CODE_TTL_S),
            )
        return format_code(code), CODE_TTL_S

    def redeem(self, code: str, name: str, now: float | None = None) -> tuple[dict[str, Any], str]:
        """Spend a code. Returns the device and its credential (shown to the phone once)."""
        now = time.time() if now is None else now
        name = " ".join(str(name or "").split())[:60] or "iPhone"
        with self._guard:
            self._failures = [t for t in self._failures if now - t < FAILURE_WINDOW_S]
            if len(self._failures) >= MAX_FAILURES:
                raise PairingError("too many wrong codes; wait a few minutes")
        digest = _hash(normalize_code(code))
        token = secrets.token_urlsafe(32)
        device_id = secrets.token_hex(8)
        with self._db() as db:
            row = db.execute(
                "SELECT expires_at FROM pairing WHERE code_hash=?", (digest,)
            ).fetchone()
            valid = row is not None and row["expires_at"] >= now
            db.execute("DELETE FROM pairing WHERE code_hash=?", (digest,))
            if valid:
                db.execute(
                    "INSERT INTO devices(id,name,token_hash,csrf,created_at,last_seen)"
                    " VALUES(?,?,?,?,?,?)",
                    (device_id, name, _hash(token), secrets.token_urlsafe(24), now, now),
                )
        if not valid:
            with self._guard:
                self._failures.append(now)
            raise PairingError("the code is wrong, expired or already used")
        device = self.get(device_id)
        assert device is not None
        return device, token

    # ------------------------------------------------------------ devices
    def authenticate(self, token: str | None, now: float | None = None) -> dict[str, Any] | None:
        """The live device behind a credential, or ``None`` (unknown, malformed, revoked)."""
        if not token or len(token) > 200:
            return None
        now = time.time() if now is None else now
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM devices WHERE token_hash=? AND revoked_at IS NULL", (_hash(token),)
            ).fetchone()
            if row is None:
                return None
            if now - row["last_seen"] > _TOUCH_EVERY_S:
                db.execute("UPDATE devices SET last_seen=? WHERE id=?", (now, row["id"]))
        return self._public(row, csrf=True)

    def is_active(self, device_id: str) -> bool:
        with self._db() as db:
            return db.execute(
                "SELECT 1 FROM devices WHERE id=? AND revoked_at IS NULL", (device_id,)
            ).fetchone() is not None

    def get(self, device_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        return self._public(row, csrf=True) if row else None

    def list(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM devices ORDER BY created_at DESC").fetchall()
        return [self._public(row, csrf=False) for row in rows]

    def revoke(self, device_id: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._db() as db:
            cur = db.execute(
                "UPDATE devices SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (now, device_id),
            )
        return cur.rowcount > 0

    @staticmethod
    def _public(row: sqlite3.Row, *, csrf: bool) -> dict[str, Any]:
        out = {
            "id": row["id"], "name": row["name"], "created_at": row["created_at"],
            "last_seen": row["last_seen"], "revoked": row["revoked_at"] is not None,
        }
        if csrf:
            out["csrf"] = row["csrf"]
        return out


def csrf_matches(expected: str, presented: str | None) -> bool:
    return bool(presented) and hmac.compare_digest(expected, str(presented))
