# ruff: noqa: E501  (long Italian customer messages and SQL strings; no formatter was run)
"""Provider-neutral, server-side budget ledger for the hosted Consumer (ADR-0066, block 20).

Written statically and NOT RUN. It is a self-contained module: SQLite, no network, no provider SDK,
no price list, no wiring into the running app.

What it does
* Amounts are INTEGERS in one minor unit chosen by the caller (for example micro-euro). Floats and
  bools are refused, so rounding never invents or hides cost. No price is built in: the caller
  passes the amount; ``None`` means "unknown price" and is never treated as zero.
* ``reserve`` (worst case, before a paid call) -> ``settle`` (measured usage) or ``release`` (the
  call provably never started). All checks and the insert run in one ``BEGIN IMMEDIATE``
  transaction, so concurrent turns cannot jointly pass a cap.
* A customer has a monthly total cap and optional per-class caps across the cost classes
  (AI inference, voice, compute, storage, egress). NO cap row = nothing may be reserved (never
  "unlimited by default"). No API raises a cap by itself: ``set_cap`` is an explicit, audited act.
* Held money counts as spent: ``reserved`` and ``unknown`` rows hold their worst case; ``settled``
  rows count the real amount. An actual above the reservation is recorded as it is (overrun), never
  clipped.
* ``settle`` is idempotent: the same call again returns the stored outcome, a different amount is a
  conflict and changes nothing.
* A reservation whose deadline passed (crash, lost worker) becomes ``unknown`` and KEEPS holding its
  amount: the call may have run. Only an explicit ``release(operator=True)`` or a ``settle`` with a
  provider-reported amount resolves it.
* Per-call ceiling per class and per-scope (agent loop run) ceilings on amount and number of calls.
* Costs that accrue without a call (compute hours, storage, egress) enter through ``record_usage``
  from provider usage records; they may push a customer over the cap (the money is already spent)
  and then block new spend.
* ``reconcile`` compares the ledger with a provider-reported amount; a provider figure above the
  ledger beyond a tolerance blocks that customer until ``clear_block``. ``reconcile_global`` trips the
  operator circuit breaker.
* Operator circuit breaker and a global period cap stop all new reservations.

Honest limits
* This bounds what THIS service authorizes. A provider that offers no hard cap may still bill more
  (late usage records, price changes, work the provider keeps running after a cancel). The ledger
  lags the provider's real billing; the product text must not promise an absolute ceiling.
* An estimate is an estimate: ``owner_estimate`` rows are labelled so, and a settle with
  ``measured=False`` stays "estimated" in ``usage``.
* SQLite serializes writers: right for one worker host. A multi-node deployment needs the same
  logic on a transactional server database (row locks), not copies of this file.
* The caller must still stop work (suspend the environment) when ``usage(...)['blocked']`` is true.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

COST_CLASSES = ("ai_inference", "voice", "compute", "storage", "egress")
TOTAL = "*"
MAX_AMOUNT = 10**15

#: Customer-visible text per refusal code (Italian first). Plain words; no promise of an absolute cap.
MESSAGES_IT: dict[str, str] = {
    "no_cap": "Per questo mese non e stato impostato nessun limite: Jarvis non spende nulla.",
    "cap_reached": (
        "Hai raggiunto il limite di spesa del mese. Jarvis si ferma qui e non spende altro. "
        "Nessun rinnovo o ricarica automatica: solo tu puoi alzarlo."
    ),
    "class_cap_reached": "Hai raggiunto il limite per questo tipo di consumo. Jarvis si ferma qui.",
    "unknown_price": "Il costo di questa operazione non e noto, quindi Jarvis non la esegue.",
    "call_ceiling": "Questa singola operazione costerebbe piu del massimo consentito: non e stata eseguita.",
    "scope_ceiling": "Questo lavoro ha raggiunto il suo tetto di spesa o di passaggi ed e stato fermato.",
    "customer_blocked": (
        "Le spese sono sospese in attesa di una verifica dei conti. Nulla viene speso nel frattempo."
    ),
    "breaker": "Il servizio ha sospeso temporaneamente ogni nuova spesa. Riprova piu tardi.",
    "global_cap": "Il servizio ha raggiunto il suo limite mensile: nuove operazioni sospese.",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS caps (
  customer TEXT NOT NULL, period TEXT NOT NULL, cls TEXT NOT NULL, cap INTEGER NOT NULL,
  PRIMARY KEY (customer, period, cls));
CREATE TABLE IF NOT EXISTS scopes (
  scope TEXT PRIMARY KEY, customer TEXT NOT NULL,
  max_amount INTEGER NOT NULL, max_calls INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS entries (
  res_id TEXT PRIMARY KEY, customer TEXT NOT NULL, period TEXT NOT NULL, cls TEXT NOT NULL,
  scope TEXT, idem TEXT NOT NULL, fp TEXT,
  amount INTEGER NOT NULL,
  state TEXT NOT NULL,
  price_basis TEXT NOT NULL,
  actual INTEGER, actual_basis TEXT,
  overrun INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL, deadline REAL, updated REAL NOT NULL, note TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS entries_idem ON entries(customer, idem);
CREATE INDEX IF NOT EXISTS entries_cp ON entries(customer, period, cls);
CREATE TABLE IF NOT EXISTS blocks (customer TEXT PRIMARY KEY, reason TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS recon (
  id INTEGER PRIMARY KEY AUTOINCREMENT, customer TEXT, period TEXT NOT NULL, cls TEXT NOT NULL,
  provider INTEGER NOT NULL, ledger INTEGER NOT NULL, drift INTEGER NOT NULL,
  action TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS losses (
  key TEXT PRIMARY KEY, customer TEXT NOT NULL, cls TEXT NOT NULL, period TEXT NOT NULL,
  total INTEGER NOT NULL, updated REAL NOT NULL);
"""

