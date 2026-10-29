"""The model prompt carries the personal blocks only while the privacy switch allows it."""
from __future__ import annotations

from pathlib import Path

from dodaios.brain.manager import BrainManager
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig
from dodaios.memory.user_profile import UserProfile

_PROFILE = """---
identity:
  name: SENTINEL-NAME-Zanzibar
  preferred_address: Boss
---

# About the User

## Observations over time

<!-- curator:observations:start -->
<!-- curator:observations:end -->
"""


def _manager(tmp_path: Path, *, allowed: bool) -> BrainManager:
    path = tmp_path / "USER.md"
    path.write_text(_PROFILE, encoding="utf-8")
    config = JarvisConfig()
    config.brain.personal_context_to_cloud = allowed
    return BrainManager(
        config=config, bus=EventBus(), tools={}, user_profile=UserProfile.load(path)
    )


def test_profile_is_in_the_prompt_by_default(tmp_path: Path) -> None:
    prompt = _manager(tmp_path, allowed=True)._build_system_prompt()
    assert "SENTINEL-NAME-Zanzibar" in prompt


def test_profile_is_left_out_when_the_switch_is_off(tmp_path: Path) -> None:
    prompt = _manager(tmp_path, allowed=False)._build_system_prompt()
    assert "SENTINEL-NAME-Zanzibar" not in prompt


def test_the_switch_applies_live_without_a_new_manager(tmp_path: Path) -> None:
    manager = _manager(tmp_path, allowed=True)
    assert "SENTINEL-NAME-Zanzibar" in manager._build_system_prompt()
    manager._config.brain.personal_context_to_cloud = False
    assert "SENTINEL-NAME-Zanzibar" not in manager._build_system_prompt()
    manager._config.brain.personal_context_to_cloud = True
    assert "SENTINEL-NAME-Zanzibar" in manager._build_system_prompt()
