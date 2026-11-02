"""IP-9 (offline): the WHOLE request a provider receives, with the privacy switch off and on.

A fake provider records the exact ``BrainRequest`` (system prompt, messages, tool list). The profile,
the learned notebook, the contacts and the per-turn wiki text carry sentinel strings. With
``personal_context_to_cloud = false`` no sentinel may appear anywhere in the request and the five tools
that read stored personal data are absent; with it on, the sentinels and the tools are present.

Paths this does NOT cover, by design (stated to the person in Settings): the chat history of the
current chat, and the result of any other tool (a file read, a web search).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dodaios.brain.manager import _PERSONAL_READ_TOOLS, BrainManager
from dodaios.core.bus import EventBus
from dodaios.core.config import JarvisConfig
from dodaios.core.protocols import BrainDelta, BrainRequest
from dodaios.memory.user_profile import UserProfile

_PROFILE_MARK = "SENTINEL-PROFILE-Zanzibar"
_NOTEBOOK_MARK = "SENTINEL-NOTEBOOK-Quokka"
_CONTACT_MARK = "SENTINEL-CONTACT-Marmotta"
_WIKI_MARK = "SENTINEL-WIKI-Tapiro"
_MEMORY_MARK = "SENTINEL-TURNMEMORY-Axolotl"
_MARKS = (_PROFILE_MARK, _NOTEBOOK_MARK, _CONTACT_MARK, _WIKI_MARK, _MEMORY_MARK)

_PROFILE = f"""---
identity:
  name: {_PROFILE_MARK}
  preferred_address: Boss
---

# About the User

## Observations over time

<!-- curator:observations:start -->
<!-- curator:observations:end -->
"""


class _RecordingBrain:
    name = "fake"
    context_window = 8192
    supports_tools = True
    supports_vision = False

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="stop")


class _Tool:
    risk_tier = "safe"

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} tool"
        self.schema = {"type": "object", "properties": {}}

    async def execute(self, args, ctx):  # pragma: no cover - never called here
        raise AssertionError("the fake provider never calls tools")


class _TurnMemory:
    async def recall(self, user_text: str) -> str:
        return f"Note: {_MEMORY_MARK}"


class _Contacts:
    def render_for_prompt(self, max_chars: int = 0) -> str:
        return f"Contacts: {_CONTACT_MARK} (friend)"


def _blob(req: BrainRequest) -> str:
    """Every string the provider could see: messages, tool schemas, system prompt, anything else."""
    return json.dumps(
        getattr(req, "__dict__", None) or str(req), default=lambda o: getattr(o, "__dict__", str(o))
    )


async def _request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, allowed: bool) -> BrainRequest:
    import dodaios.memory.learning.notebook as notebook

    monkeypatch.setattr(notebook, "snapshot_block", lambda *a, **k: f"Learned: {_NOTEBOOK_MARK}")
    path = tmp_path / "USER.md"
    path.write_text(_PROFILE, encoding="utf-8")
    config = JarvisConfig()
    config.brain.primary = "fake"
    config.brain.personal_context_to_cloud = allowed
    names = [*sorted(_PERSONAL_READ_TOOLS), "search_web"]
    manager = BrainManager(
        config=config,
        bus=EventBus(),
        tools={n: _Tool(n) for n in names},
        user_profile=UserProfile.load(path),
        contacts=_Contacts(),
        # Tools are only offered to the provider when an executor exists; the fake never calls one.
        tool_executor=SimpleNamespace(),  # type: ignore[arg-type]
    )
    brain = _RecordingBrain()
    manager._build_fallback_chain = lambda _l: [("fake", "fake-model")]  # type: ignore[method-assign]
    manager._get_brain = lambda _n, _m: brain  # type: ignore[method-assign]
    manager._wiki_context_suffix = f"Wiki: {_WIKI_MARK}"
    from dodaios.brain.turn_memory import set_turn_memory

    set_turn_memory(_TurnMemory())  # the external per-turn memory (ADR-0045) rides the same suffix
    try:
        await manager.generate("Che ore sono?", trace_id=uuid4(), use_history=False)
    finally:
        set_turn_memory(None)
    assert brain.requests, "the fake provider received no request"
    assert brain.requests[-1].tools, "no tools reached the provider: the tool assertions would be vacuous"
    return brain.requests[-1]


def _tool_names(req: BrainRequest) -> set[str]:
    out: set[str] = set()
    for tool in getattr(req, "tools", None) or []:
        if isinstance(tool, dict):
            out.add(str(tool.get("name") or tool.get("function", {}).get("name")))
        else:
            out.add(str(getattr(tool, "name", tool)))
    return out


@pytest.mark.asyncio
async def test_switch_off_leaves_no_personal_text_and_no_personal_read_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = await _request(tmp_path, monkeypatch, allowed=False)
    blob = _blob(req)
    for mark in _MARKS:
        assert mark not in blob, mark
    assert not (_tool_names(req) & _PERSONAL_READ_TOOLS)
    assert "search_web" in _tool_names(req)  # other tools stay: the gate is not "no tools"


@pytest.mark.asyncio
async def test_switch_on_sends_the_personal_text_and_the_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = await _request(tmp_path, monkeypatch, allowed=True)
    blob = _blob(req)
    for mark in _MARKS:
        assert mark in blob, mark
    assert _PERSONAL_READ_TOOLS <= _tool_names(req)
