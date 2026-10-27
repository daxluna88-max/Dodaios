"""Block 13 (written, NOT run): browser-held and live calls take the STOP ticket at creation."""
from __future__ import annotations

import asyncio

import pytest

from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.live.runtime import run_browser_call


class _Bus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


@pytest.fixture(autouse=True)
def _fresh():
    reset_stop_controller()
    yield
    reset_stop_controller()


async def test_browser_call_is_not_started_while_the_door_is_closed():
    await get_stop_controller().stop()
    bus = _Bus()
    result = await run_browser_call(bus, asyncio.Event(), timeout_s=1.0, session_id="s1")
    assert result == "client_stop"
    assert bus.events == []  # no BrowserVoiceRequested was published


async def test_stop_while_waiting_ends_the_browser_call_and_removes_the_listener():
    bus = _Bus()
    ctl = get_stop_controller()
    task = asyncio.create_task(
        run_browser_call(bus, asyncio.Event(), timeout_s=5.0, session_id="s2")
    )
    await asyncio.sleep(0.05)
    await ctl.stop()
    assert await asyncio.wait_for(task, 2.0) == "client_stop"
    assert ctl._listeners == []  # noqa: SLF001
