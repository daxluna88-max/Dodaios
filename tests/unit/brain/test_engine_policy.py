"""Product engine policy (ADR-0049): fake providers only, no network, no credentials, no paid
calls."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator

import pytest

from dodaios.brain import engine_policy as ep
from dodaios.brain.provider_registry import BrainProviderRegistry
from dodaios.core.protocols import BrainDelta, BrainMessage, BrainRequest

REQ = BrainRequest(messages=(BrainMessage(role="user", content="ciao"),))


class FakeBrain:
    built: list[str] = []
    supports_tools = True
    supports_vision = False
    context_window = 8192

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        FakeBrain.built.append(model or "")

    def estimate_cost(self, req: BrainRequest) -> float:
        return 0.0

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="ok")


class UnknownCapsBrain(FakeBrain):
    supports_tools = None  # type: ignore[assignment]
    supports_vision = None  # type: ignore[assignment]
    context_window = None  # type: ignore[assignment]


class NoToolsBrain(FakeBrain):
    supports_tools = False


def registry(**classes: type) -> BrainProviderRegistry:
    reg = BrainProviderRegistry()
    reg._classes = dict(classes)
    reg._loaded = True
    return reg


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    FakeBrain.built.clear()
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    monkeypatch.delenv(ep.ALLOWED_HOSTS_ENV, raising=False)
    monkeypatch.setattr(
        ep, "endpoint_problem", lambda provider: None
    )  # endpoint cases are tested apart


# -- product detection
# -----------------------------------------------------------------------------------


def test_product_detection_and_garbled_value_is_strict(monkeypatch):
    assert ep.current_product() is None
    monkeypatch.setenv("JARVIS_PRODUCT", " Consumer ")
    assert ep.current_product() == "consumer"
    monkeypatch.setenv("JARVIS_PRODUCT", "pmi-ibrido")
    assert ep.current_product() == "work"


# -- Work is local-only: registry door, per-call recheck, no remote fallback
# ------------------------------------


def test_work_refuses_remote_provider_before_building_it(monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    reg = registry(
        openai=FakeBrain, **{"claude-api": FakeBrain, "claude-cli": FakeBrain, "codex": FakeBrain}
    )
    for name in ("openai", "claude-api", "claude-cli", "codex", "desconocido"):
        with pytest.raises(ep.EnginePolicyError) as exc:
            reg.instantiate(name) if name in reg.available() else ep.check_provider(name)
        assert exc.value.code == "remote_in_local_only"
    assert FakeBrain.built == []  # refused BEFORE construction: no credential read, no connection


def test_work_allows_local_providers_and_wraps_them(monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    brain = registry(ollama=FakeBrain, **{"local-openai": FakeBrain}).instantiate(
        "ollama", model="m"
    )
    assert isinstance(brain, FakeBrain)  # isinstance keeps working through the wrappers
    assert FakeBrain.built == ["m"]

    async def run():
        return [d.content async for d in brain.complete(REQ)]

    assert asyncio.run(run()) == ["ok"]


def test_consumer_and_unset_product_are_not_restricted_and_not_wrapped(monkeypatch):
    for product in (None, "consumer"):
        if product:
            monkeypatch.setenv("JARVIS_PRODUCT", product)
        brain = registry(openai=FakeBrain).instantiate("openai")
        assert not isinstance(brain, ep.PolicyBrain)


def test_policy_is_rechecked_on_every_call_so_stale_config_cannot_reroute(monkeypatch):
    brain = ep.PolicyBrain(FakeBrain(), "openai")  # built while no restrictive policy was active
    assert list(asyncio.run(_collect(brain.complete(REQ)))) != []
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    with pytest.raises(ep.EnginePolicyError):
        brain.complete(REQ)


async def _collect(stream):
    return [d async for d in stream]


def test_work_missing_local_engine_is_an_error_not_a_cloud_fallback(monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    reg = registry(openai=FakeBrain)  # only a remote engine installed
    with pytest.raises(ep.EnginePolicyError) as exc:
        ep.select_engine(reg, "ollama")
    assert exc.value.code == "unavailable"
    assert FakeBrain.built == []


def test_negative_network_work_refusal_opens_no_connection(monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    calls: list[object] = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: calls.append(addr))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: calls.append(a))
    reg = registry(openai=FakeBrain)
    with pytest.raises(ep.EnginePolicyError):
        reg.instantiate("openai")
    with pytest.raises(ep.EnginePolicyError):
        ep.select_engine(reg, "openai")
    assert calls == []


def test_direct_construction_paths_are_gated(monkeypatch):
    from dodaios.agent_chat import runner_api

    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    with pytest.raises(ep.EnginePolicyError):
        runner_api.build_brain("openai", "x")
    from dodaios.agent_runtimes import gateway

    with pytest.raises(ep.EnginePolicyError):
        gateway._new_brain("claude-api", "x", "login-token")


# -- endpoint locality
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("[::1]", True),
        ("::1", True),
        ("10.0.0.5", False),
        ("192.168.1.9", False),
        ("api.openai.com", False),
        ("evil.localhost.example.com", False),
        ("", False),
        (None, False),
        ("127.0.0.1.evil.com", False),
    ],
)
def test_host_locality_never_uses_dns(host, local):
    assert ep.host_is_local(host) is local


def test_explicitly_allowed_lan_host_is_local(monkeypatch):
    monkeypatch.setenv(ep.ALLOWED_HOSTS_ENV, "gpu-box.lan, 10.0.0.5")
    assert ep.host_is_local("GPU-box.lan") and ep.host_is_local("10.0.0.5")
    assert not ep.host_is_local("10.0.0.6")


def test_remote_endpoint_on_a_local_provider_is_refused(monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    monkeypatch.setattr(
        ep, "endpoint_problem", lambda provider: "ollama endpoint host 'x' is not this machine"
    )
    with pytest.raises(ep.EnginePolicyError) as exc:
        registry(ollama=FakeBrain).instantiate("ollama")
    assert exc.value.code == "remote_endpoint"
    assert FakeBrain.built == []


# -- explicit selection and capabilities
# --------------------------------------------------------------------------


def test_explicit_selection_reports_provider_model_and_locality():
    sel = ep.select_engine(
        registry(ollama=FakeBrain, openai=FakeBrain), "ollama", "llama", needs_tools=True
    )
    assert (sel.provider, sel.model, sel.local) == ("ollama", "llama", True)
    assert sel.capabilities.tools is True and sel.capabilities.context_window == 8192
    assert ep.select_engine(registry(openai=FakeBrain), "openai").local is False


def test_selection_refuses_incompatible_or_undeclared_capabilities():
    with pytest.raises(ep.EnginePolicyError) as exc:
        ep.select_engine(registry(ollama=NoToolsBrain), "ollama", needs_tools=True)
    assert exc.value.code == "capability_missing"
    with pytest.raises(ep.EnginePolicyError) as exc:
        ep.select_engine(registry(ollama=UnknownCapsBrain), "ollama", needs_tools=True)
    assert exc.value.code == "capability_unknown"  # unknown is not supported
    with pytest.raises(ep.EnginePolicyError):
        ep.select_engine(registry(ollama=FakeBrain), "ollama", needs_vision=True)
    caps = ep.capabilities_of(UnknownCapsBrain())
    assert caps.tools is None and caps.context_window is None


def test_selection_of_missing_provider_is_actionable():
    with pytest.raises(ep.EnginePolicyError, match="not available"):
        ep.select_engine(registry(), "openai")


# -- turn ownership: late output of an old generation
# ----------------------------------------------------------------


def test_late_deltas_of_a_superseded_generation_are_dropped_and_source_closed():
    state = {"gen": 1, "closed": False}

    async def slow():
        try:
            yield BrainDelta(content="a")
            state["gen"] = 2  # a new turn started while A was still streaming
            yield BrainDelta(content="late")
            yield BrainDelta(tool_call={"name": "RunCommand"})
        finally:
            state["closed"] = True

    async def run():
        mine = state["gen"]
        return [d async for d in ep.guard_stream(slow(), lambda: state["gen"] == mine)]

    out = asyncio.run(run())
    assert [d.content for d in out] == ["a"]
    assert all(d.tool_call is None for d in out)  # the late tool call never reached the consumer
    assert state["closed"] is True


def test_a_broken_capability_probe_means_unknown_not_supported():
    class Broken(FakeBrain):
        def can_call_tools(self):  # type: ignore[override]
            raise RuntimeError("probe failed")

        supports_tools = None  # type: ignore[assignment]

    assert ep.capabilities_of(Broken()).tools is None
