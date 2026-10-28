"""Tests for dodaios.cloud.budget_ledger (ADR-0066, block 20). WRITTEN, NOT RUN.

Amounts are abstract integer minor units. No price, provider or currency is implied."""
from __future__ import annotations

import threading

import pytest

from dodaios.cloud.budget_ledger import BudgetLedger

P = "2026-11"
C = "cust-1"


@pytest.fixture
def ledger(tmp_path):
    led = BudgetLedger(tmp_path / "budget.db")
    led.set_cap(C, P, "*", 1000, actor="owner")
    yield led
    led.close()


def test_no_cap_means_nothing_may_be_reserved():
    led = BudgetLedger()
    d = led.reserve(C, "ai_inference", 1, idem="a", period=P)
    assert not d.ok and d.code == "no_cap" and d.message_it


def test_reserve_within_cap_then_refuse_past_it(ledger):
    assert ledger.reserve(C, "ai_inference", 600, idem="a", period=P).ok
    d = ledger.reserve(C, "voice", 401, idem="b", period=P)
    assert not d.ok and d.code == "cap_reached"
    assert ledger.reserve(C, "voice", 400, idem="c", period=P).ok


def test_concurrent_reservations_never_exceed_cap(ledger):
    results = []

    def work(i):
        results.append(ledger.reserve(C, "ai_inference", 100, idem=f"k{i}", period=P))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r.ok) == 10
    assert ledger.usage(C, P)["total_committed"] == 1000


def test_unknown_price_is_refused_never_zero(ledger):
    d = ledger.reserve(C, "ai_inference", None, idem="u", period=P)
    assert not d.ok and d.code == "unknown_price"
    assert ledger.usage(C, P)["total_committed"] == 0


def test_owner_estimate_is_held_and_labelled(ledger):
    d = ledger.reserve(C, "ai_inference", None, idem="u", period=P, owner_estimate=250)
    assert d.ok
    assert ledger.usage(C, P)["classes"]["ai_inference"]["held"] == 250


@pytest.mark.parametrize("bad", [1.5, -1, True, float("nan"), "5"])
def test_non_integer_or_negative_amounts_are_invalid(ledger, bad):
    assert ledger.reserve(C, "ai_inference", bad, idem="x", period=P).code == "invalid"


def test_reserve_is_idempotent_on_idem_key(ledger):
    a = ledger.reserve(C, "ai_inference", 300, idem="same", period=P)
    b = ledger.reserve(C, "ai_inference", 300, idem="same", period=P)
    assert a.ok and b.ok and b.replay and a.res_id == b.res_id
    assert ledger.usage(C, P)["total_committed"] == 300
    assert ledger.reserve(C, "voice", 300, idem="same", period=P).code == "conflict"


def test_settle_replaces_reservation_and_is_idempotent(ledger):
    r = ledger.reserve(C, "ai_inference", 500, idem="a", period=P)
    assert ledger.settle(r.res_id, 120).ok
    assert ledger.usage(C, P)["total_committed"] == 120
    again = ledger.settle(r.res_id, 120)
    assert again.ok and again.replay
    other = ledger.settle(r.res_id, 130)
    assert not other.ok and other.code == "conflict"
    assert ledger.usage(C, P)["total_committed"] == 120


def test_overrun_is_recorded_not_clipped(ledger):
    r = ledger.reserve(C, "ai_inference", 100, idem="a", period=P)
    ledger.settle(r.res_id, 900)
    assert ledger.usage(C, P)["total_committed"] == 900
    d = ledger.reserve(C, "ai_inference", 101, idem="b", period=P)
    assert not d.ok


def test_estimated_and_measured_stay_apart(ledger):
    a = ledger.reserve(C, "voice", 100, idem="a", period=P)
    b = ledger.reserve(C, "voice", 100, idem="b", period=P)
    ledger.settle(a.res_id, 40, measured=True)
    ledger.settle(b.res_id, 60, measured=False)
    voice = ledger.usage(C, P)["classes"]["voice"]
    assert voice["measured"] == 40 and voice["estimated"] == 60


def test_unknown_usage_keeps_holding_worst_case(ledger):
    r = ledger.reserve(C, "ai_inference", 400, idem="a", period=P)
    d = ledger.settle(r.res_id, None)
    assert d.code == "unknown_usage"
    u = ledger.usage(C, P)
    assert u["classes"]["ai_inference"]["held"] == 400
    assert u["classes"]["ai_inference"]["unknown_rows"] == 1
    assert ledger.settle(r.res_id, 150).ok  # provider report resolves it
    assert ledger.usage(C, P)["total_committed"] == 150


def test_release_only_for_calls_that_never_started(ledger):
    r = ledger.reserve(C, "ai_inference", 400, idem="a", period=P)
    assert ledger.release(r.res_id).ok
    assert ledger.release(r.res_id).replay
    assert ledger.usage(C, P)["total_committed"] == 0
    s = ledger.reserve(C, "ai_inference", 100, idem="b", period=P)
    ledger.settle(s.res_id, 50)
    assert ledger.release(s.res_id).code == "conflict"


