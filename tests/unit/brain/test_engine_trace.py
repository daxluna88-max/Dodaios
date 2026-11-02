"""``BrainManager.engine_trace``: names only, and honest about Codex."""
from __future__ import annotations

from types import SimpleNamespace

from dodaios.brain.manager import BrainManager


def _manager(available: list[str], *, codex_tools: bool = False) -> BrainManager:
    m = BrainManager.__new__(BrainManager)
    m._active_name = "openrouter"
    m._registry = SimpleNamespace(available=lambda: available)
    m._configured_fallbacks = []
    m._dead_providers = set()
    m._router_lead_key = ("keep", None)
    m._switched_to = None
    m._config = SimpleNamespace(
        brain=SimpleNamespace(
            primary="openrouter",
            deep_brain=None,
            router=None,
            worker=SimpleNamespace(provider="openrouter", model="qwen/qwen3.8-flash"),
            routing=SimpleNamespace(intelligent_router=True),
        )
    )
    m._fast_model = lambda name: "fast-" + name  # type: ignore[method-assign]
    m._deep_model = lambda name: "deep-" + name  # type: ignore[method-assign]
    m._keep_off_voice_key = lambda chain, **kw: chain  # type: ignore[method-assign]
    m._brain_can_call_tools = lambda p, model: codex_tools if p == "codex" else True  # type: ignore[method-assign]
    m._active_can_call_tools = lambda: True  # type: ignore[method-assign]
    return m


def test_trace_lists_chains_and_tiers_without_secrets() -> None:
    trace = _manager(["openrouter", "gemini"]).engine_trace()
    assert trace["active"] == "openrouter"
    assert trace["worker"]["model"] == "qwen/qwen3.8-flash"
    assert trace["chains"]["fast"][0] == {"provider": "openrouter", "model": "fast-openrouter"}
    assert {"provider": "gemini", "model": "fast-gemini"} in trace["chains"]["fast"]
    assert "key" not in str(trace).lower()


def test_codex_without_tools_is_explained_not_silently_missing() -> None:
    trace = _manager(["openrouter", "codex"]).engine_trace()
    assert trace["codex"]["on_chain"] is False
    assert "cannot emit tool calls" in trace["codex"]["reason"]


def test_codex_not_installed_is_reported_as_such() -> None:
    trace = _manager(["openrouter"]).engine_trace()
    assert trace["codex"]["available"] is False
    assert "not available" in trace["codex"]["reason"]


def test_the_trace_leaves_the_turn_state_alone() -> None:
    m = _manager(["openrouter"])
    m.engine_trace()
    assert m._router_lead_key == ("keep", None)


def test_engines_route_returns_the_trace_and_503_without_a_brain() -> None:
    import pytest
    from fastapi import HTTPException

    from dodaios.ui.web.settings_routes import get_engines

    app = SimpleNamespace(state=SimpleNamespace(brain=_manager(["openrouter"])))
    assert get_engines(SimpleNamespace(app=app))["active"] == "openrouter"
    with pytest.raises(HTTPException) as err:
        get_engines(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(brain=None))))
    assert err.value.status_code == 503
