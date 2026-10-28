# ruff: noqa: E501  (long lines; no formatter was run)
"""Adapter: the orchestrator's in-memory ``Budget`` interface backed by the server ledger (ADR-0068).

Written statically and NOT RUN. ``LedgerBudget`` is duck-type compatible with
``dodaios.brain.consumer_orchestrator.Budget`` (``reserve(amount, *, allow_unknown)``,
``release(amount)``, ``reserved``, ``limit``) so the orchestrator needs no change, but every
reservation goes through ``BudgetLedger.reserve`` (atomic, worst case, caps, scope).

Differences from ``Budget``, on purpose:
* Float amounts are converted to integer minor units with ROUND UP (``scale`` units per 1.0).
* ``allow_unknown=True`` with an unknown price still needs an explicit integer ``unknown_estimate``;
  without it the reservation is refused (hosted: unknown price is never free).
* ``release`` must name the amount; the adapter releases the matching open reservation (most recent
  first). A release for an amount it never held is ignored (never below zero, never invented).
* Settlement is not part of ``Budget``: the caller settles via ``settle(res_id, actual, measured)``
  after the branch ran; ``open_reservations`` lists what is still held.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from dodaios.cloud.budget_ledger import BudgetLedger


@dataclass(frozen=True, slots=True)
class OpenReservation:
    res_id: str
    units: int
    unknown: bool


class LedgerBudget:
    def __init__(
        self,
        ledger: BudgetLedger,
        *,
        customer: str,
        period: str,
        scope: str,
        idem_prefix: str,
        scale: int,
        cls: str = "ai_inference",
        unknown_estimate: int | None = None,
        limit_units: int | None = None,
    ) -> None:
        if not isinstance(scale, int) or isinstance(scale, bool) or scale <= 0:
            raise ValueError("scale must be a positive integer (minor units per 1.0)")
        self._ledger, self._customer, self._period = ledger, customer, period
        self._scope, self._prefix, self._scale, self._cls = scope, idem_prefix, scale, cls
        self._unknown_estimate = unknown_estimate
        self._limit_units = limit_units
        self._lock = threading.Lock()
        self._seq = 0
        self._open: list[OpenReservation] = []

    # -- Budget interface ------------------------------------------------------------------------

    def reserve(self, amount: float | None, *, allow_unknown: bool = False) -> bool:
        if amount is None:
            if not allow_unknown or self._unknown_estimate is None:
                return False
            return self._hold(None, self._unknown_estimate, unknown=True)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
            return False
        return self._hold(math.ceil(amount * self._scale), None, unknown=False)

    def release(self, amount: float | None) -> None:
        with self._lock:
            if amount is None:
                pick = next((r for r in reversed(self._open) if r.unknown), None)
            elif isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
                return
            else:
                units = math.ceil(amount * self._scale)
                pick = next((r for r in reversed(self._open) if not r.unknown and r.units == units), None)
            if pick is None:
                return
            self._open.remove(pick)
        self._ledger.release(pick.res_id)

    @property
    def reserved(self) -> float:
        with self._lock:
            return sum(r.units for r in self._open) / self._scale

    @property
    def limit(self) -> float:
        if self._limit_units is not None:
            return self._limit_units / self._scale
        remaining = self._ledger.usage(self._customer, self._period)["remaining"]
        return 0.0 if not isinstance(remaining, int) else max(0, remaining) / self._scale

    # -- beyond Budget ---------------------------------------------------------------------------

    def open_reservations(self) -> list[OpenReservation]:
        with self._lock:
            return list(self._open)

    def settle(self, res_id: str, actual_units: int | None, *, measured: bool = True) -> bool:
        """Settle one held reservation with the measured amount (None = usage unknown, keeps the hold)."""
        d = self._ledger.settle(res_id, actual_units, measured=measured)
        if d.ok:
            with self._lock:
                self._open = [r for r in self._open if r.res_id != res_id]
        return d.ok

    def _hold(self, units: int | None, estimate: int | None, *, unknown: bool) -> bool:
        with self._lock:
            self._seq += 1
            idem = f"{self._prefix}:{self._seq}"
        d = self._ledger.reserve(
            self._customer, self._cls, units, idem=idem, period=self._period,
            scope=self._scope, owner_estimate=estimate,
        )
        if not d.ok:
            return False
        held = units if units is not None else (estimate or 0)
        with self._lock:
            self._open.append(OpenReservation(d.res_id, held, unknown))
        return True
