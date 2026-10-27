"""Guided first run (issue 18): choices, mapping to #17, atomic apply, honest engine status.

Written for the PC checklist (O01, O04, O05, O06, O07, O08, O09); not run when it was written.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from dodaios.core import config_writer
from dodaios.core.config import JarvisConfig, PersonalityConfig
from dodaios.setup import first_run as fr


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('# mine\n[brain]\nprimary = "ollama"\n', encoding="utf-8")
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    return SimpleNamespace(cfg=cfg, state=tmp_path / "setup_state.json")


def toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def run_apply(env, product="consumer", **choices):
    return fr.apply(product, fr.FirstRunChoices(**choices), state_path=env.state,
                    config_path=env.cfg)


# ---- choices ------------------------------------------------------------------------------


def test_defaults_change_nothing_risky():
    c = fr.FirstRunChoices()
    assert (c.interaction, c.memory, c.personality_skip, c.personality_tone) == (
        "text", "off", False, "adapt")


def test_names_are_cleaned_and_bounded():
    c = fr.FirstRunChoices(owner_name="  Dax\x00\x07 ", space_name="   ")
    assert c.owner_name == "Dax" and c.space_name is None
    with pytest.raises(ValueError):
        fr.FirstRunChoices(owner_name="x" * 61)
    with pytest.raises(ValueError):
        fr.FirstRunChoices(interaction="video")


def test_choices_round_trip_and_a_damaged_entry_gives_the_defaults(env):
    fr.save_choices(fr.FirstRunChoices(owner_name="Dax", interaction="voice"), env.state)
    assert fr.load_choices(env.state).owner_name == "Dax"
    env.state.write_text('{"first_run": {"schema_version": 1, "choices": {"interaction": 5}}}',
                         encoding="utf-8")
    assert fr.load_choices(env.state) == fr.FirstRunChoices()
    env.state.write_text('{"first_run": "garbage"}', encoding="utf-8")
    assert fr.load_choices(env.state) == fr.FirstRunChoices()


# ---- mapping to the personality of issue 17 -------------------------------------------------


@pytest.mark.parametrize("product, preset, tone, expected", [
    ("consumer", "friendly", "adapt", {"mode": "mixed", "preset": "friendly"}),
    ("consumer", "friendly", "keep", {"mode": "manual", "preset": "friendly"}),
    ("consumer", "ironic", "adapt", {"mode": "mixed", "preset": "friendly", "humor": 3}),
    ("consumer", "essential", "keep", {"mode": "manual", "preset": "concise"}),
    ("work", "professional", "adapt", {"mode": "mixed", "preset": "professional"}),
    ("work", "collaborative", "adapt",
     {"mode": "mixed", "preset": "friendly", "humor": 0, "formality": 2}),
    ("work", "essential", "keep", {"mode": "manual", "preset": "concise"}),
])
def test_previews_map_to_the_existing_settings_and_never_to_adaptive(
        product, preset, tone, expected):
    block = fr.personality_block(product, fr.FirstRunChoices(
        personality_preset=preset, personality_tone=tone))
    assert block == expected and block["mode"] != "adaptive"
    PersonalityConfig.model_validate(block)  # a real, valid #17 profile


def test_defaults_per_product_and_a_foreign_preset_is_refused():
    assert fr.personality_block("work", fr.FirstRunChoices())["preset"] == "professional"
    assert fr.personality_block("consumer", fr.FirstRunChoices())["preset"] == "friendly"
    with pytest.raises(ValueError):
        fr.personality_block("work", fr.FirstRunChoices(personality_preset="ironic"))
    assert fr.personality_block("consumer", fr.FirstRunChoices(personality_skip=True)) is None


# ---- apply: real writes, only-then-marked ---------------------------------------------------


def test_apply_writes_personality_ask_and_memory_off_and_keeps_the_rest(env):
    out = run_apply(env, personality_preset="essential")
    assert out.applied == ["personality", "approval", "memory"] and not out.already
    data = toml(env.cfg)
    assert data["brain"]["primary"] == "ollama" and "# mine" in env.cfg.read_text("utf-8")
    assert data["brain"]["personality"]["preset"] == "concise"
    assert data["safety"]["approval_mode"] == "ask"
    assert data["memory"]["learning"]["enabled"] is False
    assert fr.applied_digest(env.state)


def test_apply_never_writes_bypass_and_memory_on_only_when_chosen(env):
    run_apply(env, memory="on")
    data = toml(env.cfg)
    assert data["safety"]["approval_mode"] == "ask"
    assert data["memory"]["learning"]["enabled"] is True


def test_skipping_personality_writes_no_block_and_removes_an_earlier_one(env):
    run_apply(env, personality_skip=True)
    assert "personality" not in toml(env.cfg)["brain"]
    run_apply(env, personality_preset="ironic")
    assert "personality" in toml(env.cfg)["brain"]
    run_apply(env, personality_skip=True, memory="on")
    assert "personality" not in toml(env.cfg)["brain"]


def test_same_choices_again_do_nothing_and_changed_choices_apply_again(env):
    run_apply(env)
    before = env.cfg.read_text("utf-8")
    assert run_apply(env).already and env.cfg.read_text("utf-8") == before
    again = run_apply(env, memory="on")
    assert not again.already and toml(env.cfg)["memory"]["learning"]["enabled"] is True


def test_a_failed_write_is_not_marked_applied_and_can_be_run_again(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(config_writer, "set_learning_enabled", boom)
    with pytest.raises(fr.FirstRunError) as err:
        run_apply(env)
    assert err.value.step == "memory" and err.value.done == ["personality", "approval"]
    assert fr.applied_digest(env.state) is None  # not reported done on a half-written config
    monkeypatch.undo()
    assert run_apply(env).applied  # idempotent: simply run again
    assert fr.applied_digest(env.state)


def test_a_bad_personality_is_refused_before_any_write(env):
    before = env.cfg.read_text("utf-8")
    with pytest.raises(ValueError):
        run_apply(env, product="work", personality_preset="ironic")
    assert env.cfg.read_text("utf-8") == before and fr.applied_digest(env.state) is None


def test_the_owner_name_does_not_change_what_is_applied(env):
    run_apply(env, owner_name="Dax")
    digest = fr.applied_digest(env.state)
    assert run_apply(env, owner_name="Someone else").already
    assert fr.applied_digest(env.state) == digest
    assert "Dax" not in env.cfg.read_text("utf-8")  # never in the configuration


def test_reset_forgets_choices_and_mark_but_not_the_config(env):
    run_apply(env)
    fr.reset_choices(env.state)
    assert fr.load_choices(env.state) == fr.FirstRunChoices()
    assert fr.applied_digest(env.state) is None
    assert toml(env.cfg)["safety"]["approval_mode"] == "ask"


# ---- engine status and the summary ----------------------------------------------------------


class Registry:
    def __init__(self, *names, secrets=()):
        self._names, self.secrets = set(names), set(secrets)

    def available(self):
        return list(self._names)

    def get_class(self, provider):
        return SimpleNamespace()


@pytest.fixture
def cfg_for():
    def make(provider):
        cfg = JarvisConfig()
        cfg.brain.primary = provider
        return cfg
    return make


def test_engine_states_are_separate_and_never_verified(cfg_for, monkeypatch):
    from dodaios.core import config as cfg_mod

    monkeypatch.setattr(
        cfg_mod, "get_provider_secret", lambda p: "key" if p == "claude-api" else None)
    reg = Registry("claude-api", "openai", "ollama")
    missing = fr.engine_status("consumer", cfg_for("nope"), reg)
    assert (missing["installed"], missing["ready"], missing["reason"]) == (
        False, False, "not_installed")
    no_key = fr.engine_status("consumer", cfg_for("openai"), reg)
    assert no_key["installed"] and not no_key["authenticated"]
    assert no_key["reason"] == "not_connected"
    ok = fr.engine_status("consumer", cfg_for("claude-api"), reg)
    assert ok["ready"] and ok["verified"] is False and ok["kind"] == "api"
    local = fr.engine_status("consumer", cfg_for("ollama"), reg)
    assert local["ready"] and local["reason"] == "local_server_not_checked"
    assert local["verified"] is False


def test_work_never_reports_a_remote_engine_as_ready(cfg_for, monkeypatch):
    from dodaios.core import config as cfg_mod

    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda p: "key")
    out = fr.engine_status("work", cfg_for("claude-api"), Registry("claude-api", "ollama"))
    assert not out["ready"] and not out["allowed"] and out["reason"] == "work_local_only"
    assert fr.engine_status("work", cfg_for("ollama"), Registry("ollama"))["ready"]


def test_summary_reads_the_configuration_and_offers_explore_when_not_ready(cfg_for):
    cfg = cfg_for("nope")
    cfg.safety.approval_mode = "bypass"  # the real shipped default
    out = fr.summary("consumer", fr.FirstRunChoices(interaction="voice"), cfg, Registry("ollama"))
    assert out["actions"]["approval_mode"] == "bypass"  # shown as it is, not as hoped
    assert out["memory"]["learning_enabled"] is True
    assert out["interaction"] == "voice" and out["start"] == "explore"
    assert out["personality"] == {"preset": "friendly", "mode": "mixed"}
    ready = fr.summary("consumer", fr.FirstRunChoices(), cfg_for("ollama"), Registry("ollama"))
    assert ready["start"] == "start"
