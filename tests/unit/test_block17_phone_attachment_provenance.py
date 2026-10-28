"""Block 17 (ADR-0062, GPT round 4, H1): a phone attachment is born UNAVAILABLE, the destination
is decided BEFORE a model sees an image, and the label travels with the record. Written, NOT run.
"""

from __future__ import annotations

import asyncio
import contextvars
from types import SimpleNamespace
from typing import Any

import pytest

from dodaios.agent_chat import attachments as chat_attachments
from dodaios.agent_chat import send_queue
from dodaios.agentic_ide import drop_analysis
from dodaios.brain import engine_policy
from dodaios.brain import provenance as prov
from dodaios.brain.drop_context import DroppedItem
from dodaios.brain.provenance import Provenance

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class _Delta:
    def __init__(self, content: str) -> None:
        self.content = content


class _Brain:
    """A vision engine that records every request it is handed."""

    supports_vision = True

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.calls = 0

    async def complete(self, _request):
        self.calls += 1
        yield _Delta("a screenshot with an error")


def _image() -> tuple[DroppedItem, str]:
    return DroppedItem(name="shot.png", mime="image/png", data=PNG), "@shot.png"


@pytest.fixture
def locality(monkeypatch: pytest.MonkeyPatch):
    """Decide locality by provider name: only 'ollama' is a verified loopback engine."""

    def fake(provider: str, brain: Any = None) -> engine_policy.Destination:
        local = provider == "ollama"
        return engine_policy.Destination("127.0.0.1" if local else None, local, "test")

    monkeypatch.setattr(engine_policy, "destination_of", fake)


@pytest.fixture
def consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_policy, "work_active", lambda product=None: False)


@pytest.fixture
def work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_policy, "work_active", lambda product=None: True)


@pytest.fixture(autouse=True)
def fast_resolve(monkeypatch: pytest.MonkeyPatch):
    """Never resolve a real engine: tests pass ``brain=`` or patch the resolver."""
    monkeypatch.setattr(drop_analysis, "_resolve_vision_brain", lambda **_kw: None)


# --------------------------------------------- (1) destination BEFORE the model call


@pytest.mark.parametrize("label", [Provenance.UNAVAILABLE, Provenance.RESTRICTED])
async def test_unlabelled_image_is_never_sent_to_a_remote_vision_engine(
    consumer, locality, label
) -> None:
    remote = _Brain("openai")
    [out] = await drop_analysis.analyze([_image()], brain=remote, provenance=label)
    assert remote.calls == 0  # the image bytes never left
    assert out.described_by == drop_analysis.BY_NONE and out.detail == ""
    assert "remote" in out.note and "not described" in out.note  # said out loud, no downgrade
    assert out.provenance == label.value


async def test_unavailable_image_may_be_described_by_a_verified_local_engine(
    consumer, locality
) -> None:
    local = _Brain("ollama")
    [out] = await drop_analysis.analyze(
        [_image()], brain=local, provenance=Provenance.UNAVAILABLE)
    assert local.calls == 1 and out.detail
    assert out.provenance == "unavailable"  # the description keeps the label of its source


async def test_a_positively_unrestricted_image_may_use_a_remote_engine_in_consumer(
    consumer, locality
) -> None:
    remote = _Brain("openai")
    [out] = await drop_analysis.analyze(
        [_image()], brain=remote, provenance=Provenance.UNRESTRICTED)
    assert remote.calls == 1 and out.provenance == "unrestricted"


async def test_work_requires_a_verified_local_engine_whatever_the_label(work, locality) -> None:
    remote = _Brain("openai")
    [out] = await drop_analysis.analyze(
        [_image()], brain=remote, provenance=Provenance.UNRESTRICTED)
    assert remote.calls == 0 and "remote" in out.note
    local = _Brain("ollama")
    await drop_analysis.analyze([_image()], brain=local, provenance=Provenance.UNRESTRICTED)
    assert local.calls == 1


async def test_an_engine_with_no_name_is_treated_as_remote(consumer, locality) -> None:
    nameless = _Brain("")
    await drop_analysis.analyze([_image()], brain=nameless, provenance=Provenance.UNAVAILABLE)
    assert nameless.calls == 0


