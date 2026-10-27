"""Effect ledger: a side-effecting call already issued is never issued twice (ADR-0044, block 7).

Fallback, provider retry, a replayed stream, a turn redelivered after a restart and multi-engine
runs can all make a model ask for "the same" call again. ``ToolExecutor`` is the one place every
tool call passes, so the ledger lives there: one row per execution attempt, keyed by
``(scope, key)`` where ``scope`` is the turn (``trace_id``) and ``key`` is the provider's call id
(or a fresh id when the caller has none), with a fingerprint of tool name + exact arguments.

What a row says, and what the executor does with it:

* ``started``  written BEFORE the tool runs. If the process dies or the task is cancelled the row
  stays ``started``: the outcome is UNKNOWN.
* ``done``     the tool returned (its ``success`` flag is kept). The same key again returns the
  stored outcome and does NOT run the tool.
* ``failed``   the tool returned an error result. A retry is allowed (it passes the normal
  approval again).
* ``unknown``  the tool raised, was cancelled, or a ``started`` row belongs to another process.
  Never retried on its own: the next attempt is forced through a confirmation (the owner decides;
  an unattended surface refuses).

The same fingerprint under a DIFFERENT key in the same scope (the model re-issued an identical
call with a new id, e.g. after a retry) is a ``duplicate``: also forced through a confirmation.

Honest limits: this is a record of what THIS app issued. It cannot know whether an external system
(mail server, remote API) applied an effect whose result we never saw, it only refuses to guess.
It does not make a tool idempotent on the far side. After a restart it only helps if the redelivered
turn keeps its ``trace_id`` and call ids. In-memory unless ``configure_default_ledger`` was given a
path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

#: Identifies this process run; a ``started`` row from another boot has an unknown outcome.
BOOT_ID = uuid.uuid4().hex

#: Rows older than this are dropped when a persistent ledger opens.
RETENTION_S = 7 * 24 * 3600.0

InspectionKind = Literal["new", "replay", "in_flight", "unknown", "duplicate"]


def call_fingerprint(tool_name: str, args: Any) -> str:
    """Stable digest of one exact call: tool name + canonical JSON of its arguments.

    Any change to the arguments changes it, which is how an approval is bound to the call it was
    asked for. The digest hides the arguments, so it is safe to put on bus events."""
    try:
        body = json.dumps(
            args, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
        )
    except (TypeError, ValueError):
        body = repr(args)
    return hashlib.sha256(f"{tool_name}\x00{body}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class EffectRow:
    scope: str
    key: str
    tool: str
    fingerprint: str
    state: str  # started | done | failed | unknown
    boot: str
    success: bool | None = None
    preview: str = ""
    error: str = ""
    ts: float = 0.0


@dataclass(frozen=True, slots=True)
class Inspection:
    kind: InspectionKind
    row: EffectRow | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS effects (
    scope TEXT NOT NULL, key TEXT NOT NULL, tool TEXT NOT NULL, fingerprint TEXT NOT NULL,
    state TEXT NOT NULL, boot TEXT NOT NULL, success INTEGER, preview TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '', ts REAL NOT NULL,
    PRIMARY KEY (scope, key)
);
CREATE INDEX IF NOT EXISTS effects_fp ON effects (scope, tool, fingerprint);
"""


