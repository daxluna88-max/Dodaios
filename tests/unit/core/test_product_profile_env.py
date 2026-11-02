"""Block 5 (ADR-0054): environment and entry points. WRITTEN, NOT RUN (owner order: no tests run).

In-process tests call ``validate_inherited`` / ``validate_entry`` with an explicit environment
(``activate_from_argv`` is used only for refusals: its success path binds the credential service
of this process, which the subprocess tests in ``test_product_profile.py`` cover). The last tests
start ``python -m dodaios`` with entries that stop BEFORE any update, relaunch or server work.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dodaios.core import product_profile as pp


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _claimed_env(home: Path, product: str = "work") -> dict[str, str]:
    """A product's environment exactly as its launch would export it, directory claimed."""
    paths = pp.paths_for(product, home / ".dodaios-data")
    pp.claim(paths)
    env = pp.environment(paths, {})
    env[pp.ACTIVE_ENV] = product
    return env


def test_a_consistent_inherited_environment_is_accepted_read_only(home):
    env = _claimed_env(home)
    paths = pp.validate_inherited("work", env)
    assert paths.product == "work"
    assert paths.data_dir == Path(env[pp.DATA_DIR_ENV])
    assert paths.config_file == Path(env[pp.CONFIG_ENV])
    assert pp.validate_entry(["update"], env) == ["update"]
    assert not paths.lock_file.exists(), "validation never takes the launch lock"


@pytest.mark.parametrize(
    "key, value, message",
    [
        (pp.PRODUCT_ENV, "consumer", "inconsistent"),
        (pp.PRODUCT_ENV, "", "incomplete"),
        (pp.KEYRING_SERVICE_ENV, "dodaios-consumer", "inconsistent"),
        (pp.KEYRING_SERVICE_ENV, "dodaios", "inconsistent"),
        (pp.DATA_DIR_ENV, "relative/dir-work", "absolute"),
        (pp.DATA_DIR_ENV, "/somewhere/else", "not a work product directory"),
        (pp.CONFIG_ENV, "/elsewhere/dodaios.toml", "config"),
        (pp.CONFIG_ENV, "", "incomplete"),
    ],
)
def test_an_inconsistent_inherited_environment_is_refused(home, key, value, message):
    env = _claimed_env(home)
    env[key] = value
    with pytest.raises(pp.ProductError, match=message):
        pp.activate_from_argv(["--version"], env)
    with pytest.raises(pp.ProductError, match=message):
        pp.validate_entry([], env)


def test_a_directory_claimed_by_the_other_product_is_refused_even_when_inherited(home):
    env = _claimed_env(home, "work")
    (Path(env[pp.DATA_DIR_ENV]) / pp.MARKER_NAME).write_text(
        '{"product": "consumer"}', encoding="utf-8"
    )
    with pytest.raises(pp.ProductConflict, match="belongs to the consumer"):
        pp.activate_from_argv([], env)


def test_an_unclaimed_or_corrupt_directory_is_refused_when_inherited(home):
    env = _claimed_env(home)
    marker = Path(env[pp.DATA_DIR_ENV]) / pp.MARKER_NAME
    marker.write_text("not json", encoding="utf-8")
    with pytest.raises(pp.ProductError):
        pp.validate_entry([], env)
    marker.unlink()
    with pytest.raises(pp.ProductError, match="never claimed"):
        pp.validate_entry([], env)


def test_the_shared_directory_is_never_accepted_as_a_product_directory(home):
    env = _claimed_env(home)
    nested = home / ".dodaios-data" / "nested-work"
    env[pp.DATA_DIR_ENV] = str(nested)
    env[pp.CONFIG_ENV] = str(nested / pp.CONFIG_FILE_NAME)
    with pytest.raises(pp.ProductConflict, match="overlaps"):
        pp.validate_entry([], env)


def test_a_product_credential_service_without_an_announced_product_is_refused(home):
    env = {pp.KEYRING_SERVICE_ENV: "dodaios-work"}
    with pytest.raises(pp.ProductError, match="inconsistent"):
        pp.activate_from_argv(["--version"], env)
    with pytest.raises(pp.ProductError, match="inconsistent"):
        pp.validate_entry(["--relauncher"], env)
    # an unrelated service name is ignored, as everywhere else
    assert pp.activate_from_argv([], {pp.KEYRING_SERVICE_ENV: "other"})[0] is None


