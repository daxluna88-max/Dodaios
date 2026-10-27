"""Block 12 (ADR-0056): limits of adaptation vs the owner's explicit request, the realtime style
block, and the STOP binding of the plan gate.

Written, NOT run (no pytest run in this block). Fakes only: no audio, no provider, no network.
"""

from __future__ import annotations

import asyncio

import pytest

from dodaios.brain import expression as ex
from dodaios.brain import mood as m
from dodaios.brain import personality as p
from dodaios.brain import style
from dodaios.control import stop as stop_mod
from dodaios.core.config import PersonalityConfig


def profile(**kw):
    return p.effective(PersonalityConfig(**kw), "consumer")


def reducer(**kw):
    return m.MoodReducer(profile(**kw), now=0.0)


@pytest.fixture(autouse=True)
def _fresh_stop():
    stop_mod.reset_stop_controller()
    yield
    stop_mod.reset_stop_controller()


# -- owner request vs range -------------------------------------------------------------------


def test_owner_request_may_exceed_the_automatic_range():
    r = reducer(expressive_range=1)
    assert r.apply(m.Activity("sensitive"), 0.0).state.intensity == 1  # automatic: capped at 1
    got = r.apply(m.OwnerMood("serious", 3), 1.0)
    assert got.accepted and got.state.intensity == 3 and got.state.origin == "owner"


def test_owner_request_with_range_zero_still_works_but_automatic_does_not():
    r = reducer(expressive_range=0)
    assert r.apply(m.Dialogue("task_done"), 0.0).refused == "range_zero"
    assert r.apply(m.OwnerMood("cheerful", 2), 1.0).accepted


def test_owner_request_never_exceeds_moods_allowed():
    r = reducer(moods_allowed=["neutral", "calm"])
    got = r.apply(m.OwnerMood("serious", 3), 0.0)
    assert not got.accepted and got.refused == "mood_not_allowed"
    assert r.state(0.0) == m.NEUTRAL_STATE
    assert r.apply(m.OwnerMood("calm", 3), 1.0).accepted


@pytest.mark.parametrize("intensity", [0, 4, -1, True, 2.5])
def test_owner_request_never_exceeds_the_maximum_intensity(intensity):
    r = reducer()
    got = r.apply(m.OwnerMood("calm", intensity), 0.0)
    assert not got.accepted and got.refused == "bad_value"


def test_a_later_profile_that_forbids_the_mood_drops_an_owner_request_too():
    r = reducer()
    r.apply(m.OwnerMood("serious", 3), 0.0)
    r.set_profile(profile(moods_allowed=["neutral", "calm"]), 1.0)
    assert r.state(1.0).mood != "serious"


def test_limits_report_two_different_numbers():
    lim = m.limits(profile(expressive_range=1, moods_allowed=["neutral", "calm"]))
    assert lim["adaptation_max_intensity"] == 1 and lim["owner_max_intensity"] == 3
    assert lim["moods_allowed"] == ["calm"]
    assert m.limits(profile(mode="manual"))["adaptation_max_intensity"] == 0


# -- adaptation off ---------------------------------------------------------------------------


def test_turning_adaptation_off_drops_an_adaptive_mood_but_keeps_an_owner_one():
    r = reducer(expressive_range=2)
    r.apply(m.Activity("coding"), 0.0)
    assert r.apply(m.OwnerAdaptation(False), 1.0).state.intensity == 0
    r2 = reducer()
    r2.apply(m.OwnerMood("calm", 2), 0.0)
    r2.apply(m.OwnerAdaptation(False), 1.0)
    assert r2.state(1.0).mood == "calm"


# -- fade re-anchoring (earlier finding, re-audited) --------------------------------------------


def test_reading_the_fade_twice_or_after_persisting_gives_the_same_level():
    r = reducer()
    r.apply(m.OwnerMood("serious", 3, 30.0), 0.0)  # expires at 30
    for now in (30.0, 49.0, 50.0, 69.0, 70.0, 95.0):
        first = r.state(now).intensity
        assert r.state(now).intensity == first
    r.apply(m.OwnerAdaptation(True), 50.0)  # persists the fade
    assert r.state(50.0).intensity == 1 and r.state(70.0).intensity == 0


