"""ADR-0064 block 18 (I2, I3): validated restore journals and durable terminal states.

Written statically, NOT run.
"""

from __future__ import annotations

import json
import os
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


def _prepare(paths, env) -> None:
    (paths.data_dir / "dodaios.toml").write_text("a = 1\n", encoding="utf-8")
    snap = pb.snapshot_product_state_checked(env)
    assert snap.verified
    (paths.data_dir / "dodaios.toml").write_text("a = 99\n", encoding="utf-8")


def _journal(**over) -> dict:
    base = {
        "schema": 2, "product": "work", "stamp": "x", "state": "swapping",
        "items": [{"name": "dodaios.toml", "staged": ".restore-t-0.tmp",
                   "prior": "dodaios.toml.before-restore", "sidecars": [], "state": "saving"}],
    }
    base.update(over)
    return base


def _write(paths, journal, tag="t") -> Path:
    path = paths.data_dir / f"{pb.RESTORE_JOURNAL_PREFIX}{tag}.json"
    path.write_text(json.dumps(journal), encoding="utf-8")
    return path


def _snapshot_dir(paths) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in paths.data_dir.iterdir() if p.is_file()}


def _item(**over) -> dict:
    item = _journal()["items"][0]
    item.update(over)
    return item


BAD_JOURNALS = {
    "absolute name": _journal(items=[_item(name="/etc/passwd")]),
    "traversal name": _journal(items=[_item(name="../outside.toml")]),
    "separator name": _journal(items=[_item(name="sub/dodaios.toml")]),
    "backslash name": _journal(items=[_item(name="sub\\dodaios.toml")]),
    "drive name": _journal(items=[_item(name="C:evil.toml")]),
    "traversal prior": _journal(items=[_item(prior="../../x.before-restore")]),
    "absolute prior": _journal(items=[_item(prior="/abs/x")]),
    "prior not generated": _journal(items=[_item(prior="other-file")]),
    "staged not generated": _journal(items=[_item(staged="dodaios.toml")]),
    "staged other token": _journal(items=[_item(staged=".restore-zzz-0.tmp")]),
    "bad item state": _journal(items=[_item(state="exploded")]),
    "bad journal state": _journal(state="exploded"),
    "wrong schema": _journal(schema=99),
    "wrong product": _journal(product="consumer"),
    "extra key": _journal(extra=1),
    "extra item key": _journal(items=[_item(cmd="rm")]),
    "duplicate items": _journal(items=[_item(), _item(staged=".restore-t-1.tmp")]),
    "sidecar of a non-database": _journal(
        items=[_item(sidecars=[
            {"name": "dodaios.toml-wal", "prior": "dodaios.toml-wal.before-restore"}
        ])]
    ),
    "sidecar traversal": _journal(
        items=[_item(name="m.sqlite", prior=None,
                     sidecars=[{"name": "../m.sqlite-wal",
                                "prior": "m.sqlite-wal.before-restore"}])]
    ),
    "sidecar prior of another name": _journal(
        items=[_item(name="m.sqlite", prior=None,
                     sidecars=[{"name": "m.sqlite-wal", "prior": "dodaios.toml.before-restore"}])]
    ),
}


@pytest.mark.parametrize("label", sorted(BAD_JOURNALS))
def test_invalid_journal_refuses_whole_recovery_without_any_mutation(work, label) -> None:
    paths, env = work
    _prepare(paths, env)
    # a perfectly valid sibling journal must NOT be acted on either (whole recovery refused)
    (paths.data_dir / "dodaios.toml.before-restore").write_text("prior\n", encoding="utf-8")
    _write(paths, _journal(items=[_item(staged=".restore-a-valid-0.tmp")]), tag="a-valid")
    _write(paths, BAD_JOURNALS[label], tag="t")
    before = _snapshot_dir(paths)
    with pytest.raises(pb.JournalInvalid) as info:
        pb.recover_product_state("work")
    assert info.value.journal.startswith(pb.RESTORE_JOURNAL_PREFIX)
    assert info.value.field  # reports which field is invalid
    assert _snapshot_dir(paths) == before


def test_journal_that_is_not_json_or_not_an_object_is_refused(work) -> None:
    paths, env = work
    _prepare(paths, env)
    (paths.data_dir / f"{pb.RESTORE_JOURNAL_PREFIX}t.json").write_text("[1,2", encoding="utf-8")
    with pytest.raises(pb.JournalInvalid):
        pb.recover_product_state("work")
    (paths.data_dir / f"{pb.RESTORE_JOURNAL_PREFIX}t.json").write_text("[]", encoding="utf-8")
    with pytest.raises(pb.JournalInvalid):
        pb.recover_product_state("work")


def test_journal_with_a_non_generated_file_name_is_refused(work) -> None:
    paths, env = work
    _prepare(paths, env)
    (paths.data_dir / f"{pb.RESTORE_JOURNAL_PREFIX} bad name.json").write_text(
        json.dumps(_journal()), encoding="utf-8"
    )
    with pytest.raises(pb.JournalInvalid):
        pb.recover_product_state("work")


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_linked_journal_file_is_refused_and_not_followed(work, tmp_path) -> None:
    paths, env = work
    _prepare(paths, env)
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps(_journal()), encoding="utf-8")
    (paths.data_dir / f"{pb.RESTORE_JOURNAL_PREFIX}t.json").symlink_to(real)
    with pytest.raises(pb.JournalInvalid):
        pb.recover_product_state("work")
    assert real.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="hard links of this kind need privileges")
