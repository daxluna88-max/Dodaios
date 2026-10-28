"""ADR-0061 block 17 (GPT round 4: H2, H3, H4). WRITTEN, NOT RUN.

Nobody ran these. They are the controls prepared for the owner to run (see the ADR).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from dodaios.memory.control import deletion
from dodaios.memory.control.proposals import (
    APPLYING,
    APPROVED,
    PROPOSED,
    REJECTED,
    UNKNOWN,
    ProposalError,
    ProposalStore,
)
from dodaios.society.notebook import (
    Entry,
    change,
    parse,
    remote_shareable,
    render,
    select_entries,
    visible_entries,
)

# ------------------------------------------------------------------ H2: entry-level labels


def test_unlabelled_entry_is_local_only_and_unavailable():
    (entry,) = parse('<!-- memory-entry: {"id": "a1", "importance": 5, "revision": 1} -->\nNote')
    assert entry.local_only is True and entry.provenance == "unavailable"
    assert remote_shareable(entry) is False


def test_legacy_entries_without_markers_are_local_only():
    (entry,) = parse("## 2026-01-02\nSomething")
    assert remote_shareable(entry) is False


def test_default_write_renders_byte_identical_to_the_old_format():
    body = render([Entry("a1", "Note", 5, 1, "agent")])
    assert "local_only" not in body and "provenance" not in body


def test_label_round_trips_and_only_exact_pair_is_shareable():
    kept = change([], "Shared note", local_only=False, provenance="unrestricted")
    (back,) = parse(render(kept))
    assert remote_shareable(back) is True
    restricted = change([], "Local note", local_only=False, provenance="restricted")
    (back,) = parse(render(restricted))
    assert back.local_only is True and back.provenance == "restricted"
    assert remote_shareable(back) is False


@pytest.mark.parametrize(
    "meta",
    [
        '{"id": "a", "local_only": "false", "provenance": "unrestricted"}',
        '{"id": "a", "local_only": false}',
        '{"id": "a", "local_only": false, "provenance": "bogus"}',
        '{"id": "a", "local_only": 0, "provenance": "unrestricted"}',
        '{"id": "a", "provenance": "unrestricted"}',
    ],
)
def test_malformed_or_partial_labels_fail_closed(meta):
    (entry,) = parse(f"<!-- memory-entry: {meta} -->\nText")
    assert remote_shareable(entry) is False


def test_replace_does_not_inherit_the_old_entries_label():
    shared = change([], "Shared", local_only=False, provenance="unrestricted")
    replaced = change(shared, "Now different", operation="replace", entry_id=shared[0].id)
    assert remote_shareable(replaced[0]) is False


def test_remote_selection_drops_unlabelled_entries():
    rows = [Entry("a", "one"), change([], "two", local_only=False, provenance="unrestricted")[0]]
    assert [e.text for e in visible_entries(rows, remote=True)] == ["two"]
    picked, omitted = select_entries(rows, max_chars=1000, remote=True)
    assert [e.text for e in picked] == ["two"] and omitted == 0
    assert len(select_entries(rows, max_chars=1000)[0]) == 2


class _FakeApply:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kw):
        self.calls.append(kw)


def test_approval_passes_local_only_and_a_non_shareable_provenance(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = store.propose(
        target="user", operation="add", text="Likes tea", evidence="e", provenance="restricted"
    )
    rec = _FakeApply()
    store.approve(item.id, rec)
    assert rec.calls[0]["local_only"] is True
    assert rec.calls[0]["provenance"] == "restricted"
    other = store.propose(target="user", operation="add", text="Likes cake", evidence="e")
    store.approve(other.id, rec)
    assert rec.calls[1]["local_only"] is True and rec.calls[1]["provenance"] == "unavailable"


def _notebook(tmp_path):
    from dodaios.memory.learning.notebook import JarvisNotebook

    return JarvisNotebook(tmp_path / "vault", name="Jarvis")


def test_notebook_apply_defaults_to_local_and_remote_snapshot_hides_it(tmp_path):
    nb = _notebook(tmp_path)
    nb.apply(target="user", operation="add", text="Lives in Lisbon", origin="review")
    assert "Lisbon" in nb.render(books=nb.entries())
    assert nb.render(books=nb.entries(), remote=True) == ""
    nb.apply(
        target="user", operation="add", text="Likes jazz", origin="review",
        local_only=False, provenance="unrestricted",
    )
    remote = nb.render(books=nb.entries(), remote=True)
    assert "jazz" in remote and "Lisbon" not in remote
    assert "less important" not in remote  # no count of hidden notes leaks


def test_snapshot_block_defaults_to_the_remote_safe_view(tmp_path):
    from dodaios.memory.learning import notebook as nbmod

    nb = _notebook(tmp_path)
    nb.apply(target="memory", operation="add", text="Secret project Zed", origin="review")
    nbmod.set_active(nb)
    try:
        nb.invalidate()
        assert nbmod.snapshot_block() == ""
        assert "Zed" in nbmod.snapshot_block(remote=False)
        assert nbmod.memory_block().startswith(nbmod.REMEMBER_DIRECTIVE)
        assert "Zed" not in nbmod.memory_block()
        assert nbmod.dated_notes() == []
    finally:
        nbmod.set_active(None)


def test_local_snapshot_with_a_local_only_entry_marks_the_turn_restricted(tmp_path):
    from dodaios.brain import provenance
    from dodaios.memory.learning import notebook as nbmod

    nb = _notebook(tmp_path)
    nb.apply(target="memory", operation="add", text="Local fact", origin="review")
    nbmod.set_active(nb)
    try:
        provenance.begin_turn("t")
        provenance.attach_unrestricted()
        nbmod.snapshot_block(remote=False)
        assert provenance.turn_provenance() is provenance.Provenance.RESTRICTED
    finally:
        nbmod.set_active(None)


def test_society_lead_head_and_recall_hide_unlabelled_entries(tmp_path):
    from dodaios.society.memory import _shareable_notebook_body

    body = render([
        Entry("a", "private"),
        change([], "public", local_only=False, provenance="unrestricted")[0],
    ])
    assert _shareable_notebook_body("society/jarvis/MEMORY.md", body) == "public"
    assert _shareable_notebook_body("society/other/MEMORY.md", body) == body


# ------------------------------------------------------------------ H3: trash confinement


def _vault(tmp_path) -> Path:
    root = tmp_path / "vault"
    (root / "entities").mkdir(parents=True)
    (root / "entities" / "ana.md").write_text("# Ana\n\nLives in Lisbon.\n", encoding="utf-8")
    return root


def _trash_one(root: Path) -> str:
    return deletion.logical_delete(root, "entities/ana.md").trash_id


def _edit_manifest(root: Path, trash_id: str, **changes):
    path = root / ".trash" / trash_id / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_normal_delete_and_restore_still_work(tmp_path):
    root = _vault(tmp_path)
    trash_id = _trash_one(root)
    assert deletion.restore(root, trash_id) == "entities/ana.md"
    assert (root / "entities" / "ana.md").is_file()


@pytest.mark.parametrize(
    "bad",
    ["../outside.md", "/etc/passwd.md", "C:/x.md", "a//b.md", "a/./b.md", "a\\b.md", "", "x.txt",
     ".trash/x.md", "_archive/x.md", ".hidden/x.md", 7, None],
)
def test_manifest_path_must_be_canonical_relative(tmp_path, bad):
    root = _vault(tmp_path)
    trash_id = _trash_one(root)
    _edit_manifest(root, trash_id, path=bad)
    outside = tmp_path / "outside.md"
    outside.write_text("keep", encoding="utf-8")
    for call in (
        lambda: deletion.restore(root, trash_id),
        lambda: deletion.permanent_delete(root, trash_id=trash_id),
    ):
        with pytest.raises(deletion.DeletionError) as err:
            call()
        assert err.value.status == 404
    assert outside.read_text(encoding="utf-8") == "keep"
    assert deletion.list_trash(root) == []
    assert deletion.purge_expired(root, now=1e12) == 0
    assert (root / ".trash" / trash_id).is_dir()  # an invalid entry is never deleted on its say-so


def test_manifest_id_must_equal_its_directory(tmp_path):
    root = _vault(tmp_path)
    trash_id = _trash_one(root)
    _edit_manifest(root, trash_id, id="aaaaaaaaaaaa")
    with pytest.raises(deletion.DeletionError):
        deletion.restore(root, trash_id)
    assert deletion.list_trash(root) == []


@pytest.mark.parametrize("key", ["deleted_at", "restore_until"])
@pytest.mark.parametrize("value", ["soon", True, None, float("nan")])
def test_manifest_timestamps_are_validated(tmp_path, key, value):
    root = _vault(tmp_path)
    trash_id = _trash_one(root)
    _edit_manifest(root, trash_id, **{key: value})
    with pytest.raises(deletion.DeletionError):
        deletion.restore(root, trash_id)


def test_manifest_of_another_product_is_refused(tmp_path):
    root = _vault(tmp_path)
    trash_id = _trash_one(root)
    _edit_manifest(root, trash_id, product="not-this-product")
    with pytest.raises(deletion.DeletionError):
        deletion.restore(root, trash_id)


def test_restore_refuses_a_trash_copy_that_changed(tmp_path):
    root = _vault(tmp_path)
    trash_id = _trash_one(root)
    (root / ".trash" / trash_id / "entities" / "ana.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(deletion.DeletionError) as err:
        deletion.restore(root, trash_id)
    assert err.value.status == 409
    assert not (root / "entities" / "ana.md").exists()


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


def test_linked_trash_folder_is_rejected(tmp_path):
    root = _vault(tmp_path)
    trash_id = _trash_one(root)
    real = root / ".trash" / trash_id
    moved = tmp_path / "elsewhere"
    real.rename(moved)
    _symlink(real, moved)
    with pytest.raises(deletion.DeletionError):
        deletion.restore(root, trash_id)
    assert deletion.list_trash(root) == []


def test_linked_trash_root_is_rejected(tmp_path):
    root = _vault(tmp_path)
    trash_id = _trash_one(root)
    trash = root / ".trash"
    moved = tmp_path / "trash-elsewhere"
    trash.rename(moved)
    _symlink(trash, moved)
    with pytest.raises(deletion.DeletionError):
        deletion.restore(root, trash_id)
    assert deletion.purge_expired(root, now=1e12) == 0


def test_restore_destination_with_a_linked_ancestor_is_refused(tmp_path):
    root = _vault(tmp_path)
    trash_id = _trash_one(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "entities").rmdir()
    _symlink(root / "entities", outside)
    with pytest.raises(deletion.DeletionError):
        deletion.restore(root, trash_id)
    assert list(outside.iterdir()) == []


def test_permanent_delete_never_follows_a_linked_archive(tmp_path):
    root = _vault(tmp_path)
    outside = tmp_path / "outside"
    (outside / "entities").mkdir(parents=True)
    victim = outside / "entities" / "ana.md"
    victim.write_text("outside data", encoding="utf-8")
    _symlink(root / "_archive", outside)
    deletion.permanent_delete(root, rel_path="entities/ana.md")
    assert victim.read_text(encoding="utf-8") == "outside data"


def test_live_page_path_through_a_link_is_refused(tmp_path):
    root = _vault(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("outside", encoding="utf-8")
    _symlink(root / "linked", outside)
    with pytest.raises(deletion.DeletionError):
        deletion.logical_delete(root, "linked/x.md")
    assert (outside / "x.md").is_file()


# ------------------------------------------------------------------ H4a: atomic approval


def test_two_store_instances_share_one_lock(tmp_path):
    a = ProposalStore(tmp_path, product="consumer")
    b = ProposalStore(tmp_path, product="consumer")
    assert a._lock is b._lock


def test_concurrent_approvals_apply_exactly_once(tmp_path):
    seed = ProposalStore(tmp_path, product="consumer")
    item = seed.propose(target="user", operation="add", text="Likes tea", evidence="e")
    calls: list[dict] = []
    gate = threading.Barrier(6)
    outcomes: list[str] = []

    def apply(**kw):
        calls.append(kw)

    def worker():
        store = ProposalStore(tmp_path, product="consumer")  # a new store per request
        gate.wait()
        try:
            store.approve(item.id, apply)
            outcomes.append("ok")
        except ProposalError:
            outcomes.append("refused")

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and outcomes.count("ok") == 1


def test_reservation_is_on_disk_before_apply_runs(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = store.propose(target="user", operation="add", text="Likes tea", evidence="e")
    seen: dict = {}

    def apply(**kw):
        data = json.loads((tmp_path / ".memory-proposals.json").read_text(encoding="utf-8"))
        seen["state"] = data["proposals"][0]["state"]
        seen["apply_id"] = kw["apply_id"]

    store.approve(item.id, apply)
    assert seen["state"] == APPLYING and seen["apply_id"]


def test_crash_after_apply_is_finished_not_reapplied(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = store.propose(target="user", operation="add", text="Likes tea", evidence="e")

    class Crash(BaseException):
        pass

    def crashing_apply(**kw):
        raise Crash  # the process dies mid-apply: the record stays ``applying``

    with pytest.raises(Crash):
        store.approve(item.id, crashing_apply)
    assert store.items(state=APPLYING)[0].state == APPLYING

    calls: list[dict] = []
    done = ProposalStore(tmp_path, product="consumer").approve(
        item.id, lambda **kw: calls.append(kw), probe=lambda **kw: "applied"
    )
    assert done.state == APPROVED and calls == []
    assert "interrupted" in done.recovery


def test_crash_before_write_returns_to_proposed_and_asks_again(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = store.propose(target="user", operation="add", text="Likes tea", evidence="e")

    class Crash(BaseException):
        pass

    def crashing_apply(**kw):
        raise Crash

    with pytest.raises(Crash):
        store.approve(item.id, crashing_apply)
    calls: list[dict] = []
    with pytest.raises(ProposalError):
        store.approve(item.id, lambda **kw: calls.append(kw), probe=lambda **kw: "not_applied")
    assert calls == []
    assert store.items(state=PROPOSED)[0].id == item.id


def test_unprovable_recovery_is_shown_as_unknown_and_never_reapplied(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = store.propose(target="user", operation="add", text="Likes tea", evidence="e")

    class Crash(BaseException):
        pass

    def crashing_apply(**kw):
        raise Crash

    with pytest.raises(Crash):
        store.approve(item.id, crashing_apply)
    calls: list[dict] = []
    with pytest.raises(ProposalError):
        store.approve(item.id, lambda **kw: calls.append(kw), probe=lambda **kw: "unknown")
    assert store.items(state=UNKNOWN)[0].recovery
    with pytest.raises(ProposalError):
        store.approve(item.id, lambda **kw: calls.append(kw))
    assert calls == []
    assert store.reject(item.id).state == REJECTED  # the owner dismisses it


def test_refused_apply_restores_the_proposal(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = store.propose(target="user", operation="add", text="Likes tea", evidence="e")

    def refuse(**kw):
        raise ValueError("the user notebook is full")

    with pytest.raises(ProposalError):
        store.approve(item.id, refuse, text="Likes green tea")
    again = store.items(state=PROPOSED)[0]
    assert again.state == PROPOSED and again.text == "Likes tea" and again.apply_id == ""


def test_notebook_probe_recognises_a_landed_add(tmp_path):
    nb = _notebook(tmp_path)
    assert nb.probe_applied(
        apply_id="x", target="user", operation="add", entry_id="", text="Likes tea", before=""
    ) == "not_applied"
    nb.apply(target="user", operation="add", text="Likes tea", apply_id="abc123")
    assert nb.probe_applied(
        apply_id="abc123", target="user", operation="add", entry_id="", text="other", before=""
    ) == "applied"


# ---- H4b: note deletion scrubs proposals


def test_deleting_a_note_scrubs_decided_and_open_proposals(tmp_path):
    nb = _notebook(tmp_path)
    store = ProposalStore(nb.folder, product="consumer")
    first = store.propose(
        target="user", operation="add", text="Lives in Lisbon", evidence="I live in Lisbon"
    )
    store.approve(first.id, nb.apply, probe=nb.probe_applied)
    open_one = store.propose(
        target="user", operation="add", text="  lives   in LISBON ", evidence="again"
    )
    assert open_one is not None
    (entry,) = [e for e in nb.entries()["user"] if "Lisbon" in e.text]
    report = deletion.delete_notebook_entry(nb, "user", entry.id)
    stores = {s.store: s for s in report.stores}
    assert stores["proposal records"].done is True
    raw = (nb.folder / ".memory-proposals.json").read_text(encoding="utf-8")
    assert "Lisbon" not in raw
    assert not [p for p in store.items(state=PROPOSED) if "Lisbon" in p.text]


def test_a_failed_proposal_scrub_is_reported_not_claimed(tmp_path, monkeypatch):
    nb = _notebook(tmp_path)
    nb.apply(target="user", operation="add", text="Lives in Lisbon")
    (entry,) = nb.entries()["user"]

    def boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ProposalStore, "scrub_note", boom)
    report = deletion.delete_notebook_entry(nb, "user", entry.id)
    stores = {s.store: s for s in report.stores}
    assert stores["proposal records"].done is False
    assert "STILL be present" in report.residual[0]
    assert stores["notebook entry"].done is True


def test_scrub_removes_text_evidence_before_and_hash(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = store.propose(
        target="user", operation="replace", entry_id="e1", text="Lives in Lisbon",
        before="Lives in Porto", evidence="I moved to Lisbon", provenance="restricted",
    )
    store.approve(item.id, lambda **kw: None)
    count, dismissed = store.scrub_note("user", "e1", "Lives in Lisbon")
    assert count == 1 and dismissed == 0
    rec = store.items(state=APPROVED)[0]
    assert (rec.text, rec.evidence, rec.before, rec.hash) == ("", "", "", "")
    assert rec.scrubbed is True
    assert os.path.exists(tmp_path / ".memory-proposals.json")
