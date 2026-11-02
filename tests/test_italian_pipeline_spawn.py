"""Italian (``it``) registration in the speech pipeline, spawn announcements,
spawn gate and workspace clarification."""

from __future__ import annotations

import asyncio

from dodaios.agentic_ide import clarify
from dodaios.brain import spawn_gate
from dodaios.brain.ack_brain import spawn_announcement as sa
from dodaios.speech import pipeline as pl


def test_pipeline_phrase_tables_and_lang():
    assert "it" in pl._PHRASE_LANGS
    assert pl._phrase_lang("it") == "it"
    assert pl._phrase_lang("it-IT") == "it"
    assert pl._phrase_lang("italian") == "it"
    assert pl._CLARIFY_QUESTION_PHRASE["it"]
    assert pl._ACTION_DONE_PHRASE["it"] == "Fatto."
    assert pl.SpeechPipeline._bcp47("it") == "it-IT"
    ph = pl.SpeechPipeline._BG_READBACK_PHRASES["it"]
    assert set(ph) == set(pl.SpeechPipeline._BG_READBACK_PHRASES["pt"])
    assert "{s}" in ph["done_summ"] and "{e}" in ph["fail"]


def test_spawn_announcement_pools():
    for pool in (sa._FALLBACK_SPAWN, sa._FALLBACK_ALREADY_RUNNING, sa.STILL_RUNNING_PHRASES):
        assert pool["it"]
    assert all("{agent}" in p for p in sa._FALLBACK_SPAWN["it"])
    assert all("{agent}" in p for p in sa._FALLBACK_ALREADY_RUNNING["it"])
    assert "it" in sa._PERSONA_LANGS
    assert "Italian" in sa.get_spawn_persona("it", "Max-Agent")
    assert sa._resolve_language("it-IT", "") == "it"
    assert sa._COMPLETION_CLAIM_RE.search("È stato inviato.")
    assert sa._COMPLETION_CLAIM_RE.search("Fatto.")


def test_spawn_gate_italian_markers():
    assert spawn_gate._DELEGATION_MARKER_RE.search("lancialo in background")
    assert spawn_gate._DELEGATION_MARKER_RE.search("delega questo a un agente")
    assert spawn_gate._DELEGATION_MARKER_RE.search("avvia una missione")


def test_clarify_classifier_accepts_italian_tag():
    assert clarify.classify_short_answer("no", "it") == "veto"
    assert clarify.classify_short_answer("", "it") == "unknown"
