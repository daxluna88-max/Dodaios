"""ADR-0054 Block 8 R8: product roots are canonical siblings, pairwise disjoint, link-free.

Written, NOT run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dodaios.core import product_profile as pp


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _env(home: Path, product: str) -> dict[str, str]:
    paths = pp.paths_for(product, home / ".dodaios-data")
    pp.claim(paths)
    env = pp.environment(paths, {})
    env[pp.ACTIVE_ENV] = product
    return env


def _point(env: dict[str, str], directory: Path) -> None:
    env[pp.DATA_DIR_ENV] = str(directory)
    env[pp.CONFIG_ENV] = str(directory / pp.CONFIG_FILE_NAME)


def test_a_consumer_directory_nested_inside_work_is_refused(home):
    _env(home, "work")
    nested = home / ".dodaios-data-work" / "nested-consumer"
    nested.mkdir()
    (nested / pp.MARKER_NAME).write_text('{"product": "consumer"}', encoding="utf-8")
    env = _env(home, "consumer")
    _point(env, nested)
    with pytest.raises(pp.ProductError):
        pp.validate_inherited("consumer", env)


def test_a_non_sibling_directory_is_refused_even_when_claimed(home):
    env = _env(home, "work")
    elsewhere = home / "elsewhere" / "x-work"
    elsewhere.mkdir(parents=True)
    (elsewhere / pp.MARKER_NAME).write_text('{"product": "work"}', encoding="utf-8")
    _point(env, elsewhere)
    with pytest.raises(pp.ProductError, match="canonical"):
        pp.validate_inherited("work", env)


def test_a_symlinked_product_directory_is_refused(home):
    env = _env(home, "work")
    real = home / "real-work"
    real.mkdir()
    (real / pp.MARKER_NAME).write_text('{"product": "work"}', encoding="utf-8")
    link = home / "link-work"
    link.symlink_to(real, target_is_directory=True)
    _point(env, link)
    with pytest.raises(pp.ProductError):
        pp.validate_inherited("work", env)


def test_a_config_symlinked_to_the_other_products_file_is_refused(home):
    consumer = _env(home, "consumer")
    other_cfg = Path(consumer[pp.CONFIG_ENV])
    other_cfg.write_text("", encoding="utf-8")
    env = _env(home, "work")
    cfg = Path(env[pp.CONFIG_ENV])
    cfg.symlink_to(other_cfg)
    with pytest.raises(pp.ProductError, match="link"):
        pp.validate_inherited("work", env)


def test_an_ancestor_of_the_other_product_is_refused(home):
    env = _env(home, "work")
    consumer_dir = pp.paths_for("consumer", home / ".dodaios-data").data_dir
    consumer_dir.mkdir()
    parent_named = consumer_dir / "inner-work"
    parent_named.mkdir()
    _point(env, parent_named)
    with pytest.raises(pp.ProductConflict, match="consumer product directory"):
        pp.check_product_root("work", parent_named)


def test_user_data_dir_refuses_a_directory_nested_in_the_other_product(home, monkeypatch):
    from dodaios.core import paths

    _env(home, "consumer")
    nested = home / ".dodaios-data-consumer" / "deep-work"
    nested.mkdir()
    monkeypatch.setenv("JARVIS_PRODUCT_ACTIVE", "work")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(nested))
    with pytest.raises(RuntimeError, match="overlaps"):
        paths.user_data_dir()


def test_the_canonical_sibling_is_accepted(home):
    env = _env(home, "work")
    assert pp.validate_inherited("work", env).data_dir == home / ".dodaios-data-work"
    assert os.path.realpath(env[pp.DATA_DIR_ENV]) == str(
        (home / ".dodaios-data-work").resolve()
    )


def test_uninstall_refuses_a_product_directory_nested_in_the_other(home, monkeypatch):
    from dodaios.setup import uninstall

    _env(home, "work")
    nested = home / ".dodaios-data-work" / "nested-consumer"
    nested.mkdir()
    (nested / pp.MARKER_NAME).write_text('{"product": "consumer"}', encoding="utf-8")
    monkeypatch.setenv(pp.DATA_DIR_ENV, str(nested))
    result = uninstall._product_data_dir("consumer", home / "install")
    assert isinstance(result, str)