@pytest.mark.parametrize(
    "argv",
    [
        ["--product", "work", "update"],
        ["update", "--product", "work"],
        ["update", "--product=work"],
        ["--product=work", "update", "--check"],
    ],
)
def test_the_product_flag_is_found_in_any_position_and_spelling(home, argv):
    env = _claimed_env(home)
    rest = pp.validate_entry(argv, env)
    assert "update" in rest
    assert not any(a.startswith("--product") for a in rest) and "work" not in rest
    with pytest.raises(pp.ProductError, match="contradicts"):
        pp.validate_entry([a.replace("work", "consumer") for a in argv], env)


def test_a_helper_entry_takes_no_product_flag(home):
    env = _claimed_env(home)
    with pytest.raises(pp.ProductError, match="not accepted here"):
        pp.validate_entry(["--product", "work", "1", "cwd"], env, allow_flag=False)
    assert pp.validate_entry(["1", "cwd"], env, allow_flag=False) == ["1", "cwd"]


def test_flag_errors_surface_on_every_entry():
    with pytest.raises(pp.ProductError, match="needs a value"):
        pp.validate_entry(["update", "--product"], {})
    with pytest.raises(pp.ProductError, match="unknown product"):
        pp.validate_entry(["update", "--product=enterprise"], {})


def test_a_relaunch_environment_reuses_the_directory_with_no_second_suffix(home):
    env = _claimed_env(home)
    first = pp.validate_inherited("work", env)
    relaunched = dict(env)  # what relauncher.fresh_user_env hands the new launcher
    again = pp.validate_inherited("work", relaunched)
    assert again == first
    assert again.data_dir.name == ".dodaios-data-work"


def test_user_data_dir_refuses_a_path_that_is_not_the_active_products(tmp_path, monkeypatch):
    from dodaios.core import paths

    monkeypatch.setenv("JARVIS_PRODUCT_ACTIVE", "work")
    monkeypatch.setenv("JARVIS_DATA_DIR", "relative-work")
    with pytest.raises(RuntimeError):
        paths.user_data_dir()
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "plain"))
    with pytest.raises(RuntimeError, match="not the work product directory"):
        paths.user_data_dir()
    owned = tmp_path / "x-work"
    owned.mkdir()
    (owned / pp.MARKER_NAME).write_text('{"product": "consumer"}', encoding="utf-8")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(owned))
    with pytest.raises(RuntimeError, match="belongs to the consumer"):
        paths.user_data_dir()
    (owned / pp.MARKER_NAME).write_text('{"product": "work"}', encoding="utf-8")
    assert paths.user_data_dir() == owned


# -- early entries of ``python -m dodaios``: refused before anything is updated or served ------


def _run_entry(home: Path, extra_env: dict[str, str], *args: str):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("JARVIS_") and k != "LOCALAPPDATA"}
    env["HOME"] = str(home)
    env.update(extra_env)
    return subprocess.run([sys.executable, "-m", "dodaios", *args], env=env,  # noqa: S603
                          capture_output=True, text=True, timeout=120, check=False)


@pytest.mark.parametrize("entry", ["--relauncher", "--turn-host"])
def test_helper_entries_refuse_a_product_flag(tmp_path, entry):
    for flag in (["--product", "work"], ["--product=work"]):
        out = _run_entry(tmp_path, {}, *flag, entry, "1", "x")
        assert out.returncode == 2 and "not accepted here" in out.stderr
        out = _run_entry(tmp_path, {}, entry, "1", "x", *flag)
        assert out.returncode == 2 and "not accepted here" in out.stderr


@pytest.mark.parametrize("entry", ["--relauncher", "--turn-host"])
def test_helper_entries_refuse_an_incomplete_inherited_product(tmp_path, entry):
    out = _run_entry(tmp_path, {"JARVIS_PRODUCT_ACTIVE": "work"}, entry, "1", "x")
    assert out.returncode == 2 and "incomplete" in out.stderr
    out = _run_entry(tmp_path, {"JARVIS_KEYRING_SERVICE": "dodaios-work"}, entry, "1", "x")
    assert out.returncode == 2 and "inconsistent" in out.stderr


def test_update_entry_refuses_a_contradicting_or_unknown_product(tmp_path):
    out = _run_entry(tmp_path, {}, "update", "--product", "enterprise")
    assert out.returncode == 2 and "unknown product" in out.stderr
    out = _run_entry(tmp_path, {}, "--product=enterprise", "update", "--check")
    assert out.returncode == 2 and "unknown product" in out.stderr
