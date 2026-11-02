"""P2 of the 2026-10-26 work order: ONE final answer, through the same privacy gate."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from dodaios.brain import consumer_orchestrator as co
from dodaios.core.protocols import BrainDelta

MARKER = "PRIVATE-MARKER"


class Engine:
    def __init__(self, text: str = "", *, root: str | None = None, fail: bool = False) -> None:
        if root:
            self._server_root = root
        self.text, self.fail = text, fail
        self.prompts: list[str] = []
        self.tools_seen: list[tuple] = []

    async def complete(self, req) -> AsyncIterator[BrainDelta]:
        self.prompts.append(req.messages[0].content)
        self.tools_seen.append(tuple(req.tools))
        if self.fail:
            raise ConnectionError("boom")
        yield BrainDelta(tool_call={"name": "RunCommand", "input": {}})  # must be ignored
        yield BrainDelta(content=self.text)


@pytest.fixture(autouse=True)
def _loopback_config(monkeypatch):
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    monkeypatch.setattr(
        "dodaios.core.config.resolve_provider_endpoint",
        lambda provider, vendor_default_base_url=None, config=None: SimpleNamespace(
            base_url="http://127.0.0.1:8080", via_proxy=False, credential=None
        ),
    )


def _orch(limit: float = 10.0) -> co.Orchestrator:
    return co.Orchestrator(co.PrivacyGate(allowed_categories=frozenset({"task"})), co.Budget(limit))


def _items(private: bool = False):
    items = [co.ContextItem("What is 2+2?", "task")]
    if private:
        items.append(co.ContextItem(MARKER, "file", local_only=True))
    return items


def _local(bid: str, text: str, **kw) -> co.Branch:
    return co.Branch(bid, "local-openai", Engine(text, root="http://127.0.0.1:8080", **kw),
                     "local", est_cost=0.0)


def _remote(bid: str, text: str, **kw) -> co.Branch:
    return co.Branch(bid, "openai", Engine(text, **kw), "api", est_cost=0.01)


def _flow(branches, synth, *, items=None, orch=None, between=None):
    async def go():
        o = orch or _orch()
        results = await o.run("t", items or _items(), branches)
        if between:
            between(o)
        return o, results, await o.synthesize("t", items or _items(), results, synth)

    return asyncio.run(go())


def test_concurrent_engines_become_one_answer_with_both_answers_offered():
    a, b = _remote("a", "four"), _remote("b", "4")
    synth = _remote("s", "The answer is 4.")
    _, _, final = _flow([a, b], synth)
    assert final.status == "ok" and final.text == "The answer is 4."
    assert set(final.sources) == {"a", "b"}
    prompt = synth.brain.prompts[0]
    assert "four" in prompt and "[openai] 4" in prompt and "disagree" in prompt
    assert synth.brain.tools_seen == [()], "the synthesizer has no tools"


def test_partial_failure_synthesizes_from_the_engines_that_answered():
    ok, bad = _remote("a", "four"), _remote("b", "", fail=True)
    synth = _remote("s", "Four.")
    _, results, final = _flow([ok, bad], synth)
    assert {r.branch_id: r.status for r in results} == {"a": "ok", "b": "failed"}
    assert final.status == "ok" and final.sources == ("a",)


def test_no_answer_means_no_synthesis_call():
    synth = _remote("s", "x")
    _, _, final = _flow([_remote("a", "", fail=True)], synth)
    assert final.status == "no_input" and synth.brain.prompts == []


def test_synthesis_failure_is_reported_and_branch_results_are_kept():
    synth = _remote("s", "", fail=True)
    _, results, final = _flow([_remote("a", "four")], synth)
    assert final.status == "failed" and final.text == ""
    assert results[0].status == "ok" and results[0].text == "four"


def test_superseded_turn_never_reaches_the_synthesizer():
    synth = _remote("s", "late")
    _, _, final = _flow([_remote("a", "four")], synth, between=lambda o: o.cancel_turn("t"))
    assert final.status == "stale" and synth.brain.prompts == []


def test_local_answer_on_private_context_is_not_forwarded_to_a_remote_synthesizer():
    local = _local("loc", f"summary of {MARKER}")
    synth = _remote("s", "x")
    _, results, final = _flow([local], synth, items=_items(private=True))
    assert results[0].tainted and MARKER in local.brain.prompts[0]
    assert final.status == "no_input" and final.withheld == 1
    assert synth.brain.prompts == [], "the remote synthesizer was not called at all"


def test_mixed_answers_forward_only_the_ones_a_remote_synthesizer_may_see():
    local = _local("loc", f"derived from {MARKER}")
    remote = _remote("rem", "public answer")
    synth = _remote("s", "Final.")
    _, _, final = _flow([local, remote], synth, items=_items(private=True))
    assert final.status == "ok" and final.sources == ("rem",) and final.withheld == 1
    prompt = synth.brain.prompts[0]
    assert MARKER not in prompt and "derived from" not in prompt and "public answer" in prompt
    assert final.egress is not None and final.egress.destination == "openai"


def test_loopback_synthesizer_may_receive_the_restricted_answer():
    local = _local("loc", f"derived from {MARKER}")
    synth = _local("s", "Final.")
    _, _, final = _flow([local], synth, items=_items(private=True))
    assert final.status == "ok" and final.withheld == 0
    assert "derived from" in synth.brain.prompts[0]


def test_remote_answers_are_redacted_before_synthesis():
    a = _remote("a", "mail me at jane@example.com")
    synth = _remote("s", "Final.")
    _flow([a], synth)
    assert "jane@example.com" not in synth.brain.prompts[0]


def test_synthesis_spends_from_the_same_budget():
    orch = _orch(limit=0.015)  # the branch (0.01) fits, the synthesizer (0.01) does not
    synth = _remote("s", "x")
    _, _, final = _flow([_remote("a", "four")], synth, orch=orch)
    assert final.status == "refused" and synth.brain.prompts == []


def test_work_product_refuses_a_remote_synthesizer(monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    loc = _local("a", "four")
    synth = _remote("s", "x")
    _, _, final = _flow([loc], synth)
    assert final.status == "refused" and synth.brain.prompts == []
