"""``_last_turn_all_failed`` belongs to one turn, not to the manager (ADR-0058, block 3)."""
from __future__ import annotations

import asyncio

from dodaios.brain.manager import BrainManager
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig


def _brain() -> BrainManager:
    return BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})


async def test_a_concurrent_turn_does_not_see_or_reset_another_turns_failure():
    brain = _brain()
    a_failed = asyncio.Event()
    b_started = asyncio.Event()
    seen: dict[str, bool] = {}

    async def fake_generate(user_text: str, *args, **kwargs) -> str:
        brain._last_turn_all_failed = False  # what every real turn does first
        if user_text == "a":
            await b_started.wait()
            brain._last_turn_all_failed = True
            a_failed.set()
            await asyncio.sleep(0)
            seen["a"] = brain._last_turn_all_failed
        else:
            b_started.set()
            await a_failed.wait()
            await asyncio.sleep(0)
            seen["b"] = brain._last_turn_all_failed  # A failed; B must not inherit it
        return ""

    brain._generate = fake_generate  # type: ignore[method-assign]
    await asyncio.gather(brain.generate("a"), brain.generate("b"))
    assert seen == {"a": True, "b": False}


async def test_the_flag_of_the_last_finished_turn_stays_readable_after_it_ends():
    brain = _brain()

    async def failing(user_text: str, *args, **kwargs) -> str:
        brain._last_turn_all_failed = True
        return "all providers are down"

    brain._generate = failing  # type: ignore[method-assign]
    await brain.generate("x")
    assert brain._last_turn_all_failed is True  # the voice pipeline reads it after generate()

    async def fine(user_text: str, *args, **kwargs) -> str:
        brain._last_turn_all_failed = False
        return "ok"

    brain._generate = fine  # type: ignore[method-assign]
    await brain.generate("y")
    assert brain._last_turn_all_failed is False


async def test_setting_the_flag_outside_a_turn_still_works():
    brain = _brain()
    brain._last_turn_all_failed = True
    assert brain._last_turn_all_failed is True
