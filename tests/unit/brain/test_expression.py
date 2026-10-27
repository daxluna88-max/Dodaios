"""Expression plan (issue 17): one plan for text/voice/avatar, honest about what is unsupported.

Written for the PC checklist (P08, P09); not run when it was written.
"""

from __future__ import annotations

from dodaios.brain import expression as ex
from dodaios.brain import mood as m
from dodaios.brain import personality as p
from dodaios.core.config import PersonalityConfig

PROFILE = p.effective(PersonalityConfig(), "consumer")
CHEERFUL = m.MoodState("cheerful", 1, "rule", 0.0, 1.0, "dialogue_task_done", m.ADAPTATION)
SERIOUS = m.MoodState("serious", 2, "owner", 0.0, None, "owner_request", m.OWNER)


def ident(turn="t1", gen=1, session="s1"):
    return ex.TurnIdentity("consumer", session, turn, gen)


def plan(state=m.NEUTRAL_STATE, **kw):
    kw.setdefault("tts_provider", "elevenlabs")
    kw.setdefault("avatar_expressions", ex.AVATAR_EXPRESSIONS)
    return ex.build_plan(PROFILE, state, ident(), **kw)


def test_cheerful_smiles_and_everything_else_stays_neutral():
    assert plan(CHEERFUL).expression == "smile"
    for state in (SERIOUS, m.MoodState("calm", 2), m.MoodState("focused", 1), m.NEUTRAL_STATE):
        assert plan(state).expression == "neutral"


def test_curious_and_surprised_are_never_taken_from_a_mood():
    for mood in ("cheerful", "calm", "focused", "serious", "neutral"):
        assert plan(m.MoodState(mood, 3)).expression not in ("curious", "surprised")


def test_an_avatar_without_the_expression_or_without_a_declaration_gets_neutral_and_a_note():
    no_smile = plan(CHEERFUL, avatar_expressions=("neutral",))
    assert no_smile.expression == "neutral" and "avatar_expression_unsupported" in no_smile.notes
    unknown = plan(CHEERFUL, avatar_expressions=None)
    assert unknown.expression == "neutral" and "avatar_support_unknown" in unknown.notes


def test_a_mood_at_zero_intensity_shows_nothing():
    assert plan(m.MoodState("cheerful", 0)).expression == "neutral"


def test_voice_is_unavailable_for_every_provider_until_one_declares_a_parameter():
    for provider in ("elevenlabs", "gemini-flash-tts", "piper", "cartesia"):
        got = plan(CHEERFUL, tts_provider=provider)
        assert got.voice_status == "unavailable" and got.voice_params == {}
        assert "voice_unavailable" in got.notes
    assert plan(CHEERFUL, tts_provider=None).voice_status == "unknown_provider"


def test_the_version_follows_the_profile_and_mood_and_holds_no_values():
    a, b = plan(CHEERFUL), plan(SERIOUS)
    assert a.version != b.version and plan(CHEERFUL).version == a.version
    assert "cheerful" not in a.version and len(a.version) == 12


def test_the_gate_accepts_only_the_current_turn_and_generation():
    gate = ex.PlanGate()
    assert gate.begin("consumer", "s1", "t1", 1)
    assert gate.accepts(plan())
    assert gate.begin("consumer", "s1", "t2", 2)
    assert not gate.accepts(plan())  # t1 / generation 1 is old now


def test_stop_invalidates_the_plan_and_an_older_begin_cannot_reopen_it():
    gate = ex.PlanGate()
    gate.begin("consumer", "s1", "t1", 1)
    gate.cancel(1)
    assert not gate.accepts(plan())
    assert not gate.begin("consumer", "s1", "t1", 1)  # a delayed start of the cancelled turn
    assert gate.begin("consumer", "s1", "t3", 2)


def test_another_session_or_product_turn_is_not_accepted():
    gate = ex.PlanGate()
    gate.begin("consumer", "s1", "t1", 1)
    assert not gate.accepts(ex.build_plan(
        PROFILE, m.NEUTRAL_STATE, ident(session="other"), tts_provider="x",
        avatar_expressions=None))


def test_a_plan_made_for_another_product_is_not_accepted():
    gate = ex.PlanGate()
    gate.begin("work", "s1", "t1", 1)
    assert not gate.accepts(plan())  # same session, turn and generation, but a Consumer plan
    assert gate.accepts(ex.build_plan(
        PROFILE, m.NEUTRAL_STATE, ex.TurnIdentity("work", "s1", "t1", 1), tts_provider="x",
        avatar_expressions=None))


def test_the_avatar_event_carries_the_turn_identity_and_only_the_expression():
    event = ex.avatar_event(plan(CHEERFUL), seq=3)
    assert event == {"type": "expression_plan", "turn_id": "t1", "generation": 1, "seq": 3,
                     "expression": "smile"}
