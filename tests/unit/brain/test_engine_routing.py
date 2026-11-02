"""Overrides, safe fallback and measured reporting (ADR-0052): fake streams, no network."""

from __future__ import annotations

import asyncio

import pytest

from dodaios.brain import engine_policy as ep
from dodaios.brain.consumer_orchestrator import (
    Branch,
    Budget,
    ContextItem,
    Orchestrator,
    PrivacyGate,
    fan_out_report,
)
from dodaios.brain.engine_routing import (
    ActiveEngines,
    Candidate,
    FallbackRouter,
    classify_failure,
    resolve_engine,
)
from dodaios.core.protocols import BrainDelta


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    monkeypatch.setattr(ep, "endpoint_problem", lambda provider: None)


def streams(spec):
    """spec: provider -> list of deltas or an exception instance raised before/after deltas."""
    calls = []

    def stream(cand):
        calls.append(cand.provider)

        async def gen():
            plan = spec[cand.provider]
            for item in plan:
                if isinstance(item, BaseException):
                    raise item
                yield item

        return gen()

    stream.calls = calls
    return stream


def cand(provider, kind="api", **kw):
    kw.setdefault("est_cost", 0.1)
    kw.setdefault("egress_authorized", True)
    return Candidate(provider, None, kind, **kw)


def run(router, cands, spec, **kw):
    stream = streams(spec)
    return asyncio.run(router.run(cands, stream, **kw)), stream.calls


# -- overrides ----------------------------------------------------------------------------


def test_override_precedence_task_over_project_over_default():
    assert resolve_engine(("ollama", "a")).source == "default"
    assert resolve_engine(("ollama", "a"), ("local-openai", "b")).provider == "local-openai"
    choice = resolve_engine(("ollama", "a"), ("local-openai", "b"), ("ollama", "c"))
    assert (choice.provider, choice.model, choice.source) == ("ollama", "c", "task")


def test_consumer_may_override_with_any_provider():
    assert (
        resolve_engine(("ollama", None), ("claude-api", "x"), product="consumer").provider
        == "claude-api"
    )


@pytest.mark.parametrize("layer", ["default", "project", "task"])
def test_work_rejects_a_remote_layer_and_names_it_even_when_it_would_not_win(layer):
    layers = {"default": ("ollama", None), "project": None, "task": None}
    layers[layer] = ("openai", "gpt")
    if layer != "task":
        layers["task"] = ("ollama", "local")  # the remote layer is shadowed, still refused
    with pytest.raises(ep.EnginePolicyError) as exc:
        resolve_engine(layers["default"], layers["project"], layers["task"], product="work")
    assert exc.value.code == "override_rejected" and layer in str(exc.value)


def test_active_engines_report_what_actually_runs():
    active = ActiveEngines()
    choice = resolve_engine(("ollama", "m"), None, ("local-openai", "t"))
    active.set("job-1", choice)
    assert active.get("job-1").source == "task" and active.get("job-2") is None
    active.clear("job-1")
    assert active.snapshot() == {}


# -- fallback -----------------------------------------------------------------------------


def test_fallback_after_a_transient_failure_before_any_output():
    router = FallbackRouter(Budget(10))
    result, calls = run(
        router, [cand("a"), cand("b")], {"a": [TimeoutError()], "b": [BrainDelta(content="ok")]}
    )
    assert result.text == "ok" and result.provider == "b" and calls == ["a", "b"]
    assert [a.outcome for a in result.attempts] == ["failed:transient", "ok"]


def test_no_fallback_after_visible_output_and_no_splicing():
    router = FallbackRouter(Budget(10))
    result, calls = run(
        router,
        [cand("a"), cand("b")],
        {
            "a": [BrainDelta(content="Half an ans"), ConnectionError("dropped")],
            "b": [BrainDelta(content="other")],
        },
    )
    assert calls == ["a"] and result.provider == "a" and "other" not in result.text
    assert (
        result.attempts[-1].outcome == "stopped:transient"
        and "after producing output" in result.error
    )


def test_no_fallback_after_a_tool_call_so_a_side_effect_is_never_replayed():
    router = FallbackRouter(Budget(10))
    result, calls = run(
        router,
        [cand("a", tools=True), cand("b", tools=True)],
        {
            "a": [BrainDelta(tool_call={"name": "RunCommand", "input": {}}), TimeoutError()],
            "b": [BrainDelta(content="ran it again")],
        },
        needs_tools=True,
    )
    assert calls == ["a"] and result.tool_calls and result.error


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("HTTP 429 rate limit"),
        RuntimeError("insufficient credits"),
        RuntimeError("401 unauthorized"),
        ValueError("bad request"),
    ],
)
def test_quota_auth_and_fatal_failures_stop_without_a_retry_storm(exc):
    router = FallbackRouter(Budget(10))
    result, calls = run(
        router,
        [cand("a"), cand("b"), cand("c")],
        {"a": [exc], "b": [BrainDelta(content="x")], "c": [BrainDelta(content="y")]},
    )
    assert calls == ["a"] and result.attempts[0].outcome.startswith("stopped:") and result.error