async def test_resolution_asks_for_a_local_engine_when_the_label_requires_it(
    consumer, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[bool] = []

    def spy(*, local_only: bool = False):
        asked.append(local_only)

    monkeypatch.setattr(drop_analysis, "_resolve_vision_brain", spy)
    await drop_analysis.analyze([_image()], provenance=Provenance.UNAVAILABLE)
    await drop_analysis.analyze([_image()], provenance=Provenance.UNRESTRICTED)
    assert asked == [True, False]


def test_provenance_is_a_required_argument_of_analysis_and_ingest() -> None:
    with pytest.raises(TypeError):
        asyncio.run(drop_analysis.analyze([_image()]))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        asyncio.run(chat_attachments.ingest(".", uploads=[]))  # type: ignore[call-arg]


async def test_ingest_passes_the_label_to_the_analysis_before_any_model(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def spy(items, *, provenance, **_kw):
        seen["label"] = provenance
        return []

    monkeypatch.setattr(drop_analysis, "analyze", spy)
    await chat_attachments.ingest(
        tmp_path, uploads=[("shot.png", PNG)], provenance=Provenance.UNAVAILABLE)
    assert seen["label"] is Provenance.UNAVAILABLE


# --------------------------------------------- (2) the label survives the record


def test_the_wire_form_of_an_analysis_carries_and_restores_the_label() -> None:
    row = drop_analysis.DropAnalysis(
        name="a", reference="@a", kind="text", detail="x", provenance="unavailable")
    assert row.to_dict()["provenance"] == "unavailable"
    assert drop_analysis.DropAnalysis.from_dict(row.to_dict()).provenance == "unavailable"


@pytest.mark.parametrize("bad", [None, "", "nonsense", 7, "UNRESTRICTED "])
def test_an_old_or_malformed_wire_entry_reads_unavailable_never_unrestricted(bad) -> None:
    raw: dict[str, Any] = {"name": "a", "kind": "text"}
    if bad is not None:
        raw["provenance"] = bad
    got = drop_analysis.DropAnalysis.from_dict(raw).provenance
    # Only a well-formed value is honoured; "UNRESTRICTED " parses (strip/lower) by design.
    assert got == ("unrestricted" if bad == "UNRESTRICTED " else "unavailable")


def test_a_queued_record_serialises_its_label_and_an_old_row_is_unavailable() -> None:
    item = send_queue._Queued("q1", "hi", [{"name": "a"}], None, "Europe/Rome", 3, "unavailable")  # noqa: SLF001
    again = send_queue._Queued.from_dict(item.to_dict())  # noqa: SLF001
    assert again.provenance == "unavailable" and again.queue_id == "q1" and again.stop_ticket == 3
    old = send_queue._Queued.from_dict({"queue_id": "q0", "text": "t"})  # noqa: SLF001
    assert old.provenance == "unavailable"
    assert send_queue._Queued("q", "t", None, None).provenance == "unavailable"  # noqa: SLF001


class _Chat:
    """The slice of the chat service the queue uses; records what each send received."""

    def __init__(self) -> None:
        self.busy: set[str] = set()
        self.sent: list[dict[str, Any]] = []
        self.store = SimpleNamespace(
            get_session=lambda sid: SimpleNamespace(session_id=sid, surface="society"),
            queue_notice_events=lambda sid: [],
        )

    def is_running(self, sid: str) -> bool:
        return sid in self.busy

    async def send(self, sid, text, attachments=None, *, tool_choices=None, provenance=None):
        if sid in self.busy:
            from dodaios.agent_chat.service import SessionBusy

            raise SessionBusy(sid)
        # What the turn would see: the ambient request context must NOT be the source.
        self.sent.append({"text": text, "provenance": provenance,
                          "ambient": prov.turn_provenance()})
        return "turn"

    async def post_notice(self, sid, payload) -> None:
        return None


async def test_a_queued_message_is_dispatched_with_the_label_of_its_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(send_queue, "_POLL_S", 0.01)
    chat = _Chat()
    chat.busy.add("s1")
    # The HTTP request that queued it had NO provenance in its context at all.
    turn_id, queue_id = await send_queue.send_or_queue(
        chat, "s1", "look", [{"name": "a.png"}], provenance="unavailable")
    assert turn_id == "" and queue_id
    chat.busy.clear()
    for _ in range(200):
        if chat.sent:
            break
        await asyncio.sleep(0.01)
    assert chat.sent and chat.sent[0]["provenance"] == "unavailable"


async def test_an_immediate_send_also_hands_the_label_to_the_service() -> None:
    chat = _Chat()
    await send_queue.send_or_queue(chat, "s1", "look", None, provenance="unavailable")
    assert chat.sent[0]["provenance"] == "unavailable"


def test_the_turn_label_comes_from_the_record_and_only_tightens() -> None:
    unavailable = drop_analysis.DropAnalysis(name="a", reference="r", kind="image",
                                             provenance="unavailable")
    clean = drop_analysis.DropAnalysis(name="b", reference="r", kind="image",
                                       provenance="unrestricted")
    assert chat_attachments.turn_label([unavailable]) is Provenance.UNAVAILABLE
    assert chat_attachments.turn_label([clean, unavailable]) is Provenance.UNAVAILABLE
    assert chat_attachments.turn_label([clean], "restricted") is Provenance.RESTRICTED
    assert chat_attachments.turn_label([clean]) is None  # never loosens, never claims clean
    assert chat_attachments.turn_label(None) is None
    assert chat_attachments.turn_label([SimpleNamespace(name="x")]) is Provenance.UNAVAILABLE


def test_a_turn_context_copy_carries_the_label_without_touching_the_caller() -> None:
    ctx = contextvars.copy_context()
    ctx.run(prov.set_turn_provenance, Provenance.UNAVAILABLE)
    assert ctx.run(prov.turn_provenance) is Provenance.UNAVAILABLE
    assert ctx.run(prov.remote_speech_blocked, prov.label("hello", Provenance.UNRESTRICTED))
    assert prov.turn_provenance() is Provenance.UNAVAILABLE  # caller: no state at all


# --------------------------------------------- (3) downstream, existing fail-closed rules


def test_attachment_derived_text_is_refused_by_remote_doors_until_declared_clean() -> None:
    text = prov.label("a screenshot with an error", Provenance.UNAVAILABLE)
    assert prov.remote_speech_blocked(text)
    assert not prov.remote_replayable("unavailable")
    assert not prov.remote_replayable(None)  # an old row
