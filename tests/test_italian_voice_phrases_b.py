"""Italian coverage for the voice number speller, live tools and browser voice."""
from __future__ import annotations

import pytest

from dodaios.browser_voice.session import BrowserVoiceSession
from dodaios.live import tools as live_tools
from dodaios.voice.number_speller import spell_out_numbers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", "zero"),
        ("1", "uno"),
        ("21", "ventuno"),
        ("100", "cento"),
        ("1234", "milleduecentotrentaquattro"),
        ("1,5", "uno virgola cinque"),
        ("1.000", "mille"),
    ],
)
def test_italian_numbers(text: str, expected: str) -> None:
    assert spell_out_numbers(text, "it") == expected
    assert spell_out_numbers(text, "it-IT") == expected


def test_italian_time_date_and_version() -> None:
    assert spell_out_numbers("20:30", "it") == "venti e trenta"
    assert spell_out_numbers("20:00", "it") == "venti in punto"
    assert spell_out_numbers("1:10", "it") == "una e dieci"
    assert spell_out_numbers("17.08.2026", "it") == "diciassette agosto duemilaventisei"
    assert spell_out_numbers("01.03.2026", "it") == "primo marzo duemilaventisei"
    assert spell_out_numbers("3.11", "it") == "tre punto undici"
    assert spell_out_numbers("192.168.1.1", "it") == (
        "centonovantadue punto centosessantotto punto uno punto uno"
    )


def test_other_languages_unchanged() -> None:
    assert spell_out_numbers("1,5", "de") == "eins Komma fünf"
    assert spell_out_numbers("1.5", "en") == "one point five"


def test_live_tools_italian_go_ahead_words() -> None:
    assert {"sì", "procedi", "conferma"} <= live_tools._GO_AHEAD_WORDS


def test_browser_voice_lang_short_accepts_italian() -> None:
    session = BrowserVoiceSession.__new__(BrowserVoiceSession)
    session.language_code = "it-IT"
    assert session._lang_short() == "it"
    session.language_code = "fr-FR"
    assert session._lang_short() == "en"
