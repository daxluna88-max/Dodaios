"""Function coverage under the strict offline profile (ADR-0060). Written, not run."""

from __future__ import annotations

import sys

import pytest

from dodaios.core import egress_guard as eg


@pytest.fixture(autouse=True)
def _clean():
    eg.uninstall()
    yield
    eg.uninstall()


def test_profile_off_refuses_nothing():
    assert eg.tool_refusal("run_shell") is None
    assert eg.child_refusal("x") is None
    eg.require_child("x", ["prog"])


@pytest.mark.parametrize("name", ["run_shell", "cli_git", "navigate", "gmail", "search_web",
                                  "spawn_worker", "github/create_issue"])
def test_uncoverable_tools_refused_when_active(name):
    eg.install()
    assert "strict offline profile" in (eg.tool_refusal(name) or "")


def test_unclassified_in_process_tool_not_refused():
    eg.install()
    assert eg.tool_refusal("wiki-list") is None


def test_child_refused_unless_proven_covered(monkeypatch):
    eg.install()
    with pytest.raises(eg.OfflineUnavailable):
        eg.require_child("a worker", ["ffmpeg", "-i", "x"])
    argv = [sys.executable, "-m", "dodaios.fake_child"]
    env = {eg.POLICY_ENV: "local-only"}
    with pytest.raises(eg.OfflineUnavailable):  # module not verified
        eg.require_child("c", argv, env)
    monkeypatch.setattr(eg, "GUARDED_CHILD_MODULES", frozenset({"dodaios.fake_child"}))
    eg.require_child("c", argv, env)
    with pytest.raises(eg.OfflineUnavailable):  # policy not inherited
        eg.require_child("c", argv, {})


def test_status_never_claims_os_enforcement():
    eg.install()
    st = eg.status()
    assert st["python_guard_enforced"] is True
    assert st["os_enforcement_verified"] is False
    assert st["unavailable"] and st["unclassified"]
    assert eg.child_environment({})[eg.POLICY_ENV] == "local-only"
