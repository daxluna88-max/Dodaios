# ruff: noqa: E501  (long lines; no formatter was run)
"""Tests for dodaios.cloud.ledger_budget (ADR-0068, block 20 adapter). WRITTEN, NOT RUN."""
from __future__ import annotations

import pytest

from dodaios.cloud.budget_ledger import BudgetLedger
from dodaios.cloud.ledger_budget import LedgerBudget

P = "2026-11"
C = "c1"
SCALE = 1_000_000


def make(cap=10_000, calls=10, **kw):
    ledger = BudgetLedger()
    ledger.set_cap(C, P, "*", cap, actor="owner")
    ledger.open_scope("s", C, max_amount=cap, max_calls=calls)
    return ledger, LedgerBudget(ledger, customer=C, period=P, scope="s", idem_prefix="turn1", scale=SCALE, **kw)


def test_has_the_budget_interface():
    _, b = make()
    for name in ("reserve", "release", "reserved", "limit"):
        assert hasattr(b, name)


def test_reserve_converts_with_round_up_and_goes_through_the_ledger():
    ledger, b = make()
    assert b.reserve(0.0000011)  # 1.1 units -> 2
    assert ledger.usage(C, P)["total_committed"] == 2
    assert b.reserved == pytest.approx(2 / SCALE)


def test_cap_refuses_and_release_restores_room():
    ledger, b = make(cap=5000)
    assert b.reserve(0.004)
    assert not b.reserve(0.002)
    b.release(0.004)
    assert ledger.usage(C, P)["total_committed"] == 0
    assert b.reserve(0.002)


def test_unknown_price_needs_allow_and_explicit_estimate():
    ledger, b = make()
    assert not b.reserve(None)
    assert not b.reserve(None, allow_unknown=True)  # no estimate: still refused
    ledger, b = make(unknown_estimate=300)
    assert not b.reserve(None)
    assert b.reserve(None, allow_unknown=True)
    assert ledger.usage(C, P)["total_committed"] == 300
    b.release(None)
    assert ledger.usage(C, P)["total_committed"] == 0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, True, "1"])
def test_bad_amounts_are_refused(bad):
    _, b = make()
    assert not b.reserve(bad)


def test_release_of_an_amount_never_held_is_ignored():
    ledger, b = make()
    assert b.reserve(0.001)
    b.release(0.5)
    b.release(float("nan"))
    assert ledger.usage(C, P)["total_committed"] == 1000


def test_local_unpriced_zero_reservation_counts_a_scope_call():
    _, b = make(calls=1)
    assert b.reserve(0.0)
    assert not b.reserve(0.001)  # scope allows one paid call


def test_settle_replaces_hold_and_closes_it():
    ledger, b = make()
    assert b.reserve(0.002)
    (res,) = b.open_reservations()
    assert b.settle(res.res_id, 800)
    assert ledger.usage(C, P)["total_committed"] == 800 and b.open_reservations() == []


def test_limit_property_reports_remaining_cap():
    _, b = make(cap=4000)
    assert b.limit == pytest.approx(4000 / SCALE)
    b.reserve(0.001)
    assert b.limit == pytest.approx(3000 / SCALE)


def test_invalid_scale_is_rejected():
    with pytest.raises(ValueError):
        LedgerBudget(BudgetLedger(), customer=C, period=P, scope="s", idem_prefix="x", scale=0)
