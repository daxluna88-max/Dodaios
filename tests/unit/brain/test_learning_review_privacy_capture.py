"""Learning loop (offline): what the reviewer sends to a seat, with the privacy switch off and on.

A fake seat brain records the exact ``BrainRequest``. The prompt carries a sentinel standing for the
person's words and the notebooks. With ``personal_context_to_cloud = false`` no seat is built and
nothing is sent; with it on, the sentinel reaches the seat (the person's current choice).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from dodaios.core.config import JarvisConfig
from dodaios.core.protocols import BrainDelta, BrainRequest
from dodaios.memory.learning import review as review_module

_MARK = "SENTINEL-LEARNING-Pangolino"


class _RecordingBrain:
    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        yield BrainDelta(content='{"changes": []}')
        yield BrainDelta(finish_reason="stop")


async def _run(monkeypatch: pytest.MonkeyPatch, *, allowed: bool) -> tuple[_RecordingBrain, int, object]:
    import dodaios.society.seat_brain as seat_module

    config = JarvisConfig()
    config.brain.personal_context_to_cloud = allowed
    brain = _RecordingBrain()
    built: list[object] = []
    seat = SimpleNamespace(provider="fake", model="m", runner="brain", keyed=True, agent_key=False)

    async def fake_seat_brain(_config, chosen, *, caller=""):
        built.append(chosen)
        return brain

    monkeypatch.setattr(seat_module, "seat_brain", fake_seat_brain)
    monkeypatch.setattr(
        review_module,
        "seat_choices",
        lambda _c: [review_module.SeatChoice(seat, review_module.BILLING_METERED, "test")],
    )
    reviewer = review_module.ModelReviewer(config)
    monkeypatch.setattr(reviewer, "_live_config", lambda: config)
    result = await reviewer(f'{{"said": [{{"user": "{_MARK}"}}]}}')
    return brain, len(built), result


@pytest.mark.asyncio
async def test_switch_off_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    brain, built, result = await _run(monkeypatch, allowed=False)
    assert result is None
    assert built == 0
    assert brain.requests == []


@pytest.mark.asyncio
async def test_switch_on_sends_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    brain, built, result = await _run(monkeypatch, allowed=True)
    assert built == 1
    assert result == []
    assert _MARK in brain.requests[-1].messages[0].content
