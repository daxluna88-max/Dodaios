"""Background work of a chat (parked results, fleet briefings) is re-verified after STOP."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dodaios.agent_chat.parked_work import ParkedWork, ParkedWorkBoard
from dodaios.brain.manager import BrainManager
from dodaios.control.stop import get_stop_controller, reset_stop_controller, work_ticket
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig


@pytest.fixture(autouse=True)
def _fresh_controller():
    reset_stop_controller()
    yield
    reset_stop_controller()


class _Service:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.notices: list[dict[str, Any]] = []

    def is_running(self, _sid: str) -> bool:
        return False

    async def send(self, _sid: str, prompt: str, **_kw: Any) -> str:
        self.sent.append(prompt)
        return "turn"

    async def post_notice(self, _sid: str, payload: dict[str, Any]) -> None:
        self.notices.append(payload)


def _parked(ticket: int | None) -> ParkedWork:
    gate = SimpleNamespace(stop=asyncio.Event(), work={}, delivered=set(), detached=False)
    return ParkedWork(
        turn_id="t1", request="fix it", original="fix it", gate=gate,  # type: ignore[arg-type]
        stop_ticket=ticket,
    )


async def test_a_background_result_of_work_parked_before_stop_is_not_brought_back_after_resume():
    service = _Service()
    board = ParkedWorkBoard(service)
    parked = _parked(get_stop_controller().ticket())
    await get_stop_controller().stop()
    get_stop_controller().resume()
    await board._resume("s1", parked, [{"name": "A", "status": "completed", "report": "done"}])
    assert service.sent == []
    assert [n["kind"] for n in service.notices] == ["background_result_lost"]


async def test_a_background_result_parked_after_resume_is_brought_back():
    service = _Service()
    board = ParkedWorkBoard(service)
    await get_stop_controller().stop()
    get_stop_controller().resume()
    parked = _parked(get_stop_controller().ticket())
    await board._resume("s1", parked, [{"name": "A", "status": "completed", "report": "done"}])
    assert len(service.sent) == 1 and service.notices == []


# ---- the fleet briefing task of the brain ---------------------------------------------------


def _brain() -> BrainManager:
    return BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})


async def test_a_fleet_briefing_created_before_stop_never_starts_after_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from dodaios.agentic_ide import fanout, fleet_actions

    called: list[str] = []

    async def _ready(*_a: Any) -> tuple[str, ...]:
        called.append("ready")
        return ("a",)

    async def _deliver(**_kw: Any) -> Any:
        called.append("deliver")
        return SimpleNamespace(delivered=())

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", _ready)
    monkeypatch.setattr(fanout, "deliver", _deliver)
    ticket = get_stop_controller().ticket()
    await get_stop_controller().stop()
    get_stop_controller().resume()
    token = work_ticket.set(ticket)  # what the creating code does around create_task
    try:
        await _brain()._brief_spawned_agentic_ide_fleet(
            SimpleNamespace(), ["a"], "Spawn one Codex terminal and prompt it to review"
        )
    finally:
        work_ticket.reset(token)
    assert called == []


async def test_a_fleet_briefing_stopped_while_the_panes_boot_delivers_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    from dodaios.agentic_ide import fanout, fleet_actions

    delivered: list[str] = []

    async def _ready(_session: Any, wanted: list[str]) -> tuple[str, ...]:
        await get_stop_controller().stop()  # STOP lands while the panes come up
        return tuple(wanted)

    async def _deliver(**_kw: Any) -> Any:
        delivered.append("deliver")
        return SimpleNamespace(delivered=())

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", _ready)
    monkeypatch.setattr(fanout, "deliver", _deliver)
    token = work_ticket.set(get_stop_controller().ticket())
    try:
        await _brain()._brief_spawned_agentic_ide_fleet(
            SimpleNamespace(), ["a"], "Spawn one Codex terminal and prompt it to review"
        )
    finally:
        work_ticket.reset(token)
    assert delivered == []
