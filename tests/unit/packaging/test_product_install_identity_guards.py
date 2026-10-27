"""Product identity through install, update and restore (ADR-0064). Written, NOT run."""

from __future__ import annotations

from pathlib import Path

import pytest

from dodaios.core import product_backup as pb
from dodaios.core import product_profile as pp

REPO = Path(__file__).resolve().parents[3]


def test_relaunch_args_carry_the_active_product_only() -> None:
    assert pp.relaunch_args({}) == ()
    assert pp.relaunch_args({pp.ACTIVE_ENV: "work"}) == ("--product", "work")
    with pytest.raises(pp.ProductError):
        pp.relaunch_args({pp.ACTIVE_ENV: "both"})


def test_running_products_sees_a_held_lock_and_skips_its_own(tmp_path, monkeypatch) -> None:
    shared = tmp_path / "data"
    monkeypatch.setattr(pp, "_shared_roots", lambda: [shared])
    work = pp.paths_for("work", shared)
    work.data_dir.mkdir(parents=True)
    assert pp.running_products() == ((), ())  # no lock file: never ran
    lock = pp.acquire_launch_lock(work)
    try:
        assert pp.running_products() == (("work",), ())
        assert pp.running_products(exclude="work") == ((), ())
    finally:
        lock.release()
    assert pp.running_products() == ((), ())


def test_backup_and_restore_stay_inside_one_product(tmp_path, monkeypatch) -> None:
    shared = tmp_path / "data"
    monkeypatch.setattr(pp, "_shared_roots", lambda: [shared])
    paths = pp.paths_for("work", shared)
    pp.claim(paths)
    other = pp.paths_for("consumer", shared)
    pp.claim(other)
    paths.config_file.write_text("a = 1\n", encoding="utf-8")
    other.config_file.write_text("secret = 2\n", encoding="utf-8")
    env = pp.environment(paths, {})
    env[pp.ACTIVE_ENV] = "work"
    snap = pb.snapshot_product_state(env)
    assert snap is not None and snap.is_relative_to(paths.data_dir)
    assert not (other.data_dir / pb.BACKUP_DIR_NAME).exists()
    paths.config_file.write_text("a = 99\n", encoding="utf-8")
    pb.restore_product_state("work")
    assert paths.config_file.read_text(encoding="utf-8") == "a = 1\n"
    assert other.config_file.read_text(encoding="utf-8") == "secret = 2\n"


def test_restore_refuses_while_the_product_runs(tmp_path, monkeypatch) -> None:
    shared = tmp_path / "data"
    monkeypatch.setattr(pp, "_shared_roots", lambda: [shared])
    paths = pp.paths_for("work", shared)
    pp.claim(paths)
    paths.config_file.write_text("a = 1\n", encoding="utf-8")
    env = pp.environment(paths, {})
    env[pp.ACTIVE_ENV] = "work"
    assert pb.snapshot_product_state(env) is not None
    lock = pp.acquire_launch_lock(paths)
    try:
        with pytest.raises(pp.AlreadyRunning):
            pb.restore_product_state("work")
    finally:
        lock.release()


def test_unscoped_snapshot_does_nothing() -> None:
    assert pb.snapshot_product_state({}) is None


def test_installers_refuse_product_before_any_step() -> None:
    sh = (REPO / "install/install.sh").read_text(encoding="utf-8")
    ps = (REPO / "install/install.ps1").read_text(encoding="utf-8")
    py = (REPO / "install/installer.py").read_text(encoding="utf-8")
    needle = "--product|--product=*"
    assert needle in sh and sh.index(needle) < sh.index("ensure_linux")
    marker = "prerequisite-bootstrap begin"
    assert "--product=*" in ps and ps.index("--product=*") < ps.index(marker)
    assert '"--product"' in py and "return 3" in py


def test_windows_setup_passes_the_product_to_the_relaunch_entry() -> None:
    iss = (REPO / "packaging/windows/Dodaios.iss").read_text(encoding="utf-8")
    assert "{code:ProductParam}" in iss
    assert "(Value = 'work') or (Value = 'consumer')" in iss
    from dodaios.core import installer_update as iu

    assert iu.WINDOWS_PRODUCT_PARAM == "/PRODUCT"


def test_update_entries_refuse_while_another_product_runs() -> None:
    routes = (REPO / "dodaios/ui/web/update_routes.py").read_text(encoding="utf-8")
    cli = (REPO / "dodaios/cli/app_update.py").read_text(encoding="utf-8")
    assert "_refuse_if_other_product_runs()" in routes
    assert "_other_product_refusal()" in cli
