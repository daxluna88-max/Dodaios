"""Live voice answered by an app-side provider (ADR-0051): worker and app bridges wired in-process.

Fake providers only: no subprocess, no network, no keys.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import pytest

from dodaios.core.protocols import BrainDelta
from dodaios.realtime.llm_bridge import BrainLlmBridge, to_brain_request
from dodaios.voice_engine import protocol as p
from dodaios.voice_engine.bridge_llm import BridgeChat
from dodaios.voice_engine.chunker import ClauseChunker


class FakeBrain:
    def __init__(self, deltas=None, delay=0.0, fail=None, hang=False):
        self.deltas = deltas if deltas is not None else [BrainDelta(content="Ciao, sono pronto.")]
        self.delay, self.fail, self.hang = delay, fail, hang
        self.requests = []
        self.closed = False

    def complete(self, req) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        return self._stream()

    async def _stream(self):
        try:
            if self.fail:
                raise RuntimeError(self.fail)
            if self.hang:
                await asyncio.sleep(30)
            for delta in self.deltas:
                await asyncio.sleep(self.delay)
                yield delta
            yield BrainDelta(finish_reason="stop")
        finally:
            self.closed = True


class Loop:
    """A BridgeChat (worker side, in a thread) wired to a BrainLlmBridge (app side, on the loop)."""

    def __init__(self, brain, loop):
        self.loop, self.chat, self.sent = (
            loop,
            BridgeChat(first_delta_timeout_s=2, idle_timeout_s=2),
            [],
        )
        self.bridge = BrainLlmBridge(self._app_send, lambda: brain)
        self.chat.attach(self)  # the worker's emitter

    # worker -> app (called from the engine thread)
    def json(self, message):
        self.sent.append(message)
        self.loop.call_soon_threadsafe(self.bridge.route, message)

    # app -> worker
    async def _app_send(self, message):
        self.sent.append(message)
        self.chat.deliver(message)


def run_chat(brain, messages=None, tools=None, stop=None, hook=None):
    async def go():
        loop = asyncio.get_running_loop()
        wire = Loop(brain, loop)
        if hook:
            hook(wire)
        clauses = []
        result = await asyncio.to_thread(
            wire.chat.chat,
            messages or [{"role": "user", "content": "ciao"}],
            tools=tools,
            chunker=ClauseChunker(),
            on_clause=lambda c, _t: clauses.append(c),
            stop=stop,
            session="s1",
        )
        await wire.bridge.stop()
        return wire, result, clauses

    return asyncio.run(go())


def test_streamed_text_becomes_clauses_and_the_request_is_normalized():
    brain = FakeBrain(
        [
            BrainDelta(content="Ciao, "),
            BrainDelta(content="sono pronto. "),
            BrainDelta(content="Dimmi pure."),
        ]
    )
    wire, result, clauses = run_chat(
        brain, [{"role": "system", "content": "Sei Jarvis."}, {"role": "user", "content": "ciao"}]
    )
    assert result.error == "" and result.text == "Ciao, sono pronto. Dimmi pure."
    assert clauses and " ".join(clauses).replace("  ", " ").startswith("Ciao,")
    assert result.t_first_clause is not None and result.t_first_token is not None
    req = brain.requests[0]
    assert req.system == "Sei Jarvis." and [m.role for m in req.messages] == ["user"]
    request_frame = next(m for m in wire.sent if m["type"] == p.LLM_REQUEST)
    assert request_frame["session"] == "s1" and request_frame["id"].startswith("llm_")


def test_tool_calls_come_back_as_data_for_the_engine_gate_and_history_is_converted():
    brain = FakeBrain(
        [BrainDelta(tool_call={"id": "x", "name": "OpenApp", "input": {"app": "mail"}})]
    )
    messages = [
        {"role": "user", "content": "apri la posta"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "OpenApp", "arguments": {"app": "mail"}}}],
        },
        {"role": "tool", "tool_name": "OpenApp", "content": '{"success": true}'},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "OpenApp",
                "description": "d",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    _, result, _ = run_chat(brain, messages, tools)
    assert result.tool_calls == [{"name": "OpenApp", "arguments": {"app": "mail"}}]
    req = brain.requests[0]
    assert req.tools[0]["name"] == "OpenApp" and "input_schema" in req.tools[0]
    assistant, tool = req.messages[1], req.messages[2]
    assert assistant.content[0]["type"] == "tool_use" and assistant.content[0]["id"] == "call_1"
    assert tool.role == "tool" and tool.tool_call_id == "call_1"
    assert tool.content[0]["tool_use_id"] == "call_1"


def test_provider_error_reaches_the_engine_as_an_error_not_a_hang():
    _, result, _ = run_chat(FakeBrain(fail="quota exceeded"))
    assert "quota exceeded" in result.error and result.text == ""


def test_stop_cancels_the_call_closes_the_stream_and_no_late_frame_is_accepted():
    stop = threading.Event()
    brain = FakeBrain([BrainDelta(content=f"parola{i}. ") for i in range(50)], delay=0.05)
    threading.Timer(0.3, stop.set).start()
    wire, result, _ = run_chat(brain, stop=stop)
    assert result.error == "cancelled"
    assert any(m["type"] == p.LLM_CANCEL for m in wire.sent)
    assert brain.closed is True  # the provider stream was closed, not left running
    late = {
        "type": p.LLM_DELTA,
        "id": next(m["id"] for m in wire.sent if m["type"] == p.LLM_REQUEST),
        "content": "tardiva",
    }
    assert wire.chat.deliver(late) is False and wire.chat.dropped_stale >= 1


def test_silence_from_the_provider_times_out_with_a_cancel():
    wire, result, _ = run_chat(FakeBrain(hang=True))
    assert "did not answer in time" in result.error
    assert any(m["type"] == p.LLM_CANCEL for m in wire.sent)


def test_unknown_or_finished_call_ids_are_dropped():
    chat = BridgeChat()
    assert chat.deliver({"type": p.LLM_DELTA, "id": "llm_nope", "content": "x"}) is False
    assert chat.dropped_stale == 1


def test_unattached_bridge_fails_cleanly():
    assert "not attached" in BridgeChat().chat([{"role": "user", "content": "x"}]).error


def test_work_product_reaches_only_a_local_provider_through_the_bridge(monkeypatch):
    from dodaios.brain.provider_registry import BrainProviderRegistry
    from dodaios.realtime.llm_bridge import brain_resolver

    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    monkeypatch.setattr("dodaios.brain.engine_policy.endpoint_problem", lambda provider: None)

    class Cloud(FakeBrain):
        built = 0

        def __init__(self, model=None):
            super().__init__()
            Cloud.built += 1

    registry = BrainProviderRegistry()
    registry._classes = {"openai": Cloud, "ollama": Cloud}
    registry._loaded = True
    monkeypatch.setattr("dodaios.brain.provider_registry.BrainProviderRegistry", lambda: registry)

    async def go(provider):
        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        bridge = BrainLlmBridge(send, brain_resolver(provider, None))
        bridge.handle({"id": "c1", "messages": [{"role": "user", "content": "x"}], "tools": []})
        await asyncio.sleep(0.2)
        await bridge.stop()
        return sent

    remote = asyncio.run(go("openai"))
    assert Cloud.built == 0 and remote[-1].get("error", "").startswith("EnginePolicyError")
    assert "remote" in remote[-1]["error"]
    local = asyncio.run(go("ollama"))
    assert Cloud.built == 1 and local[-1].get("finish") is True


def test_history_conversion_pairs_results_with_calls_in_order_and_survives_orphans():
    req = to_brain_request(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "A", "arguments": {}}},
                    {"function": {"name": "B", "arguments": {}}},
                ],
            },
            {"role": "tool", "tool_name": "B", "content": "rb"},
            {"role": "tool", "tool_name": "A", "content": "ra"},
            {"role": "tool", "tool_name": "Z", "content": "orphan"},
        ],
        None,
    )
    ids = [m.tool_call_id for m in req.messages[1:]]
    assert ids[0] == "call_2" and ids[1] == "call_1" and ids[2].startswith("call_orphan")


def test_runtime_config_selects_the_bridge_source():
    from dodaios.voice_engine.runtime import RuntimeConfig

    assert RuntimeConfig.from_message({"llm": {"source": "bridge"}}).llm_source == "bridge"
    assert RuntimeConfig.from_message({"llm": {"source": "x"}}).llm_source == "ollama"
    assert RuntimeConfig.from_message({}).llm_source == "ollama"


def test_engine_passes_the_session_only_to_models_that_ask_for_it():
    from dodaios.voice_engine.bridge_llm import BridgeChat as B

    assert B.needs_session is True
    from dodaios.voice_engine.llm import OllamaChat

    assert getattr(OllamaChat, "needs_session", False) is False


@pytest.mark.parametrize("raw", ["brain", "BRAIN", " brain "])
def test_config_source_values(raw):
    from dodaios.core.config import VoiceEngineConfig

    assert VoiceEngineConfig(llm_source=raw).llm_source == "brain"
    assert VoiceEngineConfig(llm_source="x").llm_source == "ollama"