def test_attempts_are_bounded():
    router = FallbackRouter(Budget(10), max_attempts=2)
    result, calls = run(router, [cand(c) for c in "abcd"], {c: [TimeoutError()] for c in "abcd"})
    assert calls == ["a", "b"] and result.error == "no engine could answer"


def test_fallback_candidates_need_egress_authorization_budget_and_capabilities():
    router = FallbackRouter(Budget(0.15))
    spec = {
        "a": [TimeoutError()],
        "b": [BrainDelta(content="b")],
        "c": [BrainDelta(content="c")],
        "d": [BrainDelta(content="d")],
        "e": [BrainDelta(content="e")],
    }
    cands = [
        cand("a", tools=True),
        cand("b", egress_authorized=False, tools=True),  # privacy gate says no
        cand("c", tools=None),  # tool support undeclared
        cand("d", authenticated=False, tools=True),
        cand("e", tools=True, est_cost=0.1),
    ]  # 0.1 + 0.1 > 0.15
    result, calls = run(router, cands, spec, needs_tools=True)
    reasons = {a.provider: a.reason for a in result.attempts if a.outcome == "skipped"}
    assert calls == ["a"] and "not authorized" in reasons["b"] and "not declared" in reasons["c"]
    assert "not authenticated" in reasons["d"] and "budget" in reasons["e"]
    assert result.error == "no engine could answer"


def test_work_never_tries_a_remote_candidate_and_a_local_failure_is_not_a_cloud_fallback(
    monkeypatch,
):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    router = FallbackRouter(Budget(10))
    result, calls = run(
        router,
        [
            cand("ollama", kind="local", est_cost=None, egress_authorized=False),
            cand("openai", egress_authorized=True),
        ],
        {"ollama": [TimeoutError()], "openai": [BrainDelta(content="cloud")]},
    )
    assert calls == ["ollama"] and result.text == ""
    assert "remote" in [a.reason for a in result.attempts if a.provider == "openai"][0]


def test_unknown_price_and_subscription_quota_do_not_fall_through_silently():
    router = FallbackRouter(Budget(10))
    result, calls = run(
        router,
        [cand("a"), cand("sub", kind="subscription", est_cost=None), cand("priced", est_cost=None)],
        {
            "a": [TimeoutError()],
            "sub": [BrainDelta(content="s")],
            "priced": [BrainDelta(content="p")],
        },
    )
    assert calls == ["a"] and not result.text
    assert FallbackRouter(Budget(10), allow_unknown_cost=True).max_attempts == 3


def test_failure_classification():
    assert classify_failure(TimeoutError()) == "transient"
    assert classify_failure(RuntimeError("HTTP 503 overloaded")) == "transient"
    assert classify_failure(RuntimeError("429 Too Many Requests")) == "quota"
    assert classify_failure(RuntimeError("Invalid API key")) == "auth"
    assert classify_failure(KeyError("x")) == "fatal"
    with pytest.raises(ValueError):
        FallbackRouter(Budget(1), max_attempts=0)


# -- measured reporting -------------------------------------------------------------------


class Timed:
    def __init__(self, delay=0.05, text="hello", usage=None):
        self.delay, self.text, self.usage = delay, text, usage

    async def complete(self, req):
        await asyncio.sleep(self.delay)
        yield BrainDelta(content=self.text, usage=self.usage)


def test_fan_out_report_measures_each_branch_and_never_adds_different_currencies():
    o = Orchestrator(PrivacyGate(), Budget(5.0))
    items = [ContextItem("hello", "task")]
    results = asyncio.run(
        o.run(
            "t",
            items,
            [
                Branch(
                    "a",
                    "codex",
                    Timed(0.05, usage={"input_tokens": 3, "output_tokens": 5}),
                    "subscription",
                    est_cost=0.0,
                ),
                Branch("b", "claude-api", Timed(0.1), "api", est_cost=0.5),
                Branch("c", "ollama", Timed(0.02), "local"),
                Branch("d", "openai", Timed(), "api", est_cost=None),  # refused: unknown price
            ],
        )
    )
    report = fan_out_report(results, o.budget)
    by = {b["branch"]: b for b in report["branches"]}
    assert by["a"]["cost_basis"] == "subscription_quota" and by["b"]["cost_basis"] == "api_charge"
    assert by["c"]["cost_basis"] == "local_resources"
    assert by["b"]["first_token_s"] >= 0.1 and by["b"]["total_s"] >= by["b"]["first_token_s"]
    assert by["a"]["usage"] == {"input_tokens": 3, "output_tokens": 5} and by["c"]["usage"] is None
    assert (
        by["d"]["status"] == "refused"
        and by["d"]["first_token_s"] is None
        and by["d"]["total_s"] is None
    )
    assert report["by_basis"] == {"subscription_quota": 1, "api_charge": 1, "local_resources": 1}
    assert report["budget_reserved"] == pytest.approx(0.5) and "total" not in report
    assert by["a"]["data_sent"]["categories"] == ["task"] and by["c"]["data_sent"]["items"] == 1
