"""Synthesis is bound to the run that produced its INPUT results, not to the latest run."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from dodaios.brain import consumer_orchestrator as co
from dodaios.core.protocols import BrainDelta


class Engine:
    def __init__(self, text: str, *, hold: asyncio.Event | None = None) -> None:
        self.text, self.hold = text, hold
        self.prompts: list[str] = []

    async def complete(self, req) -> AsyncIterator[BrainDelta]:
        self.prompts.append(req.messages[0].content)
        if self.hold is not None:
            await self.hold.wait()
        yield BrainDelta(content=self.text)


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    monkeypatch.setattr(
        "dodaios.core.config.resolve_provider_endpoint",
        lambda provider, vendor_default_base_url=None, config=None: SimpleNamespace(
            base_url=None, via_proxy=False, credential=None
        ),
    )


def _orch(limit: float = 10.0) -> co.Orchestrator:
    gate = co.PrivacyGate(allowed_categories=frozenset({"task"}))
    return co.Orchestrator(gate, co.Budget(limit))


def _branch(bid: str, text: str, **kw) -> co.Branch:
    return co.Branch(bid, "openai", Engine(text, **kw), "api", est_cost=0.01)


def _items(text: str = "q"):
    return [co.ContextItem(text, "task")]


def test_results_of_a_superseded_run_are_refused_without_a_call_or_reservation():
    async def go():
        orch, synth = _orch(), _branch("s", "final")
        old = await orch.run("same-turn", _items("old"), [_branch("a", "OLD ANSWER")])
        await orch.run("same-turn", _items("new"), [_branch("a", "NEW ANSWER")])
        reserved = orch.budget.reserved
        out = await orch.synthesize("same-turn", _items("old"), old, synth)
        return orch, synth, out, reserved

    orch, synth, out, reserved = asyncio.run(go())
    assert out.status == "stale"
    assert synth.brain.prompts == []
    assert orch.budget.reserved == reserved, "no reservation for obsolete inputs"


def test_overlapping_runs_only_the_newest_results_may_be_synthesized():
    async def go():
        orch, synth = _orch(), _branch("s", "final")
        gate = asyncio.Event()
        slow = asyncio.ensure_future(
            orch.run("t", _items(), [_branch("a", "SLOW", hold=gate)])
        )
        await asyncio.sleep(0.02)
        fast = await orch.run("t", _items(), [_branch("a", "FAST")])
        gate.set()
        slow_results = await slow
        return (
            await orch.synthesize("t", _items(), slow_results, synth),
            await orch.synthesize("t", _items(), fast, synth),
            synth,
        )

    stale, fresh, synth = asyncio.run(go())
    assert stale.status == "stale"
    assert fresh.status == "ok"
    assert len(synth.brain.prompts) == 1 and "FAST" in synth.brain.prompts[0]
    assert "SLOW" not in synth.brain.prompts[0]


def test_cancel_then_new_run_does_not_revive_the_old_results():
    async def go():
        orch, synth = _orch(), _branch("s", "final")
        old = await orch.run("t", _items(), [_branch("a", "OLD")])
        orch.cancel_turn("t")
        await orch.run("t", _items(), [_branch("a", "NEW")])
        return await orch.synthesize("t", _items(), old, synth), synth

    out, synth = asyncio.run(go())
    assert out.status == "stale" and synth.brain.prompts == []


def test_foreign_handbuilt_and_mixed_results_are_refused():
    async def go():
        orch, other, synth = _orch(), _orch(), _branch("s", "final")
        mine = await orch.run("t", _items(), [_branch("a", "MINE")])
        theirs = await other.run("t", _items(), [_branch("a", "THEIRS")])
        forged = [co.BranchResult("a", "openai", "ok", text="FORGED")]
        guessed = [co.BranchResult("a", "openai", "ok", text="GUESSED", run_id="t/1/00")]
        second = await orch.run("u", _items(), [_branch("a", "OTHER TURN")])
        return [
            await orch.synthesize("t", _items(), theirs, synth),
            await orch.synthesize("t", _items(), forged, synth),
            await orch.synthesize("t", _items(), guessed, synth),
            await orch.synthesize("t", _items(), [*mine, *theirs], synth),
            await orch.synthesize("t", _items(), second, synth),  # another turn's results
        ], synth

    outs, synth = asyncio.run(go())
    assert [o.status for o in outs] == ["stale"] * 5
    assert synth.brain.prompts == []


def test_cancelled_during_synthesis_returns_no_output():
    async def go():
        orch = _orch()
        hold = asyncio.Event()
        synth = _branch("s", "LATE", hold=hold)
        results = await orch.run("t", _items(), [_branch("a", "four")])
        task = asyncio.ensure_future(orch.synthesize("t", _items(), results, synth))
        await asyncio.sleep(0.05)  # the synthesizer is generating
        orch.cancel_turn("t")
        hold.set()
        return await task

    out = asyncio.run(go())
    assert out.status == "stale" and out.text == ""


def test_the_current_run_still_synthesizes():
    async def go():
        orch, synth = _orch(), _branch("s", "final")
        results = await orch.run("t", _items(), [_branch("a", "four")])
        return await orch.synthesize("t", _items(), results, synth)

    assert asyncio.run(go()).status == "ok"
