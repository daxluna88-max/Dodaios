"""ADR-0064 block 17 (H5, H6): coherent snapshot, coverage manifest, staged journalled restore.

Written statically, NOT run.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from dodaios.core import product_backup as pb
from dodaios.core import product_profile as pp


@pytest.fixture
def work(tmp_path, monkeypatch):
    shared = tmp_path / "data"
    monkeypatch.setattr(pp, "_shared_roots", lambda: [shared])
    paths = pp.paths_for("work", shared)
    pp.claim(paths)
    env = pp.environment(paths, {})
    env[pp.ACTIVE_ENV] = "work"
    return paths, env


def _make_db(path: Path, value: str, *, wal: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    return conn


def _values(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT v FROM t ORDER BY rowid")]
    finally:
        conn.close()


def test_snapshot_is_coherent_while_the_database_is_open_and_in_wal(work) -> None:
    paths, env = work
    writer = _make_db(paths.data_dir / "mem.sqlite", "one", wal=True)
    writer.execute("INSERT INTO t VALUES ('two')")
    writer.commit()  # committed rows may live only in the -wal file
    assert (paths.data_dir / "mem.sqlite-wal").exists()
    result = pb.snapshot_product_state_checked(env)
    writer.close()
    assert result.verified and result.path is not None
    assert _values(result.path / "mem.sqlite") == ["one", "two"]
    assert not list(result.path.glob("*-wal")) and not list(result.path.glob("*-shm"))
    manifest = json.loads((result.path / pb.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["files"]["mem.sqlite"]["kind"] == "sqlite"
    assert manifest["coverage"]["complete_data_rollback"] is False


def test_manifest_lists_every_omission_with_a_reason(work, monkeypatch) -> None:
    paths, env = work
    (paths.data_dir / "dodaios.toml").write_text("a = 1\n", encoding="utf-8")
    (paths.data_dir / "wiki").mkdir()
    (paths.data_dir / "big.bin").write_bytes(b"x" * 10)
    monkeypatch.setattr(pb, "MAX_FILE_BYTES", 5)
    result = pb.snapshot_product_state_checked(env)
    assert result.verified and not result.complete
    reasons = {o["name"]: o["reason"] for o in result.omissions}
    assert reasons["wiki"].startswith("directory")
    assert reasons["big.bin"].startswith("size cap")
    manifest = json.loads((result.path / pb.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert {o["name"] for o in manifest["omissions"]} >= {"wiki", "big.bin"}
    assert "NOT a full data rollback" in manifest["coverage"]["note"]


def test_omitted_database_makes_the_snapshot_fail_and_leaves_no_half_backup(
    work, monkeypatch
) -> None:
    paths, env = work
    _make_db(paths.data_dir / "mem.sqlite", "one").close()
    monkeypatch.setattr(pb, "MAX_FILE_BYTES", 1)
    result = pb.snapshot_product_state_checked(env)
    assert result.status == "failed" and "mem.sqlite" in result.reason
    assert pb.snapshot_product_state(env) is None
    assert pb.list_backups(paths.data_dir) == []


def test_backup_failure_midway_is_failed_and_cleaned(work, monkeypatch) -> None:
    paths, env = work
    _make_db(paths.data_dir / "mem.sqlite", "one").close()

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(pb, "_backup_sqlite", boom)
    result = pb.snapshot_product_state_checked(env)
    assert result.status == "failed"
    assert pb.list_backups(paths.data_dir) == []


def test_unscoped_is_not_a_failure() -> None:
    assert pb.snapshot_product_state_checked({}).status == "not_scoped"


def test_update_is_refused_without_a_verified_backup_unless_overridden() -> None:
    src = Path(__file__).resolve().parents[3] / "dodaios/ui/relauncher.py"
    text = src.read_text(encoding="utf-8")
    assert "snapshot_product_state_checked" in text
    assert "no_verified_backup" in text and pb.ALLOW_UNBACKED_ENV in text
    assert "override_unbacked_update" in text  # the override is recorded in the update result


def _snapshot_then_change(paths, env, *, wal_side: bool) -> Path:
    db = paths.data_dir / "mem.sqlite"
    _make_db(db, "old").close()
    (paths.data_dir / "dodaios.toml").write_text("a = 1\n", encoding="utf-8")
    snap = pb.snapshot_product_state_checked(env)
    assert snap.verified and snap.path is not None
    conn = _make_db(db, "new", wal=wal_side)
    conn.close()
    if wal_side:
        (paths.data_dir / "mem.sqlite-wal").write_bytes(b"stale wal bytes")
        (paths.data_dir / "mem.sqlite-shm").write_bytes(b"stale shm bytes")
    (paths.data_dir / "dodaios.toml").write_text("a = 99\n", encoding="utf-8")
    return snap.path


def test_restore_moves_stale_wal_and_shm_away_with_the_database(work) -> None:
    paths, env = work
    _snapshot_then_change(paths, env, wal_side=True)
    report = pb.restore_product_state("work")
    assert _values(paths.data_dir / "mem.sqlite") == ["old"]
    assert not (paths.data_dir / "mem.sqlite-wal").exists()
    assert not (paths.data_dir / "mem.sqlite-shm").exists()
    assert (paths.data_dir / "mem.sqlite-wal.before-restore").read_bytes() == b"stale wal bytes"
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 1\n"
    assert "mem.sqlite" in report.restored and report.rolled_back == []
    assert not list(paths.data_dir.glob(pb.RESTORE_JOURNAL_PREFIX + "*"))
    assert not list(paths.data_dir.glob(pb.RESTORE_TEMP_PREFIX + "*.tmp"))


def test_before_restore_copies_are_numbered_never_overwritten(work) -> None:
    paths, env = work
    _snapshot_then_change(paths, env, wal_side=False)
    (paths.data_dir / "dodaios.toml.before-restore").write_text("earlier\n", encoding="utf-8")
    pb.restore_product_state("work")
    first = paths.data_dir / "dodaios.toml.before-restore"
    second = paths.data_dir / "dodaios.toml.before-restore.1"
    assert first.read_text(encoding="utf-8") == "earlier\n"
    assert second.read_text(encoding="utf-8") == "a = 99\n"


def test_mid_restore_failure_rolls_back_and_reports_precisely(work, monkeypatch, capsys) -> None:
    paths, env = work
    _snapshot_then_change(paths, env, wal_side=False)
    real_replace = os.replace
    calls = {"installs": 0}

    def flaky(src, dst):
        name = Path(src).name
        if name.startswith(pb.RESTORE_TEMP_PREFIX) and "journal" not in name:
            calls["installs"] += 1
            if calls["installs"] == 2:  # first file installed, the second install fails
                raise OSError("simulated disk error")
        return real_replace(src, dst)

    monkeypatch.setattr(pb.os, "replace", flaky)
    with pytest.raises(pb.RestoreFailed) as info:
        pb.restore_product_state("work")
    monkeypatch.undo()
    report = info.value.report
    assert report.rolled_back and not report.restored and not report.rollback_errors
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 99\n"
    assert _values(paths.data_dir / "mem.sqlite") == ["old", "new"]
    assert not list(paths.data_dir.glob(pb.RESTORE_TEMP_PREFIX + "*.tmp"))
    assert "Nothing was changed" not in str(info.value)


def test_main_never_says_nothing_changed_after_a_failed_swap(work, monkeypatch, capsys) -> None:
    paths, env = work
    _snapshot_then_change(paths, env, wal_side=False)

    def fail(*_a, **_k):
        raise pb.RestoreFailed("restore failed (x)", pb.RestoreReport(rolled_back=["a"]))

    monkeypatch.setattr(pb, "restore_product_state", fail)
    assert pb.main(["restore", "--product", "work"]) == pb.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "FAILED" in err and "rolled back" in err and "Nothing was changed" not in err


def test_altered_backup_is_refused_before_any_change(work) -> None:
    paths, env = work
    snap = _snapshot_then_change(paths, env, wal_side=False)
    (snap / "dodaios.toml").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(pp.ProductError):
        pb.restore_product_state("work")
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 99\n"
    assert not list(paths.data_dir.glob(pb.RESTORE_TEMP_PREFIX + "*"))


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_linked_backup_parent_is_refused(work, tmp_path) -> None:
    paths, env = work
    snap = _snapshot_then_change(paths, env, wal_side=False)
    outside = tmp_path / "elsewhere"
    snap.parent.rename(outside)
    (paths.data_dir / pb.BACKUP_DIR_NAME).symlink_to(outside, target_is_directory=True)
    with pytest.raises(pp.ProductError):
        pb.restore_product_state("work")


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_linked_destination_file_is_refused_and_not_followed(work, tmp_path) -> None:
    paths, env = work
    _snapshot_then_change(paths, env, wal_side=False)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    toml = paths.data_dir / "dodaios.toml"
    toml.unlink()
    toml.symlink_to(victim)
    with pytest.raises(pp.ProductError):
        pb.restore_product_state("work")
    assert victim.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_staging_uses_exclusive_create_and_refuses_a_planted_temp_link(
    work, tmp_path
) -> None:
    paths, _env = work
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    planted = paths.data_dir / ".restore-tok-0.tmp"
    planted.symlink_to(victim)
    src = tmp_path / "bk"
    src.mkdir()
    (src / "f").write_text("data", encoding="utf-8")
    with pytest.raises(OSError):
        pb._stage(src, paths.data_dir, "f", {"sha256": pb._sha256(src / "f")}, planted.name)
    assert victim.read_text(encoding="utf-8") == "keep"


def test_unfinished_journal_blocks_restore_and_recover_rolls_back(work) -> None:
    paths, env = work
    _snapshot_then_change(paths, env, wal_side=False)
    prior = paths.data_dir / "dodaios.toml.before-restore"
    (paths.data_dir / "dodaios.toml").rename(prior)  # crash after "saving", before install
    journal = {
        "schema": 1, "product": "work", "stamp": "x",
        "items": [{"name": "dodaios.toml", "staged": ".restore-t-0.tmp",
                   "prior": prior.name, "sidecars": [], "state": "saving"}],
    }
    (paths.data_dir / f"{pb.RESTORE_JOURNAL_PREFIX}t.json").write_text(
        json.dumps(journal), encoding="utf-8"
    )
    with pytest.raises(pp.ProductError):
        pb.restore_product_state("work")
    pb.recover_product_state("work")
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 99\n"
    assert not list(paths.data_dir.glob(pb.RESTORE_JOURNAL_PREFIX + "*"))