def test_hard_linked_journal_is_refused(work, tmp_path) -> None:
    paths, env = work
    _prepare(paths, env)
    path = _write(paths, _journal())
    os.link(path, tmp_path / "second-name.json")
    with pytest.raises(pb.JournalInvalid):
        pb.recover_product_state("work")


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_item_path_that_is_a_link_is_refused_and_victim_untouched(work, tmp_path) -> None:
    paths, env = work
    _prepare(paths, env)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    (paths.data_dir / "dodaios.toml.before-restore").symlink_to(victim)
    _write(paths, _journal())
    with pytest.raises(pb.JournalInvalid):
        pb.recover_product_state("work")
    assert victim.read_text(encoding="utf-8") == "keep"


def test_valid_interrupted_journal_still_rolls_back(work) -> None:
    paths, env = work
    _prepare(paths, env)
    prior = paths.data_dir / "dodaios.toml.before-restore"
    (paths.data_dir / "dodaios.toml").rename(prior)
    _write(paths, _journal())
    report = pb.recover_product_state("work")
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 99\n"
    assert report.final_state == "rolled_back"
    assert not list(paths.data_dir.glob(pb.RESTORE_JOURNAL_PREFIX + "*"))


def test_committed_journal_is_only_removed_never_rolled_back(work) -> None:
    paths, env = work
    _prepare(paths, env)
    # restored file in place, previous one kept: a rollback would WRONGLY bring "a = 99" back
    (paths.data_dir / "dodaios.toml").write_text("a = 1\n", encoding="utf-8")
    (paths.data_dir / "dodaios.toml.before-restore").write_text("a = 99\n", encoding="utf-8")
    item = _item(state="installed")
    _write(paths, _journal(state="committed", items=[item]))
    report = pb.recover_product_state("work")
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 1\n"
    assert report.final_state == "committed" and not report.rolled_back
    assert not list(paths.data_dir.glob(pb.RESTORE_JOURNAL_PREFIX + "*"))


def test_rolled_back_journal_is_only_cleaned(work) -> None:
    paths, env = work
    _prepare(paths, env)
    _write(paths, _journal(state="rolled_back"))
    report = pb.recover_product_state("work")
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 99\n"
    assert report.final_state == "rolled_back" and not report.rolled_back


def test_cleanup_failure_after_a_successful_swap_is_reported_as_restored(
    work, monkeypatch, capsys
) -> None:
    paths, env = work
    _prepare(paths, env)
    real_unlink = Path.unlink

    def deny(self, *a, **k):
        if self.name.startswith(pb.RESTORE_JOURNAL_PREFIX):
            raise PermissionError("simulated: journal is locked")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", deny)
    code = pb.main(["restore", "--product", "work"])
    monkeypatch.undo()
    out = capsys.readouterr()
    assert code == pb.EXIT_CLEANUP_PENDING
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 1\n"
    assert "No data file was replaced" not in out.out + out.err
    assert "restored" in out.out and "committed" in out.out
    journals = list(paths.data_dir.glob(pb.RESTORE_JOURNAL_PREFIX + "*.json"))
    assert len(journals) == 1
    assert json.loads(journals[0].read_text(encoding="utf-8"))["state"] == "committed"
    # a later recover must clean it WITHOUT rolling the finished restore back
    pb.recover_product_state("work")
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 1\n"
    assert not list(paths.data_dir.glob(pb.RESTORE_JOURNAL_PREFIX + "*"))


def test_failure_to_record_the_commit_rolls_back_and_says_so(work, monkeypatch) -> None:
    paths, env = work
    _prepare(paths, env)
    real = pb._persist_state

    def fail_commit(path, journal, state):
        if state == "committed":
            journal["state"] = state
            return "simulated: disk full"
        return real(path, journal, state)

    monkeypatch.setattr(pb, "_persist_state", fail_commit)
    with pytest.raises(pb.RestoreFailed) as info:
        pb.restore_product_state("work")
    monkeypatch.undo()
    assert info.value.report.final_state == "rolled_back"
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 99\n"


def test_cleanup_failure_after_partial_recovery_reports_real_state(
    work, monkeypatch, capsys
) -> None:
    paths, env = work
    _prepare(paths, env)
    prior = paths.data_dir / "dodaios.toml.before-restore"
    (paths.data_dir / "dodaios.toml").rename(prior)
    _write(paths, _journal())
    real_unlink = Path.unlink

    def deny(self, *a, **k):
        if self.name.startswith(pb.RESTORE_JOURNAL_PREFIX):
            raise PermissionError("simulated")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", deny)
    code = pb.main(["recover", "--product", "work"])
    monkeypatch.undo()
    out = capsys.readouterr()
    assert code == pb.EXIT_CLEANUP_PENDING
    assert (paths.data_dir / "dodaios.toml").read_text(encoding="utf-8") == "a = 99\n"
    assert "Nothing was changed" not in out.out + out.err
    assert "rolled_back" in out.out and "journal left behind" in out.out


def test_exit_codes_are_distinct_and_refusal_is_before_any_change(work, capsys) -> None:
    assert len({pb.EXIT_OK, pb.EXIT_REFUSED, pb.EXIT_CLEANUP_PENDING, pb.EXIT_FAILED}) == 4
    paths, env = work
    _prepare(paths, env)
    _write(paths, BAD_JOURNALS["traversal name"])
    assert pb.main(["recover", "--product", "work"]) == pb.EXIT_REFUSED
    assert "refused before any change" in capsys.readouterr().err
