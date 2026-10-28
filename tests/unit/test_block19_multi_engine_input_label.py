"""Block 19 (ADR-0050, GPT round 6, J1): the explicit multi-engine path honours the carried input
label (text / PDF / image attachment). Written, NOT run.
"""

from __future__ import annotations

import contextvars
from types import SimpleNamespace
from typing import Any

import pytest

from dodaios.brain import engine_policy, multi_engine_chat
from dodaios.brain import provenance as prov
from dodaios.brain.provenance import InputItem, Provenance

LOCAL = {"ollama"}


@pytest.fixture(autouse=True)
def locality(monkeypatch: pytest.MonkeyPatch):
    def fake(provider: str, brain: Any = None) -> engine_policy.Destination:
        local = provider in LOCAL
        return engine_policy.Destination("127.0.0.1" if local else None, local, "test")

    monkeypatch.setattr(engine_policy, "destination_of", fake)
    monkeypatch.setattr(engine_policy, "current_product", lambda: "consumer")
    monkeypatch.setattr(engine_policy, "check_provider", lambda *a, **k: None)
    monkeypatch.setattr(multi_engine_chat, "authenticated", lambda *a, **k: True)


class _Brain:
    def __init__(self, name: str, answer: str, sink: list[tuple[str, str]]) -> None:
        self.name, self.answer, self.sink = name, answer, sink

    async def complete(self, req):
        self.sink.append((self.name, req.messages[0].content))
        yield SimpleNamespace(content=self.answer, tool_call=None, usage=None)


class _Registry:
    def __init__(self, sink: list[tuple[str, str]]) -> None:
        self.sink = sink

    def available(self):
        return ["ollama", "openai", "anthropic"]

    def instantiate(self, provider, model=None):
        return _Brain(provider, f"{provider}-answer", self.sink)

    def get_class(self, provider):
        return SimpleNamespace()


def _config(engines: list[str], synthesizer: str | None = None) -> Any:
    def spec(p: str) -> Any:
        return SimpleNamespace(provider=p, model=None, needs_api=False, est_cost=0.0, timeout_s=5.0)

    return SimpleNamespace(
        enabled=True, engines=[spec(p) for p in engines], budget=1.0, max_concurrent=3,
        allow_unknown_cost=True, synthesizer=spec(synthesizer) if synthesizer else None,
    )


def _declare(kind: str | None, label: Provenance | None) -> None:
    items = (InputItem(kind, f"f.{kind}", label or Provenance.UNAVAILABLE),) if kind else ()
    prov.declare_input(label, items)


def _run(coro_fn):
    return contextvars.copy_context().run(coro_fn)


@pytest.mark.parametrize("kind", ["text", "pdf", "image"])
@pytest.mark.parametrize("label", [Provenance.UNAVAILABLE, Provenance.RESTRICTED])
def test_label_maps_into_context_item(kind: str, label: Provenance) -> None:
    def go():
        _declare(kind, label)
        (item,) = multi_engine_chat.context_items("q + attachment text")
        assert item.local_only is True
        assert f"attachment_{kind}" in item.derived_from
        assert item.restricted_for(frozenset({"task"}))

    _run(go)


def test_unlabelled_input_is_restricted() -> None:
    def go():
        _declare("pdf", None)  # no label at all reads UNAVAILABLE
        (item,) = multi_engine_chat.context_items("q")
        assert item.local_only is True

    _run(go)


def test_no_attachment_stays_an_ordinary_task() -> None:
    (item,) = multi_engine_chat.context_items("q")
    assert not item.local_only and item.category == "task" and not item.derived_from


@pytest.mark.parametrize("kind", ["text", "pdf", "image"])
async def test_remote_only_engines_get_a_refusal_and_nothing_is_sent(kind: str) -> None:
    sent: list[tuple[str, str]] = []

    async def go():
        _declare(kind, Provenance.RESTRICTED)
        return await multi_engine_chat.ask(
            "secret body", _config(["openai", "anthropic"]), _Registry(sent), turn_id="t")

    out = await contextvars.copy_context().run(go)
    assert out.status == "refused" and out.text == "" and out.reason
    assert sent == []


async def test_mixed_engines_only_the_local_one_sees_it_and_answer_is_restricted() -> None:
    sent: list[tuple[str, str]] = []

    async def go():
        _declare("text", Provenance.UNAVAILABLE)
        return await multi_engine_chat.ask(
            "secret body", _config(["ollama", "openai"]), _Registry(sent), turn_id="t")

    out = await contextvars.copy_context().run(go)
    assert {n for n, _ in sent} == {"ollama"}
    assert out.status == "ok" and out.tainted
    assert out.provenance is Provenance.RESTRICTED


async def test_local_reviewers_then_remote_synthesizer_falls_back_to_local() -> None:
    sent: list[tuple[str, str]] = []

    async def go():
        _declare("pdf", Provenance.RESTRICTED)
        return await multi_engine_chat.ask(
            "secret body", _config(["ollama"], synthesizer="openai"), _Registry(sent), turn_id="t")

    out = await contextvars.copy_context().run(go)
    assert all(name == "ollama" for name, _ in sent)  # the remote synthesizer saw nothing
    assert out.status == "ok" and out.tainted


async def test_unrestricted_input_still_uses_remote_engines() -> None:
    sent: list[tuple[str, str]] = []
    out = await multi_engine_chat.ask(
        "plain", _config(["openai", "anthropic"]), _Registry(sent), turn_id="t")
    assert out.status == "ok"
    assert {n for n, _ in sent} >= {"openai", "anthropic"}


async def test_orchestrator_refuses_remote_branch_even_with_clean_items() -> None:
    """Defense in depth: the gate fires at the direct brain call, whatever the items say."""
    from dodaios.brain.consumer_orchestrator import (
        Branch,
        Budget,
        ContextItem,
        Orchestrator,
        PrivacyGate,
    )

    sent: list[tuple[str, str]] = []

    async def go():
        _declare("image", Provenance.RESTRICTED)
        orch = Orchestrator(PrivacyGate(), Budget(1.0), allow_unknown_cost=True)
        b = Branch("b1", "openai", _Brain("openai", "x", sent), "api", est_cost=0.0)
        res = await orch.run("t", [ContextItem("clean-looking", "task")], [b])
        synth = await orch.synthesize(
            "t", [ContextItem("clean-looking", "task")],
            res, b)  # results of this run; remote synthesizer refused
        return res, synth

    res, synth = await contextvars.copy_context().run(go)
    assert res[0].status == "refused" and sent == []
    assert synth.status in {"refused", "no_input"}
