"""Block 19 (ADR-0062, GPT round 6, J2): the STORED history carries a durable typed label and a
remote destination is shown only positively UNRESTRICTED events. Written, NOT run.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from dodaios.agent_chat import dodaios_harness, history_labels, runner_api, runner_brain
from dodaios.agent_chat.service import AgentChatService
from dodaios.brain import engine_policy
from dodaios.brain.provenance import Provenance
from dodaios.core.protocols import BrainDelta

R, U, A = "restricted", "unrestricted", "unavailable"


@pytest.fixture(autouse=True)
def locality(monkeypatch: pytest.MonkeyPatch):
    def fake(provider: str, brain: Any = None) -> engine_policy.Destination:
        local = provider == "ollama"
        return engine_policy.Destination("127.0.0.1" if local else None, local, "test")

    monkeypatch.setattr(engine_policy, "destination_of", fake)
    monkeypatch.setattr(engine_policy, "work_active", lambda product=None: False)
    monkeypatch.setattr(engine_policy, "require_local_inference", lambda *a, **k: None)


def ev(kind: str, label: str | None, seq: int = 0, **payload: Any) -> dict[str, Any]:
    if label is not None:
        payload["provenance"] = label
    return {"seq": seq, "ts_ms": 0, "kind": kind, "payload": payload}


def history() -> list[dict[str, Any]]:
    """A local engine read a restricted attachment; then a clean exchange, a tool round, reports."""
    return [
        ev("user_message", R, 1, text="SECRET-ATTACHMENT-TEXT summarise"),
        ev("assistant_text", R, 2, text="SECRET-SUMMARY", turn_id="t1"),
        ev("turn_finished", None, 3, turn_id="t1", status="done"),
        ev("user_message", U, 4, text="clean question"),
        ev("assistant_text", U, 5, text="clean answer", turn_id="t2"),
        ev("tool_call", R, 6, call_id="c1", name="Read", input={}, turn_id="t3"),
        ev("tool_result", R, 7, call_id="c1", name="Read", output="SECRET-TOOL", turn_id="t3"),
        ev("notice", R, 8, kind="delegation_report", report="SECRET-REPORT"),
        ev("notice", U, 9, kind="delegation_report", report="clean report"),
    ]


def dump(messages: list[Any]) -> str:
    return json.dumps([(m.role, m.content) for m in messages], default=str)


# ------------------------------------------------------------ (2) labels survive the rebuild


def test_messages_from_events_keeps_every_stored_label() -> None:
    msgs = runner_api.messages_from_events(history())
    by = {m.provenance for m in msgs}
    assert by == {R, U}
    assert all(m.restricted for m in msgs if m.provenance == R)
    tool = [m for m in msgs if m.role == "tool"][0]
    assert tool.provenance == R


def test_old_rows_without_a_label_read_as_unavailable() -> None:
    old = [ev("user_message", None, 1, text="old"), ev("assistant_text", None, 2, text="old a"),
           ev("tool_result", "garbage", 3, call_id="x", output="o"),
           ev("notice", "", 4, kind="coding_result", text="r")]
    msgs = runner_api.messages_from_events(old)
    assert msgs and {m.provenance for m in msgs} == {A}
    kept, withheld = history_labels.for_destination(msgs, local=False)
    assert kept == [] and withheld == len(msgs)


def test_brain_history_keeps_labels_and_merges_to_the_worst() -> None:
    evs = [ev("user_message", U, 1, text="q"),
           ev("assistant_text", U, 2, text="one", turn_id="t"),
           ev("tool_call", R, 3, call_id="c", name="Read", input={}, turn_id="t"),
           ev("tool_result", R, 4, call_id="c", name="Read", output="x", turn_id="t"),
           ev("assistant_text", U, 5, text="two", turn_id="t")]
    out = runner_brain.brain_history_from_events(evs)
    assert [m.role for m in out] == ["user", "assistant"]
    assert out[0].provenance == U and out[1].provenance == R  # merged answer = its worst part


def test_labels_survive_a_serialisation_round_trip() -> None:
    again = json.loads(json.dumps(history()))  # what the SQLite JSON payload does on restart
    assert [m.provenance for m in runner_api.messages_from_events(again)] == [
        m.provenance for m in runner_api.messages_from_events(history())]


# ----------------------------------------------- (3) remote sees only UNRESTRICTED, local all


def test_remote_gets_only_unrestricted_and_local_gets_everything() -> None:
    msgs = runner_api.messages_from_events(history())
    kept, withheld = history_labels.for_destination(msgs, local=False)
    text = dump(kept)
    assert "clean question" in text and "clean answer" in text and "clean report" in text
    for secret in ("SECRET-ATTACHMENT-TEXT", "SECRET-SUMMARY", "SECRET-TOOL", "SECRET-REPORT"):
        assert secret not in text
    assert withheld == len(msgs) - len(kept) > 0
    same, none = history_labels.for_destination(msgs, local=True)
    assert same == msgs and none == 0


def test_tool_pairs_stay_coherent_when_one_side_is_withheld() -> None:
    evs = [ev("user_message", U, 1, text="q"),
           ev("tool_call", U, 2, call_id="c", name="Read", input={}, turn_id="t"),
           ev("tool_result", R, 3, call_id="c", name="Read", output="SECRET", turn_id="t")]
    kept, _ = history_labels.for_destination(runner_api.messages_from_events(evs), local=False)
    assert [m.role for m in kept] == ["user"]  # no open tool_use, no orphan result


def test_transcript_for_a_vendor_cli_is_filtered_and_says_so() -> None:
    out = dodaios_harness.render_transcript(history())
    assert "clean answer" in out and "SECRET" not in out
    assert "withheld from the remote engine" in out


# ------------------------------------------------ the J2 scenario through the API runner


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _handle(provider: str, sink: _Sink, hist: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(
        session=SimpleNamespace(provider=provider, model="m", cwd="", effort=None,
                                vendor_session=None, surface="jarvis", permission_mode="default"),
        turn_id="t9", emit=sink.emit, history=hist, images=(), tools_disabled=True,
        plain_system="", assistant_name="J", output_language="", request_approval=None,
        cancel=asyncio.Event(),
    )


@pytest.mark.parametrize(("provider", "expect_secret"), [("openai", False), ("ollama", True)])
async def test_text_only_followup_after_a_local_restricted_turn(
    monkeypatch: pytest.MonkeyPatch, provider: str, expect_secret: bool
) -> None:
    sent: list[Any] = []

    class Brain:
        name = provider

        async def complete(self, req):
            sent.append(req)
            yield BrainDelta(content="ok")

    monkeypatch.setattr(runner_api, "build_brain", lambda p, m: Brain())
    monkeypatch.setattr(runner_api, "normalize_effort", lambda p, e: e)
    sink = _Sink()
    # No attachment on this message: the CURRENT-input gate allows it. Only history can leak.
    await runner_api.run_api_turn(_handle(provider, sink, history()), "and now?")
    wire = dump(list(sent[0].messages)) + (sent[0].system or "")
    assert ("SECRET-SUMMARY" in wire) is expect_secret
    notices = [e for e in sink.events if e["kind"] == "notice"]
    assert bool(notices) is (not expect_secret)
    if notices:
        assert notices[0]["payload"]["kind"] == "history_withheld"
        assert "withheld from the remote engine" in (sent[0].system or "")


# ------------------------------------------------- (1) labels are written at creation


class _Store:
    def __init__(self, tail: list[dict[str, Any]]) -> None:
        self.tail = tail

    def list_events(self, session_id: str, **_k: Any) -> list[dict[str, Any]]:
        return self.tail


def _svc(tail: list[dict[str, Any]] | None = None) -> AgentChatService:
    svc = AgentChatService.__new__(AgentChatService)
    svc._turn_labels = {}
    svc.store = _Store(tail or [])
    return svc


def _session(provider: str) -> Any:
    return SimpleNamespace(provider=provider)


def test_open_turn_label_by_engine_and_input() -> None:
    svc = _svc()
    dirty = [ev("assistant_text", R, 1, text="x")]
    # remote engine: output label = input label (its history was already filtered)
    assert svc._open_turn_label(_session("openai"), "t", dirty, carried=None, runner="api",
                                incoming=None, internal=False)[1:] == (
        Provenance.UNRESTRICTED, Provenance.UNRESTRICTED)
    # local api engine: output derived from the whole history it saw
    assert svc._open_turn_label(_session("ollama"), "t", dirty, carried=None, runner="api",
                                incoming=None, internal=False)[1:] == (
        Provenance.UNRESTRICTED, Provenance.RESTRICTED)
    # local engine, clean history: positively unrestricted (switching to remote stays coherent)
    assert svc._open_turn_label(_session("ollama"), "t", [], carried=None, runner="api",
                                incoming=None, internal=False)[2] is Provenance.UNRESTRICTED
    # local brain runner: memory feeds the prompt, so never positively unrestricted
    assert svc._open_turn_label(_session("ollama"), "t", [], carried=None, runner="brain",
                                incoming=None, internal=False)[2] is Provenance.UNAVAILABLE
    # an attachment label wins everywhere; a vendor CLI is remote
    got = svc._open_turn_label(_session("claude"), "t", [], carried=Provenance.RESTRICTED,
                               runner="claude-cli", incoming=None, internal=False)
    assert got[1] is Provenance.RESTRICTED and got[2] is Provenance.RESTRICTED


def test_events_are_stamped_from_the_turn_and_never_loosened() -> None:
    svc = _svc()
    svc._turn_labels["s"] = ("t", Provenance.UNRESTRICTED, Provenance.RESTRICTED)
    user, reply = ev("user_message", None, text="hi"), ev("assistant_text", None, text="a")
    tool = ev("tool_result", None, call_id="c", output="o")
    for e in (user, reply, tool):
        svc._stamp_provenance("s", e)
    assert user["payload"]["provenance"] == U
    assert reply["payload"]["provenance"] == R and tool["payload"]["provenance"] == R
    tight = ev("assistant_text", A, text="voice")
    svc._stamp_provenance("s", tight)
    assert tight["payload"]["provenance"] == R  # combine(RESTRICTED turn, explicit UNAVAILABLE)
    svc._turn_labels["s"] = ("t", Provenance.UNRESTRICTED, Provenance.UNRESTRICTED)
    loose = ev("assistant_text", A, text="voice")
    svc._stamp_provenance("s", loose)
    assert loose["payload"]["provenance"] == A  # an explicit label is never loosened


def test_reports_outside_a_turn_take_the_stored_tail_and_agent_messages_fail_closed() -> None:
    clean = _svc([ev("user_message", U, 1, text="q"), ev("assistant_text", U, 2, text="a")])
    report = ev("notice", None, kind="delegation_report", report="r")
    clean._stamp_provenance("s", report)
    assert report["payload"]["provenance"] == U
    dirty = _svc([ev("user_message", U, 1, text="q"), ev("assistant_text", R, 2, text="a")])
    report2 = ev("notice", None, kind="delegation_report", report="r")
    dirty._stamp_provenance("s", report2)
    assert report2["payload"]["provenance"] == R
    legacy = _svc([ev("user_message", None, 1, text="q")])
    report3 = ev("notice", None, kind="delegation_report", report="r")
    legacy._stamp_provenance("s", report3)
    assert report3["payload"]["provenance"] == A  # restart / old rows: never promoted
    agent = ev("agent_message", None, sender_kind="agent", prompt="p", message_id="m")
    clean._stamp_provenance("s", agent)
    assert agent["payload"]["provenance"] == A


def test_unlabelled_kinds_are_not_stamped() -> None:
    svc = _svc()
    other = ev("turn_started", None, turn_id="t")
    svc._stamp_provenance("s", other)
    assert "provenance" not in other["payload"]
