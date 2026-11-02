"""The dated plans from the notebooks reach the system prompt only when the privacy switch is on."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dodaios.brain.manager import BrainManager

_MARK = "SENTINEL-UPCOMING-Pangolino"


class _Bare(BrainManager):
    """A manager with no wiring: any private collaborator that was never set reads as ``None``."""

    def __getattr__(self, name: str):
        if name.startswith("_") and not name.startswith("__"):
            return None
        raise AttributeError(name)


def _prompt(monkeypatch: pytest.MonkeyPatch, *, allowed: bool) -> str:
    manager = _Bare.__new__(_Bare)
    manager._config = SimpleNamespace(
        brain=SimpleNamespace(max_tokens=1024, personal_context_to_cloud=allowed)
    )
    monkeypatch.setattr(_Bare, "_upcoming_block", lambda self: f"## Coming up\n{_MARK}")
    monkeypatch.setattr(_Bare, "_cache_optimized", lambda self: False)
    return manager._build_system_prompt()


def test_switch_on_puts_the_plans_in_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _MARK in _prompt(monkeypatch, allowed=True)


def test_switch_off_keeps_the_plans_out(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _MARK not in _prompt(monkeypatch, allowed=False)
