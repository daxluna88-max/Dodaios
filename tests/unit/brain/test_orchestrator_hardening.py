"""P1 of the 2026-10-26 work order: cancellation, duplicate ids, finite numbers, cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from dodaios.brain import consumer_orchestrator as co
from dodaios.core.protocols import BrainDelta


class Brain:
    def __init__(self, *, hold: asyncio.Event | None = None, text: str = "ok") -> None:
        self.calls = 0
        self.closed = 0
        self.hold = hold
        self.text = text

    async def complete(self, req) -> AsyncIterator[BrainDelta]:
        self.calls += 1
        try:
            if self.hold is not None:
                await self.hold.wait()
            yield BrainDelta(content=self.text)
            yield BrainDelta(content="more")
        finally:
            self.closed += 1


def _orch(limit: float = 10.0, **kw) -> co.Orchestrator:
    gate = co.PrivacyGate(allowed_categories=frozenset({"task"}))
    return co.Orchestrator(gate, co.Budget(limit), **kw)


def _items():
    return [co.ContextItem("hi", "task")]


def test_duplicate_branch_ids_are_rejected_before_any_call():
    a, b = Brain(), Brain()
    branches = [co.Branch("x", "ollama", a, "local", est_cost=0.0),
                co.Branch("x", "ollama", b, "local", est_cost=0.0)]
    with pytest.raises(ValueError):
        asyncio.run(_orch().run("t", _items(), branches))
    assert a.calls == b.calls == 0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 0.0, True, "5"])
def test_timeout_must_be_a_finite_positive_number(bad):
    brain = Brain()
    branch = co.Branch("x", "ollama", brain, "local", est_cost=0.0, timeout_s=bad)
    res = asyncio.run(_orch().run("t", _items(), [branch]))[0]
    assert res.status == "refused"
    assert brain.calls == 0


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1, True, "1"])
def test_budget_limit_must_be_finite(bad):
    with pytest.raises(ValueError):
        co.Budget(bad)


def test_infinite_cost_is_never_admitted():
    assert not co.Budget(5.0).reserve(float("inf"))
    assert not co.Budget(5.0).reserve(float("nan"))


def test_branch_cancelled_while_waiting_for_the_semaphore_never_calls_the_provider():
    async def go():
        gate = asyncio.Event()
        first, second = Brain(hold=gate), Brain()
        orch = _orch(max_concurrent=1)
        branches = [co.Branch("a", "ollama", first, "local", est_cost=1.0),
                    co.Branch("b", "ollama", second, "local", est_cost=1.0)]
        task = asyncio.ensure_future(orch.run("t", _items(), branches))
        await asyncio.sleep(0.05)  # "a" is generating, "b" waits for the slot
        orch.cancel_turn("t")
        gate.set()
        results = await task
        return first, second, orch, results

    first, second, orch, results = asyncio.run(go())
    assert second.calls == 0, "a cancelled turn must not start a new provider request"
    assert {r.branch_id: r.status for r in results}["b"] in {"stale", "cancelled"}
    assert orch.budget.reserved == 1.0, "the branch that never started gives its reservation back"


def test_stream_is_closed_on_timeout_and_when_stale():
    async def go():
        brain = Brain(hold=asyncio.Event())
        branch = co.Branch("a", "ollama", brain, "local", est_cost=0.0, timeout_s=0.05)
        res = await _orch().run("t", _items(), [branch])
        return brain, res[0]

    brain, res = asyncio.run(go())
    assert res.status == "timeout"
    assert brain.closed == 1


def test_stale_stream_is_closed_not_abandoned():
    async def go():
        orch = _orch()
        brain = Brain()
        orig = brain.complete

        async def complete(req):
            async for d in orig(req):
                orch.cancel_turn("t")  # the turn is superseded mid-stream
                yield d

        brain.complete = complete  # type: ignore[method-assign]
        branch = co.Branch("a", "ollama", brain, "local", est_cost=0.0)
        res = await orch.run("t", _items(), [branch])
        return brain, res[0]

    brain, res = asyncio.run(go())
    assert res.status == "stale"
    assert brain.closed == 1