class EffectLedger:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._lock = threading.Lock()
        target = ":memory:" if path is None else str(path)
        if path is not None:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            target, check_same_thread=False, isolation_level=None, timeout=2.0
        )
        self._db.executescript(_SCHEMA)
        self.persistent = path is not None
        if self.persistent:
            self._db.execute("DELETE FROM effects WHERE ts < ?", (time.time() - RETENTION_S,))

    @staticmethod
    def _row(r: tuple[Any, ...]) -> EffectRow:
        return EffectRow(r[0], r[1], r[2], r[3], r[4], r[5],
                         None if r[6] is None else bool(r[6]), r[7], r[8], r[9])

    _COLS = "scope, key, tool, fingerprint, state, boot, success, preview, error, ts"

    def inspect(self, scope: str, key: str, tool: str, fingerprint: str) -> Inspection:
        with self._lock:
            same = self._db.execute(
                f"SELECT {self._COLS} FROM effects WHERE scope=? AND key=?",  # noqa: S608
                (scope, key),
            ).fetchone()
            if same is not None:
                row = self._row(same)
                if row.fingerprint == fingerprint and row.tool == tool:
                    if row.state == "done":
                        return Inspection("replay", row)
                    if row.state == "started":
                        return Inspection("in_flight" if row.boot == BOOT_ID else "unknown", row)
                    if row.state == "unknown":
                        return Inspection("unknown", row)
                    # failed: a retry is allowed
                else:
                    # the same call id carrying other arguments is a different call
                    log.warning("effect ledger: call id %s reused with other arguments", key)
            other = self._db.execute(
                f"SELECT {self._COLS} FROM effects "  # noqa: S608
                "WHERE scope=? AND tool=? AND fingerprint=? "
                "AND key<>? AND state IN ('done','started','unknown') ORDER BY ts DESC LIMIT 1",
                (scope, tool, fingerprint, key),
            ).fetchone()
            if other is not None:
                return Inspection("duplicate", self._row(other))
        return Inspection("new")

    def claim(self, scope: str, key: str, tool: str, fingerprint: str) -> bool:
        """Write ``started`` before the tool runs. False when a live or finished row blocks it
        (another task claimed the same key between ``inspect`` and now)."""
        with self._lock:
            row = self._db.execute(
                "SELECT state, boot FROM effects WHERE scope=? AND key=?", (scope, key)
            ).fetchone()
            live = row is not None and row[0] == "started" and row[1] == BOOT_ID
            if row is not None and (row[0] == "done" or live):
                return False
            self._db.execute(
                "INSERT OR REPLACE INTO effects (scope, key, tool, fingerprint, state, boot, ts) "
                "VALUES (?,?,?,?, 'started', ?, ?)",
                (scope, key, tool, fingerprint, BOOT_ID, time.time()),
            )
        return True

    def finish(
        self, scope: str, key: str, *, success: bool, preview: str = "", error: str = ""
    ) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE effects SET state=?, success=?, preview=?, error=?, ts=? "
                "WHERE scope=? AND key=?",
                ("done" if success else "failed", int(success), preview[:500],
                 (error or "")[:500], time.time(), scope, key),
            )

    def mark_unknown(self, scope: str, key: str, error: str = "") -> None:
        with self._lock:
            self._db.execute(
                "UPDATE effects SET state='unknown', error=?, ts=? WHERE scope=? AND key=?",
                ((error or "")[:500], time.time(), scope, key),
            )

    def get(self, scope: str, key: str) -> EffectRow | None:
        with self._lock:
            r = self._db.execute(
                f"SELECT {self._COLS} FROM effects WHERE scope=? AND key=?",  # noqa: S608
                (scope, key),
            ).fetchone()
        return None if r is None else self._row(r)


_default: EffectLedger | None = None
_default_lock = threading.Lock()


def get_default_ledger() -> EffectLedger:
    """The process-wide ledger for executors without their own (memory until configured)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = EffectLedger()
        return _default


def set_default_ledger(ledger: EffectLedger | None) -> None:
    global _default
    with _default_lock:
        _default = ledger


def configure_default_ledger(path: str | os.PathLike[str]) -> EffectLedger:
    """Make the default ledger persistent (survives a restart). Idempotent; falls back to the
    in-memory ledger, loudly, when the file cannot be opened."""
    global _default
    with _default_lock:
        if _default is not None and _default.persistent:
            return _default
        try:
            _default = EffectLedger(path)
        except Exception:  # noqa: BLE001 - never block start-up; say what is lost
            log.warning(
                "effect ledger: could not open %s; duplicate protection is per-process only",
                path, exc_info=True,
            )
            if _default is None:
                _default = EffectLedger()
        return _default


def is_side_effecting(tool: Any, args: dict[str, Any], tier: str) -> bool:
    """Conservative: only a tool that declares itself read-only or lookup-only is exempt, and a
    ``safe`` tier tool that makes no claim either way is treated as a read (otherwise every
    unflagged lookup would be asked about when repeated)."""
    from dodaios.core.tool_read_only import allows_read

    if getattr(tool, "is_lookup_only", False):
        return False
    if allows_read(tool, args):
        return False
    return tier != "safe" or getattr(tool, "read_only", None) is False


__all__ = [
    "BOOT_ID",
    "EffectLedger",
    "EffectRow",
    "Inspection",
    "call_fingerprint",
    "configure_default_ledger",
    "get_default_ledger",
    "is_side_effecting",
    "set_default_ledger",
]
