"""Italian mission readback contracts; pure formatting, no TTS or provider calls."""
from __future__ import annotations

from collections import Counter
from string import Formatter
from types import SimpleNamespace
from typing import get_args

import pytest

from dodaios.i18n.locales import SUPPORTED_LOCALES
from dodaios.missions.events import CAPACITY_WAIT_REASONS, MissionApproved
from dodaios.missions.voice import readback


def _fields(template: str) -> Counter[str]:
    return Counter(name for _, name, _, _ in Formatter().parse(template) if name is not None)


@pytest.mark.parametrize("key", tuple(readback.READBACK_TEMPLATES))
def test_every_status_template_has_italian_with_matching_placeholders(key: str) -> None:
    table = readback.READBACK_TEMPLATES[key]
    assert table.get("it"), f"{key} has no Italian status template"
    english_fields = [_fields(template) for template in table["en"]]
    for template in table["it"]:
        assert _fields(template) in english_fields, (key, template)
        values = {field: "VALORE" for field in _fields(template)}
        assert "{" not in template.format(**values)


def test_mission_readback_literal_covers_every_registered_locale() -> None:
    assert set(get_args(readback.Lang)) == set(SUPPORTED_LOCALES)


@pytest.mark.parametrize("table", (readback.FAILURE_REASON_PHRASES, readback.CAPACITY_WAIT_PHRASES))
def test_italian_failure_and_capacity_key_placeholder_parity(table: dict) -> None:
    assert table["it"].keys() == table["en"].keys()
    for key, template in table["it"].items():
        assert template
        assert _fields(template) == _fields(table["en"][key]), key


@pytest.mark.parametrize(
    ("method", "key", "kwargs"),
    [
        ("render_timeout", "timeout", {}),
        ("render_cancelled", "cancelled", {}),
        ("render_budget_warn", "budget_warn_50", {"pct": 50}),
        ("render_budget_warn", "budget_warn_80", {"pct": 80}),
        ("render_budget_exceeded", "budget_exceeded", {}),
        ("render_injection_blocked", "injection_blocked", {}),
        ("render_path_guard_blocked", "path_guard_blocked", {}),
        ("render_crash_recovery", "crash_recovery", {}),
        ("render_iteration_running", "iteration_running", {"n": 3}),
        ("render_destructive_confirm", "destructive_confirm", {"target": "file-demo"}),
        ("render_approved", "approved", {"summary": "Risultato verificato."}),
    ],
)
def test_status_renderers_select_italian_templates(method: str, key: str, kwargs: dict) -> None:
    result = getattr(readback.MissionReadback(), method)(language="it", **kwargs)
    templates = readback.READBACK_TEMPLATES[key]["it"]
    expected = {template.format(**kwargs) for template in templates}
    assert result in expected
    assert len(result) <= readback.MAX_VOICE_CHARS


@pytest.mark.parametrize("reason", tuple(readback.FAILURE_REASON_PHRASES["en"]))
def test_known_failure_reasons_are_rendered_in_italian(reason: str, monkeypatch) -> None:
    monkeypatch.setattr(readback, "render_agent_brand", lambda text: text.replace("{agents}", "Agenti-demo").replace("{agent}", "Agente-demo"))
    result = readback.MissionReadback().render_failed(reason=reason, language="it")
    if reason == "crash_recovery":
        assert result in readback.READBACK_TEMPLATES["crash_recovery"]["it"]
    else:
        phrase = readback.render_agent_brand(readback.FAILURE_REASON_PHRASES["it"][reason])
        if reason == "interrupted":
            assert result == phrase
        else:
            assert result in {template.format(reason=phrase) for template in readback.READBACK_TEMPLATES["failed"]["it"]}
    assert len(result) <= readback.MAX_VOICE_CHARS
    assert "{agent" not in result


def test_failure_error_class_precedes_generic_reason_and_empty_reason_is_italian() -> None:
    result = readback.MissionReadback().render_failed(reason="task_error", error_class="provider_auth", language="it")
    assert readback.FAILURE_REASON_PHRASES["it"]["provider_auth"] in result
    assert readback.FAILURE_REASON_PHRASES["it"]["task_error"] not in result
    empty = readback.MissionReadback().render_failed(language="it")
    assert readback._UNKNOWN_REASON["it"] in empty


def test_false_approval_uses_italian_failure_instead_of_success() -> None:
    honesty = SimpleNamespace(honesty_overridden=True, verdict=SimpleNamespace(summary_de="GERMAN-PRIVATE", summary="ENGLISH-PRIVATE"))
    result = readback.MissionReadback().render_approved(summary="SUCCESS-MARKER", language="it", honesty_check=honesty)
    expected = {template.format(reason=readback._NO_TOOL_CALL["it"]) for template in readback.READBACK_TEMPLATES["failed"]["it"]}
    assert result in expected
    assert "SUCCESS-MARKER" not in result
    assert "GERMAN-PRIVATE" not in result
    assert "ENGLISH-PRIVATE" not in result


def test_local_approved_summary_is_used_for_italian() -> None:
    payload = MissionApproved(result_uri="mission://test", tokens_used=0, cost_usd=0, wall_ms=0, summary_de="DE", summary_en="EN", summary_local="Risultato italiano.")
    assert readback.approved_summary(payload, "it") == "Risultato italiano."
    assert readback.approved_summary(payload.model_copy(update={"summary_local": ""}), "it") == "EN"


def test_capacity_wait_uses_italian_auth_and_explicit_paid_consent() -> None:
    table = readback.CAPACITY_WAIT_PHRASES["it"]
    result = readback.render_capacity_wait(reason="provider_auth", provider="X", steps_done=0, steps_total=1, files_saved=0, checkpoint_saved=True, language="it")
    assert result.startswith(table["provider_auth"].format(provider="X"))
    assert table["saved"] in result
    assert table["wait_auth"] in result
    assert table["paid"] in result
    assert len(result) <= readback.MAX_VOICE_CHARS


@pytest.mark.parametrize("reason", CAPACITY_WAIT_REASONS)
def test_capacity_wait_reasons_format_italian_without_unresolved_placeholders(reason: str) -> None:
    result = readback.render_capacity_wait(reason=reason, provider="X", steps_done=1, steps_total=2, files_saved=1, checkpoint_saved=False, language="it")
    table = readback.CAPACITY_WAIT_PHRASES["it"]
    assert result.startswith(table[reason].format(provider="X"))
    assert table["paid"] in result
    assert "{" not in result
    assert len(result) <= readback.MAX_VOICE_CHARS


def test_italian_long_summary_and_target_keep_voice_length_cap() -> None:
    rb = readback.MissionReadback()
    assert len(rb.render_approved(summary="testo " * 300, language="it")) <= readback.MAX_VOICE_CHARS
    assert len(rb.render_destructive_confirm(target="file " * 300, language="it")) <= readback.MAX_VOICE_CHARS
