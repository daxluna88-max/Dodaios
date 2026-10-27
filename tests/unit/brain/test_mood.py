"""Simulated mood reducer (issue 17): precedence, cooldown, expiry, restart, no autonomy.

Written for the PC checklist (P01, P03, P11); not run when it was written. The clock is a plain
number passed in, so nothing here sleeps.
"""

from __future__ import annotations

import math

import pytest

from dodaios.brain import mood as m
from dodaios.brain import personality as p
from dodaios.core.config import PersonalityConfig


def profile(**kw):
    return p.effective(PersonalityConfig(**kw), "consumer")


def reducer(**kw):
    return m.MoodReducer(profile(**kw), now=0.0)


def test_starts_neutral_and_stays_neutral_with_time_alone():
    r = reducer()
    for now in (0.0, 10.0, 10_000.0, 1e9):
        assert r.state(now) == m.NEUTRAL_STATE


def test_owner_request_sets_a_bounded_expiring_mood_with_a_reason_code():
    r = reducer()
    out = r.apply(m.OwnerMood("serious", 2, ttl_s=100), now=10.0)
    assert out.accepted and out.state.origin == "owner" and out.state.reason == "owner_request"
    assert r.state(50.0).mood == "serious" and r.state(50.0).intensity == 2


def test_expiry_fades_one_level_at_a_time_then_neutral():
    r = reducer()
    r.apply(m.OwnerMood("calm", 3, ttl_s=100), now=0.0)
    assert r.state(99.0).intensity == 3
    assert r.state(100.0).intensity == 2 and r.state(100.0).reason == "fading"
    assert r.state(100.0 + m.DECAY_S).intensity == 1
    assert r.state(100.0 + 2 * m.DECAY_S) == m.NEUTRAL_STATE


def test_hold_keeps_it_for_the_session_and_needs_something_to_hold():
    r = reducer()
    assert r.apply(m.OwnerHold(), 1.0).refused == "nothing_to_hold"
    r.apply(m.OwnerMood("calm", 2, ttl_s=60), 1.0)
    r.apply(m.OwnerHold(), 2.0)
    assert r.state(1e7).mood == "calm" and r.state(1e7).expires_at is None


def test_back_to_neutral_is_immediate_and_a_rule_cannot_bounce_it_back():
    r = reducer()
    r.apply(m.OwnerMood("serious", 2), 0.0)
    r.apply(m.OwnerNeutral(), 5.0)
    assert r.state(5.0) == m.NEUTRAL_STATE
    assert r.apply(m.Dialogue("task_done"), 6.0).refused == "cooldown"


def test_priority_owner_over_activity_over_adaptation():
    r = reducer(expressive_range=2)
    assert r.apply(m.Dialogue("task_done"), 0.0).accepted           # adaptation: cheerful
    assert r.apply(m.Activity("coding"), 1.0).accepted              # activity beats it
    assert r.state(1.0).mood == "focused"
    # once the activity has expired and faded, an adaptation may act again
    assert r.state(500.0) == m.NEUTRAL_STATE
    assert r.apply(m.Dialogue("task_failed"), 500.0).accepted
    r.apply(m.OwnerMood("calm", 2, ttl_s=1000), 600.0)
    assert r.apply(m.Activity("sensitive"), 601.0).refused == "higher_priority_active"
    assert r.state(601.0).mood == "calm"


def test_cooldown_stops_flipping_between_adaptations():
    r = reducer(expressive_range=2)
    assert r.apply(m.Dialogue("task_done"), 0.0).accepted
    flip = r.apply(m.Dialogue("task_failed"), m.COOLDOWN_S - 1)
    assert flip.refused == "cooldown" and r.state(m.COOLDOWN_S - 1).mood == "cheerful"
    assert r.apply(m.Dialogue("task_failed"), m.COOLDOWN_S + 1).accepted


def test_the_same_signal_again_only_renews_the_expiry():
    r = reducer(expressive_range=2)
    r.apply(m.Dialogue("task_done"), 0.0)
    first = r.state(0.0)
    r.apply(m.Dialogue("task_done"), 30.0)
    again = r.state(30.0)
    assert again.mood == first.mood and again.since == first.since
    assert again.expires_at == 30.0 + m.ADAPT_TTL_S


