"""Chat turns take their STOP ticket at creation, queued ones included (ADR-0058)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from dodaios.agent_chat import runner_brain, send_queue
from dodaios.agent_chat.send_queue import send_or_queue
from dodaios.agent_chat.service import AgentChatService, SessionBusy
from dodaios.agent_chat.store import AgentChatStore
from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.core.bus import EventBus
from tests.fakes.fake_brain_manager import FakeBrainManager


@pytest.fixture(autouse=True)
def _fresh_controller(monkeypatch: pytest.MonkeyPatch):
    reset_stop_controller()
    monkeypatch.setattr(send_queue, "_POLL_S", 0.01)
    yield
    reset_stop_controller()


class _Chat:
    def __init__(self) -> None:
        self.busy: set[str] = set()
        self.sent: list[str] = []
        self.notices: list[dict] = []
        self.store = SimpleNamespace(
            get_session=lambda sid: SimpleNamespace(session_id=sid, surface="society"),
            queue_notice_events=lambda sid: [],
        )

    def is_running(self, session_id: str) -> bool:
        return session_id in self.busy

    async def send(self, session_id, text, attachments=None, *, tool_choices=None,
                   provenance=None) -> str:
        if session_id in self.busy:
            raise SessionBusy(session_id)
        self.sent.append(text)
        return "turn"

    async def post_notice(self, session_id: str, payload: dict) -> None:
        self.notices.append(dict(payload))


async def _until(predicate, limit_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit_s
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


async def _queue_one(chat: _Chat, text: str) -> str:
    chat.busy.add("society:scout")
    turn_id, queue_id = await send_or_queue(chat, "society:scout", text)
    assert turn_id == "" and queue_id
    return queue_id


async def test_a_message_queued_before_stop_is_not_sent_after_resume():
    chat = _Chat()
    queue_id = await _queue_one(chat, "old message")
    await get_stop_controller().stop()
    get_stop_controller().resume()
    chat.busy.clear()  # the seat is free again
    await _until(lambda: any(n.get("kind") == "message_dequeued" for n in chat.notices))
    assert chat.sent == []
    done = next(n for n in chat.notices if n.get("kind") == "message_dequeued")
    assert done["queue_id"] == queue_id and done["status"] == "failed"


async def test_a_queued_message_is_not_sent_while_the_door_is_closed():
    chat = _Chat()
    await _queue_one(chat, "waiting")
    await get_stop_controller().stop()
    chat.busy.clear()
    await _until(lambda: any(n.get("kind") == "message_dequeued" for n in chat.notices))
    assert chat.sent == []


async def test_a_message_queued_after_resume_is_sent_normally():
    chat = _Chat()
    await get_stop_controller().stop()
    get_stop_controller().resume()
    await _queue_one(chat, "new message")
    chat.busy.clear()
    await _until(lambda: chat.sent)
    assert chat.sent == ["new message"]
    assert [n["status"] for n in chat.notices if n.get("kind") == "message_dequeued"] == ["sent"]


async def test_only_the_stale_message_is_dropped_the_newer_one_runs():
    chat = _Chat()
    await _queue_one(chat, "old")
    await get_stop_controller().stop()
    get_stop_controller().resume()
    await send_or_queue(chat, "society:scout", "new")  # same chat, queued under the new generation
    chat.busy.clear()
    await _until(lambda: chat.sent == ["new"])
    assert chat.sent == ["new"]


async def test_a_brain_turn_carries_the_ticket_taken_when_the_message_was_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from dodaios.agent_chat import service as svc_mod

    monkeypatch.setattr(svc_mod, "_claude_cli_installed", lambda: False)
    bus = EventBus()
    fake = FakeBrainManager(bus=bus)
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: fake)
    monkeypatch.setattr(runner_brain, "_agent_secret", lambda _r, _p: None)
    svc = AgentChatService(
        AgentChatStore(":memory:"), assistant_name=lambda: "Testo", bus=lambda: bus
    )
    session = svc.create_session(
        provider="openai", model="gpt-x", effort="high", cwd=str(tmp_path),
        permission_mode="ask", surface="jarvis",
    )
    q = svc.subscribe(session.session_id)
    ticket = get_stop_controller().ticket()
    await svc.send(session.session_id, "hello")
    async with asyncio.timeout(5):
        while (await q.get())["kind"] != "turn_finished":
            pass
    assert fake.calls and fake.calls[0][1].get("stop_ticket") == ticket
