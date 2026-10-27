"""Block 7 provenance (ADR-0050). Written, not run."""
from __future__ import annotations

import pytest

from dodaios.brain import provenance
from dodaios.brain.consumer_orchestrator import ContextItem, PrivacyGate
from dodaios.brain.turn_memory import MemoryNote, recall_block, set_turn_memory


@pytest.fixture(autouse=True)
def _clean():
    provenance.clear()
    set_turn_memory(None)
    yield
    provenance.clear()
    set_turn_memory(None)


def _prep(items, local=False):
    return PrivacyGate().prepare(
        items, turn_id="t", branch_id="b", destination="x", local=local
    )


def test_derived_item_inherits_local_only():
    src = ContextItem("secret mail body", "file", local_only=True)
    summary = ContextItem.derive([src], "short summary", "task")
    assert summary.local_only
    text, rec = _prep([summary])
    assert text == "" and rec.withheld_local_only == 1


def test_derived_item_cannot_launder_category():
    src = ContextItem("contents of a private file", "file")
    summary = ContextItem.derive([src], "summary", "task")  # relabelled as plain task
    assert "file" in summary.derived_from
    text, _ = _prep([summary])
    assert text == ""  # "file" is not an allowed category
    text_local, _ = _prep([summary], local=True)
    assert text_local == "summary"


def test_plain_task_still_passes():
    text, _ = _prep([ContextItem("hello", "task")])
    assert text == "hello"


def test_restricted_registry_matches_fragment_and_whole():
    provenance.mark_restricted("Your account number is private. Call the bank tomorrow.")
    assert provenance.is_restricted("Call the bank tomorrow.")
    assert provenance.is_restricted("  YOUR ACCOUNT number is private. call the bank tomorrow. ")
    assert not provenance.is_restricted("It will rain tomorrow in Rome.")


def test_tts_is_local():
    class T:
        def __init__(self, name):
            self.name = name

    class Wrapped:
        def __init__(self, inner):
            self._inner = inner

    class Pair:
        def __init__(self, a, b):
            self._primary, self._fallback = a, b

    assert provenance.tts_is_local(Wrapped(T("piper-local")))
    assert not provenance.tts_is_local(Wrapped(T("elevenlabs")))
    assert not provenance.tts_is_local(Pair(T("piper-local"), T("elevenlabs")))
    assert provenance.tts_is_local(Pair(T("piper-local"), T("piper-local")))
    assert not provenance.tts_is_local(None)


@pytest.mark.asyncio
async def test_metered_tts_skips_remote_for_restricted_text():
    from dodaios.speech.usage_meter import MeteredTTS

    class Remote:
        name = "elevenlabs"
        called = False

        async def synthesize(self, text, voice=None, language_code=None):
            Remote.called = True
            yield b"x"

    provenance.mark_restricted("this answer came from a local only note")
    tts = MeteredTTS(Remote(), None)
    out = [c async for c in tts.synthesize("this answer came from a local only note")]
    assert out == [] and Remote.called is False


class _Labeled:
    async def recall(self, user_text):
        return "- plain unlabeled note"

    async def recall_notes(self, user_text):
        return [MemoryNote("shareable note"), MemoryNote("private note", local_only=True)]


class _Unlabeled:
    async def recall(self, user_text):
        return "- note of unknown provenance"


@pytest.mark.asyncio
async def test_remote_recall_drops_local_only_notes():
    set_turn_memory(_Labeled())
    block = await recall_block("q", remote=True)
    assert "shareable note" in block and "private note" not in block


@pytest.mark.asyncio
async def test_remote_recall_withholds_unlabeled_memory():
    set_turn_memory(_Unlabeled())
    assert await recall_block("q", remote=True) == ""
    assert "unknown provenance" in await recall_block("q")  # local use unchanged
