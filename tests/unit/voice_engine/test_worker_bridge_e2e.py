"""The real Worker over real OS pipes and real frames, answered by an app-side provider (ADR-0051).

Fake VAD / turn / STT / TTS models and a fake provider; everything between them is the production
code path: frame encoding, the worker dispatcher, ConversationSession, BridgeChat, BrainLlmBridge.
No subprocess, no network, no keys, no model downloads.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any

import numpy as np
import pytest

from dodaios.core.protocols import BrainDelta
from dodaios.realtime.llm_bridge import BrainLlmBridge
from dodaios.voice_engine import protocol as p
from dodaios.voice_engine.audio import to_pcm16
from dodaios.voice_engine.bridge_llm import BridgeChat
from dodaios.voice_engine.engine import EngineConfig, EngineModels
from dodaios.voice_engine.runtime import RuntimeConfig
from dodaios.voice_engine.worker import StdioTransport, Worker

RATE = 16_000


class FakeVad:
    def __call__(self, frame):
        return 0.9 if float(np.sqrt(np.mean(frame**2))) > 0.05 else 0.02

    def reset(self):
        """Stateless."""


class FakeTurn:
    def probability(self, audio):
        return 0.95


class FakeStt:
    def transcribe(self, audio, rate=RATE):
        return "what time is it"


class FakeTts:
    name = "fake"
    sample_rate = 24_000

    def stream(self, text, *, stop=None):
        for _ in range(2):
            if stop is not None and stop.is_set():
                return
            time.sleep(0.02)
            yield np.full(int(self.sample_rate * 0.1), 0.1, dtype=np.float32)


class FakeBrain:
    def __init__(self, plans):
        self.plans = list(plans)  # one list of deltas per call
        self.requests: list[Any] = []
        self.closed: list[bool] = []

    def complete(self, req):
        self.requests.append(req)
        plan = self.plans[min(len(self.requests) - 1, len(self.plans) - 1)]
        return self._stream(plan)

    async def _stream(self, plan):
        try:
            for item in plan:
                if item == "hang":
                    await asyncio.sleep(30)
                await asyncio.sleep(0.02)
                yield item
            yield BrainDelta(finish_reason="stop")
        finally:
            self.closed.append(True)


def speech(seconds):
    t = np.arange(int(seconds * RATE)) / RATE
    return to_pcm16(0.3 * np.sin(2 * np.pi * 220 * t).astype(np.float32))


def silence(seconds):
    return to_pcm16(np.zeros(int(seconds * RATE), dtype=np.float32))


class Rig:
    """App side: writes frames to the worker, collects replies, answers llm.request with a brain."""

    def __init__(self, brain):
        self.brain = brain
        self.frames: list[Any] = []
        self.audio = 0
        self.llm = BridgeChat(first_delta_timeout_s=3, idle_timeout_s=3)

    async def __aenter__(self):
        self.loop = asyncio.get_running_loop()
        to_worker_r, self._to_worker_w = os.pipe()
        from_worker_r, from_worker_w = os.pipe()
        transport = StdioTransport(
            os.fdopen(to_worker_r, "rb", buffering=0), os.fdopen(from_worker_w, "wb", buffering=0)
        )
        self.worker = Worker(transport)
        models = EngineModels(
            vad_factory=FakeVad,
            turn=FakeTurn(),
            stt=FakeStt(),
            tts_for=lambda _l: FakeTts(),
            llm=self.llm,
        )
        self.llm.attach(self.worker._emit)
        self.worker._models = models
        self.worker._runtime = RuntimeConfig(
            languages=["en"], engine=EngineConfig(incomplete_wait_ms=200)
        )
        self._worker_thread = threading.Thread(
            target=lambda: asyncio.run(self.worker.run()), daemon=True
        )
        self._worker_thread.start()
        self.bridge = BrainLlmBridge(self.send, lambda: self.brain)
        self._reader_fd = from_worker_r
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        await self.wait(p.HELLO)
        return self

    def _read(self):
        reader = p.FrameReader()
        while True:
            data = os.read(self._reader_fd, 65536)
            if not data:
                return
            for frame in reader.feed(data):
                self.loop.call_soon_threadsafe(self._on_frame, frame)

    def _on_frame(self, frame):
        if isinstance(frame, p.AudioFrame):
            self.audio += 1
            return
        if frame["type"] in (p.LLM_REQUEST, p.LLM_CANCEL):
            self.bridge.route(frame)
        self.frames.append(frame)

    async def send(self, message):
        os.write(self._to_worker_w, p.encode_json(message))

    def send_audio(self, slot, seq, pcm):
        os.write(self._to_worker_w, p.encode_audio(slot, seq, pcm))

    async def wait(self, kind, count=1, within=8.0):
        end = time.monotonic() + within
        while time.monotonic() < end:
            hits = [f for f in self.frames if f["type"] == kind]
            if len(hits) >= count:
                return hits[count - 1]
            await asyncio.sleep(0.01)
        raise AssertionError(f"no {kind} #{count}; got {[f['type'] for f in self.frames]}")

    async def open_and_ask(self, tools=None):
        await self.send(
            {
                "type": p.SESSION_OPEN,
                "session": "s1",
                "slot": 1,
                "language": "en",
                "instructions": "You are Jarvis.",
                "tools": tools or [],
            }
        )
        await self.wait(p.SESSION_READY)
        seq = 0
        for chunk in (speech(0.8), silence(0.4)):
            for i in range(0, len(chunk), 3200):
                self.send_audio(1, seq, chunk[i : i + 3200])
                seq += 1
        await self.wait(p.TRANSCRIPT_INPUT)
        await self.send({"type": p.RESPONSE_REQUEST, "session": "s1", "language": "en"})

    async def __aexit__(self, *exc):
        await self.bridge.stop()
        await self.send({"type": p.SHUTDOWN})
        self._worker_thread.join(5)
        os.close(self._to_worker_w)


@pytest.mark.asyncio
async def test_a_spoken_turn_is_answered_by_the_app_provider_and_spoken_clause_by_clause():
    brain = FakeBrain([[BrainDelta(content="It is noon. "), BrainDelta(content="Anything else?")]])
    async with Rig(brain) as rig:
        await rig.open_and_ask()
        done = await rig.wait(p.RESPONSE_DONE)
        assert done["status"] == "completed"
        spoken = [f["delta"].strip() for f in rig.frames if f["type"] == p.TRANSCRIPT_OUTPUT]
        assert spoken == ["It is noon.", "Anything else?"]
        await asyncio.sleep(0.1)
        assert rig.audio > 0  # real audio frames came back over the pipe
        request = next(f for f in rig.frames if f["type"] == p.LLM_REQUEST)
        assert request["session"] == "s1" and request["messages"][0]["role"] == "system"
        assert brain.requests[0].messages[-1].content == "what time is it"


@pytest.mark.asyncio
async def test_a_tool_call_goes_back_through_the_engine_gate_and_the_second_round_sees_the_result():
    tools = [
        {
            "name": "set_timer",
            "description": "Start a timer.",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    brain = FakeBrain(
        [
            [BrainDelta(tool_call={"id": "x", "name": "set_timer", "input": {"minutes": 10}})],
            [BrainDelta(content="Your timer is running.")],
        ]
    )
    async with Rig(brain) as rig:
        await rig.open_and_ask(tools)
        call = await rig.wait(p.TOOL_CALL)
        assert call["name"] == "set_timer" and call["arguments"] == {"minutes": 10}
        await rig.send(
            {
                "type": p.TOOL_RESULT,
                "session": "s1",
                "call_id": call["call_id"],
                "result": {"success": True, "output": "Timer started."},
            }
        )
        assert (await rig.wait(p.RESPONSE_DONE))["status"] == "completed"
        second = brain.requests[1]
        roles = [m.role for m in second.messages]
        assert roles[-2:] == ["assistant", "tool"] and "Timer started." in str(
            second.messages[-1].content
        )
        assert second.messages[-2].content[0]["type"] == "tool_use"


@pytest.mark.asyncio
async def test_interrupting_cancels_the_provider_call_and_a_late_delta_is_ignored():
    brain = FakeBrain([[BrainDelta(content="One. "), "hang"]])
    async with Rig(brain) as rig:
        await rig.open_and_ask()
        await rig.wait(p.TRANSCRIPT_OUTPUT)
        await rig.send({"type": p.INTERRUPT, "session": "s1"})
        cancel = await rig.wait(p.LLM_CANCEL)
        request = next(f for f in rig.frames if f["type"] == p.LLM_REQUEST)
        assert cancel["id"] == request["id"]
        await rig.wait(p.RESPONSE_DONE)
        await asyncio.sleep(0.2)
        assert brain.closed  # the provider stream was closed
        before = rig.llm.dropped_stale
        await rig.send({"type": p.LLM_DELTA, "id": request["id"], "content": "late words"})
        await asyncio.sleep(0.2)
        assert rig.llm.dropped_stale == before + 1  # nobody is waiting for it any more
        assert all("late words" not in f.get("delta", "") for f in rig.frames)


@pytest.mark.asyncio
async def test_a_provider_error_becomes_a_recoverable_engine_error_not_a_hang():
    class Broken(FakeBrain):
        async def _stream(self, plan):
            raise RuntimeError("quota exceeded")
            yield  # pragma: no cover - makes this an async generator

    async with Rig(Broken([[]])) as rig:
        await rig.open_and_ask()
        error = await rig.wait(p.ERROR)
        assert error["code"] == "llm_failed" and "quota exceeded" in error["message"]
        assert (await rig.wait(p.RESPONSE_DONE))["status"] == "failed"
