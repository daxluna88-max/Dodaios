# ruff: noqa: E501  (long lines; no formatter was run)
"""Tests for dodaios.cloud.broker (ADR-0068, block 20). WRITTEN, NOT RUN.

Abstract integer minor units; the prices below are test fixtures, not real prices."""
from __future__ import annotations

import dataclasses
import itertools
import threading

import pytest

from dodaios.cloud.broker import (
    BROKER_MESSAGES_IT,
    OPERATOR_CUSTOMER,
    Broker,
    BrokerLimits,
    CallSpec,
    CostClass,
    EngineTier,
    GatewayReply,
    Grant,
    GuestCredentialIssuer,
    GuestGateway,
    OwnerPriceTable,
    Price,
    StopAction,
    Usage,
    forbidden_env_names,
    guest_environment,
    worst_case,
)
from dodaios.cloud.budget_ledger import MESSAGES_IT, BudgetLedger, period_of

NOW = 1_793_000_000.0
PERIOD = period_of(NOW)
C = "c1"
_ids = itertools.count(1)


def spec(**kw) -> CallSpec:
    base = dict(cls=CostClass.INFERENCE, engine="eco", turn_id="t1", step=1,
                request_id=f"r{next(_ids)}", max_input_tokens=1000, max_output_tokens=500)
    base.update(kw)
    return CallSpec(**base)  # worst case with the fixture prices: 1000 + 1000 = 2000


def make(*, cap=100_000, allowance=10_000, **limits):
    ledger = BudgetLedger()
    if cap is not None:
        ledger.set_cap(C, PERIOD, "*", cap, actor="owner")
    prices = OwnerPriceTable()
    p = Price(per_m_input=1_000_000, per_m_output=2_000_000, per_image=500, per_voice_second=10)
    prices.set_price("eco", p, actor="owner")
    prices.set_price("pro", p, actor="owner")
    issuer = GuestCredentialIssuer("https://broker.invalid/v1")
    lim = BrokerLimits(per_turn_ceiling=50_000, max_steps_per_turn=50, **limits)
    broker = Broker(ledger, prices, issuer, limits=lim)
    broker.set_grant(
        C, Grant({"eco": EngineTier.ECONOMY, "pro": EngineTier.ESCALATED, "mystery": EngineTier.ECONOMY}, allowance),
        actor="owner",
    )
    cred = issuer.issue(C, "s1", ttl_s=3600, now=NOW)
    return broker, cred.token


def auth(broker, token, **kw):
    return broker.authorize(token, spec(**kw), now=NOW)


# -- guest separation ---------------------------------------------------------------------------


def test_guest_environment_has_only_endpoint_and_token():
    broker, token = make()
    cred = broker.issuer.issue(C, "s2", ttl_s=60, now=NOW)
    env = guest_environment(cred)
    assert set(env) == {"JARVIS_BROKER_ENDPOINT", "JARVIS_BROKER_TOKEN"}
    assert forbidden_env_names(env) == []


@pytest.mark.parametrize("name,value", [("OPENAI_API_KEY", "x"), ("PROVIDER_SECRET", "x"), ("FOO", "sk-live-abc")])
def test_forbidden_env_detects_provider_keys(name, value):
    cred = GuestCredentialIssuer("e").issue("c", "s", ttl_s=5)
    env = {**guest_environment(cred), name: value}
    assert forbidden_env_names(env) == [name]


def test_token_is_opaque_hashed_expiring_and_revocable():
    issuer = GuestCredentialIssuer("e")
    cred = issuer.issue(C, "s", ttl_s=100, now=NOW)
    assert cred.token not in issuer._rows  # noqa: SLF001 - only a hash is stored
    assert issuer.resolve(cred.token, now=NOW).customer == C
    assert issuer.resolve("nope", now=NOW) is None
    assert issuer.resolve(cred.token, now=NOW + 101) is None
    assert issuer.revoke_customer(C) == 1
    assert issuer.resolve(cred.token, now=NOW) is None


def test_unauthenticated_call_is_refused():
    broker, _ = make()
    d = broker.authorize("bad-token", spec(), now=NOW)
    assert not d.ok and d.code == "unauthenticated" and d.message_it


