"""The REAL ``ollama`` provider plugin over real HTTP on loopback, under the Work product.

ADR-0049 and ADR-0051. A tiny in-process server speaks the slice of Ollama's API the plugin uses
(``/api/tags``, ``/api/show``, ``/api/ps`` and OpenAI-style streaming ``/v1/chat/completions``).
Nothing leaves this machine, no model is downloaded and no key is read: it proves the policy doors,
the egress guard and the voice bridge work with the shipped plugin class, not only with test
doubles.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dodaios.brain import engine_policy as ep
from dodaios.brain.provider_registry import BrainProviderRegistry
from dodaios.core import egress_guard
from dodaios.core.protocols import BrainMessage, BrainRequest
from dodaios.plugins.brain.ollama import OllamaBrain
from dodaios.realtime.llm_bridge import BrainLlmBridge, brain_resolver

SEEN: list[tuple[str, str]] = []


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        SEEN.append(("GET", self.path))
        if self.path.startswith("/api/tags"):
            return self._json({"models": [{"name": "tiny:1b", "model": "tiny:1b", "details": {}}]})
        if self.path.startswith("/api/ps"):
            return self._json({"models": []})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        SEEN.append(("POST", self.path))
        if self.path.startswith("/api/show"):
            return self._json(
                {"capabilities": ["completion", "tools"], "model_info": {}, "parameters": ""}
            )
        if self.path.startswith("/v1/chat/completions"):
            sent = json.loads(raw or b"{}")
            assert sent.get("stream") is True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ("Ciao, ", "sono ", "locale."):
                chunk = {
                    "id": "c1",
                    "object": "chat.completion.chunk",
                    "model": sent.get("model", ""),
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            done = {
                "id": "c1",
                "object": "chat.completion.chunk",
                "model": sent.get("model", ""),
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
            self.wfile.write(f"data: {json.dumps(done)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return None
        return self._json({"error": "not found"}, 404)


@pytest.fixture
def server(monkeypatch):
    SEEN.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    from dodaios.core import config as cfg

    monkeypatch.setattr(cfg, "load_config", cfg.JarvisConfig, raising=False)
    yield srv
    srv.shutdown()
    srv.server_close()


def registry() -> BrainProviderRegistry:
    reg = BrainProviderRegistry()
    reg._classes = {"ollama": OllamaBrain}
    reg._loaded = True
    return reg


REQ = BrainRequest(messages=(BrainMessage(role="user", content="ciao"),))


async def collect(brain) -> list:
    return [d async for d in brain.complete(REQ)]


def test_work_product_runs_the_real_plugin_over_loopback_and_streams_the_answer(
    server, monkeypatch
):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    brain = registry().instantiate("ollama", model="tiny:1b")
    assert isinstance(brain, OllamaBrain)  # the policy wrapper keeps the plugin's identity
    deltas = asyncio.run(collect(brain))
    assert "".join(d.content or "" for d in deltas) == "Ciao, sono locale."
    assert any(path.startswith("/v1/chat/completions") for _, path in SEEN)


def test_work_product_refuses_a_non_local_ollama_host_before_connecting(server, monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    monkeypatch.setenv(
        "OLLAMA_HOST", "http://203.0.113.9:11434"
    )  # documentation range: never routable
    SEEN.clear()
    with pytest.raises(ep.EnginePolicyError) as exc:
        registry().instantiate("ollama", model="tiny:1b")
    assert exc.value.code == "remote_endpoint"
    assert SEEN == []


def test_consumer_is_not_restricted_for_the_same_remote_host(server, monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "consumer")
    monkeypatch.setenv("OLLAMA_HOST", "http://203.0.113.9:11434")
    assert isinstance(registry().instantiate("ollama", model="tiny:1b"), OllamaBrain)


def test_the_strict_offline_profile_lets_loopback_through_and_nothing_else(server, monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    egress_guard.uninstall()
    egress_guard.install()
    try:
        brain = registry().instantiate("ollama", model="tiny:1b")
        assert "".join(d.content or "" for d in asyncio.run(collect(brain))) == "Ciao, sono locale."
        assert egress_guard.blocked_attempts()[0] == 0  # the whole turn stayed on loopback
        import urllib.request

        with pytest.raises(OSError):
            urllib.request.urlopen("http://203.0.113.9/", timeout=1)
        assert egress_guard.blocked_attempts()[0] == 1
    finally:
        egress_guard.uninstall()


def test_voice_bridge_answers_with_the_real_plugin_under_work(server, monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    monkeypatch.setattr("dodaios.brain.provider_registry.BrainProviderRegistry", registry)

    async def go():
        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        bridge = BrainLlmBridge(send, brain_resolver("ollama", "tiny:1b"))
        bridge.handle({"id": "c1", "messages": [{"role": "user", "content": "ciao"}], "tools": []})
        for _ in range(100):
            await asyncio.sleep(0.1)
            if any(m.get("finish") or m.get("error") for m in sent):
                break
        await bridge.stop()
        return sent

    frames = asyncio.run(go())
    assert not [f for f in frames if f.get("error")], frames
    assert "".join(f.get("content", "") for f in frames) == "Ciao, sono locale."
    assert frames[-1].get("finish") is True
