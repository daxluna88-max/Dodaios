"""Per-turn memory for native live voice (ADR-0048): context reaches the engine pre-answer."""

from __future__ import annotations

import asyncio

import pytest

from dodaios.brain.turn_memory import BLOCK_HEADER, set_turn_memory
from dodaios.realtime.protocol import RealtimeEvent
from tests.fakes.fake_native_voice import (
    FakeNativeConnection,
    gemini_like_provider,
    local_voice_like_provider,
)
from tests.unit.live.test_native_live_fixes import _in_call, _settle


class _ContextConnection(FakeNativeConnection):
    """The local engine's shape: it takes a turn context."""

    async def set_turn_context(self, text: str) -> None:
        self.calls.append(("set_turn_context", text))


class _Memory:
    def __init__(
        self, text: str = "- Dax prende il treno", delay: float = 0.0, exc: Exception | None = None
    ):
        self.text, self.delay, self.exc, self.seen = text, delay, exc, []

    async def recall(self, user_text: str) -> str:
        self.seen.append(user_text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.text


@pytest.fixture(autouse=True)
def _no_memory():
    set_turn_memory(None)
    yield
    set_turn_memory(None)


def _final(text: str) -> RealtimeEvent:
    return RealtimeEvent(type="input_transcript", text=text, is_final=True)


async def _turn(tmp_path, connection_cls, text: str):
    provider = local_voice_like_provider()
    session, _plain, _frames = _in_call(provider, tmp_path)
    connection = connection_cls(creates_responses_automatically=False, model="m")
    session._connection = connection
    try:
        await session._native_event(_final(text))
        await _settle(session)
    finally:
        session._ledger.close()
    return session, connection


@pytest.mark.asyncio
async def test_the_memory_block_reaches_the_engine_before_the_response(tmp_path):
    memory = _Memory()
    set_turn_memory(memory)
    _session, connection = await _turn(tmp_path, _ContextConnection, "come mi sposto a Milano?")
    names = connection.names()
    assert names.index("set_turn_context") < names.index("request_response")
    block = dict(connection.calls)["set_turn_context"]
    assert block.startswith(BLOCK_HEADER) and "Dax prende il treno" in block
    assert memory.seen == ["come mi sposto a Milano?"]


@pytest.mark.asyncio
async def test_without_a_provider_an_empty_context_still_clears_the_previous_one(tmp_path):
    _session, connection = await _turn(tmp_path, _ContextConnection, "ciao")
    assert ("set_turn_context", "") in connection.calls
    assert connection.names().index("set_turn_context") < connection.names().index(
        "request_response"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("memory", [_Memory(delay=2.0), _Memory(exc=RuntimeError("boom"))])
async def test_a_slow_or_failing_memory_never_blocks_the_answer(tmp_path, memory):
    set_turn_memory(memory)
    started = asyncio.get_running_loop().time()
    _session, connection = await _turn(tmp_path, _ContextConnection, "ciao")
    assert asyncio.get_running_loop().time() - started < 1.0  # bounded by the 200 ms budget
    assert ("set_turn_context", "") in connection.calls  # answered, without the block
    assert "request_response" in connection.names()


@pytest.mark.asyncio
async def test_connections_without_turn_context_are_untouched(tmp_path):
    set_turn_memory(_Memory())
    _session, connection = await _turn(tmp_path, FakeNativeConnection, "ciao")
    assert "set_turn_context" not in connection.names()
    assert connection.names() == ["update_session", "request_response"]


@pytest.mark.asyncio
async def test_a_connection_that_answers_by_itself_gets_no_context(tmp_path):
    set_turn_memory(_Memory())
    provider = gemini_like_provider()
    session, connection, _ = _in_call(provider, tmp_path)
    try:
        await session._native_event(_final("ciao"))
        await _settle(session)
        assert connection.names() == []
    finally:
        session._ledger.close()