# -- price table and worst case -----------------------------------------------------------------


def test_worst_case_arithmetic_and_unknown_components():
    p = Price(per_m_input=1_000_000, per_m_output=2_000_000)
    assert worst_case(spec(), p) == 2000
    assert worst_case(spec(), None) is None
    assert worst_case(spec(images=1, cls=CostClass.VISION), p) is None  # per_image unknown, not zero
    assert worst_case(spec(max_input_tokens=1, max_output_tokens=0), Price(per_m_input=1)) == 1  # rounds up


def test_unknown_price_refused_then_explicit_owner_estimate():
    broker, token = make()
    d = auth(broker, token, engine="mystery")
    assert not d.ok and d.code == "unknown_price" and d.stop is StopAction.REFUSE_CALL
    broker.prices.set_call_estimate("mystery", CostClass.INFERENCE, 700, actor="owner")
    d = auth(broker, token, engine="mystery")
    assert d.ok and d.price_basis == "owner_estimate" and d.amount == 700


def test_engine_not_in_grant_is_refused():
    broker, token = make()
    assert auth(broker, token, engine="other").code == "engine_not_allowed"


# -- reserve / settle / release -----------------------------------------------------------------


def test_authorize_holds_worst_case_and_settle_refunds_difference():
    broker, token = make()
    d = auth(broker, token)
    assert d.ok and d.amount == 2000
    assert broker.ledger.usage(C, PERIOD)["total_committed"] == 2000
    r = broker.settle(d.res_id, Usage(input_tokens=300, output_tokens=100), now=NOW)
    assert r.ok and r.customer_amount == 500 and r.operator_loss == 0
    assert broker.ledger.usage(C, PERIOD)["total_committed"] == 500


def test_over_reservation_is_operator_loss_not_customer_debt():
    broker, token = make()
    d = auth(broker, token)
    r = broker.settle(d.res_id, Usage(input_tokens=4000, output_tokens=1000), now=NOW)  # actual 6000
    assert r.ok and r.customer_amount == 2000 and r.operator_loss == 4000
    assert broker.ledger.usage(C, PERIOD)["total_committed"] == 2000  # customer never above the reservation
    assert broker.losses.period_total(PERIOD) == 4000
    assert broker.ledger.usage(OPERATOR_CUSTOMER, PERIOD)["total_committed"] == 4000
    assert C in broker.holds() and C in broker.needs_reconcile
    view = broker.customer_usage_view(C, PERIOD)
    assert "4000" not in repr(view) and view["stopped"] is True and view["no_overage"] is True


def test_operator_loss_stops_new_work_with_neutral_message():
    broker, token = make()
    d = auth(broker, token)
    broker.settle(d.res_id, Usage(provider_cost=9000), now=NOW)
    again = auth(broker, token)
    assert not again.ok and again.code == "operator_hold" and again.stop is StopAction.STOP_NEW_WORK
    assert "debito" not in again.message_it.lower() and "pagare" not in again.message_it.lower()
    assert broker.clear_hold(C, actor="op", note="reviewed") is True
    assert auth(broker, token).ok


def test_double_settle_replays_and_conflicts_without_double_loss():
    broker, token = make()
    d = auth(broker, token)
    u = Usage(provider_cost=2500)
    first = broker.settle(d.res_id, u, now=NOW)
    again = broker.settle(d.res_id, u, now=NOW)
    assert first.ok and again.code == "replay" and broker.losses.period_total(PERIOD) == 500
    other = broker.settle(d.res_id, Usage(provider_cost=1000), now=NOW)
    assert not other.ok
    assert broker.ledger.usage(C, PERIOD)["total_committed"] == 2000


def test_unknown_usage_keeps_worst_case_held():
    broker, token = make()
    d = auth(broker, token)
    r = broker.settle(d.res_id, None, now=NOW)
    assert r.code == "unknown_usage"
    assert broker.ledger.usage(C, PERIOD)["total_committed"] == 2000


