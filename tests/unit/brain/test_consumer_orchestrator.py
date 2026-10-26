"""Consumer multi-engine orchestration (ADR-0050): fake brains only; no network or keys."""

from __future__ import annotations

import asyncio

import pytest

from dodaios.brain.consumer_orchestrator import (
    Branch,
    Budget,
    ContextItem,
    Orchestrator,
    PrivacyGate,
    default_redactor,
)
from dodaios.core.protocols import BrainDelta


class Fake:
    def __init__(self, text="ok", delay=0.0, fail=False, tool=False, usage=None):
        self.text, self.delay, self.fail, self.tool, self.usage = text, delay, fail, tool, usage
        self.requests = []
        self.cancelled = False

    async def complete(self, req):
        self.requests.append(req)
        try:
            if self.fail:
                raise RuntimeError("boom")
            await asyncio.sleep(self.delay)
            if self.tool:
                yield BrainDelta(tool_call={"name": "RunCommand"})
            yield BrainDelta(content=self.text, usage=self.usage)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def sent(self) -> str:
        return " ".join(m.content for r in self.requests for m in r.messages)


ITEMS = [
    ContextItem("Plan a trip to Rome", "task"),
    ContextItem("my IBAN note: private", "note", local_only=True),
    ContextItem("diary entry about health", "history"),
    ContextItem("mail me at dax@example.com about sk-ABCDEFGHIJKLMNOP", "task"),
]


def orch(budget=10.0, **kw):
    return Orchestrator(PrivacyGate(), Budget(budget), **kw)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)


def test_three_connection_kinds_run_concurrently_and_only_authorized_context_leaves():
    sub, api, loc = Fake("A"), Fake("B"), Fake("C")
    o = orch()
    res = run(
        o.run(
            "t1",
            ITEMS,
            [
                Branch("a", "codex", sub, "subscription", est_cost=0.0),
                Branch("b", "claude-api", api, "api", est_cost=1.0),
                Branch("c", "ollama", loc, "local"),
            ],
        )
    )
    assert [r.status for r in res] == ["ok", "ok", "ok"] and [r.text for r in res] == [
        "A",
        "B",
        "C",
    ]
    for remote in (sub, api):
        text = remote.sent()
        assert "Rome" in text
        assert (
            "IBAN" not in text and "diary" not in text
        )  # local-only item and unauthorized category
        assert "dax@example.com" not in text and "sk-ABCDEFGHIJKLMNOP" not in text  # redacted
    assert "IBAN" in loc.sent() and "diary" in loc.sent()  # a local engine keeps the whole context
    log = o.gate.egress_log
    assert {r.destination for r in log} == {"codex", "claude-api"}  # the local branch left nothing
    assert all(r.withheld_local_only == 1 and r.categories == ("task",) for r in log)
    assert not any(
        hasattr(r, "text") or "Rome" in repr(r) for r in log
    )  # metadata only, no prompt copy


def test_branches_really_overlap_in_time():
    import time

    o = orch()
    t0 = time.perf_counter()
    run(
        o.run(
            "t",
            ITEMS,
            [Branch(str(i), "claude-api", Fake(delay=0.2), "api", est_cost=0.1) for i in range(3)],
        )
    )
    assert time.perf_counter() - t0 < 0.5  # three 0.2 s branches in parallel, not 0.6 s in series


def test_concurrency_is_bounded():
    live = {"now": 0, "max": 0}

    class Counting(Fake):
        async def complete(self, req):
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
            await asyncio.sleep(0.05)
            live["now"] -= 1
            yield BrainDelta(content="x")

    o = orch(max_concurrent=2)
    run(
        o.run(
            "t",
            ITEMS,
            [Branch(str(i), "claude-api", Counting(), "api", est_cost=0.1) for i in range(5)],
        )
    )
    assert live["max"] == 2


def test_aggregate_budget_is_one_ceiling_not_one_per_branch():
    o = orch(budget=1.0)
    brs = [Branch(str(i), "claude-api", Fake(), "api", est_cost=0.4) for i in range(3)]
    res = run(o.run("t", ITEMS, brs))
    assert [r.status for r in res] == ["ok", "ok", "refused"] and "budget" in res[2].reason
    assert o.budget.reserved == pytest.approx(0.8)