def test_crash_recovery_marks_expired_unknown_and_still_held(ledger):
    r = ledger.reserve(C, "compute", 300, idem="a", period=P, ttl_s=10, now=1000.0)
    assert ledger.recover(now=1005.0) == 0
    assert ledger.recover(now=1011.0) == 1
    assert ledger.usage(C, P)["classes"]["compute"]["held"] == 300
    assert ledger.release(r.res_id).code == "conflict"  # not without an operator
    assert ledger.release(r.res_id, operator=True).ok
    assert ledger.usage(C, P)["total_committed"] == 0


def test_class_cap_and_call_ceiling(ledger):
    ledger.set_cap(C, P, "voice", 200, actor="owner")
    ledger.set_call_ceiling("ai_inference", 50, actor="op")
    assert ledger.reserve(C, "voice", 201, idem="a", period=P).code == "class_cap_reached"
    assert ledger.reserve(C, "ai_inference", 51, idem="b", period=P).code == "call_ceiling"
    assert ledger.reserve(C, "ai_inference", 50, idem="c", period=P).ok


def test_scope_ceiling_limits_amount_and_number_of_calls(ledger):
    assert ledger.open_scope("run-1", C, max_amount=250, max_calls=3)
    assert ledger.reserve(C, "ai_inference", 100, idem="a", period=P, scope="run-1").ok
    assert ledger.reserve(C, "ai_inference", 100, idem="b", period=P, scope="run-1").ok
    over = ledger.reserve(C, "ai_inference", 100, idem="c", period=P, scope="run-1")
    assert over.code == "scope_ceiling"
    ledger.open_scope("run-2", C, max_amount=10_000, max_calls=1)
    assert ledger.reserve(C, "ai_inference", 1, idem="d", period=P, scope="run-2").ok
    second = ledger.reserve(C, "ai_inference", 1, idem="e", period=P, scope="run-2")
    assert second.code == "scope_ceiling"
    unknown = ledger.reserve(C, "ai_inference", 1, idem="f", period=P, scope="missing")
    assert unknown.code == "scope_ceiling"


def test_accruals_may_exceed_cap_and_then_block_new_spend(ledger):
    d = ledger.record_usage(C, "compute", 1500, idem="acc-1", period=P)
    assert d.ok
    assert ledger.record_usage(C, "compute", 1500, idem="acc-1", period=P).replay
    assert ledger.record_usage(C, "compute", 1400, idem="acc-1", period=P).code == "conflict"
    u = ledger.usage(C, P)
    assert u["over_cap"] and u["blocked"]
    assert ledger.reserve(C, "ai_inference", 1, idem="x", period=P).code == "cap_reached"


def test_breaker_blocks_all_customers_and_needs_a_note_to_reset(ledger):
    ledger.set_cap("cust-2", P, "*", 1000, actor="owner")
    ledger.trip_breaker("provider outage", actor="op")
    assert ledger.reserve(C, "ai_inference", 1, idem="a", period=P).code == "breaker"
    assert ledger.reserve("cust-2", "ai_inference", 1, idem="a", period=P).code == "breaker"
    with pytest.raises(ValueError):
        ledger.reset_breaker(actor="op", note="")
    ledger.reset_breaker(actor="op", note="checked")
    assert ledger.reserve(C, "ai_inference", 1, idem="b", period=P).ok


def test_global_cap(ledger):
    ledger.set_cap("cust-2", P, "*", 1000, actor="owner")
    ledger.set_global_cap(P, 700, actor="op")
    assert ledger.reserve(C, "ai_inference", 500, idem="a", period=P).ok
    assert ledger.reserve("cust-2", "ai_inference", 201, idem="a", period=P).code == "global_cap"


def test_reconcile_blocks_when_provider_is_above_ledger(ledger):
    r = ledger.reserve(C, "ai_inference", 100, idem="a", period=P)
    ledger.settle(r.res_id, 100)
    ok = ledger.reconcile(C, P, "ai_inference", 105, tolerance=10)
    assert ok.action == "ok"
    bad = ledger.reconcile(C, P, "ai_inference", 200, tolerance=10)
    assert bad.action == "blocked" and bad.drift == 100
    assert ledger.reserve(C, "ai_inference", 1, idem="b", period=P).code == "customer_blocked"
    assert ledger.clear_block(C, actor="op", note="reviewed")
    assert ledger.reserve(C, "ai_inference", 1, idem="c", period=P).ok


def test_reconcile_over_recorded_is_flagged_not_blocked(ledger):
    r = ledger.reserve(C, "ai_inference", 100, idem="a", period=P)
    ledger.settle(r.res_id, 100)
    res = ledger.reconcile(C, P, "ai_inference", 10, tolerance=5)
    assert res.action == "review_over_recorded"
    assert ledger.reserve(C, "ai_inference", 1, idem="b", period=P).ok


def test_global_reconcile_trips_breaker(ledger):
    r = ledger.reserve(C, "voice", 100, idem="a", period=P)
    ledger.settle(r.res_id, 100)
    assert ledger.reconcile_global(P, "voice", 100).action == "ok"
    assert ledger.reconcile_global(P, "voice", 500).action == "breaker"
    assert ledger.breaker()


def test_state_survives_reopen(tmp_path):
    path = tmp_path / "b.db"
    a = BudgetLedger(path)
    a.set_cap(C, P, "*", 1000, actor="owner")
    a.reserve(C, "ai_inference", 700, idem="a", period=P)
    a.close()
    b = BudgetLedger(path)
    assert b.reserve(C, "ai_inference", 400, idem="b", period=P).code == "cap_reached"
    b.close()
