"""``JARVIS_OPENROUTER_DATA_COLLECTION=deny`` asks OpenRouter to route only to providers that keep no data."""
import pytest

from dodaios.core.protocols import BrainDelta, BrainRequest
from dodaios.plugins.brain import openrouter
from dodaios.plugins.brain.openrouter import OpenRouterBrain

_DENY = {"provider": {"data_collection": "deny"}}


def _install(monkeypatch, behaviour):
    """Replace ``stream_complete``; ``behaviour(n)`` raises or returns the texts of call number n."""
    seen: list[dict | None] = []

    async def fake(client, model, req, extra_body=None):
        seen.append(extra_body)
        step = behaviour(len(seen))
        if isinstance(step, Exception):
            raise step
        for text in step:
            yield BrainDelta(content=text)

    monkeypatch.setattr(openrouter, "stream_complete", fake)
    brain = OpenRouterBrain(model="deepseek/deepseek-v4.1-flash")
    brain._client = object()
    return brain, seen


async def _run(brain, effort=None):
    req = BrainRequest(messages=(), reasoning_effort=effort)
    return [d.content async for d in brain._complete_once(req)]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("JARVIS_OPENROUTER_DATA_COLLECTION", raising=False)
    monkeypatch.delenv("JARVIS_OPENROUTER_FALLBACK_MODELS", raising=False)


@pytest.mark.asyncio
async def test_off_by_default_sends_no_routing_rule(monkeypatch):
    brain, seen = _install(monkeypatch, lambda n: ["ok"])
    assert await _run(brain) == ["ok"]
    assert seen == [None]


@pytest.mark.asyncio
async def test_deny_sends_the_rule(monkeypatch):
    monkeypatch.setenv("JARVIS_OPENROUTER_DATA_COLLECTION", "deny")
    brain, seen = _install(monkeypatch, lambda n: ["ok"])
    await _run(brain)
    assert seen == [_DENY]


@pytest.mark.asyncio
async def test_deny_is_kept_with_the_reasoning_hint_and_the_fallback_models(monkeypatch):
    monkeypatch.setenv("JARVIS_OPENROUTER_DATA_COLLECTION", "deny")
    monkeypatch.setenv("JARVIS_OPENROUTER_FALLBACK_MODELS", "qwen/qwen3.8-flash")
    brain, seen = _install(monkeypatch, lambda n: ["ok"])
    await _run(brain, effort="none")
    body = seen[0]
    assert body["provider"] == {"data_collection": "deny"}
    assert body["reasoning"] == {"enabled": False}
    assert body["models"] == ["deepseek/deepseek-v4.1-flash", "qwen/qwen3.8-flash"]


@pytest.mark.asyncio
async def test_the_rule_survives_the_reasoning_retry(monkeypatch):
    monkeypatch.setenv("JARVIS_OPENROUTER_DATA_COLLECTION", "deny")
    brain, seen = _install(
        monkeypatch, lambda n: Exception("reasoning is required") if n == 1 else ["ok"]
    )
    assert await _run(brain, effort="none") == ["ok"]
    assert len(seen) == 2
    assert seen[1] == _DENY  # the retry drops the reasoning hint but never the privacy rule


@pytest.mark.asyncio
async def test_other_values_do_nothing(monkeypatch):
    monkeypatch.setenv("JARVIS_OPENROUTER_DATA_COLLECTION", "allow")
    brain, seen = _install(monkeypatch, lambda n: ["ok"])
    await _run(brain)
    assert seen == [None]
