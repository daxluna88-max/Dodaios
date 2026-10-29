"""The personal blocks stay out of the model prompt when the privacy switch is off."""
from __future__ import annotations

from types import SimpleNamespace

from dodaios.brain.manager import _personal_context_allowed


def _cfg(value):
    return SimpleNamespace(brain=SimpleNamespace(personal_context_to_cloud=value))


def test_default_when_config_is_missing_or_incomplete() -> None:
    assert _personal_context_allowed(None) is True
    assert _personal_context_allowed(SimpleNamespace()) is True
    assert _personal_context_allowed(SimpleNamespace(brain=SimpleNamespace())) is True


def test_the_switch_value_wins() -> None:
    assert _personal_context_allowed(_cfg(True)) is True
    assert _personal_context_allowed(_cfg(False)) is False


def test_real_config_default_is_on() -> None:
    from dodaios.core.config import BrainConfig

    assert BrainConfig().personal_context_to_cloud is True
    assert _personal_context_allowed(SimpleNamespace(brain=BrainConfig())) is True
    assert _personal_context_allowed(
        SimpleNamespace(brain=BrainConfig(personal_context_to_cloud=False))
    ) is False