# -- realtime style text ----------------------------------------------------------------------


def test_realtime_instruction_keeps_the_rules_and_does_not_claim_a_voice_change():
    state = m.MoodState("cheerful", 2, "owner", 0.0, None, "owner_request", m.OWNER)
    text = style.realtime_instruction(profile(), state)
    assert text.startswith(style.instruction(profile(), state))
    assert "not a feeling" in text and "Never describe your own voice" in text
    assert "lip" not in text.lower()


def test_realtime_style_block_is_empty_while_stop_has_the_door_closed():
    from dodaios.brain.manager import BrainManager
    from dodaios.core.bus import EventBus
    from dodaios.core.config import JarvisConfig

    brain = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    assert "Speaking style" in brain.realtime_style_block()
    asyncio.run(stop_mod.get_stop_controller().stop())
    assert brain.realtime_style_block() == ""
    stop_mod.get_stop_controller().resume()
    assert "Speaking style" in brain.realtime_style_block()


def test_realtime_delivery_is_honest_about_unknown_and_connect_only_engines():
    assert ex.realtime_delivery("openai-realtime") == "every_turn"
    assert ex.realtime_delivery("gemini-live") == "connect_only"
    assert ex.realtime_delivery("something-new") == "unknown"
    assert ex.CHANNELS["voice"] == "unavailable" and ex.CHANNELS["lip_sync"] == "none"


# -- PlanGate bound to STOP --------------------------------------------------------------------


def _plan(gen, turn="t1", product="consumer"):
    return ex.build_plan(
        profile(), m.NEUTRAL_STATE, ex.TurnIdentity(product, "s1", turn, gen),
        tts_provider="x", avatar_expressions=ex.AVATAR_EXPRESSIONS)


def test_two_turns_of_the_same_stop_generation_both_begin_in_order():
    ctl = stop_mod.get_stop_controller()
    gate = ex.PlanGate(admit=ex.stop_admits)
    g = ctl.ticket()
    assert gate.begin("consumer", "s1", "t1", g) and gate.accepts(_plan(g, "t1"))
    assert gate.begin("consumer", "s1", "t2", g)
    assert not gate.accepts(_plan(g, "t1")) and gate.accepts(_plan(g, "t2"))


def test_a_plan_is_refused_after_stop_even_if_the_gate_was_never_told():
    ctl = stop_mod.get_stop_controller()
    gate = ex.PlanGate(admit=ex.stop_admits)
    g = ctl.ticket()
    gate.begin("consumer", "s1", "t1", g)
    asyncio.run(ctl.stop())
    assert not gate.accepts(_plan(g))
    assert not gate.begin("consumer", "s1", "t2", g)  # a late start of the old generation
    ctl.resume()
    assert not gate.accepts(_plan(g))  # resume opens a NEW generation; the old plan stays dead
    assert gate.begin("consumer", "s1", "t3", ctl.ticket())


def test_cancel_still_blocks_a_late_begin_of_the_same_generation():
    gate = ex.PlanGate()
    gate.begin("consumer", "s1", "t1", 1)
    gate.cancel(1)
    assert not gate.begin("consumer", "s1", "t1", 1)
    assert gate.begin("consumer", "s1", "t3", 2)


def test_session_instructions_carry_the_style_after_the_voice_contract(monkeypatch):
    from dodaios.brain import persona_loader
    from dodaios.realtime import session as session_mod

    monkeypatch.setattr(
        persona_loader, "load_effective_persona_prompt", lambda *, compact=False: "PERSONA")
    text = session_mod._session_instructions("de", style="STYLE_MARKER")
    assert text.index("Produce exactly one assistant response") < text.index("STYLE_MARKER")
    assert "STYLE_MARKER" not in session_mod._session_instructions("de")


def test_style_block_helper_degrades_to_empty_without_a_brain(monkeypatch):
    from dodaios.core import runtime_refs
    from dodaios.realtime import session as session_mod

    monkeypatch.setattr(runtime_refs, "get_brain_manager", lambda: None)
    assert session_mod._style_block() == ""
