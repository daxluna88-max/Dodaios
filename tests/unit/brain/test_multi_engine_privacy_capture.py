"""Fan-out privacy (integration study): what EVERY engine and the synthesizer receive.

The privacy switch is deliberately ON here (the pilot runs with it on, memory needs it). The manager holds
sentinel strings in the profile, the learned notebook, contacts, the per-turn wiki text, the external
turn memory and the chat history. A fake provider records the exact text of every request it gets. In a
"Chiedi a più modelli" turn, no engine and no synthesizer may receive any of them: a branch gets the
question only (ADR-0055), the synthesizer gets the branch answers and an enumerated style sentence.

Stays on the integration branch: it needs the multi-engine stack, which `it-complete` does not have yet.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from dodaios.brain import engine_policy as ep
from dodaios.brain.manager import BrainManager
from dodaios.brain.provider_registry import BrainProviderRegistry
from dodaios.core import config as cfg_mod
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig, MultiEngineConfig, MultiEngineEngineConfig
from dodaios.core.protocols import BrainDelta
from dodaios.memory.user_profile import UserProfile

_MARKS = {
    "profile": "SENTINEL-PROFILE-Zanzibar",
    "notebook": "SENTINEL-NOTEBOOK-Quokka",
    "contact": "SENTINEL-CONTACT-Marmotta",
    "wiki": "SENTINEL-WIKI-Tapiro",
    "memory": "SENTINEL-TURNMEMORY-Axolotl",
    "history": "SENTINEL-HISTORY-Gnu",
}

_PROFILE = f"""---
identity:
  name: {_MARKS["profile"]}
  preferred_address: Boss
---

# About the User

## Observations over time

<!-- curator:observations:start -->
<!-- curator:observations:end -->
"""


class Fake:
    prompts: list[str] = []
    tools_seen: list[tuple] = []

    def __init__(self, model=None):
        pass

    def estimate_cost(self, req):
        return 0.0

    async def complete(self, req):
        Fake.prompts.append(" ".join([str(req.system or "")] + [str(m.content) for m in req.messages]))
        Fake.tools_seen.append(tuple(req.tools))
        yield BrainDelta(content="risposta")


class _TurnMemory:
    async def recall(self, user_text: str) -> str:
        return f"Note: {_MARKS['memory']}"


class _Contacts:
    def render_for_prompt(self, max_chars: int = 0) -> str:
        return f"Contacts: {_MARKS['contact']} (friend)"


@pytest.mark.asyncio
async def test_no_engine_and_no_synthesizer_sees_personal_context_with_the_switch_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dodaios.memory.learning.notebook as notebook
    from dodaios.brain.turn_memory import set_turn_memory

    Fake.prompts.clear()
    Fake.tools_seen.clear()
    monkeypatch.delenv("JARVIS_PRODUCT", raising=False)
    registry = BrainProviderRegistry()
    registry._classes = {"claude-api": Fake, "openai": Fake}
    registry._loaded = True
    monkeypatch.setattr(ep, "endpoint_problem", lambda provider: None)
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda provider: "key")
    # The chat turn builds its own registry; serve the fake one (same seam as test_multi_engine_chat).
    monkeypatch.setattr("dodaios.brain.provider_registry.BrainProviderRegistry", lambda: registry)
    monkeypatch.setattr(notebook, "snapshot_block", lambda *a, **k: f"Learned: {_MARKS['notebook']}")

    path = tmp_path / "USER.md"
    path.write_text(_PROFILE, encoding="utf-8")
    cfg = JarvisConfig()
    assert cfg.brain.personal_context_to_cloud is True  # the pilot setting
    cfg.brain.multi_engine = MultiEngineConfig(
        enabled=True,
        budget=5.0,
        engines=[MultiEngineEngineConfig(provider=p, est_cost=0.5) for p in ("claude-api", "openai")],
    )
    monkeypatch.setattr("dodaios.core.config.load_config", lambda *a, **k: cfg)

    manager = BrainManager(
        config=cfg, bus=EventBus(), tools={}, user_profile=UserProfile.load(path), contacts=_Contacts()
    )
    manager._wiki_context_suffix = f"Wiki: {_MARKS['wiki']}"
    manager._history = [{"role": "user", "content": f"earlier message {_MARKS['history']}"}]
    # Positive control: this manager really would send its personal blocks in a normal turn.
    normal_prompt = manager._build_system_prompt()
    for name in ("profile", "contact", "notebook"):
        assert _MARKS[name] in normal_prompt, f"{name} is not in the normal prompt: the test would be vacuous"
    set_turn_memory(_TurnMemory())
    try:
        reply = await asyncio.wait_for(
            manager.generate("Chiedi a più modelli: che ore sono a Roma?", trace_id=uuid4(), use_history=True),
            timeout=30,
        )
    finally:
        set_turn_memory(None)

    # Two engines plus the synthesizer: the turn really went through the fan-out, so this is not vacuous.
    assert len(Fake.prompts) == 3, (reply, Fake.prompts)
    assert any("che ore sono a Roma" in p for p in Fake.prompts)
    for prompt in Fake.prompts:
        for name, mark in _MARKS.items():
            assert mark not in prompt, f"{name} sentinel reached an engine"
    assert all(tools == () for tools in Fake.tools_seen)  # branches and synthesizer run without tools
