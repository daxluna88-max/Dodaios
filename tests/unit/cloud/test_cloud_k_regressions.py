# ruff: noqa: E501
"""Regression tests for review findings K3-K8 (cloud broker/ledger/adapter). Offline, abstract units."""
from __future__ import annotations

import sqlite3

import pytest

from dodaios.cloud.broker import (
    Broker,
    CostClass,
    Usage,
)
from dodaios.cloud.budget_ledger import BudgetLedger
from dodaios.cloud.ledger_budget import LedgerBudget
from tests.unit.cloud.test_broker import NOW, PERIOD, C, make, spec


def auth(broker, token, **kw):
    return broker.authorize(token, spec(**kw), now=NOW)


def test_k3_replay_with_larger_cost_is_refused():
    broker, token = make()
    first = auth(broker, token, request_id="r1", max_input_tokens=100, max_output_tokens=0)
    assert first.ok
    bigger = auth(broker, token, request_id="r1", max_input_tokens=900, max_output_tokens=0)
    assert not bigger.ok and bigger.code == "conflict"


def test_k3_identical_replay_still_works_while_reserved():
    broker, token = make()
    first = auth(broker, token, request_id="r1")
    again = auth(broker, token, request_id="r1")
    assert again.ok and again.code == "replay" and again.res_id == first.res_id


def test_k3_replay_of_a_settled_request_is_not_permission_to_call_again():
    broker, token = make()
    first = auth(broker, token, request_id="r1")
    broker.settle(first.res_id, Usage(input_tokens=10, output_tokens=10), now=NOW)
    again = auth(broker, token, request_id="r1")
    assert not again.ok and again.code == "replay_closed"


def test_k3_recreated_adapter_does_not_reuse_a_smaller_reservation():
    ledger = BudgetLedger()
    ledger.set_cap(C, PERIOD, "*", 10_000, actor="owner")
    ledger.open_scope("s", C, max_amount=10_000, max_calls=10)

    def adapter():
        return LedgerBudget(ledger, customer=C, period=PERIOD, scope="s", idem_prefix="turn1", scale=1)

    assert adapter().reserve(100)
    second = adapter()
    assert second.reserve(900)
    held = ledger.usage(C, PERIOD)["total_committed"]
    assert held >= 900 + 100  # the new claim is really held; the old one is not silently reused
    assert second.reserved == 900


def test_k4_owner_estimate_obeys_the_per_step_ceiling():
    broker, token = make(per_step_ceiling=500)
    broker.prices.set_call_estimate("mystery", CostClass.INFERENCE, 700, actor="owner")
    out = auth(broker, token, engine="mystery")
    assert not out.ok and out.code == "call_ceiling"


def test_k5_repeated_release_refunds_escalation_only_once():
    broker, token = make(allowance=4000)
    a = auth(broker, token, engine="pro", request_id="a")
    b = auth(broker, token, engine="pro", request_id="b", turn_id="t2")
    assert a.ok and b.ok
    broker.release(a.res_id, now=NOW)
    broker.release(a.res_id, now=NOW)
    third = auth(broker, token, engine="pro", request_id="c", turn_id="t3", max_input_tokens=2000, max_output_tokens=1000)
    assert not third.ok and third.code == "escalation_not_allowed"


@pytest.mark.parametrize("bad", [
    Usage(input_tokens=-1, output_tokens=-1),
    Usage(input_tokens=True),  # type: ignore[arg-type]
    Usage(input_tokens=10**13),
    Usage(provider_cost=-5),
])
def test_k6_malformed_usage_keeps_the_hold(bad):
    broker, token = make()
    ledger = broker.ledger
    a = auth(broker, token)
    res = broker.settle(a.res_id, bad, now=NOW)
    assert not res.ok and res.code == "invalid"
    held = ledger.usage(C, PERIOD)["total_committed"]
    assert held == a.amount and C in broker.needs_reconcile


def test_k7_loss_retry_after_storage_error_is_not_lost(monkeypatch):
    broker, token = make()
    ledger = broker.ledger
    a = auth(broker, token)
    broker.settle(a.res_id, Usage(input_tokens=1000, output_tokens=500), now=NOW)
    real = ledger.raise_loss
    calls = {"n": 0}

    def flaky(*args, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return real(*args, **kw)

    monkeypatch.setattr(ledger, "raise_loss", flaky)
    with pytest.raises(sqlite3.OperationalError):
        broker.report_late_usage(a.res_id, a.amount + 100, now=NOW)
    assert C in broker.holds()  # fail closed even though the write failed
    assert broker.losses.period_total(PERIOD) == 0
    broker.report_late_usage(a.res_id, a.amount + 100, now=NOW)
    assert broker.losses.period_total(PERIOD) == 100


def test_k7_loss_survives_broker_recreation_without_double_count():
    broker, token = make()
    ledger = broker.ledger
    a = auth(broker, token)
    broker.report_late_usage(a.res_id, a.amount + 100, now=NOW)  # reserved -> settles, 100 over -> loss 100
    assert broker.losses.period_total(PERIOD) == 100
    fresh = Broker(ledger, broker.prices, broker.issuer, limits=broker.limits)
    fresh.report_late_usage(a.res_id, a.amount + 200, now=NOW)
    assert fresh.losses.period_total(PERIOD) == 200
    assert fresh.losses.total_for(C, PERIOD, "ai_inference") == 200


def test_k8_unknown_settlement_stays_visible():
    ledger = BudgetLedger()
    ledger.set_cap(C, PERIOD, "*", 10_000, actor="owner")
    ledger.open_scope("s", C, max_amount=10_000, max_calls=10)
    b = LedgerBudget(ledger, customer=C, period=PERIOD, scope="s", idem_prefix="t", scale=1)
    assert b.reserve(1000)
    res_id = b.open_reservations()[0].res_id
    assert b.settle(res_id, None)
    assert [r.res_id for r in b.open_reservations()] == [res_id] and b.reserved == 1000
    assert b.settle(res_id, 400)
    assert b.open_reservations() == []


def test_k8_failed_release_keeps_local_state(monkeypatch):
    ledger = BudgetLedger()
    ledger.set_cap(C, PERIOD, "*", 10_000, actor="owner")
    ledger.open_scope("s", C, max_amount=10_000, max_calls=10)
    b = LedgerBudget(ledger, customer=C, period=PERIOD, scope="s", idem_prefix="t", scale=1)
    assert b.reserve(1000)
    res_id = b.open_reservations()[0].res_id
    b.settle(res_id, None)
    b.release(1000)  # unknown row cannot be released by a non-operator: stays held and visible
    assert b.reserved == 1000