def test_unknown_price_is_refused_unless_allowed_and_is_never_counted_as_zero():
    brs = [Branch("a", "claude-api", Fake(), "api", est_cost=None)]
    res = run(orch().run("t", ITEMS, brs))
    assert res[0].status == "refused"
    o = orch(allow_unknown_cost=True)
    assert run(o.run("t", ITEMS, brs))[0].status == "ok"
    assert o.budget.reserved == 0.0
    with pytest.raises(ValueError):
        Budget(float("nan"))
    assert Budget(1).reserve(float("nan")) is False and Budget(1).reserve(-1) is False


def test_subscription_is_not_api_access_and_nothing_falls_back_silently():
    api_fake = Fake()
    brs = [
        Branch("a", "codex", Fake(), "subscription", needs_api=True),
        Branch("b", "claude-api", Fake(), "api", authenticated=False, est_cost=0.1),
    ]
    res = run(orch().run("t", ITEMS, brs))
    assert [r.status for r in res] == ["refused", "refused"]
    assert "not API access" in res[0].reason and "not authenticated" in res[1].reason
    assert api_fake.requests == []


def test_partial_failure_and_timeout_are_reported_not_hidden():
    brs = [
        Branch("ok", "claude-api", Fake("fine"), "api", est_cost=0.1),
        Branch("bad", "openai", Fake(fail=True), "api", est_cost=0.1),
        Branch("slow", "gemini", Fake(delay=2), "api", est_cost=0.1, timeout_s=0.1),
    ]
    res = {r.branch_id: r for r in run(orch().run("t", ITEMS, brs))}
    assert (
        res["ok"].status == "ok"
        and res["bad"].status == "failed"
        and res["bad"].reason == "RuntimeError"
    )
    assert res["slow"].status == "timeout"


def test_first_success_cancels_the_losers():
    slow = Fake(delay=5)
    brs = [
        Branch("fast", "claude-api", Fake("quick"), "api", est_cost=0.1),
        Branch("slow", "openai", slow, "api", est_cost=0.1),
    ]
    res = {r.branch_id: r for r in run(orch().run("t", ITEMS, brs, first_success=True))}
    assert res["fast"].status == "ok" and res["slow"].status == "cancelled"
    assert slow.cancelled is True


def test_a_branch_cannot_trigger_tools_and_gets_none():
    br = Fake("review", tool=True)
    res = run(orch().run("t", ITEMS, [Branch("r", "claude-api", br, "api", est_cost=0.1)]))
    assert res[0].status == "ok" and res[0].text == "review"
    assert all(r.tools == () for r in br.requests)


def test_results_of_a_cancelled_turn_are_dropped_as_stale():
    o = orch()

    async def go():
        task = asyncio.ensure_future(
            o.run(
                "t",
                ITEMS,
                [Branch("a", "claude-api", Fake("late", delay=0.2), "api", est_cost=0.1)],
            )
        )
        await asyncio.sleep(0.05)
        o.cancel_turn("t")
        return await task

    res = run(go())
    assert res[0].status == "stale" and res[0].text == ""


def test_work_product_refuses_every_remote_branch_and_opens_nothing(monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    monkeypatch.setattr("dodaios.brain.engine_policy.endpoint_problem", lambda p: None)
    remote = Fake()
    res = run(
        orch().run(
            "t",
            ITEMS,
            [
                Branch("a", "claude-api", remote, "api", est_cost=0.1),
                Branch("b", "codex", Fake(), "subscription", est_cost=0.0),
                Branch("c", "ollama", Fake("local"), "local"),
            ],
        )
    )
    assert [r.status for r in res] == ["refused", "refused", "ok"]
    assert remote.requests == []


def test_redactor_masks_common_identifiers():
    out = default_redactor("a@b.com sk-ABCDEFGHIJKLMNOP 1234 5678 9012 3456")
    assert "a@b.com" not in out and "sk-ABC" not in out and "1234 5678" not in out


def test_invalid_orchestrator_settings_are_rejected():
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            orch(max_concurrent=bad)
