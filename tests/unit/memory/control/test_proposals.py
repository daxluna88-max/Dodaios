"""ADR-0061 proposals. WRITTEN, NOT RUN."""
from __future__ import annotations

import pytest

from dodaios.memory.control.proposals import (
    APPROVED,
    PROPOSED,
    REJECTED,
    ProposalError,
    ProposalStore,
    content_hash,
)


def _add(store: ProposalStore, text: str = "Prefers short answers"):
    return store.propose(target="user", operation="add", text=text, evidence="I like short answers")


class Recorder:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def __call__(self, **kw):
        if self.fail:
            raise self.fail
        self.calls.append(kw)


def test_proposal_is_not_applied_until_approved(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = _add(store)
    assert item is not None and item.state == PROPOSED
    assert item.local_only is True
    rec = Recorder()
    assert rec.calls == []
    done = store.approve(item.id, rec)
    assert done.state == APPROVED
    assert len(rec.calls) == 1 and rec.calls[0]["text"] == "Prefers short answers"
    assert rec.calls[0]["source"] == "approved by owner"


def test_edit_and_approve_writes_the_edited_text(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = _add(store)
    rec = Recorder()
    done = store.approve(item.id, rec, text="  Prefers   very short answers ")
    assert rec.calls[0]["text"] == "Prefers very short answers"
    assert done.edited is True


def test_edited_text_still_goes_through_the_guard(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = _add(store)
    rec = Recorder()
    with pytest.raises(ProposalError) as err:
        store.approve(item.id, rec, text="password is sk-" + "a" * 40)
    assert err.value.status == 422
    assert rec.calls == []
    assert store.items()[0].state == PROPOSED


def test_failed_write_leaves_the_proposal_open(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = _add(store)
    with pytest.raises(ProposalError):
        store.approve(item.id, Recorder(fail=ValueError("the user notebook is full")))
    assert store.items()[0].state == PROPOSED


def test_rejected_is_not_proposed_again_and_text_is_dropped(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = _add(store)
    rejected = store.reject(item.id)
    assert rejected.state == REJECTED and rejected.text == "" and rejected.evidence == ""
    assert _add(store, "prefers SHORT  answers") is None  # same content, different spacing/case
    assert _add(store, "Likes tea") is not None


def test_cannot_decide_twice(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = _add(store)
    store.reject(item.id)
    with pytest.raises(ProposalError):
        store.approve(item.id, Recorder())


def test_open_duplicate_is_not_queued_twice(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    assert _add(store) is not None
    assert _add(store) is None


def test_products_never_see_each_other(tmp_path):
    work = ProposalStore(tmp_path, product="work")
    consumer = ProposalStore(tmp_path, product="consumer")
    item = _add(work)
    assert consumer.items() == []
    with pytest.raises(ProposalError) as err:
        consumer.approve(item.id, Recorder())
    assert err.value.status == 404
    with pytest.raises(ProposalError):
        consumer.reject(item.id)
    # A rejection in one product does not suppress the same text in the other.
    work.reject(item.id)
    assert _add(consumer) is not None


def test_there_is_no_bulk_approve():
    assert not [n for n in dir(ProposalStore) if "all" in n and "approve" in n]
    assert not hasattr(ProposalStore, "approve_all")


def test_hash_ignores_case_and_spacing_but_not_target():
    a = content_hash("user", "add", "", "A  b")
    assert a == content_hash("user", "add", "", "a b")
    assert a != content_hash("memory", "add", "", "a b")


def test_forget_all_for_product_clears_hashes_too(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    item = _add(store)
    store.reject(item.id)
    assert store.forget_all_for_product() == 1
    assert _add(store) is not None
