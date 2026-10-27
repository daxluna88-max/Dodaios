"""Desktop output mirror per product (ADR-0054, block 5). Written, not run.

Nothing here touches a real Desktop: Path.home() points at tmp_path and the Windows junction
command is a recorder.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from dodaios.core import identity_boot, paths

needs_symlink = pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    (h / "Desktop").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    monkeypatch.delenv("JARVIS_PRODUCT_ACTIVE", raising=False)
    identity_boot.reset_resolution_cache()
    return h


def test_products_get_distinct_mirror_roots(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = {}
    for product in ("work", "consumer"):
        monkeypatch.setenv("JARVIS_PRODUCT_ACTIVE", product)
        roots[product] = paths.desktop_mirror_dir()
    assert roots["work"] == home / "Desktop" / "Dodaios-Output-work"
    assert roots["consumer"] == home / "Desktop" / "Dodaios-Output-consumer"
    assert roots["work"] != roots["consumer"]
    monkeypatch.delenv("JARVIS_PRODUCT_ACTIVE")
    assert paths.desktop_mirror_dir() == home / "Desktop" / "Dodaios-Output"


@needs_symlink
def test_mirror_is_safe_only_for_a_link_to_this_canonical(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    mine, other = tmp_path / "mine", tmp_path / "other"
    mine.mkdir()
    other.mkdir()
    good, foreign = root / "good", root / "foreign"
    os.symlink(mine, good, target_is_directory=True)
    os.symlink(other, foreign, target_is_directory=True)
    real = root / "real"
    real.mkdir()
    assert paths.mirror_is_safe(root, good, mine)
    assert not paths.mirror_is_safe(root, foreign, mine)
    assert not paths.mirror_is_safe(root, real, mine)
    assert not paths.mirror_is_safe(root, root / "missing", mine)


@needs_symlink
def test_a_link_root_is_never_trusted(tmp_path: Path) -> None:
    target_root = tmp_path / "elsewhere"
    target_root.mkdir()
    link_root = tmp_path / "linkroot"
    os.symlink(target_root, link_root, target_is_directory=True)
    canonical = tmp_path / "canon"
    canonical.mkdir()
    os.symlink(canonical, target_root / "s", target_is_directory=True)
    assert not paths.mirror_is_safe(link_root, link_root / "s", canonical)


@needs_symlink
def test_existing_foreign_link_is_left_alone_and_not_returned(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = tmp_path / "outputs"
    monkeypatch.setattr(paths, "user_outputs_dir", lambda: outputs)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("JARVIS_PRODUCT_ACTIVE", "work")
    foreign = tmp_path / "consumer-output" / "s1"
    foreign.mkdir(parents=True)
    root = home / "Desktop" / "Dodaios-Output-work"
    root.mkdir()
    os.symlink(foreign, root / "s1", target_is_directory=True)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    canonical, mirror = paths.ensure_session_output_dir("s1")

    assert canonical == outputs / "s1"
    assert mirror == canonical  # the foreign link is not adopted
    assert os.path.realpath(root / "s1") == os.path.realpath(foreign)  # and not replaced
    assert foreign.is_dir()
    assert calls == []  # no mklink over an occupied name


def test_a_real_folder_at_the_mirror_name_is_not_adopted(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = tmp_path / "outputs"
    monkeypatch.setattr(paths, "user_outputs_dir", lambda: outputs)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("JARVIS_PRODUCT_ACTIVE", "consumer")
    keep = home / "Desktop" / "Dodaios-Output-consumer" / "s1"
    keep.mkdir(parents=True)
    (keep / "user-file.txt").write_text("mine")
    canonical, mirror = paths.ensure_session_output_dir("s1")
    assert mirror == canonical
    assert (keep / "user-file.txt").read_text() == "mine"


def test_failed_mklink_returns_canonical(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = tmp_path / "outputs"
    monkeypatch.setattr(paths, "user_outputs_dir", lambda: outputs)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("JARVIS_PRODUCT_ACTIVE", "work")
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    canonical, mirror = paths.ensure_session_output_dir("s2")
    assert calls and calls[0][:4] == ["cmd", "/c", "mklink", "/J"]
    assert str(home / "Desktop" / "Dodaios-Output-work" / "s2") in calls[0]
    assert mirror == canonical  # the recorder created no junction
