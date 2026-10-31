"""Consumer privacy follows the real destination of a local-type adapter, not its name (P0)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from dodaios.brain import consumer_orchestrator as co
from dodaios.brain import engine_policy as ep
from dodaios.core.protocols import BrainDelta

MARKER = "PRIVATE-MARKER"


class SpyBrain:
    """Records every prompt it is sent; ``_server_root`` is what the real adapters expose."""

    def __init__(self, root: str) -> None:
        self._server_root = root
        self.seen: list[str] = []

    async def complete(self, req) -> AsyncIterator[BrainDelta]:
        self.seen.append(req.messages[0].content)
        yield BrainDelta(content="ok")


def _configure(monkeypatch: pytest.MonkeyPatch, base_url: str | None, *, proxy: bool = False):
    monkeypatch.setattr(
        "dodaios.core.config.resolve_provider_endpoint",
        lambda provider, vendor_default_base_url=None, config=None: SimpleNamespace(
            base_url=base_url, via_proxy=proxy, credential=None
        ),
    )
    monkeypatch.setattr(
        "dodaios.plugins.brain.ollama.default_server_root", lambda: "http://127.0.0.1:11434"
    )


def _run(brain: SpyBrain, provider: str = "local-openai"):
    items = [
        co.ContextItem("summarise", "task"),
        co.ContextItem(MARKER, "file", local_only=True),
    ]
    orch = co.Orchestrator(
        co.PrivacyGate(allowed_categories=frozenset({"task"})), co.Budget(1.0)
    )
    branch = co.Branch("b1", provider, brain, "local", est_cost=0.0)
    return asyncio.run(orch.run("t", items, [branch]))[0]


def test_loopback_adapter_is_local_and_keeps_local_only_context(monkeypatch):
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    _configure(monkeypatch, "http://127.0.0.1:8080")
    brain = SpyBrain("http://127.0.0.1:8080")
    res = _run(brain)
    assert res.status == "ok"
    assert MARKER in brain.seen[0]


@pytest.mark.parametrize(
    "root",
    ["https://example.com/v1", "http://192.168.1.20:8080", "http://gpu-box.lan:8080", "not a url"],
)
def test_external_destination_never_receives_local_only_text(monkeypatch, root):
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    monkeypatch.setenv("JARVIS_LOCAL_ALLOWED_HOSTS", "192.168.1.20,gpu-box.lan")
    _configure(monkeypatch, root)
    brain = SpyBrain(root)
    res = _run(brain)
    assert all(MARKER not in prompt for prompt in brain.seen)
    assert res.egress is not None  # remote metadata is recorded


@pytest.mark.parametrize("base_url, proxy", [(None, False), ("http://127.0.0.1:1", True)])
def test_unknown_or_proxied_endpoint_fails_closed(monkeypatch, base_url, proxy):
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    _configure(monkeypatch, base_url, proxy=proxy)
    brain = SpyBrain("")
    _run(brain)
    assert all(MARKER not in prompt for prompt in brain.seen)
    assert not ep.destination_of("local-openai", brain).local


def test_endpoint_changed_after_build_is_remote(monkeypatch):
    _configure(monkeypatch, "http://127.0.0.1:8080")
    brain = SpyBrain("http://127.0.0.1:8080")  # built when the config named this port
    assert ep.destination_of("local-openai", brain).local
    _configure(monkeypatch, "http://127.0.0.1:9999")  # the config now names another port
    assert not ep.destination_of("local-openai", brain).local


def test_endpoint_moving_between_prepare_and_call_is_refused(monkeypatch):
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    _configure(monkeypatch, "http://127.0.0.1:8080")
    brain = SpyBrain("http://127.0.0.1:8080")
    real = ep.destination_of
    calls = {"n": 0}

    def flipping(provider, b=None):
        calls["n"] += 1
        if calls["n"] > 1:  # second look, right before the call
            return ep.Destination("evil.example", False, "moved")
        return real(provider, b)

    monkeypatch.setattr(ep, "destination_of", flipping)
    res = _run(brain)
    assert res.status == "refused"
    assert brain.seen == []


def test_provider_name_alone_is_remote_for_unknown_provider():
    assert not ep.destination_of("openai").local


def test_work_still_rejects_remote_before_any_call(monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    brain = SpyBrain("https://example.com/v1")
    res = _run(brain, provider="openai")
    assert res.status == "refused"
    assert brain.seen == []
