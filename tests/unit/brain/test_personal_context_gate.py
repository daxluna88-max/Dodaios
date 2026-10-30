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


def _dispatcher_tools(allowed: bool) -> set[str]:
    from dodaios.brain.manager import BrainManager

    manager = BrainManager.__new__(BrainManager)
    names = ["whoami", "wiki-list", "wiki-page-read", "wiki-recall", "contact-lookup", "search_web", "remember"]
    manager._tools = {name: SimpleNamespace(name=name) for name in names}
    manager._tool_executor = None
    manager._config = SimpleNamespace(
        brain=SimpleNamespace(max_tokens=1024, personal_context_to_cloud=allowed)
    )
    manager._build_system_prompt = lambda: "BASE"  # type: ignore[method-assign]
    manager._plugin_usage_cards_block = lambda tools: ""  # type: ignore[method-assign]
    manager._shortlist_tools = lambda tools, *a, **k: (tools, frozenset())  # type: ignore[method-assign]
    return set(manager._build_dispatcher(object())._tools)


def test_tools_that_read_personal_data_are_withdrawn_when_the_switch_is_off() -> None:
    shown = _dispatcher_tools(allowed=False)
    assert shown == {"search_web", "remember"}


def test_all_tools_stay_when_the_switch_is_on() -> None:
    assert {"whoami", "wiki-recall", "contact-lookup"} <= _dispatcher_tools(allowed=True)
