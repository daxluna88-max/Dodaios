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
