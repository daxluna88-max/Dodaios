"""Style text and runtime (issue 17): enumerated only, mood applied, one state for everyone.

Written for the PC checklist (P05, P06, P11); not run when it was written.
"""

from __future__ import annotations

import asyncio

from dodaios.brain import mood as m
from dodaios.brain import multi_engine_chat as mec
from dodaios.brain import personality as p
from dodaios.brain import style
from dodaios.brain.provider_registry import BrainProviderRegistry
from dodaios.core.config import MultiEngineConfig, MultiEngineEngineConfig, PersonalityConfig
from dodaios.core.protocols import BrainDelta


def prof(**kw):
    return p.effective(PersonalityConfig(**kw), "consumer")


def test_the_text_comes_from_the_enumerations_only():
    text = style.instruction(prof(), m.NEUTRAL_STATE)
    assert "friendly" in text and "Speaking style" in text
    assert "Right now" not in text  # neutral: no mood sentence


def test_mood_adds_one_sentence_with_a_strength_and_nothing_else_about_it():
    state = m.MoodState("calm", 2, "owner", 0.0, None, "owner_request", m.OWNER)
    text = style.instruction(prof(), state)
    assert "noticeably calmer and slower" in text
    assert "owner_request" not in text and "owner" not in text.split("Right now")[1].split(".")[0]


def test_serious_mood_turns_humor_off():
    jokey = prof(humor=4)
    serious = m.MoodState("serious", 1, "rule", 0.0, 1.0, "dialogue_task_failed", m.ADAPTATION)
    assert "often playful" in style.instruction(jokey, m.NEUTRAL_STATE)
    assert "no humor" in style.instruction(jokey, serious)


def test_the_rules_are_always_there_and_say_it_is_a_style():
    for compact in (False, True):
        text = style.instruction(prof(), m.NEUTRAL_STATE, compact=compact)
        assert "not a feeling" in text and "never changes facts" in text


def test_presets_change_the_text_per_product():
    work = style.instruction(p.effective(PersonalityConfig(), "work"), m.NEUTRAL_STATE)
    consumer = style.instruction(p.effective(PersonalityConfig(), "consumer"), m.NEUTRAL_STATE)
    assert work != consumer and "formal" in work


def test_runtime_follows_a_changed_profile_and_keeps_the_session_mood():
    clock = [0.0]
    rt = style.StyleRuntime(clock=lambda: clock[0])
    assert rt.apply(m.OwnerMood("calm", 2)) is None  # no profile yet
    rt.sync(PersonalityConfig(), "consumer")
    assert rt.apply(m.OwnerMood("calm", 2)).accepted
    clock[0] = 10.0
    _, state = rt.sync(PersonalityConfig(humor=0), "consumer")  # profile changed, mood kept
    assert state.mood == "calm"
    _, state = rt.sync(PersonalityConfig(moods_allowed=["serious"]), "consumer")
    assert state == m.NEUTRAL_STATE  # a limit applies at once


# ---- the final answer of several engines carries the style, the branches do not -------------


class Fake:
    prompts: list[str] = []

    def __init__(self, model=None):
        pass

    def estimate_cost(self, req):
        return 0.0

    async def complete(self, req):
        Fake.prompts.append(" ".join(str(msg.content) for msg in req.messages))
        yield BrainDelta(content="answer")


def test_only_the_synthesis_receives_the_style_instruction(monkeypatch):
    from dodaios.brain import engine_policy as ep
    from dodaios.core import config as cfg_mod

    Fake.prompts.clear()
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    reg = BrainProviderRegistry()
    reg._classes = {"claude-api": Fake, "openai": Fake}
    reg._loaded = True
    monkeypatch.setattr(ep, "endpoint_problem", lambda provider: None)
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda provider: "key")
    cfg = MultiEngineConfig(
        enabled=True, budget=5.0,
        engines=[MultiEngineEngineConfig(provider="claude-api", est_cost=0.5),
                 MultiEngineEngineConfig(provider="openai", est_cost=0.5)])
    marker = "Speaking style: STYLE-MARKER"
    out = asyncio.run(mec.ask("2+2?", cfg, reg, turn_id="t", style_instruction=marker))
    assert out.status == "ok"
    with_style = [pr for pr in Fake.prompts if "STYLE-MARKER" in pr]
    assert len(with_style) == 1 and len(Fake.prompts) == 3  # two reviewers + one synthesis
