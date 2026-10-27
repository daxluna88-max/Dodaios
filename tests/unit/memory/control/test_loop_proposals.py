"""ADR-0061: the learning loop holds reviewer changes as proposals. WRITTEN, NOT RUN."""
from __future__ import annotations

from dodaios.memory.control.proposals import ProposalStore
from dodaios.memory.learning.loop import JarvisLearningLoop
from dodaios.memory.learning.review import Proposal


class FakeNotebook:
    def __init__(self) -> None:
        self.applied: list[dict] = []

    def apply(self, **kw):
        self.applied.append(kw)
        return object()


async def _reviewer(prompt):  # pragma: no cover - not called here
    return []


def _proposal() -> Proposal:
    return Proposal(
        target="user", operation="add", text="Lives in Lisbon", entry_id="",
        evidence="I live in Lisbon now", importance=7,
    )


def test_gated_loop_writes_nothing_to_the_notebook(tmp_path):
    book = FakeNotebook()
    store = ProposalStore(tmp_path, product="consumer")
    loop = JarvisLearningLoop(book, _reviewer, proposals=store)
    assert loop._apply_proposal(_proposal(), "call ended", {}) == 1
    assert book.applied == []
    held = store.items()
    assert len(held) == 1 and held[0].state == "proposed"
    assert held[0].source == "review: call ended"
    assert held[0].local_only is True
    assert held[0].provenance in ("restricted", "unlabelled")


def test_same_proposal_after_rejection_is_not_stored_again(tmp_path):
    store = ProposalStore(tmp_path, product="consumer")
    loop = JarvisLearningLoop(FakeNotebook(), _reviewer, proposals=store)
    loop._apply_proposal(_proposal(), "call ended", {})
    store.reject(store.items()[0].id)
    assert loop._apply_proposal(_proposal(), "call ended", {}) == 0
    assert store.items() == []


def test_without_a_store_the_loop_still_writes_directly():
    book = FakeNotebook()
    loop = JarvisLearningLoop(book, _reviewer)
    assert loop._apply_proposal(_proposal(), "call ended", {}) == 1
    assert len(book.applied) == 1