def test_release_on_non_start_gives_room_back_and_is_idempotent():
    broker, token = make()
    d = auth(broker, token)
    assert broker.release(d.res_id).ok
    assert broker.release(d.res_id).code == "replay"
    assert broker.ledger.usage(C, PERIOD)["total_committed"] == 0
    s = auth(broker, token)
    broker.settle(s.res_id, Usage(provider_cost=10), now=NOW)
    assert not broker.release(s.res_id).ok


def test_same_request_id_is_an_idempotent_replay():
    broker, token = make()
    s = spec()
    a = broker.authorize(token, s, now=NOW)
    b = broker.authorize(token, s, now=NOW)
    assert a.ok and b.ok and b.code == "replay" and a.res_id == b.res_id
    assert broker.ledger.usage(C, PERIOD)["total_committed"] == 2000


# -- crash, late records, unreserved ------------------------------------------------------------


def test_crash_after_reserve_holds_unknown_and_survives_restart():
    broker, token = make()
    d = auth(broker, token)
    assert broker.recover(now=NOW + 1000) == 1
    assert broker.ledger.usage(C, PERIOD)["classes"]["ai_inference"]["unknown_rows"] == 1
    fresh = Broker(broker.ledger, broker.prices, broker.issuer, limits=broker.limits)  # restart: memory empty
    unpriced = fresh.settle(d.res_id, Usage(input_tokens=1, output_tokens=1), now=NOW + 1001)
    assert unpriced.code == "unknown_usage"  # engine unknown after restart: stays held, never zero
    resolved = fresh.settle(d.res_id, Usage(provider_cost=1500), now=NOW + 1002)
    assert resolved.ok and resolved.customer_amount == 1500
    assert fresh.ledger.usage(C, PERIOD)["total_committed"] == 1500


def test_late_provider_record_after_release_is_operator_loss():
    broker, token = make()
    d = auth(broker, token)
    broker.release(d.res_id)
    r = broker.report_late_usage(d.res_id, 1800, now=NOW + 5)
    assert r.ok and r.operator_loss == 1800
    assert broker.ledger.usage(C, PERIOD)["total_committed"] == 0
    assert C in broker.holds()
    assert broker.report_late_usage(d.res_id, 1800, now=NOW + 6).operator_loss == 1800  # no extra row
    assert broker.losses.period_total(PERIOD) == 1800


def test_late_record_above_settled_amount_books_only_the_difference():
    broker, token = make()
    d = auth(broker, token)
    broker.settle(d.res_id, Usage(provider_cost=500), now=NOW)
    r = broker.report_late_usage(d.res_id, 900, now=NOW + 5)
    assert r.operator_loss == 400 and broker.ledger.usage(C, PERIOD)["total_committed"] == 500


def test_unreserved_usage_is_operator_loss_and_holds_customer():
    broker, _ = make()
    r = broker.report_unreserved_usage(C, CostClass.INFERENCE, 777, idem="x1", period=PERIOD, now=NOW)
    assert r.operator_loss == 777 and C in broker.holds()
    assert broker.ledger.usage(C, PERIOD)["total_committed"] == 0


def test_operator_loss_threshold_trips_global_breaker():
    broker, _ = make(operator_loss_breaker_threshold=500)
    broker.report_unreserved_usage(C, CostClass.INFERENCE, 600, idem="x", period=PERIOD, now=NOW)
    assert broker.ledger.breaker() is not None


# -- limits -------------------------------------------------------------------------------------


def test_token_limits():
    broker, token = make(max_input_tokens=500)
    assert auth(broker, token, max_input_tokens=501).code == "limit_tokens"
    assert auth(broker, token, max_output_tokens=10_000).code == "limit_tokens"


def test_max_steps_per_turn():
    broker, token = make()
    broker.limits = dataclasses.replace(broker.limits, max_steps_per_turn=2)
    assert auth(broker, token, step=1).ok and auth(broker, token, step=2).ok
    d = auth(broker, token, step=3)
    assert d.code == "limit_steps" and d.stop is StopAction.STOP_TURN


