"""Per-product uninstall guards (ADR-0054, block 5). Written, not run.

Everything runs on tmp_path with the removal helpers replaced by recorders; no real keyring,
autostart entry or install folder is touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dodaios.core import identity_boot
from dodaios.core.identity_boot import DirectoryPair
from dodaios.setup import uninstall
from dodaios.setup.uninstall import UninstallPlan


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    called: list[str] = []
    for name in (
        "_stop_running_instances",
        "_remove_desktop_registration",
        "_remove_autostart",
        "_remove_cli_commands",
        "_remove_folder",
        "_remove_install_links",
    ):
        monkeypatch.setattr(uninstall, name, lambda *a, _n=name, **k: called.append(_n) or 0)
    monkeypatch.setattr(uninstall, "_remove_keys", lambda keys: called.append(f"keys:{keys}"))
    monkeypatch.setattr(uninstall, "_keyring_keys_present", lambda: ["K1"])
    monkeypatch.setattr(uninstall, "_confirm", lambda: True)
    monkeypatch.delenv("JARVIS_PRODUCT_ACTIVE", raising=False)
    return called


def _plan(tmp: Path, **kw: object) -> UninstallPlan:
    return UninstallPlan(
        install_dir=tmp,
        is_jarvis_install=True,
        autostart_supported=True,
        autostart_entry=None,
        **kw,  # type: ignore[arg-type]
    )


def _install(tmp: Path) -> Path:
    root = tmp / "install"
    (root / "dodaios").mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    return root


def _product_env(
    tmp: Path, monkeypatch: pytest.MonkeyPatch, product: str = "consumer", owner: str | None = None
) -> Path:
    data = tmp / f"data-{product}"
    data.mkdir()
    (data / ".product").write_text(json.dumps({"product": owner or product}))
    (data / "memory.db").write_text("x")
    monkeypatch.setenv("JARVIS_PRODUCT_ACTIVE", product)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data))
    monkeypatch.setattr(uninstall.cfg, "KEYRING_SERVICE", f"dodaios-{product}")
    monkeypatch.setattr(uninstall.cfg, "PROJECT_ROOT", str(_install(tmp)))
    return data


def test_full_uninstall_refuses_while_a_product_dir_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    pdir = tmp_path / "Dodaios-work"
    pdir.mkdir()
    monkeypatch.setattr(uninstall, "build_plan", lambda: _plan(tmp_path, product_dirs=(pdir,)))
    assert uninstall.run_uninstall(assume_yes=True) == 3
    assert uninstall.run_uninstall(assume_yes=True, dry_run=True) == 3
    assert steps == [] and pdir.is_dir()


def test_full_uninstall_refuses_when_the_product_scan_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    monkeypatch.setattr(uninstall, "build_plan", lambda: _plan(tmp_path, product_scan_failed=True))
    assert uninstall.run_uninstall(assume_yes=True) == 3
    assert steps == []


def test_full_uninstall_still_runs_with_no_product_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    monkeypatch.setattr(uninstall, "build_plan", lambda: _plan(tmp_path))
    assert uninstall.run_uninstall(assume_yes=True) == 0
    assert "_remove_folder" in steps


def test_product_scan_lists_only_existing_dirs_without_creating_any(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "Dodaios"
    monkeypatch.setattr(
        identity_boot, "user_data_dir_pair", lambda *a, **k: DirectoryPair(shared, ())
    )
    assert uninstall._product_dirs_present() == ((), False)
    (tmp_path / "Dodaios-consumer").mkdir()
    dirs, failed = uninstall._product_dirs_present()
    assert dirs == (tmp_path / "Dodaios-consumer",) and failed is False
    assert not (tmp_path / "Dodaios-work").exists()


def test_product_mode_removes_only_its_data_and_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    data = _product_env(tmp_path, monkeypatch)
    other = tmp_path / "data-work"
    other.mkdir()
    assert uninstall.run_uninstall(assume_yes=True) == 0
    assert not data.exists()
    assert other.is_dir()
    assert steps == ["keys:['K1']"]  # no folder, autostart, desktop, CLI or process step


def test_product_mode_dry_run_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    data = _product_env(tmp_path, monkeypatch)
    assert uninstall.run_uninstall(dry_run=True) == 0
    assert (data / "memory.db").is_file() and steps == []


def test_product_mode_keep_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    data = _product_env(tmp_path, monkeypatch)
    assert uninstall.run_uninstall(assume_yes=True, keep_keys=True, keep_folder=True) == 0
    assert data.is_dir() and steps == []


def test_product_mode_refuses_a_foreign_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    data = _product_env(tmp_path, monkeypatch, product="consumer", owner="work")
    assert uninstall.run_uninstall(assume_yes=True) == 3
    assert data.is_dir() and steps == []


def test_product_mode_refuses_a_missing_or_corrupt_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    data = _product_env(tmp_path, monkeypatch)
    (data / ".product").write_text("not json")
    assert uninstall.run_uninstall(assume_yes=True) == 3
    (data / ".product").unlink()
    assert uninstall.run_uninstall(assume_yes=True) == 3
    assert data.is_dir() and steps == []


def test_product_mode_refuses_when_the_credential_service_is_not_the_products(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    data = _product_env(tmp_path, monkeypatch)
    monkeypatch.setattr(uninstall.cfg, "KEYRING_SERVICE", "dodaios")
    assert uninstall.run_uninstall(assume_yes=True) == 3
    assert data.is_dir() and steps == []


def test_product_mode_refuses_a_contradicting_product_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    data = _product_env(tmp_path, monkeypatch, product="consumer")
    assert uninstall.run_uninstall(assume_yes=True, product="work") == 3
    assert data.is_dir() and steps == []


def test_product_mode_refuses_a_data_dir_inside_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    data = _product_env(tmp_path, monkeypatch)
    monkeypatch.setattr(uninstall.cfg, "PROJECT_ROOT", str(data.parent))
    (data.parent / "dodaios").mkdir(exist_ok=True)
    (data.parent / "pyproject.toml").write_text("")
    assert uninstall.run_uninstall(assume_yes=True) == 3
    assert data.is_dir()


def test_product_mode_refuses_when_the_data_dir_is_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]
) -> None:
    real = _product_env(tmp_path, monkeypatch)
    link = tmp_path / "link-consumer"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(link))
    assert uninstall.run_uninstall(assume_yes=True) == 3
    assert (real / "memory.db").is_file()
