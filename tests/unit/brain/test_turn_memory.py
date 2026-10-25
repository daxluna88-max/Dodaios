"""The optional per-turn memory provider (ADR-0045)."""
from __future__ import annotations

import asyncio

import pytest

from dodaios.brain.turn_memory import BLOCK_HEADER, recall_block, set_turn_memory

pytestmark = pytest.mark.asyncio


class _P:
    def __init__(self, text="- Dax prende il treno", delay=0.0, exc=None):
        self.text, self.delay, self.exc, self.seen = text, delay, exc, []

    async def recall(self, user_text: str) -> str:
        self.seen.append(user_text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.text


@pytest.fixture(autouse=True)
def _clear():
    set_turn_memory(None)
    yield
    set_turn_memory(None)


async def test_no_provider_no_block():
    assert await recall_block("ciao") == ""


async def test_block_is_framed():
    p = _P()
    set_turn_memory(p)
    out = await recall_block("come viaggio?")
    assert out.startswith(BLOCK_HEADER) and "treno" in out and p.seen == ["come viaggio?"]


async def test_empty_utterance_or_text_gives_nothing():
    set_turn_memory(_P(text="  "))
    assert await recall_block("ciao") == ""
    assert await recall_block("   ") == ""


async def test_slow_provider_is_cut_off():
    set_turn_memory(_P(delay=1.0))
    assert await recall_block("ciao", budget_ms=50) == ""


async def test_failing_provider_never_raises():
    set_turn_memory(_P(exc=RuntimeError("boom")))
    assert await recall_block("ciao") == ""