def test_retry_cap_per_step():
    broker, token = make(max_retries=1, max_concurrency=5)
    assert auth(broker, token, step=1).ok and auth(broker, token, step=1).ok
    d = auth(broker, token, step=1)
    assert d.code == "limit_retries" and d.stop is StopAction.STOP_TURN


def test_concurrency_cap_and_release_by_settle():
    broker, token = make(max_concurrency=2)
    a, b = auth(broker, token, step=1), auth(broker, token, step=2)
    assert a.ok and b.ok
    assert auth(broker, token, step=3).code == "limit_concurrency"
    broker.settle(a.res_id, Usage(provider_cost=1), now=NOW)
    assert auth(broker, token, step=3).ok


def test_concurrency_cap_holds_under_threads():
    broker, token = make(max_concurrency=2)
    out = []
    threads = [threading.Thread(target=lambda i=i: out.append(auth(broker, token, step=i))) for i in range(1, 21)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for d in out if d.ok) == 2


def test_voice_segment_and_session_caps():
    broker, token = make(max_voice_seconds_per_segment=30, max_voice_seconds_per_session=50, max_concurrency=9)
    v = dict(cls=CostClass.VOICE, max_input_tokens=0, max_output_tokens=0)
    assert auth(broker, token, voice_seconds=31, step=1, **v).code == "limit_voice"
    assert auth(broker, token, voice_seconds=20, step=2, **v).ok
    assert auth(broker, token, voice_seconds=20, step=3, **v).ok
    d = auth(broker, token, voice_seconds=20, step=4, **v)
    assert d.code == "limit_voice" and d.message_it


def test_vision_image_caps_and_pricing():
    broker, token = make(max_images_per_call=1, max_images_per_turn=2, max_concurrency=9)
    v = dict(cls=CostClass.VISION, max_input_tokens=100, max_output_tokens=100)
    assert auth(broker, token, images=2, step=1, **v).code == "limit_images"
    a = auth(broker, token, images=1, step=2, **v)
    assert a.ok and a.amount == 100 + 200 + 500
    assert auth(broker, token, images=1, step=3, **v).ok
    assert auth(broker, token, images=1, step=4, **v).code == "limit_images"


# -- escalation, ceilings, quota ----------------------------------------------------------------


def test_escalation_needs_allowance_and_is_bounded():
    broker, token = make(allowance=0)
    assert auth(broker, token, engine="pro").code == "escalation_not_allowed"
    broker, token = make(allowance=3000, max_concurrency=9)
    first = auth(broker, token, engine="pro", step=1)
    assert first.ok
    assert auth(broker, token, engine="pro", step=2).code == "escalation_not_allowed"
    broker.settle(first.res_id, Usage(provider_cost=500), now=NOW)
    assert auth(broker, token, engine="pro", step=3).ok
    assert auth(broker, token, engine="eco", step=4).ok  # economy default is unaffected


def test_vision_draws_on_the_escalation_allowance():
    broker, token = make(allowance=0)
    d = auth(broker, token, cls=CostClass.VISION, images=1, max_input_tokens=10, max_output_tokens=10)
    assert d.code == "escalation_not_allowed"


def test_per_step_and_per_turn_ceilings():
    broker, token = make(per_step_ceiling=1000)
    d = auth(broker, token)
    assert d.code == "call_ceiling" and d.stop is StopAction.STOP_TURN
    broker, token = make(max_concurrency=9)
    broker.limits = dataclasses.replace(broker.limits, per_turn_ceiling=3000)
    assert auth(broker, token, step=1).ok
    d = auth(broker, token, step=2)
    assert d.code == "scope_ceiling" and d.stop is StopAction.STOP_TURN


def test_default_per_turn_ceiling_fails_closed():
    broker, token = make()
    broker.limits = dataclasses.replace(broker.limits, per_turn_ceiling=0)
    assert auth(broker, token).code == "scope_ceiling"


def test_cap_reached_stops_new_work_with_italian_message():
    broker, token = make(cap=1000)
    d = auth(broker, token)
    assert not d.ok and d.code == "cap_reached" and d.stop is StopAction.STOP_NEW_WORK
    assert d.message_it == MESSAGES_IT["cap_reached"]


