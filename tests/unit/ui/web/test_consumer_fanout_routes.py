"""Consumer fan-out route (ADR-0050): fake providers, real registry; no network or keys."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dodaios.brain import engine_policy as ep
from dodaios.brain.provider_registry import BrainProviderRegistry
from dodaios.core import config as cfg_mod
from dodaios.core.protocols import BrainDelta
from dodaios.ui.web import consumer_fanout_routes as routes


class Fake:
    prompts: list[str] = []
    built: list[str] = []

    def __init__(self, model=None):
        Fake.built.append(model or "")

    def estimate_cost(self, req):
        return 0.0

    async def complete(self, req):
        Fake.prompts.append(" ".join(str(m.content) for m in req.messages))
        assert req.tools == ()
        yield BrainDelta(content="answer")


class Broken(Fake):
    def __init__(self, model=None):
        raise RuntimeError("no such model")


@pytest.fixture
def client(monkeypatch):
    Fake.prompts.clear()
    Fake.built.clear()
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    registry = BrainProviderRegistry()
    registry._classes = {
        "claude-api": Fake,
        "openai": Fake,
        "codex": Fake,
        "ollama": Fake,
        "bad": Broken,
    }
    registry._loaded = True
    monkeypatch.setattr("dodaios.brain.provider_registry.BrainProviderRegistry", lambda: registry)
    monkeypatch.setattr(ep, "endpoint_problem", lambda provider: None)
    monkeypatch.setattr(
        cfg_mod, "get_provider_secret", lambda provider: "key" if provider != "openai" else None
    )
    monkeypatch.setattr(Fake, "subscription_connected", staticmethod(lambda: True), raising=False)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def body(**kw):
    base = {
        "task": "Plan a trip to Rome",
        "context": [
            {"text": "my IBAN is private", "category": "note", "local_only": True},
            {"text": "diary about health", "category": "history"},
            {"text": "mail dax@example.com", "category": "task"},
        ],
        "branches": [{"provider": "claude-api", "est_cost": 0.5}, {"provider": "ollama"}],
        "budget": 2.0,
    }
    base.update(kw)
    return base


def test_connection_kinds():
    assert routes.connection_kind("ollama") == "local"
    assert routes.connection_kind("codex") == "subscription"
    assert routes.connection_kind("claude-api") == "api"


def test_only_authorized_context_reaches_the_remote_branch_and_the_report_is_measured(client):
    resp = client.post("/api/consumer/fanout", json=body())
    assert resp.status_code == 200
    data = resp.json()
    by = {b["provider"]: b for b in data["branches"]}
    assert by["claude-api"]["status"] == "ok" and by["ollama"]["status"] == "ok"
    remote = next(p for p in Fake.prompts if "Rome" in p and "IBAN" not in p)
    assert "diary" not in remote and "dax@example.com" not in remote  # category + redaction
    assert any("IBAN" in p for p in Fake.prompts)  # the local branch keeps everything
    assert [e["destination"] for e in data["egress"]] == ["claude-api"]
    assert data["egress"][0]["withheld_local_only"] == 1
    assert data["report"]["by_basis"] == {
        "subscription_quota": 0,
        "api_charge": 1,
        "local_resources": 1,
    }
    assert data["report"]["budget_reserved"] == pytest.approx(0.5)
    assert "IBAN" not in str(data["egress"])  # metadata only


def test_budget_unknown_price_and_missing_credentials_are_refused_not_charged(client):
    over = client.post(
        "/api/consumer/fanout",
        json=body(
            branches=[
                {"provider": "claude-api", "est_cost": 1.5},
                {"provider": "codex", "est_cost": 1.0},
            ],
            budget=2.0,
        ),
    ).json()
    assert [b["status"] for b in over["branches"]] == ["ok", "refused"]
    unknown = client.post(
        "/api/consumer/fanout", json=body(branches=[{"provider": "claude-api"}])
    ).json()
    assert unknown["branches"][0]["status"] == "refused"
    nokey = client.post(
        "/api/consumer/fanout", json=body(branches=[{"provider": "openai", "est_cost": 0.1}])
    ).json()
    assert (
        nokey["branches"][0]["status"] == "refused"
        and "not authenticated" in nokey["branches"][0]["reason"]
    )


def test_a_subscription_is_not_api_access(client):
    data = client.post(
        "/api/consumer/fanout",
        json=body(branches=[{"provider": "codex", "est_cost": 0.0, "needs_api": True}]),
    ).json()
    assert (
        data["branches"][0]["status"] == "refused"
        and "not API access" in data["branches"][0]["reason"]
    )


def test_unknown_and_unbuildable_providers_are_reported_per_branch(client):
    data = client.post(
        "/api/consumer/fanout",
        json=body(
            branches=[
                {"provider": "nope"},
                {"provider": "bad", "est_cost": 0.1},
                {"provider": "ollama"},
            ]
        ),
    ).json()
    by = {b["provider"]: b for b in data["branches"]}
    assert "not an installed provider" in by["nope"]["reason"]
    assert by["bad"]["status"] == "refused" and "RuntimeError" in by["bad"]["reason"]
    assert by["ollama"]["status"] == "ok"


def test_work_product_is_refused_with_403_and_nothing_is_built(client, monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    resp = client.post("/api/consumer/fanout", json=body())
    assert resp.status_code == 403 and "Work" in resp.json()["detail"]
    assert Fake.built == [] and Fake.prompts == []


@pytest.mark.parametrize(
    "patch",
    [
        {"branches": []},
        {"task": ""},
        {"budget": -1},
        {"max_concurrent": 0},
        {"branches": [{"provider": "ollama"}] * 7},
        {"context": [{"text": "x"}] * 21},
    ],
)
def test_request_limits_are_enforced(client, patch):
    assert client.post("/api/consumer/fanout", json=body(**patch)).status_code == 422


def test_status_lists_engines_with_kind_and_authentication_without_contacting_them(client):
    data = client.get("/api/consumer/status").json()
    assert data["available"] is True
    rows = {r["id"]: r for r in data["providers"]}
    assert rows["ollama"] == {"id": "ollama", "kind": "local", "local": True, "authenticated": True}
    assert rows["codex"]["kind"] == "subscription" and rows["codex"]["authenticated"] is True
    assert rows["claude-api"]["kind"] == "api" and rows["claude-api"]["authenticated"] is True
    assert rows["openai"]["authenticated"] is False  # no stored key
    assert Fake.built == [] and Fake.prompts == []  # nothing was built or called


def test_status_under_work_says_fanout_is_not_available_and_lists_nothing(client, monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    data = client.get("/api/consumer/status").json()
    assert data == {"available": False, "reason": "work_local_only", "providers": []}
