"""Effect ledger: a side-effecting call already issued is never issued twice (ADR-0044, block 7).

Fallback, provider retry, a replayed stream, a turn redelivered after a restart and multi-engine
runs can all make a model ask for "the same" call again. ``ToolExecutor`` is the one place every
tool call passes, so the ledger lives there: one row per execution attempt, keyed by
``(scope, key)`` where ``scope`` is the turn (``trace_id``) and ``key`` is the provider's call id
(or a fresh id when the caller has none), with a fingerprint of tool name + exact arguments.

What a row says, and what the executor does with it:

* ``started``  written BEFORE the tool runs, inside the same IMMEDIATE transaction that checked
  the call id AND the (scope, tool, fingerprint) identity (``reserve``). If the process dies or
  the task is cancelled the row stays ``started``: the outcome is UNKNOWN.
* ``done``     the tool succeeded. The same key again returns the stored outcome and does NOT
  run the tool.
* ``failed``   the tool returned an error AND vouched that nothing was applied
  (``ToolResult.no_effect_guaranteed`` or the tool's ``failure_means_no_effect``). A retry is
  allowed (it passes the normal approval again).
* ``unknown``  the tool raised, was cancelled, returned an error WITHOUT that evidence (a shell or
  remote operation can apply an effect and then report an error), or a ``started`` row belongs
  to another process. Never retried on its own: the next attempt is forced through a
  confirmation (the owner decides; an unattended surface refuses). The confirmation's one-use
  approval id becomes the ``attempt`` of the new row; nothing is ever overwritten.

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
ReservationKind = Literal["reserved", "replay", "in_flight", "unknown", "duplicate"]


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
    attempt: str
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


@dataclass(frozen=True, slots=True)
class Reservation:
    """Outcome of :meth:`EffectLedger.reserve`. ``kind == "reserved"`` is the only one that lets
    the tool run; ``attempt`` identifies the row that was written."""

    kind: ReservationKind
    row: EffectRow | None = None
    attempt: str = ""


class LedgerUnavailable(RuntimeError):
    """The effect ledger cannot be used, so an effectful call must not run (fails closed)."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS effect_calls (
    scope TEXT NOT NULL, key TEXT NOT NULL, attempt TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL, fingerprint TEXT NOT NULL,
    state TEXT NOT NULL, boot TEXT NOT NULL, success INTEGER, preview TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '', ts REAL NOT NULL,
    PRIMARY KEY (scope, key, attempt)
);
CREATE INDEX IF NOT EXISTS effect_calls_fp ON effect_calls (scope, tool, fingerprint);
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
            self._db.execute("DELETE FROM effect_calls WHERE ts < ?", (time.time() - RETENTION_S,))

    @staticmethod
    def _row(r: tuple[Any, ...]) -> EffectRow:
        return EffectRow(r[0], r[1], r[2], r[3], r[4], r[5], r[6],
                         None if r[7] is None else bool(r[7]), r[8], r[9], r[10])

    _COLS = "scope, key, attempt, tool, fingerprint, state, boot, success, preview, error, ts"

    def _decide(
        self, scope: str, key: str, tool: str, fingerprint: str, attempt: str
    ) -> tuple[str, EffectRow | None]:
        """What the ledger says about this call right now. Caller holds ``_lock`` and, when it
        is going to write, an IMMEDIATE transaction, so the answer cannot go stale.

        Identity checked: the call id ``(scope, key)`` AND the call content ``(scope, tool,
        fingerprint)``. ``attempt`` is the one-use id of an approval the owner gave to run an
        unknown or duplicate call again; it overrides ``unknown``/``duplicate`` only, never a
        finished or still-running call."""
        same = [
            self._row(r)
            for r in self._db.execute(
                f"SELECT {self._COLS} FROM effect_calls WHERE scope=? AND key=? "  # noqa: S608
                "ORDER BY ts DESC",
                (scope, key),
            ).fetchall()
        ]
        mine = [r for r in same if r.fingerprint == fingerprint and r.tool == tool]
        if len(mine) != len(same):
            # the same call id carrying other arguments is a different call
            log.warning("effect ledger: call id %s reused with other arguments", key)
        if attempt and any(r.attempt == attempt for r in same):
            # an approval id is good for one execution
            return "in_flight", next(r for r in same if r.attempt == attempt)
        for r in mine:
            if r.state == "done":
                return "replay", r
        for r in mine:
            if r.state == "started" and r.boot == BOOT_ID:
                return "in_flight", r
        if not attempt:
            for r in mine:
                if r.state in ("started", "unknown"):
                    return "unknown", r
        other = self._db.execute(
            f"SELECT {self._COLS} FROM effect_calls "  # noqa: S608
            "WHERE scope=? AND tool=? AND fingerprint=? AND key<>? "
            "AND state IN ('done','started','unknown') ORDER BY ts DESC",
            (scope, tool, fingerprint, key),
        ).fetchall()
        others = [self._row(r) for r in other]
        for r in others:
            if r.state == "started" and r.boot == BOOT_ID:
                return "in_flight", r
        if others and not attempt:
            return "duplicate", others[0]
        return "new", None

    def inspect(
        self, scope: str, key: str, tool: str, fingerprint: str, attempt: str = ""
    ) -> Inspection:
        """Advisory, read-only view. Admission is :meth:`reserve`, never inspect-then-claim."""
        with self._lock:
            kind, row = self._decide(scope, key, tool, fingerprint, attempt)
        return Inspection(kind, row)  # type: ignore[arg-type]

    def reserve(
        self, scope: str, key: str, tool: str, fingerprint: str, attempt: str = ""
    ) -> Reservation:
        """Inspect AND write ``started`` in ONE immediate transaction (cross-process safe).

        Nothing is ever overwritten: another process's reservation, a finished call or an
        unknown one stays as it is. Re-running an unknown or duplicate call needs ``attempt``,
        the one-use id of the explicit approval the owner gave for it."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                kind, row = self._decide(scope, key, tool, fingerprint, attempt)
                if kind != "new":
                    self._db.execute("ROLLBACK")
                    return Reservation(kind, row)  # type: ignore[arg-type]
                try:
                    self._db.execute(
                        "INSERT INTO effect_calls "
                        "(scope, key, attempt, tool, fingerprint, state, boot, ts) "
                        "VALUES (?,?,?,?,?, 'started', ?, ?)",
                        (scope, key, attempt, tool, fingerprint, BOOT_ID, time.time()),
                    )
                except sqlite3.IntegrityError:
                    self._db.execute("ROLLBACK")
                    return Reservation("in_flight", None)
                self._db.execute("COMMIT")
            except BaseException:
                try:
                    self._db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        return Reservation("reserved", None, attempt)

    def claim(self, scope: str, key: str, tool: str, fingerprint: str, attempt: str = "") -> bool:
        """Compatibility wrapper: True only when :meth:`reserve` wrote the row."""
        return self.reserve(scope, key, tool, fingerprint, attempt).kind == "reserved"

    def finish(
        self,
        scope: str,
        key: str,
        *,
        success: bool,
        attempt: str = "",
        no_effect: bool = False,
        preview: str = "",
        error: str = "",
    ) -> None:
        """Record what the tool returned.

        ``success``  -> ``done`` (replayable).
        ``no_effect`` (the tool supplied trusted evidence that nothing was applied) -> ``failed``
        (a retry through the normal approval is allowed).
        any other failure -> ``unknown``: a shell or remote operation can apply an effect and then
        report an error, so it is never retried on its own."""
        if success:
            state = "done"
        elif no_effect:
            state = "failed"
        else:
            state = "unknown"
        with self._lock:
            self._db.execute(
                "UPDATE effect_calls SET state=?, success=?, preview=?, error=?, ts=? "
                "WHERE scope=? AND key=? AND attempt=? AND state='started' AND boot=?",
                (state, int(success), preview[:500], (error or "")[:500], time.time(),
                 scope, key, attempt, BOOT_ID),
            )

    def mark_unknown(self, scope: str, key: str, error: str = "", attempt: str = "") -> None:
        with self._lock:
            self._db.execute(
                "UPDATE effect_calls SET state='unknown', error=?, ts=? "
                "WHERE scope=? AND key=? AND attempt=? AND state='started'",
                ((error or "")[:500], time.time(), scope, key, attempt),
            )

    def release(self, scope: str, key: str, attempt: str = "") -> None:
        """Drop a reservation whose tool provably never started."""
        with self._lock:
            self._db.execute(
                "DELETE FROM effect_calls WHERE scope=? AND key=? AND attempt=? "
                "AND state='started' AND boot=?",
                (scope, key, attempt, BOOT_ID),
            )

    def get(self, scope: str, key: str) -> EffectRow | None:
        with self._lock:
            r = self._db.execute(
                f"SELECT {self._COLS} FROM effect_calls WHERE scope=? AND key=? "  # noqa: S608
                "ORDER BY ts DESC LIMIT 1",
                (scope, key),
            ).fetchone()
        return None if r is None else self._row(r)


# --------------------------------------------------------------------------- process-wide state

ProtectionMode = Literal["memory", "persistent", "reduced", "unavailable"]


@dataclass(frozen=True, slots=True)
class ProtectionState:
    """How well duplicate effects are guarded right now, in plain terms (for status surfaces)."""

    mode: ProtectionMode
    detail: str


_default: EffectLedger | None = None
_mode: ProtectionMode = "memory"
_detail = "per-process ledger (nothing persistent was configured)"
_default_lock = threading.Lock()


def protection_state() -> ProtectionState:
    with _default_lock:
        return ProtectionState(_mode, _detail)


def get_default_ledger() -> EffectLedger:
    """The process-wide ledger. Raises :class:`LedgerUnavailable` when persistence was required
    and could not be provided: effectful calls then fail closed instead of running unrecorded."""
    global _default
    with _default_lock:
        if _mode == "unavailable":
            raise LedgerUnavailable(_detail)
        if _default is None:
            _default = EffectLedger()
        return _default


def set_default_ledger(ledger: EffectLedger | None) -> None:
    global _default, _mode, _detail
    with _default_lock:
        _default = ledger
        if ledger is None:
            _mode, _detail = "memory", "per-process ledger (nothing persistent was configured)"
        elif ledger.persistent:
            _mode, _detail = "persistent", "persistent effect ledger"
        else:
            _mode, _detail = "memory", "per-process ledger (nothing persistent was configured)"


def configure_default_ledger(
    path: str | os.PathLike[str], *, allow_reduced_protection: bool = False
) -> EffectLedger | None:
    """Make the default ledger persistent (survives a restart). Idempotent.

    When the file cannot be opened, effectful calls are REFUSED (``get_default_ledger`` raises)
    unless the owner enabled reduced protection in the config. Reduced protection is a
    per-process, in-memory ledger and is reported as such by :func:`protection_state`; it never
    happens by default and never silently."""
    global _default, _mode, _detail
    with _default_lock:
        if _default is not None and _default.persistent and _mode == "persistent":
            return _default
        try:
            _default = EffectLedger(path)
            _mode, _detail = "persistent", f"persistent effect ledger at {path}"
            return _default
        except Exception as exc:  # noqa: BLE001 - never block start-up; decide what is lost
            if allow_reduced_protection:
                _default = EffectLedger()
                _mode = "reduced"
                _detail = (
                    f"REDUCED PROTECTION (owner-enabled): the effect ledger at {path} could not "
                    f"be opened ({type(exc).__name__}). Duplicate effects are detected only "
                    "within this running process; a restart or a second process is NOT covered."
                )
                log.warning("effect ledger: %s", _detail, exc_info=True)
                return _default
            _default = None
            _mode = "unavailable"
            _detail = (
                f"effect ledger at {path} could not be opened ({type(exc).__name__}): calls with "
                "side effects are refused until it can be; reads and chat keep working"
            )
            log.error("effect ledger: %s", _detail, exc_info=True)
            return None


def mark_unavailable(reason: str) -> None:
    """Record that persistence was required but could not even be attempted: effectful calls are
    refused until ``configure_default_ledger`` succeeds."""
    global _default, _mode, _detail
    with _default_lock:
        _default = None
        _mode = "unavailable"
        _detail = f"effect ledger not configured ({reason}): calls with side effects are refused"
    log.error("effect ledger: %s", _detail)


def is_side_effecting(tool: Any, args: dict[str, Any], tier: str = "") -> bool:
    """Only a tool that DECLARES itself read-only (``allows_read``: the same standard STOP and
    plan mode use) skips the ledger. A ``safe`` tier is a risk label, not an effect claim, and an
    unknown or broken capability counts as an effect."""
    from dodaios.core.tool_read_only import allows_read

    return not allows_read(tool, args)


def no_effect_evidence(tool: Any, result: Any) -> bool:
    """True only when the tool itself vouches that this failure applied nothing: the result says
    ``no_effect_guaranteed`` or the tool declares ``failure_means_no_effect``. Absent evidence a
    failed side-effecting call is UNKNOWN."""
    return (
        getattr(result, "no_effect_guaranteed", False) is True
        or getattr(tool, "failure_means_no_effect", False) is True
    )


__all__ = [
    "BOOT_ID",
    "EffectLedger",
    "EffectRow",
    "Inspection",
    "LedgerUnavailable",
    "ProtectionState",
    "Reservation",
    "call_fingerprint",
    "configure_default_ledger",
    "get_default_ledger",
    "is_side_effecting",
    "mark_unavailable",
    "no_effect_evidence",
    "protection_state",
    "set_default_ledger",
]