def test_no_cap_row_means_nothing_is_reserved():
    broker, token = make(cap=None)
    d = auth(broker, token)
    assert d.code == "no_cap" and d.stop is StopAction.STOP_NEW_WORK


def test_breaker_blocks_new_authorizations():
    broker, token = make()
    broker.ledger.trip_breaker("test", actor="op")
    assert auth(broker, token).code == "breaker"


# -- reconcile, accruals, customer view ---------------------------------------------------------


def test_reconcile_excludes_operator_loss_attributed_to_the_customer():
    broker, token = make()
    d = auth(broker, token)
    broker.settle(d.res_id, Usage(provider_cost=6000), now=NOW)  # customer 2000, loss 4000
    assert broker.reconcile(C, PERIOD, CostClass.INFERENCE, 6000) == "ok"
    assert broker.reconcile(C, PERIOD, CostClass.INFERENCE, 7000) == "blocked"
    assert "drift" in broker.holds()[C] or "operator_loss" in broker.holds()[C]


def test_global_reconcile_includes_operator_loss_rows():
    broker, token = make()
    d = auth(broker, token)
    broker.settle(d.res_id, Usage(provider_cost=6000), now=NOW)
    assert broker.reconcile_global(PERIOD, CostClass.INFERENCE, 6000) == "ok"
    assert broker.reconcile_global(PERIOD, CostClass.INFERENCE, 9000) == "breaker"


def test_accruals_only_for_non_call_classes():
    broker, _ = make()
    assert broker.record_accrual(C, CostClass.COMPUTE, 300, idem="a", period=PERIOD)
    assert broker.record_accrual(C, CostClass.BACKUP, 50, idem="b", period=PERIOD)
    assert not broker.record_accrual(C, CostClass.INFERENCE, 1, idem="c", period=PERIOD)
    assert not broker.record_accrual(C, CostClass.OPERATOR_LOSS, 1, idem="d", period=PERIOD)
    assert broker.ledger.usage(C, PERIOD)["total_committed"] == 350


def test_operator_loss_class_is_never_reservable():
    broker, token = make()
    assert auth(broker, token, cls=CostClass.OPERATOR_LOSS).code == "invalid"


def test_customer_view_has_no_overage_and_no_top_up():
    broker, token = make()
    view = broker.customer_usage_view(C, PERIOD)
    assert view["no_overage"] is True and view["auto_top_up"] is False and view["cap"] == 100_000


# -- guest gateway: broker down means no inference ----------------------------------------------


def test_gateway_stops_when_broker_is_down():
    cred = GuestCredentialIssuer("e").issue("c", "s", ttl_s=5)

    def boom(endpoint, token, request):
        raise ConnectionError("down")

    r = GuestGateway(cred, boom).call({"x": 1})
    assert isinstance(r, GatewayReply) and not r.ok and r.code == "broker_down"
    assert r.stop is StopAction.STOP_TURN and r.message_it == BROKER_MESSAGES_IT["broker_down"]


@pytest.mark.parametrize("reply", [None, "x", {}, {"code": "ok"}])
def test_gateway_treats_malformed_reply_as_down(reply):
    cred = GuestCredentialIssuer("e").issue("c", "s", ttl_s=5)
    assert GuestGateway(cred, lambda *a: reply).call({}).code == "broker_down"


def test_gateway_passes_through_a_refusal_and_survives_bad_stop_value():
    cred = GuestCredentialIssuer("e").issue("c", "s", ttl_s=5)
    ok = GuestGateway(cred, lambda *a: {"ok": False, "code": "cap_reached", "message_it": "m", "stop": "stop_new_work"}).call({})
    assert not ok.ok and ok.stop is StopAction.STOP_NEW_WORK
    odd = GuestGateway(cred, lambda *a: {"ok": False, "code": "x", "stop": "???"}).call({})
    assert odd.stop is StopAction.STOP_TURN


def test_messages_never_present_debt_or_absolute_caps():
    for text in BROKER_MESSAGES_IT.values():
        low = text.lower()
        assert "debito" not in low and "devi pagare" not in low and "massimo che puoi pagare" not in low
