"""Personality profile (issue 17): schema, presets, resolution, damaged blocks, atomic writes.

Written for the PC checklist (P01, P02, P10); not run when it was written.
"""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from dodaios.brain import personality as p
from dodaios.core import config_writer
from dodaios.core.config import JarvisConfig, PersonalityConfig


def test_defaults_are_the_mixed_mode_with_every_mood_allowed():
    cfg = PersonalityConfig()
    assert cfg.mode == "mixed" and cfg.preset is None and cfg.locked_mood is None
    assert cfg.moods_allowed[0] == "neutral" and len(cfg.moods_allowed) == 5
    assert JarvisConfig().brain.personality == cfg


def test_product_defaults_differ_and_stay_separate():
    work = p.effective(PersonalityConfig(), "work")
    consumer = p.effective(PersonalityConfig(), "consumer")
    assert (work.preset, consumer.preset) == ("professional", "friendly")
    assert work.humor < consumer.humor and work.formality > consumer.formality
    assert p.effective(PersonalityConfig(), None).preset == "friendly"


def test_owner_values_beat_the_preset_one_dimension_at_a_time():
    cfg = PersonalityConfig(preset="professional", humor=3, tone="calm")
    eff = p.effective(cfg, "work")
    assert (eff.humor, eff.tone) == (3, "calm")
    assert eff.formality == 3 and eff.warmth == 1 and eff.concision == 3


@pytest.mark.parametrize("bad", [
    {"humor": 5}, {"humor": -1}, {"humor": 1.5}, {"humor": True}, {"humor": float("nan")},
    {"humor": math.inf}, {"expressive_range": 3}, {"tone": "furious"}, {"mode": "auto"},
    {"preset": "wild"}, {"locked_mood": "angry"}, {"moods_allowed": ["happy"]},
    {"schema_version": -1},
])
def test_bad_values_are_refused(bad):
    with pytest.raises(ValidationError):
        PersonalityConfig.model_validate(bad)


def test_neutral_is_always_allowed_and_moods_are_unique():
    cfg = PersonalityConfig(moods_allowed=["calm", "calm", "serious"])
    assert cfg.moods_allowed == ["neutral", "calm", "serious"]
    assert PersonalityConfig(moods_allowed=[]).moods_allowed == ["neutral"]


def test_a_locked_mood_that_is_not_allowed_is_ignored():
    cfg = PersonalityConfig(moods_allowed=["calm"], locked_mood="serious")
    assert p.effective(cfg).locked_mood is None
    assert p.effective(PersonalityConfig(locked_mood="calm")).locked_mood == "calm"


def test_parse_none_and_empty_are_the_defaults_with_status_ok():
    assert p.parse(None).status == "ok" and p.parse({}).config == PersonalityConfig()


def test_a_block_without_a_version_is_migrated():
    got = p.parse({"mode": "manual", "humor": 2})
    assert got.status == "migrated" and got.config.mode == "manual"
    assert got.config.schema_version == 1


def test_a_newer_schema_is_not_guessed_at():
    got = p.parse({"schema_version": 2, "mode": "manual"})
    assert got.status == "newer" and got.config == PersonalityConfig()


@pytest.mark.parametrize("raw", ["text", 3, ["a"], {"humor": 99}, {"tone": 5}, {"mode": None}])
def test_a_damaged_block_gives_the_defaults_and_says_why(raw):
    got = p.parse(raw)
    assert got.status == "defaults" and got.reason
    assert got.config == PersonalityConfig()


def test_a_partly_valid_block_is_not_partly_applied():
    got = p.parse({"schema_version": 1, "mode": "manual", "humor": 99})
    assert got.config.mode == "mixed"  # not "manual": nothing of a bad block is kept


# ---- the writer -----------------------------------------------------------------------------


@pytest.fixture
def toml_file(tmp_path: Path) -> Path:
    path = tmp_path / "jarvis.toml"
    path.write_text('# keep me\n[brain]\nprimary = "ollama"\n', encoding="utf-8")
    return path


def _read(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_set_round_trips_and_keeps_the_rest_of_the_file(toml_file):
    stored = config_writer.set_personality(
        {"mode": "manual", "preset": "concise", "humor": 0, "moods_allowed": ["calm"]},
        path=toml_file,
    )
    assert stored["schema_version"] == 1 and "tone" not in stored  # unset stays unset
    data = _read(toml_file)
    assert data["brain"]["primary"] == "ollama"
    parsed = p.parse(data["brain"]["personality"])
    assert parsed.status == "ok" and parsed.config.mode == "manual"
    assert parsed.config.moods_allowed == ["neutral", "calm"]
    assert "# keep me" in toml_file.read_text(encoding="utf-8")


def test_a_bad_value_writes_nothing(toml_file):
    before = toml_file.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        config_writer.set_personality({"humor": 9}, path=toml_file)
    assert toml_file.read_text(encoding="utf-8") == before


def test_saving_again_replaces_the_block(toml_file):
    config_writer.set_personality({"tone": "calm", "warmth": 4}, path=toml_file)
    config_writer.set_personality({"humor": 1}, path=toml_file)
    block = _read(toml_file)["brain"]["personality"]
    assert "tone" not in block and "warmth" not in block and block["humor"] == 1


def test_reset_removes_the_block_and_is_safe_twice(toml_file):
    config_writer.set_personality({"tone": "calm"}, path=toml_file)
    config_writer.reset_personality(path=toml_file)
    config_writer.reset_personality(path=toml_file)
    data = _read(toml_file)
    assert "personality" not in data["brain"] and data["brain"]["primary"] == "ollama"
    assert p.parse(data["brain"].get("personality")).config == PersonalityConfig()
