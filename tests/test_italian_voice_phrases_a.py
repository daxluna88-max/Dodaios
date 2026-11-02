"""Italian (``it``) coverage of the voice modules: instant_ack, action_phrases, parked_results.

Exercises the REAL tables: every key ``pt`` has must exist for ``it``, the
``{placeholders}`` must match the pt entries, and nothing may fall back to
English.
"""
from __future__ import annotations

import re

import pytest

from dodaios.voice import action_phrases, instant_ack, parked_results

_FIELD_RE = re.compile(r"\{(\w+)\}")


def _fields(text: str) -> set[str]:
    return set(_FIELD_RE.findall(text))


def test_it_is_a_supported_language_everywhere() -> None:
    assert "it" in instant_ack._SUPPORTED_LANGUAGES
    assert "it" in action_phrases._SUPPORTED
    assert "it" in parked_results._SUPPORTED_LANGUAGES
    assert instant_ack._language_key("it-IT") == "it"
    assert parked_results._language_key("it") == "it"
    assert instant_ack.language_name("it") == "Italian"


@pytest.mark.parametrize("work_class", list(instant_ack.WorkClass))
def test_instant_ack_pools_have_italian(work_class: instant_ack.WorkClass) -> None:
    pt = instant_ack.instant_ack_pool(work_class, "pt", agent_brand="Nova-Agent")
    it = instant_ack.instant_ack_pool(work_class, "it", agent_brand="Nova-Agent")
    en = instant_ack.instant_ack_pool(work_class, "en", agent_brand="Nova-Agent")
    assert bool(it) == bool(pt)
    assert len(it) == len(pt)
    assert not set(it) & set(en)
    if work_class is instant_ack.WorkClass.MISSION:
        assert all("Nova-Agent" in line for line in it)
    for line in it:
        assert "{" not in line


def test_instant_ack_pick_is_italian_not_default() -> None:
    line = instant_ack.pick_instant_ack_text(instant_ack.WorkClass.RESEARCH, "it")
    assert line in instant_ack.instant_ack_pool(instant_ack.WorkClass.RESEARCH, "it")
    assert line not in instant_ack.instant_ack_pool(instant_ack.WorkClass.RESEARCH, "en")
    assert instant_ack.normalize_ack_line(line) in instant_ack.all_instant_ack_lines("it")


@pytest.mark.parametrize("activity", list(instant_ack.ToolActivity))
def test_progress_pools_have_italian(activity: instant_ack.ToolActivity) -> None:
    pt = instant_ack.progress_pool(activity, "pt")
    it = instant_ack.progress_pool(activity, "it")
    en = instant_ack.progress_pool(activity, "en")
    assert len(it) == len(pt)
    assert not set(it) & set(en)
    if it:
        assert instant_ack.pick_progress_text(activity, "it") in it
    else:
        assert instant_ack.pick_progress_text(activity, "it") == ""


def test_intent_and_free_word_tables_have_italian() -> None:
    assert instant_ack._INTENT_VERBS["it"]
    assert instant_ack._FREE_WORDS["it"]
    assert set(instant_ack._INTENT_VERBS) == set(instant_ack._FREE_WORDS)
    assert "it" in instant_ack._INTENT_VERBS


def test_contextual_ack_validator_italian() -> None:
    ok = instant_ack.contextual_ack_is_valid
    assert ok("Apro Spotify.", utterance="apri Spotify per favore", language="it")
    assert ok(
        "Controllo le tue note.", utterance="controlla le note", language="it"
    )
    # Result claims and invented words are rejected.
    assert not ok("Spotify è aperto.", utterance="apri Spotify", language="it")
    assert not ok("Ho aperto Spotify.", utterance="apri Spotify", language="it")
    assert not ok("Fatto.", utterance="apri Spotify", language="it")
    assert not ok("Apro Spotify e Netflix.", utterance="apri Spotify", language="it")
    assert not ok("Apro Spotify?", utterance="apri Spotify", language="it")


@pytest.mark.parametrize("lang", ["it", "pt"])
def test_wait_query_classification(lang: str) -> None:
    probes = {
        "it": {
            "a che punto sei": parked_results.WAIT_QUERY_PROGRESS,
            "come va": parked_results.WAIT_QUERY_PROGRESS,
            "hai già finito": parked_results.WAIT_QUERY_PROGRESS,
            "quanto manca": parked_results.WAIT_QUERY_PROGRESS,
            "stai ancora lavorando": parked_results.WAIT_QUERY_PROGRESS,
            "cosa hai trovato": parked_results.WAIT_QUERY_RESULT,
            "e il risultato": parked_results.WAIT_QUERY_RESULT,
            "qual è il risultato": parked_results.WAIT_QUERY_RESULT,
            "ciao, dimmi il risultato adesso": parked_results.WAIT_QUERY_RESULT,
            "cosa hai trovato sui prezzi dei voli nel wiki di ieri sera": "",
            "apri spotify": "",
        },
        "pt": {"como vais": parked_results.WAIT_QUERY_PROGRESS},
    }[lang]
    for text, expected in probes.items():
        assert parked_results.classify_wait_query(text) == expected, text


def test_parked_topic_and_anchor_italian() -> None:
    assert parked_results._TOPIC_STOPWORDS["it"]
    assert parked_results.topic_of("per favore apri il calendario", "it") == "apri il calendario"
    pool = parked_results.anchor_pool("it")
    assert pool != parked_results.anchor_pool("en")
    assert len(pool) == len(parked_results.anchor_pool("pt"))
    assert all(_fields(line) == {"topic", "result"} for line in pool)
    bare = parked_results.anchor_pool("it", with_topic=False)
    assert bare != parked_results.anchor_pool("en", with_topic=False)
    assert all(_fields(line) == {"result"} for line in bare)
    out = parked_results.reanchor(
        "Sono le tre.", request_text="che ore sono", language="it", intervening_turns=1
    )
    assert "Sono le tre." in out


def test_action_phrase_catalog_italian() -> None:
    for key, by_lang in action_phrases._PHRASES.items():
        assert "it" in by_lang, key
        assert _fields(by_lang["it"]) == _fields(by_lang["pt"]), key
        if by_lang["en"] != by_lang["pt"]:
            assert by_lang["it"] != by_lang["en"], key


def test_reason_families_and_fixed_causes_italian() -> None:
    for _pattern, by_lang in action_phrases._REASON_FAMILIES:
        assert set(by_lang) >= {"it"}
        assert _fields(by_lang["it"]) == _fields(by_lang["pt"])
        assert by_lang["it"] != by_lang["en"]
    for _en, by_lang in action_phrases._PERMISSION_FIXED_CAUSES.items():
        assert by_lang["it"] != by_lang["en"]
        assert _fields(by_lang["it"]) == _fields(by_lang["pt"])


def test_blocked_permission_names_italian() -> None:
    for keyword, names in action_phrases._BLOCKED_PERMISSION_NAMES:
        assert "it" in names, keyword
        permission, pane = names["it"]
        assert permission and pane
        assert (permission, pane) != names["en"] if "en" in names else True
        assert (permission, pane) != names["pt"]


def test_blocked_permission_readback_italian() -> None:
    sentence = "Accessibility permission is off. Allow it in System Settings."
    text = action_phrases._blocked_permission_readback("it", sentence)
    assert text and "Accessibilit" in text
    assert text != action_phrases._blocked_permission_readback("en", sentence)
    assert action_phrases._spoken_pane("A > B & C", "it") == "A, poi B e C"
    assert action_phrases.resolve_phrase_language("it", "hello") == "it"
