"""A provider-side 429 relayed by OpenRouter is retried before the first words."""
import pytest

from dodaios.core.protocols import BrainDelta
from dodaios.plugins.brain import openrouter
from dodaios.plugins.brain.openrouter import OpenRouterBrain, _is_upstream_rate_limit


class _Err(Exception):
    def __init__(self, message="Provider returned error", body=None, status_code=None):
        super().__init__(message)
        self.body = body
        self.status_code = status_code


RATE = _Err(body={"code": 429, "message": "Provider returned error",
                  "metadata": {"error_type": "rate_limit_exceeded"}})


def _brain(script):
    brain = OpenRouterBrain(model="qwen/qwen3.8-flash")
    calls = {"n": 0}

    async def once(req):
        step = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        if isinstance(step, Exception):
            raise step
        for text in step:
            yield BrainDelta(content=text)

    brain._complete_once = once  # type: ignore[method-assign]
    return brain, calls


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(openrouter.asyncio, "sleep", instant)


async def _drain(brain):
    return [d.content async for d in brain.complete(object())]


def test_detects_the_relayed_rate_limit_shapes():
    assert _is_upstream_rate_limit(RATE)
    assert _is_upstream_rate_limit(_Err(status_code=429))
    assert _is_upstream_rate_limit(_Err("error: rate_limit_exceeded"))
    assert not _is_upstream_rate_limit(_Err(body={"code": 402, "message": "no credits"}))


@pytest.mark.asyncio
async def test_a_rate_limit_is_retried_then_succeeds():
    brain, calls = _brain([RATE, RATE, ["Ciao"]])
    assert await _drain(brain) == ["Ciao"]
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_it_gives_up_after_the_last_wait():
    brain, calls = _brain([RATE])
    with pytest.raises(_Err):
        await _drain(brain)
    assert calls["n"] == 4  # the first call plus three retries


@pytest.mark.asyncio
async def test_another_error_is_not_retried():
    brain, calls = _brain([_Err(body={"code": 402, "message": "no credits"})])
    with pytest.raises(_Err):
        await _drain(brain)
    assert calls["n"] == 1
