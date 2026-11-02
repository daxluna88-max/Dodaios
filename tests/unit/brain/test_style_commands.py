"""Style commands in the chat (issue 17): deterministic, session-only, never a normal turn's words.

Written for the PC checklist (P01, P04, P11); not run when it was written.
"""

from __future__ import annotations

import pytest

from dodaios.brain import mood as m
from dodaios.brain import style
from dodaios.brain import style_commands as sc
from dodaios.core.config import PersonalityConfig


@pytest.mark.parametrize("text, kind, arg", [
    ("più serio", "mood", "serious"),
    ("Sii più serio, per favore!", "mood", "serious"),
    ("be more serious", "mood", "serious"),
    ("più allegro", "mood", "cheerful"),
    ("calmati", "mood", "calm"),
    ("resta così", "hold", None),
    ("stay like this", "hold", None),
    ("torna neutro", "neutral", None),
    ("back to neutral", "neutral", None),
    ("disattiva adattamento", "adaptation", False),
    ("turn off adaptation", "adaptation", False),
    ("attiva l'adattamento", "adaptation", True),
    ("meno ironia", "nudge", ("humor", -1)),
    ("less humor", "nudge", ("humor", -1)),
    ("niente ironia", "nudge", ("humor", -4)),
    ("più formale", "nudge", ("formality", 1)),
    ("more formal", "nudge", ("formality", 1)),
    ("più breve", "nudge", ("concision", 1)),
    ("stile predefinito", "reset_style", None),
])
def test_clear_commands_are_recognised(text, kind, arg):
    cmd = sc.parse(text)
    assert cmd is not None and (cmd.kind, cmd.argument) == (kind, arg)


@pytest.mark.parametrize("text", [
    "scrivi una lettera più seria al mio capo",
    "write a more serious letter to my boss",
    "puoi spiegarmi perché l'ironia è meno efficace nei testi legali",
    "ciao", "che ore sono", "resta così come ti ho chiesto di fare il report del mese scorso",
    "",
])
def test_ordinary_messages_are_not_commands(text):
    assert sc.parse(text) is None


@pytest.mark.parametrize("text", [
    "sii più triste", "be more mysterious", "cambia tono", "change your tone", "sii diverso",
])
def test_unclear_commands_ask_and_change_nothing(text):
    cmd = sc.parse(text)
    assert cmd is not None and cmd.kind == "clarify" and cmd.event() is None
    assert "?" in sc.reply(cmd)


def test_a_permanent_wish_is_applied_to_the_session_only_and_says_where_to_make_it_permanent():
    cmd = sc.parse("sii sempre più serio")
    assert cmd.kind == "mood" and cmd.wanted_permanent
    assert "impostazioni" in sc.reply(cmd)
    en = sc.parse("from now on be more serious")
    assert en.wanted_permanent and "settings" in sc.reply(en)
    assert "permanent" not in sc.reply(sc.parse("be more serious"))


def test_the_reply_follows_the_language_of_the_command():
    assert "Va bene" in sc.reply(sc.parse("più serio"))
    assert sc.reply(sc.parse("be more serious")).startswith("Okay")


def test_a_command_never_writes_the_saved_profile_and_nudges_are_session_only():
    clock = [0.0]
    rt = style.StyleRuntime(clock=lambda: clock[0])
    cfg = PersonalityConfig(humor=2)
    rt.sync(cfg, "consumer")
    cmd = sc.parse("meno ironia")
    rt.nudge(*cmd.argument)
    profile, _ = rt.sync(cfg, "consumer")
    assert profile.humor == 1 and cfg.humor == 2  # the profile object is untouched
    rt.nudge("humor", -9)
    assert rt.sync(cfg, "consumer")[0].humor == 0  # clamped to the scale
    rt.reset_nudges()
    assert rt.sync(cfg, "consumer")[0].humor == 2
    assert style.StyleRuntime().sync(cfg, "consumer")[0].humor == 2  # a new session starts clean


def test_commands_drive_the_mood_through_the_same_reducer():
    rt = style.StyleRuntime(clock=lambda: 0.0)
    rt.sync(PersonalityConfig(), "consumer")
    assert rt.apply(sc.parse("più serio").event()).accepted
    assert rt.sync(PersonalityConfig(), "consumer")[1].mood == "serious"
    assert rt.apply(sc.parse("torna neutro").event()).accepted
    assert rt.sync(PersonalityConfig(), "consumer")[1] == m.NEUTRAL_STATE


def test_a_refused_command_says_why_in_the_commands_language():
    rt = style.StyleRuntime(clock=lambda: 0.0)
    cfg = PersonalityConfig(moods_allowed=["calm"])
    rt.sync(cfg, "consumer")
    cmd = sc.parse("più allegro")
    applied = rt.apply(cmd.event())
    assert not applied.accepted
    text = sc.reply(cmd, refused=sc.refusal_text(applied.refused, cmd.language))
    assert "consentito" in text
