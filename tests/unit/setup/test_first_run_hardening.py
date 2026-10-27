"""Block 11 hardening of the first run: journal, reset, isolation, honest levels, permissions.

WRITTEN, NOT RUN (owner order: no test execution). Check on a PC before relying on it.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from dodaios.core import config_writer
from dodaios.core import product_profile as pp
from dodaios.setup import first_run as fr
from dodaios.setup import state as st


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = tmp_path / "dodaios.toml"
    cfg.write_text('[brain]\nprimary = "ollama"\n', encoding="utf-8")
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    return SimpleNamespace(cfg=cfg, state=tmp_path / "setup_state.json", root=tmp_path)


def cfg_obj(approval="ask", learning=False):
    return SimpleNamespace(
        safety=SimpleNamespace(approval_mode=approval),
        memory=SimpleNamespace(learning=SimpleNamespace(enabled=learning)))


# ---- partial saves / journal ---------------------------------------------------------------


def test_reset_clears_the_real_applied_mark(env):
    """Regression: the applied key was missing from the onboarding reset (a replay skipped writes)."""
    choices = fr.FirstRunChoices()
    fr.apply("consumer", choices, state_path=env.state, config_path=env.cfg)
    assert fr.is_applied("consumer", choices, env.state)
    st.reset_onboarding(env.state)
    assert not fr.is_applied("consumer", choices, env.state)


def test_interrupted_apply_leaves_a_journal_and_no_applied_mark(env, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(config_writer, "set_learning_enabled", boom)
    choices = fr.FirstRunChoices()
    with pytest.raises(fr.FirstRunError) as err:
        fr.apply("consumer", choices, state_path=env.state, config_path=env.cfg)
    assert err.value.step == "memory"
    assert not fr.is_applied("consumer", choices, env.state)
    j = fr.journal(env.state)
    assert j["done"] == ["personality", "approval"] and j["resume"] == "memory"


def test_rerun_completes_and_removes_the_journal(env, monkeypatch):
    real = config_writer.set_learning_enabled
    monkeypatch.setattr(config_writer, "set_learning_enabled",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    choices = fr.FirstRunChoices(memory="on")
    with pytest.raises(fr.FirstRunError):
        fr.apply("consumer", choices, state_path=env.state, config_path=env.cfg)
    monkeypatch.setattr(config_writer, "set_learning_enabled", real)
    result = fr.apply("consumer", choices, state_path=env.state, config_path=env.cfg)
    assert result.applied[-1] == "memory" and not result.already
    assert fr.journal(env.state)["planned"] == []
    assert tomllib.loads(env.cfg.read_text())["memory"]["learning"]["enabled"] is True


def test_a_failed_mark_write_is_not_reported_as_applied(env, monkeypatch):
    real = st._merge_state

    def drop_applied(updates, path=None):
        if fr.APPLIED_KEY in updates:
            return  # the real writer only logs a failed write
        real(updates, path)

    monkeypatch.setattr(st, "_merge_state", drop_applied)
    with pytest.raises(fr.FirstRunError) as err:
        fr.apply("consumer", fr.FirstRunChoices(), state_path=env.state, config_path=env.cfg)
    assert err.value.step == "mark"


def test_a_choice_that_did_not_reach_the_disk_raises(env, monkeypatch):
    monkeypatch.setattr(st, "_merge_state", lambda *a, **k: None)
    with pytest.raises(fr.FirstRunError) as err:
        fr.save_choices(fr.FirstRunChoices(owner_name="Dax"), env.state)
    assert err.value.step == "save"


# ---- reset ---------------------------------------------------------------------------------


def test_reset_removes_only_first_run_state_and_keeps_terms(env):
    st.accept_terms("v1", env.state)
    st.set_onboarding_step("connect", path=env.state)
    fr.save_choices(fr.FirstRunChoices(owner_name="Dax"), env.state)
    fr.apply("consumer", fr.FirstRunChoices(), state_path=env.state, config_path=env.cfg)
    before_cfg = env.cfg.read_text()
    plan = fr.reset_choices(env.state)
    data = json.loads(env.state.read_text())
    assert "terms_accepted_at" in data
    for key in (fr.STATE_KEY, fr.APPLIED_KEY, fr.JOURNAL_KEY, "onboarding_step"):
        assert key not in data
    assert env.cfg.read_text() == before_cfg  # config untouched without an explicit option
    assert "credentials" in plan["keeps"] and "memory_content" in plan["keeps"]


def test_reset_plan_lists_what_goes_and_rejects_unknown_options(env):
    fr.save_choices(fr.FirstRunChoices(), env.state)
    plan = fr.reset_plan(env.state, ("learning_switch",))
    assert fr.STATE_KEY in plan["removes_state"] and plan["removes_config"] == ["learning_switch"]
    with pytest.raises(ValueError):
        fr.reset_plan(env.state, ("credentials",))  # never offered
    with pytest.raises(ValueError):
        fr.reset_choices(env.state, include=("memory_content",))


def test_reset_with_options_changes_only_the_ticked_config(env):
    fr.apply("consumer", fr.FirstRunChoices(memory="on"), state_path=env.state, config_path=env.cfg)
    fr.reset_choices(env.state, include=("learning_switch",), config_path=env.cfg)
    data = tomllib.loads(env.cfg.read_text())
    assert data["memory"]["learning"]["enabled"] is False
    assert "personality" in data["brain"]  # not ticked: stays
    assert data["safety"]["approval_mode"] == "ask"  # approval is never reverted by a reset


# ---- isolation -----------------------------------------------------------------------------


@pytest.fixture
def product_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    shared = tmp_path / "data"
    shared.mkdir()
    monkeypatch.setattr(pp, "_shared_roots", lambda: [shared])
    paths = {p: pp.paths_for(p, shared) for p in pp.PRODUCTS}
    for p in paths.values():
        pp.claim(p)
    return paths


def test_work_state_in_its_own_directory_passes(product_dirs, monkeypatch):
    work = product_dirs["work"]
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    fr.check_isolation("work", work.data_dir / "setup_state.json", work.config_file)


def test_a_state_file_in_the_other_products_directory_is_refused(product_dirs, monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    other = product_dirs["consumer"]
    with pytest.raises(fr.IsolationError):
        fr.check_isolation("work", other.data_dir / "setup_state.json")
    with pytest.raises(fr.IsolationError):
        fr.save_choices(fr.FirstRunChoices(), other.data_dir / "setup_state.json", "work")
    assert not (other.data_dir / "setup_state.json").exists()


def test_a_config_outside_the_product_directory_is_refused_and_nothing_is_written(
        product_dirs, monkeypatch, tmp_path):
    work = product_dirs["work"]
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    stray = tmp_path / "dodaios.toml"
    stray.write_text("[brain]\n", encoding="utf-8")
    with pytest.raises(fr.IsolationError):
        fr.apply("work", fr.FirstRunChoices(personality_preset="professional"),
                 state_path=work.data_dir / "setup_state.json", config_path=stray)
    assert stray.read_text() == "[brain]\n"


def test_a_run_for_the_other_product_is_refused(product_dirs, monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    with pytest.raises(fr.IsolationError):
        fr.check_isolation("consumer", product_dirs["work"].data_dir / "setup_state.json")


def test_work_never_offers_a_cloud_engine(env):
    cfg = SimpleNamespace(brain=SimpleNamespace(primary="anthropic"))
    status = fr.engine_status("work", cfg, SimpleNamespace(available=lambda: ["anthropic"]))
    assert status["allowed"] is False and status["ready"] is False
    assert status["reason"] == "work_local_only"


# ---- honest availability and permissions ---------------------------------------------------


def test_a_local_engine_whose_program_is_missing_is_not_ready(monkeypatch):
    monkeypatch.setattr(fr.shutil, "which", lambda _n: None)
    cfg = SimpleNamespace(brain=SimpleNamespace(primary="ollama"))
    status = fr.engine_status("work", cfg, SimpleNamespace(available=lambda: ["ollama"]))
    assert status["ready"] is False and status["reason"] == "local_not_found"
    assert status["levels"] == {"configured": "yes", "detected": "no",
                                "reachable": "not_checked", "verified": "not_run"}


def test_a_detected_local_engine_is_ready_to_try_but_never_reachable_or_verified(monkeypatch):
    monkeypatch.setattr(fr.shutil, "which", lambda _n: "/usr/bin/ollama")
    cfg = SimpleNamespace(brain=SimpleNamespace(primary="ollama"))
    status = fr.engine_status("work", cfg, SimpleNamespace(available=lambda: ["ollama"]))
    assert status["ready"] is True and status["verified"] is False
    assert status["levels"]["reachable"] == "not_checked"
    assert status["levels"]["verified"] == "not_run"


def test_a_generic_local_server_cannot_be_confirmed_without_contact():
    cfg = SimpleNamespace(brain=SimpleNamespace(primary="local-openai"))
    status = fr.engine_status("work", cfg, SimpleNamespace(available=lambda: ["local-openai"]))
    assert status["ready"] is False and status["reason"] == "local_unconfirmed"
    assert status["levels"]["detected"] == "unknown"


def test_every_permission_defaults_off_and_nothing_is_granted_by_choosing_voice():
    perms = {p["id"]: p for p in fr.permissions(fr.FirstRunChoices(interaction="voice"), cfg_obj())}
    for pid in ("microphone", "screen", "files", "network", "memory_learning"):
        assert perms[pid]["state"] == "off"
    assert perms["microphone"]["note"] == "asked_at_first_use"
    assert perms["tools"]["state"] == "ask"


def test_memory_learning_is_on_only_when_the_configuration_says_so():
    rows = fr.permissions(fr.FirstRunChoices(), cfg_obj(learning=True))
    perms = {p["id"]: p["state"] for p in rows}
    assert perms["memory_learning"] == "on"


def test_components_probe_reads_only_and_never_claims_model_or_microphone(monkeypatch):
    monkeypatch.setattr(fr.importlib.util, "find_spec", lambda _n: None)
    voice = fr.components(fr.FirstRunChoices(interaction="both"))["voice"]
    assert voice["speech_package"] == "missing"
    assert voice["model_weights"] == "not_checked" and voice["microphone"] == "not_asked"
    assert fr.components(fr.FirstRunChoices())["voice"]["speech_package"] == "not_needed"
