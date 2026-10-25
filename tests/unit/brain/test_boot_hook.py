"""The optional boot hook (ADR-0046)."""
from __future__ import annotations

import sys
import types

from dodaios.brain.boot_hook import ENV_VAR, run_boot_hook


def _module(monkeypatch, name="fake_boot_mod", **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)


def test_unset_does_nothing(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert run_boot_hook("bus", "cfg") is None


def test_hook_receives_bus_and_config(monkeypatch):
    seen = []
    _module(monkeypatch, start=lambda bus, cfg: seen.append((bus, cfg)) or "ok")
    monkeypatch.setenv(ENV_VAR, "fake_boot_mod:start")
    assert run_boot_hook("bus", "cfg") == "ok" and seen == [("bus", "cfg")]


def test_failing_or_malformed_hook_does_not_raise(monkeypatch):
    def boom(bus, cfg):
        raise RuntimeError("x")
    _module(monkeypatch, boom=boom)
    for spec in ("fake_boot_mod:boom", "fake_boot_mod:missing", "no_such_module_xyz:f", "nocolon"):
        monkeypatch.setenv(ENV_VAR, spec)
        assert run_boot_hook("bus", "cfg") is None


import asyncio

from dodaios.brain.boot_hook import REQUIRED_VAR
from dodaios.safety.gate import GateRequest, get_default_gate, set_default_gate


def _review():
    gate = get_default_gate()
    assert gate is not None
    req = GateRequest("t", {}, "safe", None, "", "", "", None)  # type: ignore[arg-type]
    return asyncio.run(gate.review(req))


def test_required_hook_failing_fails_closed(monkeypatch):
    set_default_gate(None)
    monkeypatch.setenv(REQUIRED_VAR, "1")
    monkeypatch.setenv(ENV_VAR, "no_such_module_xyz:f")
    try:
        assert run_boot_hook("bus", "cfg") is None
        d = _review()
        assert d.verdict == "deny" and "not ready" in d.reason
    finally:
        set_default_gate(None)


def test_required_hook_unset_or_returning_none_fails_closed(monkeypatch):
    monkeypatch.setenv(REQUIRED_VAR, "true")
    _module(monkeypatch, quiet=lambda bus, cfg: None)
    for spec in ("", "fake_boot_mod:quiet"):
        set_default_gate(None)
        monkeypatch.setenv(ENV_VAR, spec)
        try:
            run_boot_hook("bus", "cfg")
            assert _review().verdict == "deny"
        finally:
            set_default_gate(None)


def test_required_hook_ok_leaves_the_gate_alone(monkeypatch):
    set_default_gate(None)
    monkeypatch.setenv(REQUIRED_VAR, "1")
    _module(monkeypatch, start=lambda bus, cfg: "ready")
    monkeypatch.setenv(ENV_VAR, "fake_boot_mod:start")
    assert run_boot_hook("bus", "cfg") == "ready" and get_default_gate() is None


def test_not_required_failure_installs_no_gate(monkeypatch):
    set_default_gate(None)
    monkeypatch.delenv(REQUIRED_VAR, raising=False)
    monkeypatch.setenv(ENV_VAR, "no_such_module_xyz:f")
    assert run_boot_hook("bus", "cfg") is None and get_default_gate() is None