# Held money: settled -> the real amount, reserved/unknown -> the worst case, released -> nothing.
_COMMITTED = (
    "SELECT COALESCE(SUM(CASE state WHEN 'settled' THEN actual WHEN 'released' THEN 0 "
    "ELSE amount END), 0) FROM entries "
)
_BY_CUSTOMER = _COMMITTED + "WHERE customer=? AND period=? AND (? IS NULL OR cls=?)"
_BY_PERIOD = _COMMITTED + "WHERE period=? AND (? IS NULL OR cls=?)"


def _money(value: object) -> int | None:
    """A valid amount, or None. Floats, bools, negatives and absurd values are not amounts."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_AMOUNT else None


def period_of(ts: float) -> str:
    """UTC calendar month ``YYYY-MM``. The caller may pass its own period string instead."""
    return time.strftime("%Y-%m", time.gmtime(ts))


@dataclass(frozen=True, slots=True)
class Decision:
    ok: bool
    code: str  # "ok" | "replay" | a refusal code (see MESSAGES_IT) | "conflict" | "invalid" | ...
    res_id: str = ""
    remaining: int | None = None  # total cap minus committed, after this decision
    message_it: str = ""

    @property
    def replay(self) -> bool:
        return self.code == "replay"


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    action: str  # ok | blocked | review_over_recorded | breaker
    provider: int
    ledger: int
    drift: int  # provider - ledger


class BudgetLedger:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._lock = threading.Lock()
        target = ":memory:" if path is None else str(path)
        if path is not None:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            target, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        if "fp" not in {r["name"] for r in self._db.execute("PRAGMA table_info(entries)")}:
            self._db.execute("ALTER TABLE entries ADD COLUMN fp TEXT")  # ledgers created before K3

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    # -- configuration (explicit, audited; nothing raises a cap by itself) ----------------------

    def _audit(self, db: sqlite3.Connection, actor: str, action: str, detail: str, now: float) -> None:
        db.execute(
            "INSERT INTO audit (ts, actor, action, detail) VALUES (?,?,?,?)",
            (now, actor, action, detail),
        )

    def set_cap(
        self, customer: str, period: str, cls: str, cap: int, *, actor: str, now: float | None = None
    ) -> None:
        """Set the customer's cap for a period. ``cls`` is ``TOTAL`` or one cost class."""
        if cls != TOTAL and cls not in COST_CLASSES:
            raise ValueError(f"unknown cost class: {cls!r}")
        amount = _money(cap)
        if amount is None or not actor:
            raise ValueError("cap must be a non-negative integer and an actor is required")
        now = time.time() if now is None else now
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO caps (customer, period, cls, cap) VALUES (?,?,?,?)",
                (customer, period, cls, amount),
            )
            self._audit(db, actor, "set_cap", f"{customer}/{period}/{cls}={amount}", now)

    def set_global_cap(self, period: str, cap: int, *, actor: str, now: float | None = None) -> None:
        amount = _money(cap)
        if amount is None or not actor:
            raise ValueError("cap must be a non-negative integer and an actor is required")
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO meta (k, v) VALUES (?,?)", ("global_cap:" + period, str(amount))
            )
            self._audit(db, actor, "set_global_cap", f"{period}={amount}", now or time.time())

    def set_call_ceiling(self, cls: str, ceiling: int, *, actor: str, now: float | None = None) -> None:
        """Largest single reservation allowed for a class (any customer). Unset = no per-call limit."""
        amount = _money(ceiling)
        if cls not in COST_CLASSES or amount is None or not actor:
            raise ValueError("valid class, non-negative integer ceiling and actor are required")
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO meta (k, v) VALUES (?,?)", ("call_ceiling:" + cls, str(amount))
            )
            self._audit(db, actor, "set_call_ceiling", f"{cls}={amount}", now or time.time())

    def open_scope(
        self, scope: str, customer: str, *, max_amount: int, max_calls: int
    ) -> bool:
        """Ceiling for one agent-loop run: total amount and number of paid calls. False if it exists."""
        amount = _money(max_amount)
        calls = _money(max_calls)
        if amount is None or calls is None or not scope:
            raise ValueError("scope ceilings must be non-negative integers")
        with self._tx() as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO scopes (scope, customer, max_amount, max_calls) "
                "VALUES (?,?,?,?)",
                (scope, customer, amount, calls),
            )
            return cur.rowcount == 1

    # -- operator circuit breaker ---------------------------------------------------------------

    def trip_breaker(self, reason: str, *, actor: str, now: float | None = None) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('breaker', ?)", (reason or "tripped",))
            self._audit(db, actor, "trip_breaker", reason, now or time.time())

    def reset_breaker(self, *, actor: str, note: str, now: float | None = None) -> None:
        if not actor or not note:
            raise ValueError("resetting the breaker needs an actor and a note")
        with self._tx() as db:
            db.execute("DELETE FROM meta WHERE k='breaker'")
            self._audit(db, actor, "reset_breaker", note, now or time.time())

    def breaker(self) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT v FROM meta WHERE k='breaker'").fetchone()
        return None if row is None else str(row["v"])

    # -- reserve / settle / release -------------------------------------------------------------

    @staticmethod
    def _refuse(code: str, remaining: int | None = None) -> Decision:
        return Decision(False, code, "", remaining, MESSAGES_IT.get(code, ""))

    def _remaining(self, db: sqlite3.Connection, customer: str, period: str) -> int | None:
        cap = db.execute(
            "SELECT cap FROM caps WHERE customer=? AND period=? AND cls=?", (customer, period, TOTAL)
        ).fetchone()
        if cap is None:
            return None
        used = db.execute(_BY_CUSTOMER, (customer, period, None, None)).fetchone()[0]
        return int(cap["cap"]) - int(used)

    def reserve(
        self,
        customer: str,
        cls: str,
        amount: int | None,
        *,
        idem: str,
        period: str,
        scope: str | None = None,
        ttl_s: float = 300.0,
        owner_estimate: int | None = None,
        fingerprint: str | None = None,
        now: float | None = None,
    ) -> Decision:
        """Hold ``amount`` (the worst case of one paid call) or refuse BEFORE the call starts.

        ``amount=None`` is an unknown price: refused unless the account owner set an explicit
        ``owner_estimate`` (then the row is labelled ``owner_estimate``). Same ``idem`` again returns
        the existing reservation (retries cannot double-hold) ONLY when class, period, scope, amount,
        price basis and ``fingerprint`` (the caller's canonical request identity) all match; any
        difference is a ``conflict``: a replay never authorizes a different or larger request."""
        now = time.time() if now is None else now
        if cls not in COST_CLASSES or not customer or not idem or not period:
            return Decision(False, "invalid", "", None, "")
        basis = "known"
        if amount is None:
            est = _money(owner_estimate)
            if est is None:
                with self._tx() as db:
                    return self._refuse("unknown_price", self._remaining(db, customer, period))
            amount, basis = est, "owner_estimate"
        held = _money(amount)
        if held is None:
            return Decision(False, "invalid", "", None, "")
        with self._tx() as db:
            return self._reserve_tx(db, customer, cls, held, basis, idem, period, scope, ttl_s, now, fingerprint)

    def _reserve_tx(
        self,
        db: sqlite3.Connection,
        customer: str,
        cls: str,
        amount: int,
        basis: str,
        idem: str,
        period: str,
        scope: str | None,
        ttl_s: float,
        now: float,
        fingerprint: str | None = None,
    ) -> Decision:
        prior = db.execute(
            "SELECT * FROM entries WHERE customer=? AND idem=?", (customer, idem)
        ).fetchone()
        if prior is not None:
            same = (
                prior["cls"] == cls and prior["period"] == period and prior["scope"] == scope
                and int(prior["amount"]) == amount and prior["price_basis"] == basis
                and prior["fp"] == fingerprint
            )
            if not same or prior["state"] == "released":
                return Decision(False, "conflict", prior["res_id"], None, "")
            return Decision(True, "replay", prior["res_id"], self._remaining(db, customer, period), "")
        remaining = self._remaining(db, customer, period)
        if db.execute("SELECT 1 FROM meta WHERE k='breaker'").fetchone():
            return self._refuse("breaker", remaining)
        if db.execute("SELECT 1 FROM blocks WHERE customer=?", (customer,)).fetchone():
            return self._refuse("customer_blocked", remaining)
        if remaining is None:
            return self._refuse("no_cap", None)
        ceil = db.execute("SELECT v FROM meta WHERE k=?", ("call_ceiling:" + cls,)).fetchone()
        if ceil is not None and amount > int(ceil["v"]):
            return self._refuse("call_ceiling", remaining)
        if amount > remaining:
            return self._refuse("cap_reached", remaining)
        ccap = db.execute(
            "SELECT cap FROM caps WHERE customer=? AND period=? AND cls=?", (customer, period, cls)
        ).fetchone()
        if ccap is not None:
            used = db.execute(_BY_CUSTOMER, (customer, period, cls, cls)).fetchone()[0]
            if int(used) + amount > int(ccap["cap"]):
                return self._refuse("class_cap_reached", remaining)
        gcap = db.execute("SELECT v FROM meta WHERE k=?", ("global_cap:" + period,)).fetchone()
        if gcap is not None:
            total = db.execute(_BY_PERIOD, (period, None, None)).fetchone()[0]
            if int(total) + amount > int(gcap["v"]):
                return self._refuse("global_cap", remaining)
        if scope is not None and not self._scope_allows(db, scope, customer, amount):
            return self._refuse("scope_ceiling", remaining)
        res_id = os.urandom(12).hex()
        db.execute(
            "INSERT INTO entries (res_id, customer, period, cls, scope, idem, fp, amount, state, "
            "price_basis, created, deadline, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (res_id, customer, period, cls, scope, idem, fingerprint, amount, "reserved", basis, now,
             now + max(1.0, float(ttl_s)), now),
        )
        return Decision(True, "ok", res_id, remaining - amount, "")

    @staticmethod
    def _scope_allows(db: sqlite3.Connection, scope: str, customer: str, amount: int) -> bool:
        row = db.execute("SELECT * FROM scopes WHERE scope=?", (scope,)).fetchone()
        if row is None or row["customer"] != customer:
            return False
        used = db.execute(
            _COMMITTED + "WHERE scope=?", (scope,)
        ).fetchone()[0]
        calls = db.execute(
            "SELECT COUNT(*) FROM entries WHERE scope=? AND state != 'released'", (scope,)
        ).fetchone()[0]
        return int(used) + amount <= int(row["max_amount"]) and int(calls) + 1 <= int(row["max_calls"])

    def settle(
        self, res_id: str, actual: int | None, *, measured: bool = True, now: float | None = None
    ) -> Decision:
        """Replace a reservation by the real amount. Idempotent.

        ``actual=None`` = the call ran but its usage is unknown: the row becomes ``unknown`` and keeps
        holding the worst case (never zero). A later ``settle`` with a provider-reported amount
        resolves it. A different amount for an already settled row is a ``conflict`` (no change)."""
        now = time.time() if now is None else now
        if actual is not None and _money(actual) is None:
            return Decision(False, "invalid", res_id, None, "")
        with self._tx() as db:
            row = db.execute("SELECT * FROM entries WHERE res_id=?", (res_id,)).fetchone()
            if row is None:
                return Decision(False, "not_found", res_id, None, "")
            rem = self._remaining(db, row["customer"], row["period"])
            state = row["state"]
            if state == "settled":
                same = actual is None or actual == row["actual"]
                return Decision(same, "replay" if same else "conflict", res_id, rem, "")
            if state == "released":
                return Decision(False, "conflict", res_id, rem, "")
            if actual is None:
                if state == "reserved":
                    db.execute(
                        "UPDATE entries SET state='unknown', note='usage_unknown', updated=? "
                        "WHERE res_id=?",
                        (now, res_id),
                    )
                return Decision(True, "unknown_usage", res_id, rem, "")
            over = max(0, actual - int(row["amount"]))
            db.execute(
                "UPDATE entries SET state='settled', actual=?, actual_basis=?, overrun=?, updated=? "
                "WHERE res_id=?",
                (actual, "measured" if measured else "estimated", over, now, res_id),
            )
            return Decision(True, "ok", res_id, self._remaining(db, row["customer"], row["period"]), "")

    def release(self, res_id: str, *, operator: bool = False, now: float | None = None) -> Decision:
        """Give back a reservation whose call never started. An ``unknown`` row can be released
        only by an operator who confirmed the call did not run."""
        now = time.time() if now is None else now
        with self._tx() as db:
            row = db.execute("SELECT * FROM entries WHERE res_id=?", (res_id,)).fetchone()
            if row is None:
                return Decision(False, "not_found", res_id, None, "")
            rem = self._remaining(db, row["customer"], row["period"])
            state = row["state"]
            if state == "released":
                return Decision(True, "replay", res_id, rem, "")
            if state == "settled" or (state == "unknown" and not operator):
                return Decision(False, "conflict", res_id, rem, "")
            db.execute(
                "UPDATE entries SET state='released', note=?, updated=? WHERE res_id=?",
                ("operator_confirmed_not_started" if state == "unknown" else "not_started", now, res_id),
            )
            return Decision(True, "ok", res_id, self._remaining(db, row["customer"], row["period"]), "")

    def recover(self, *, now: float | None = None) -> int:
        """Crash recovery: reservations past their deadline become ``unknown`` (still held)."""
        now = time.time() if now is None else now
        with self._tx() as db:
            cur = db.execute(
                "UPDATE entries SET state='unknown', note='expired_unresolved', updated=? "
                "WHERE state='reserved' AND deadline < ?",
                (now, now),
            )
            return cur.rowcount

    def record_usage(
        self,
        customer: str,
        cls: str,
        amount: int,
        *,
        idem: str,
        period: str,
        measured: bool = True,
        now: float | None = None,
    ) -> Decision:
        """Cost that accrued without a reservation (compute hours, storage, egress from provider
        usage records). Always recorded, even over the cap: the money is already spent. Idempotent."""
        now = time.time() if now is None else now
        value = _money(amount)
        if cls not in COST_CLASSES or value is None or not customer or not idem or not period:
            return Decision(False, "invalid", "", None, "")
        with self._tx() as db:
            prior = db.execute(
                "SELECT * FROM entries WHERE customer=? AND idem=?", (customer, idem)
            ).fetchone()
            rem = self._remaining(db, customer, period)
            if prior is not None:
                same = prior["state"] == "settled" and prior["actual"] == value and prior["cls"] == cls
                return Decision(same, "replay" if same else "conflict", prior["res_id"], rem, "")
            res_id = os.urandom(12).hex()
            db.execute(
                "INSERT INTO entries (res_id, customer, period, cls, scope, idem, amount, state, "
                "price_basis, actual, actual_basis, created, updated, note) "
                "VALUES (?,?,?,?,NULL,?,?,'settled','accrual',?,?,?,?,'unreserved')",
                (res_id, customer, period, cls, idem, value, value,
                 "measured" if measured else "estimated", now, now),
            )
            return Decision(True, "ok", res_id, self._remaining(db, customer, period), "")

    # -- reporting (feeds the customer's consumption screen) --------------------------------------

    def usage(self, customer: str, period: str) -> dict[str, object]:
        """Per class: measured, estimated, held (reserved + unknown), plus cap and remaining.
        Measured and estimated are kept apart; nothing is merged into a made-up single truth."""
        with self._lock:
            db = self._db
            rows = db.execute(
                "SELECT cls, state, actual_basis, SUM(CASE WHEN state='settled' THEN actual "
                "ELSE amount END) AS s, COUNT(*) AS n FROM entries "
                "WHERE customer=? AND period=? AND state != 'released' "
                "GROUP BY cls, state, actual_basis",
                (customer, period),
            ).fetchall()
            caps = {
                r["cls"]: int(r["cap"])
                for r in db.execute(
                    "SELECT cls, cap FROM caps WHERE customer=? AND period=?", (customer, period)
                )
            }
            blocked = db.execute("SELECT reason FROM blocks WHERE customer=?", (customer,)).fetchone()
            breaker = db.execute("SELECT v FROM meta WHERE k='breaker'").fetchone()
        classes = {c: {"measured": 0, "estimated": 0, "held": 0, "unknown_rows": 0} for c in COST_CLASSES}
        for r in rows:
            bucket = classes[r["cls"]]
            if r["state"] == "settled":
                bucket[r["actual_basis"] or "estimated"] += int(r["s"])
            else:
                bucket["held"] += int(r["s"])
                if r["state"] == "unknown":
                    bucket["unknown_rows"] += int(r["n"])
        total = sum(b["measured"] + b["estimated"] + b["held"] for b in classes.values())
        cap = caps.get(TOTAL)
        return {
            "period": period,
            "classes": classes,
            "class_caps": {c: v for c, v in caps.items() if c != TOTAL},
            "total_committed": total,
            "cap": cap,
            "remaining": None if cap is None else cap - total,
            "over_cap": cap is not None and total > cap,
            "blocked": bool(blocked) or bool(breaker) or cap is None or total >= (cap or 0),
            "block_reason": (blocked["reason"] if blocked else None),
            "breaker": (breaker["v"] if breaker else None),
        }

    # -- reconciliation ---------------------------------------------------------------------------

    def reconcile(
        self,
        customer: str,
        period: str,
        cls: str,
        provider_amount: int,
        *,
        tolerance: int = 0,
        now: float | None = None,
    ) -> ReconcileResult:
        """Compare the ledger (settled + unknown, NOT live reservations) with a provider figure.
        Provider above ledger beyond ``tolerance``: the ledger under-recorded, so the customer is
        blocked until reviewed. Ledger above provider: kept (conservative), flagged for review."""
        value, tol = _money(provider_amount), _money(tolerance)
        if value is None or tol is None or cls not in COST_CLASSES:
            raise ValueError("invalid reconciliation input")
        now = time.time() if now is None else now
        with self._tx() as db:
            ledger = int(
                db.execute(
                    "SELECT COALESCE(SUM(CASE state WHEN 'settled' THEN actual ELSE amount END),0) "
                    "FROM entries WHERE customer=? AND period=? AND cls=? "
                    "AND state IN ('settled','unknown')",
                    (customer, period, cls),
                ).fetchone()[0]
            )
            drift = value - ledger
            action = "ok"
            if drift > tol:
                action = "blocked"
                db.execute(
                    "INSERT OR IGNORE INTO blocks (customer, reason, ts) VALUES (?,?,?)",
                    (customer, f"drift_under_recorded:{period}:{cls}", now),
                )
            elif -drift > tol:
                action = "review_over_recorded"
            db.execute(
                "INSERT INTO recon (customer, period, cls, provider, ledger, drift, action, ts) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (customer, period, cls, value, ledger, drift, action, now),
            )
            return ReconcileResult(action, value, ledger, drift)

    def reconcile_global(
        self, period: str, cls: str, provider_amount: int, *, tolerance: int = 0, now: float | None = None
    ) -> ReconcileResult:
        """Same comparison over ALL customers (provider invoices are usually per account, not per
        customer). Provider above ledger beyond tolerance trips the operator circuit breaker."""
        value, tol = _money(provider_amount), _money(tolerance)
        if value is None or tol is None or cls not in COST_CLASSES:
            raise ValueError("invalid reconciliation input")
        now = time.time() if now is None else now
        with self._tx() as db:
            ledger = int(
                db.execute(
                    "SELECT COALESCE(SUM(CASE state WHEN 'settled' THEN actual ELSE amount END),0) "
                    "FROM entries WHERE period=? AND cls=? AND state IN ('settled','unknown')",
                    (period, cls),
                ).fetchone()[0]
            )
            drift = value - ledger
            action = "ok"
            if drift > tol:
                action = "breaker"
                db.execute(
                    "INSERT OR REPLACE INTO meta (k, v) VALUES ('breaker', ?)",
                    (f"global_drift:{period}:{cls}",),
                )
                self._audit(db, "system", "trip_breaker", f"global_drift:{period}:{cls}", now)
            elif -drift > tol:
                action = "review_over_recorded"
            db.execute(
                "INSERT INTO recon (customer, period, cls, provider, ledger, drift, action, ts) "
                "VALUES (NULL,?,?,?,?,?,?,?)",
                (period, cls, value, ledger, drift, action, now),
            )
            return ReconcileResult(action, value, ledger, drift)

    def clear_block(self, customer: str, *, actor: str, note: str, now: float | None = None) -> bool:
        if not actor or not note:
            raise ValueError("clearing a block needs an actor and a note")
        with self._tx() as db:
            cur = db.execute("DELETE FROM blocks WHERE customer=?", (customer,))
            self._audit(db, actor, "clear_block", f"{customer}: {note}", now or time.time())
            return cur.rowcount == 1

    def raise_loss(
        self, key: str, attributed: str, operator: str, cls: str, period: str, total: int, *, now: float | None = None
    ) -> int:
        """Raise the OPERATOR_LOSS of ``key`` to ``total`` (monotonic) and book the difference under
        ``operator`` in ONE transaction: either both rows move or neither does, so a retry after a
        storage error adds the missing amount. Returns the newly added amount."""
        value = _money(total)
        if value is None or cls not in COST_CLASSES or not key or not attributed or not operator:
            raise ValueError("invalid loss record")
        now = time.time() if now is None else now
        with self._tx() as db:
            row = db.execute("SELECT total FROM losses WHERE key=?", (key,)).fetchone()
            before = 0 if row is None else int(row["total"])
            if value <= before:
                return 0
            db.execute(
                "INSERT OR REPLACE INTO losses (key, customer, cls, period, total, updated) VALUES (?,?,?,?,?,?)",
                (key, attributed, cls, period, value, now),
            )
            idem = f"loss:{key}"
            cur = db.execute(
                "UPDATE entries SET amount=?, actual=?, updated=? WHERE customer=? AND idem=? AND state='settled'",
                (value, value, now, operator, idem),
            )
            if cur.rowcount == 0:
                db.execute(
                    "INSERT INTO entries (res_id, customer, period, cls, scope, idem, amount, state, "
                    "price_basis, actual, actual_basis, created, updated, note) "
                    "VALUES (?,?,?,?,NULL,?,?,'settled','accrual',?,'measured',?,?,'operator_loss')",
                    (os.urandom(12).hex(), operator, period, cls, idem, value, value, now, now),
                )
            return value - before

    def losses(self) -> list[tuple[str, str, str, str, int]]:
        """(key, attributed customer, class, period, total) for every recorded operator loss."""
        with self._lock:
            rows = self._db.execute("SELECT key, customer, cls, period, total FROM losses ORDER BY key").fetchall()
        return [(r["key"], r["customer"], r["cls"], r["period"], int(r["total"])) for r in rows]

    def max_idem_seq(self, customer: str, prefix: str) -> int:
        """Highest numeric suffix of ``<prefix>:<n>`` among the customer's idempotency keys (0 if none)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT idem FROM entries WHERE customer=? AND idem LIKE ? ESCAPE '\\'",
                (customer, prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ":%"),
            ).fetchall()
        best = 0
        for r in rows:
            tail = str(r["idem"])[len(prefix) + 1 :]
            if tail.isdigit():
                best = max(best, int(tail))
        return best

    def get_entry(self, res_id: str) -> dict[str, object] | None:
        """Read-only copy of one ledger row (used by the broker for settle/late-usage after a
        restart). ``None`` when unknown."""
        with self._lock:
            row = self._db.execute("SELECT * FROM entries WHERE res_id=?", (res_id,)).fetchone()
        return None if row is None else {key: row[key] for key in row.keys()}  # noqa: SIM118

    def close(self) -> None:
        with self._lock:
            self._db.close()
