"""Ask several engines from the chat (ADR-0055): fake providers; no network, keys or voice."""

from __future__ import annotations

import asyncio

import pytest

from dodaios.brain import engine_policy as ep
from dodaios.brain import multi_engine_chat as mec
from dodaios.brain.provider_registry import BrainProviderRegistry
from dodaios.core import config as cfg_mod
from dodaios.core.config import MultiEngineConfig, MultiEngineEngineConfig
from dodaios.core.protocols import BrainDelta


class Fake:
    prompts: list[str] = []
    reply = "answer"
    delay = 0.0

    def __init__(self, model=None):
        pass

    def estimate_cost(self, req):
        return 0.0

    async def complete(self, req):
        Fake.prompts.append(" ".join(str(m.content) for m in req.messages))
        assert req.tools == ()
        await asyncio.sleep(Fake.delay)
        yield BrainDelta(content=Fake.reply)


class Down(Fake):
    async def complete(self, req):
        raise RuntimeError("boom")
        yield  # pragma: no cover


@pytest.fixture
def registry(monkeypatch):
    Fake.prompts.clear()
    Fake.reply, Fake.delay = "answer", 0.0
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    reg = BrainProviderRegistry()
    reg._classes = {"claude-api": Fake, "openai": Fake, "down": Down, "ollama": Fake}
    reg._loaded = True
    monkeypatch.setattr(ep, "endpoint_problem", lambda provider: None)
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda provider: "key")
    return reg


def conf(*engines, **kw):
    base = {"enabled": True, "budget": 5.0,
            "engines": [MultiEngineEngineConfig(provider=p, est_cost=0.5) for p in engines]}
    base.update(kw)
    return MultiEngineConfig(**base)


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("text, task", [
    ("Chiedi a più modelli: che tempo fa a Roma?", "che tempo fa a Roma?"),
    ("ask several engines - plan a trip", "plan a trip"),
    ("Frag mehrere Modelle: wie spät ist es", "wie spät ist es"),
])
def test_explicit_request_is_recognised(text, task):
    assert mec.extract_task(text) == task


@pytest.mark.parametrize("text", [
    "che tempo fa a Roma?", "open the browser", "ask several engines", "ask several engines:  ",
    "per favore chiedi a più modelli quanto fa 2+2",
])
def test_everything_else_is_a_normal_turn(text):
    assert mec.extract_task(text) is None


def test_one_final_answer_from_the_engines(registry):
    out = run(mec.ask("2+2?", conf("claude-api", "openai"), registry, turn_id="t"))
    assert out.status == "ok" and out.text == "answer"
    assert out.sources == ("b1", "b2")
    # two branches and ONE synthesis call: the owner reads a single text
    assert len(Fake.prompts) == 3


def test_off_and_no_engines_and_work_are_said_not_hidden(registry, monkeypatch):
    off = run(mec.ask("q", conf("claude-api", enabled=False), registry, turn_id="t"))
    assert off.status == "off"
    assert run(mec.ask("q", conf(), registry, turn_id="t")).status == "no_engines"
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    assert run(mec.ask("q", conf("ollama"), registry, turn_id="t")).status == "work"
    assert Fake.prompts == []


def test_unknown_price_without_opt_in_refuses_every_engine(registry):
    cfg = MultiEngineConfig(enabled=True, budget=5.0,
                            engines=[MultiEngineEngineConfig(provider="claude-api")])
    out = run(mec.ask("q", cfg, registry, turn_id="t"))
    assert out.status == "no_answer" and "price is unknown" in out.reason
    assert Fake.prompts == []


def test_budget_zero_default_calls_nothing_remote(registry):
    out = run(mec.ask("q", conf("claude-api", budget=0.0), registry, turn_id="t"))
    assert out.status == "no_answer" and Fake.prompts == []


def test_a_failed_engine_is_reported_and_the_others_still_answer(registry):
    out = run(mec.ask("q", conf("down", "claude-api"), registry, turn_id="t"))
    assert out.status == "ok" and out.sources == ("b2",)


def test_all_engines_failing_gives_a_reason_not_an_answer(registry):
    out = run(mec.ask("q", conf("down"), registry, turn_id="t"))
    assert out.status == "no_answer" and "down" in out.reason


def test_failing_synthesis_never_splices_the_branch_answers(registry):
    cfg = conf("claude-api", synthesizer=MultiEngineEngineConfig(provider="down", est_cost=0.5))
    out = run(mec.ask("q", cfg, registry, turn_id="t"))
    assert out.status == "synthesis_failed" and out.text == ""


def test_only_the_question_is_sent_and_identifiers_are_masked(registry):
    run(mec.ask("mail dax@example.com about the plan", conf("claude-api"), registry, turn_id="t"))
    assert Fake.prompts and all("dax@example.com" not in p for p in Fake.prompts)


def test_cancelling_the_turn_stops_the_engines(registry):
    Fake.delay = 5.0

    async def go():
        task = asyncio.create_task(
            mec.ask("q", conf("claude-api", "openai"), registry, turn_id="t"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(go())


# ---- through the chat turn: one answer, one response event (so the voice speaks once) -------


async def _turn(monkeypatch, registry, text, **multi):
    from dodaios.brain.manager import BrainManager
    from dodaios.core.bus import EventBus
    from dodaios.core.config import JarvisConfig
    from dodaios.core.events import ResponseGenerated

    monkeypatch.setattr("dodaios.brain.provider_registry.BrainProviderRegistry", lambda: registry)
    bus = EventBus()
    seen: list[str] = []

    async def capture(event: ResponseGenerated) -> None:
        seen.append(event.text)

    bus.subscribe(ResponseGenerated, capture)
    cfg = JarvisConfig()
    cfg.brain.multi_engine = conf("claude-api", "openai", **multi)
    reply = await BrainManager(config=cfg, bus=bus, tools={}).generate(text, use_history=False)
    return reply, seen


@pytest.mark.asyncio
async def test_chat_turn_speaks_exactly_one_final_answer(registry, monkeypatch):
    Fake.reply = "the one answer"
    reply, seen = await _turn(monkeypatch, registry, "Ask several engines: what is 2+2?")
    assert reply == "the one answer"
    assert seen == ["the one answer"]  # branch answers are never published


@pytest.mark.asyncio
async def test_chat_turn_off_says_so_and_calls_no_engine(registry, monkeypatch):
    reply, seen = await _turn(
        monkeypatch, registry, "Ask several engines: what is 2+2?", enabled=False)
    assert "switched off" in reply and seen == [reply] and Fake.prompts == []
