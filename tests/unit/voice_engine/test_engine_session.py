"""The conversation loop with fake models: turns, answers, tools, barge-in."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pytest

from dodaios.voice_engine import protocol as p
from dodaios.voice_engine.audio import to_pcm16
from dodaios.voice_engine.engine import ConversationSession, EngineConfig, EngineModels

RATE = 16_000


class FakeVad:
    def __call__(self, frame: np.ndarray) -> float:
        return 0.9 if float(np.sqrt(np.mean(frame**2))) > 0.05 else 0.02

    def reset(self) -> None:
        """Stateless fake."""


@dataclass
class FakeTurn:
    value: float = 0.95

    def probability(self, audio16k: np.ndarray) -> float:
        return self.value


@dataclass
class FakeStt:
    texts: list[str] = field(default_factory=lambda: ["what time is it"])
    calls: int = 0

    def transcribe(self, audio: np.ndarray, rate: int = RATE) -> str:
        self.calls += 1
        return self.texts[min(self.calls - 1, len(self.texts) - 1)]


class FakeTts:
    name = "fake"
    sample_rate = 24_000

    def __init__(self, chunks: int = 2, chunk_s: float = 0.1, pause_s: float = 0.0) -> None:
        self.chunks, self.chunk_s, self.pause_s = chunks, chunk_s, pause_s

    def stream(self, text: str, *, stop: threading.Event | None = None) -> Iterator[np.ndarray]:
        for _ in range(self.chunks):
            if stop is not None and stop.is_set():
                return
            if self.pause_s:
                time.sleep(self.pause_s)
            yield np.full(int(self.sample_rate * self.chunk_s), 0.1, dtype=np.float32)


@dataclass
class FakeResult:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    t_first_clause: float | None = 0.05
    error: str = ""


class FakeLlm:
    """Each chat() call plays the next scripted reply: (clauses, tool_calls)."""

    def __init__(self, script: list[tuple[list[str], list[dict[str, Any]]]]) -> None:
        self.script = script
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], *, on_clause: Callable[[str, float], None]
             | None = None, stop: threading.Event | None = None, **_: Any) -> FakeResult:
        self.calls.append([dict(m) for m in messages])
        clauses, calls = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        for clause in clauses:
            if stop is not None and stop.is_set():
                break
            if on_clause is not None:
                on_clause(clause, 0.05)
        return FakeResult(text=" ".join(clauses), tool_calls=calls)


class Collector:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.audio_frames = 0
        self.heard_audio = asyncio.Event()

    def json(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def audio(self, slot: int, seq: int, pcm16: bytes) -> None:
        self.audio_frames += 1
        self.heard_audio.set()

    def types(self) -> list[str]:
        return [m["type"] for m in self.messages]

    async def wait(self, kind: str, count: int = 1, within: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            matches = [m for m in self.messages if m["type"] == kind]
            if len(matches) >= count:
                return matches[count - 1]
            await asyncio.sleep(0.01)
        raise AssertionError(f"no {kind} #{count}; got {self.types()}")


def _speech(seconds: float) -> bytes:
    t = np.arange(int(seconds * RATE)) / RATE
    return to_pcm16(0.3 * np.sin(2 * np.pi * 220 * t).astype(np.float32))


def _silence(seconds: float) -> bytes:
    return to_pcm16(np.zeros(int(seconds * RATE), dtype=np.float32))


def _session(llm: FakeLlm, *, stt: FakeStt | None = None, tts: FakeTts | None = None,
             turn: float = 0.95) -> tuple[ConversationSession, Collector, FakeStt]:
    stt = stt or FakeStt()
    tts = tts or FakeTts()
    models = EngineModels(vad_factory=FakeVad, turn=FakeTurn(turn), stt=stt,
                          tts_for=lambda _lang: tts, llm=llm)
    collector = Collector()
    session = ConversationSession(
        slot=1, session_id="s1", models=models, emit=collector, loop=asyncio.get_running_loop(),
        config=EngineConfig(incomplete_wait_ms=200), instructions="You are Jarvis.",
        language="en", tools=[{"name": "set_timer", "description": "Start a timer.",
                               "parameters": {"type": "object", "properties": {}}}],
    )
    return session, collector, stt


async def _say(session: ConversationSession, seconds: float = 0.8, tail: float = 0.4) -> None:
    session.on_audio(_speech(seconds))
    session.on_audio(_silence(tail))


@pytest.mark.asyncio
async def test_a_finished_turn_is_transcribed_and_answered() -> None:
    llm = FakeLlm([(["It is noon.", "Anything else?"], [])])
    session, out, _ = _session(llm)
    await _say(session)
    final = await out.wait(p.TRANSCRIPT_INPUT)
    assert final["text"] == "what time is it" and final["final"] is True
    assert out.types()[0] == p.SPEECH_STARTED
    session.on_response_request("en")
    done = await out.wait(p.RESPONSE_DONE)
    assert done["status"] == "completed"
    spoken = [m["delta"].strip() for m in out.messages if m["type"] == p.TRANSCRIPT_OUTPUT]
    assert spoken == ["It is noon.", "Anything else?"]
    assert out.audio_frames > 0
    metrics = (await out.wait(p.METRICS))["turn"]
    assert metrics["status"] == "completed" and metrics["turn"] == 1
    assert "speech_end_to_first_audio_ms" in metrics
    system = llm.calls[0][0]["content"]
    assert "Reply in English" in system


@pytest.mark.asyncio
async def test_tool_calls_round_trip_through_the_app() -> None:
    llm = FakeLlm([
        ([], [{"name": "set_timer", "arguments": {"minutes": 10}}]),
        (["Your timer is running."], []),
    ])
    session, out, _ = _session(llm)
    await _say(session)
    await out.wait(p.TRANSCRIPT_INPUT)
    session.on_response_request("en")
    call = await out.wait(p.TOOL_CALL)
    assert call["name"] == "set_timer" and call["arguments"] == {"minutes": 10}
    session.on_tool_result(call["call_id"], {"success": True, "output": "Timer started."})
    done = await out.wait(p.RESPONSE_DONE)
    assert done["status"] == "completed"
    second = llm.calls[1]
    assert second[-1]["role"] == "tool" and "Timer started." in second[-1]["content"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "set_timer"


@pytest.mark.asyncio
async def test_speaking_over_the_answer_stops_it() -> None:
    llm = FakeLlm([(["One.", "Two.", "Three.", "Four."], [])])
    session, out, _ = _session(llm, tts=FakeTts(chunks=10, chunk_s=0.05, pause_s=0.05))
    await _say(session)
    await out.wait(p.TRANSCRIPT_INPUT)
    session.on_response_request("en")
    await out.wait(p.TRANSCRIPT_OUTPUT)
    await asyncio.wait_for(out.heard_audio.wait(), 5.0)
    session.on_audio(_speech(0.5))
    interrupted = await out.wait(p.INTERRUPTED)
    assert interrupted["self_initiated"] is False
    done = await out.wait(p.RESPONSE_DONE)
    assert done["status"] == "cancelled"
    assert out.types().count(p.SPEECH_STARTED) == 2


@pytest.mark.asyncio
async def test_a_user_who_keeps_talking_gets_one_merged_turn() -> None:
    llm = FakeLlm([(["Done."], [])])
    stt = FakeStt(texts=["remind me", "to call mum"])
    session, out, _ = _session(llm, stt=stt)
    await _say(session)
    first = await out.wait(p.TRANSCRIPT_INPUT)
    assert first["text"] == "remind me"
    await _say(session)  # resumes before the app asked for an answer
    second = await out.wait(p.TRANSCRIPT_INPUT, count=2)
    assert second["text"] == "remind me to call mum"
    session.on_response_request("en")
    await out.wait(p.RESPONSE_DONE)
    assert llm.calls[0][-1] == {"role": "user", "content": "remind me to call mum"}


@pytest.mark.asyncio
async def test_an_incomplete_verdict_waits_before_finalising() -> None:
    llm = FakeLlm([(["Sure."], [])])
    session, out, _ = _session(llm, turn=0.1)
    started = time.monotonic()
    await _say(session)
    await out.wait(p.TRANSCRIPT_INPUT)
    # 200 ms wait; Windows timers may wake a tick early.
    assert time.monotonic() - started >= 0.15


@pytest.mark.asyncio
async def test_a_request_without_a_pending_turn_is_ignored() -> None:
    llm = FakeLlm([(["Hi."], [])])
    session, out, _ = _session(llm)
    session.on_response_request("en")
    await asyncio.sleep(0.1)
    assert llm.calls == [] and p.RESPONSE_DONE not in out.types()
    session.close()


class SilentEdgesTts(FakeTts):
    """A voice that pads every clause with 0.5 s of near-silence at both ends."""

    def stream(self, text: str, *, stop: threading.Event | None = None) -> Iterator[np.ndarray]:
        yield np.full(12_000, 0.001, dtype=np.float32)  # 0.5 s lead-in
        yield np.full(4_800, 0.2, dtype=np.float32)  # 0.2 s voice
        yield np.full(12_000, 0.001, dtype=np.float32)  # 0.5 s tail


@pytest.mark.asyncio
async def test_voice_silence_is_trimmed_and_clauses_get_a_set_pause() -> None:
    llm = FakeLlm([(["One.", "Two."], [])])
    session, out, _ = _session(llm, tts=SilentEdgesTts())
    await _say(session)
    await out.wait(p.TRANSCRIPT_INPUT)
    session.on_response_request("en")
    await out.wait(p.RESPONSE_DONE)
    metrics = (await out.wait(p.METRICS))["turn"]
    # Two clauses of 0.2 s voice (+20 ms onset/decay each) and one 250 ms
    # pause after the full stop; none of the 2 s of padding is played.
    assert 600 <= metrics["audio_ms"] <= 760


class CountingTurn(FakeTurn):
    def __init__(self, values: list[float]) -> None:
        super().__init__()
        self.values = values
        self.calls = 0

    def probability(self, audio16k: np.ndarray) -> float:
        self.calls += 1
        return self.values[min(self.calls - 1, len(self.values) - 1)]


@pytest.mark.asyncio
async def test_transcription_starts_early_and_is_reused() -> None:
    llm = FakeLlm([(["Fine."], [])])
    session, out, stt = _session(llm)
    await _say(session)
    await out.wait(p.TRANSCRIPT_INPUT)
    assert stt.calls == 1


@pytest.mark.asyncio
async def test_an_incomplete_verdict_is_rescored_instead_of_waited_out() -> None:
    llm = FakeLlm([(["Sure."], [])])
    stt = FakeStt()
    tts = FakeTts()
    turn = CountingTurn([0.1, 0.9])
    models = EngineModels(vad_factory=FakeVad, turn=turn, stt=stt, tts_for=lambda _l: tts,
                          llm=llm)
    out = Collector()
    session = ConversationSession(
        slot=1, session_id="s1", models=models, emit=out, loop=asyncio.get_running_loop(),
        config=EngineConfig(incomplete_wait_ms=3000, reevaluate_ms=100), language="en",
    )
    started = time.monotonic()
    await _say(session, tail=0.6)
    await out.wait(p.TRANSCRIPT_INPUT)
    assert time.monotonic() - started < 2.5
    assert turn.calls == 2


@pytest.mark.asyncio
async def test_turn_context_rides_on_one_answer_and_stays_out_of_the_history() -> None:
    llm = FakeLlm([(["It is noon."], []), (["Still noon."], [])])
    session, out, _ = _session(llm, stt=FakeStt(["what time is it", "and now"]))
    await _say(session)
    await out.wait(p.TRANSCRIPT_INPUT)
    session.on_turn_context("## Personal memory\n- Dax takes the train")
    session.on_response_request("en")
    await out.wait(p.RESPONSE_DONE)
    first_user = llm.calls[0][-1]
    assert first_user["role"] == "user"
    assert first_user["content"] == "## Personal memory\n- Dax takes the train\n\nwhat time is it"
    system = llm.calls[0][0]
    assert system["role"] == "system" and "Personal memory" not in system["content"]
    # history keeps what the user said, not the injected context
    assert session._messages[1] == {"role": "user", "content": "what time is it"}
    await _say(session)
    await out.wait(p.TRANSCRIPT_INPUT, count=2)
    session.on_response_request("en")
    await out.wait(p.RESPONSE_DONE, count=2)
    assert llm.calls[1][-1]["content"] == "and now"  # consumed: not repeated on the next turn
    assert all("Personal memory" not in str(m.get("content")) for m in llm.calls[1][:-1])


@pytest.mark.asyncio
async def test_turn_context_is_dropped_when_the_user_keeps_talking_or_it_is_cleared() -> None:
    llm = FakeLlm([(["Ok."], [])])
    session, out, _ = _session(llm, stt=FakeStt(["first part", "first part and more"]))
    await _say(session)
    await out.wait(p.TRANSCRIPT_INPUT)
    session.on_turn_context("stale context for the first part")
    session.on_audio(_speech(0.8))  # the user continues: context for the old text is stale
    session.on_audio(_silence(0.4))
    await out.wait(p.TRANSCRIPT_INPUT, count=2)
    session.on_response_request("en")
    await out.wait(p.RESPONSE_DONE)
    assert "stale context" not in llm.calls[0][-1]["content"]
    session.on_turn_context("x")
    session.on_turn_context("")  # an empty block clears
    assert session._turn_context == ""


@pytest.mark.asyncio
async def test_turn_context_is_bounded_and_ignored_after_close() -> None:
    from dodaios.voice_engine.engine import MAX_TURN_CONTEXT_CHARS

    session, _out, _ = _session(FakeLlm([([], [])]))
    session.on_turn_context("a" * (MAX_TURN_CONTEXT_CHARS * 3))
    assert len(session._turn_context) == MAX_TURN_CONTEXT_CHARS
    session.close()
    session.on_turn_context("late")
    assert session._turn_context == ""