def test_manual_mode_ignores_rules_and_activities_but_obeys_the_owner():
    r = reducer(mode="manual")
    assert r.apply(m.Dialogue("task_failed"), 0.0).refused == "manual_mode"
    assert r.apply(m.Activity("coding"), 0.0).refused == "manual_mode"
    assert r.apply(m.OwnerMood("calm", 1), 0.0).accepted


def test_adaptation_can_be_turned_off_and_on_for_the_session():
    r = reducer(expressive_range=2)
    r.apply(m.OwnerAdaptation(False), 0.0)
    assert r.apply(m.Activity("coding"), 1.0).refused == "adaptation_off"
    r.apply(m.OwnerAdaptation(True), 2.0)
    assert r.apply(m.Activity("coding"), 3.0).accepted


def test_expressive_range_caps_intensity_and_zero_forbids_adaptation():
    capped = reducer(expressive_range=1)
    capped.apply(m.Activity("sensitive"), 0.0)  # preset wants 2
    assert capped.state(0.0).intensity == 1
    none = reducer(expressive_range=0)
    assert none.apply(m.Activity("coding"), 0.0).refused == "range_zero"
    assert none.apply(m.OwnerMood("calm", 3), 0.0).accepted  # the owner is not capped by it


def test_moods_not_allowed_by_the_profile_are_refused_even_for_the_owner():
    r = reducer(moods_allowed=["calm"])
    assert r.apply(m.OwnerMood("cheerful", 1), 0.0).refused == "mood_not_allowed"
    assert r.apply(m.Dialogue("task_done"), 0.0).refused == "mood_not_allowed"
    assert r.apply(m.OwnerMood("calm", 1), 0.0).accepted


def test_a_mood_pinned_in_the_profile_is_the_baseline_and_survives_expiry():
    r = m.MoodReducer(profile(locked_mood="calm"), 0.0)
    assert r.state(0.0).origin == "profile" and r.state(1e7).mood == "calm"
    assert r.apply(m.Activity("coding"), 1.0).refused == "higher_priority_active"
    r.apply(m.OwnerMood("serious", 2, ttl_s=60), 2.0)
    assert r.state(10.0).mood == "serious"
    assert r.state(1e7).mood == "calm"  # back to the pin, not to neutral


def test_a_new_reducer_is_neutral_so_a_restart_forgets_session_moods():
    r = reducer()
    r.apply(m.OwnerMood("serious", 3), 0.0)
    assert m.MoodReducer(profile(), 0.0).state(1.0) == m.NEUTRAL_STATE


@pytest.mark.parametrize("bad", [
    m.OwnerMood("calm", 0), m.OwnerMood("calm", 4), m.OwnerMood("calm", True),
    m.OwnerMood("calm", 2, ttl_s=math.nan), m.OwnerMood("calm", 2, ttl_s=math.inf),
])
def test_bad_owner_values_change_nothing(bad):
    r = reducer()
    assert r.apply(bad, 0.0).refused == "bad_value" and r.state(0.0) == m.NEUTRAL_STATE


def test_ttl_is_clamped_and_a_bad_clock_is_refused():
    r = reducer()
    r.apply(m.OwnerMood("calm", 1, ttl_s=1), 0.0)
    assert r.state(0.0).expires_at == m.MIN_TTL_S
    assert r.apply(m.Activity("coding"), math.nan).refused == "bad_time"


def test_unknown_signals_and_activities_are_refused_not_guessed():
    r = reducer(expressive_range=2)
    assert r.apply(m.Dialogue("user_is_sad"), 0.0).refused == "unknown_signal"
    assert r.apply(m.Activity("gaming"), 0.0).refused == "unknown_signal"
    assert r.state(0.0) == m.NEUTRAL_STATE


def test_set_profile_applies_limits_at_once():
    r = reducer(expressive_range=2)
    r.apply(m.OwnerMood("cheerful", 2), 0.0)
    r.set_profile(profile(moods_allowed=["calm"]), 1.0)
    assert r.state(1.0) == m.NEUTRAL_STATE
