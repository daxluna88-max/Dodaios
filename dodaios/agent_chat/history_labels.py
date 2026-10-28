"""Durable provenance of a chat's stored events (ADR-0062, block 19, J2).

Block 18 labelled the CURRENT message. The stored history is a second channel: after a LOCAL
engine processed a restricted attachment, switching the chat to a REMOTE engine and sending a
text-only follow-up has no new attachment label, yet the persisted user/assistant/tool/report
events would be replayed. So every replayable stored event carries a typed ``provenance`` in its
own payload (a plain JSON string, so it survives a restart), written when the event is created.

Rules (all fail closed):

* a missing, empty or unknown stored label (an old row) is UNAVAILABLE;
* a destination that is not PROVABLY local (``engine_policy.destination_of``; a vendor CLI is
  remote) is shown only events whose stored label is positively UNRESTRICTED;
* a local destination sees everything, unchanged;
* what was held back is counted so the caller can say so (a visible notice and a line in the
  prompt) instead of sending partial context silently.

This module never reads a ContextVar to decide what an OLD event is: the current-turn channel is
not a durable historical label. The label of a NEW event is decided by the service from the turn's
input label and the engine that produced it (``AgentChatService._stamp_provenance``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

from dodaios.brain import provenance as prov

KEY: Final[str] = "provenance"

#: Event kinds whose payload is replayed into a model's context and so must carry a label.
CONTENT_KINDS: Final[frozenset[str]] = frozenset(
    {"user_message", "assistant_text", "tool_call", "tool_result", "agent_message"}
)
#: ``notice`` kinds that runner_api replays as delegation material.
REPORT_NOTICE_KINDS: Final[frozenset[str]] = frozenset(
    {"society_result", "society_message", "coding_result", "delegation_report"}
)


def is_labelled_kind(event: dict[str, Any]) -> bool:
    kind = event.get("kind")
    if kind in CONTENT_KINDS:
        return True
    return kind == "notice" and (event.get("payload") or {}).get("kind") in REPORT_NOTICE_KINDS


def stored_label(payload: dict[str, Any] | None) -> prov.Provenance:
    """The label an event carries; an old row without one is UNAVAILABLE."""
    return prov.parse((payload or {}).get(KEY))


def event_label(event: dict[str, Any]) -> prov.Provenance:
    return stored_label(event.get("payload"))


def stamp(event: dict[str, Any], label: prov.Provenance) -> None:
    """Write ``label`` into the event payload. An existing label can only be tightened."""
    payload = event.setdefault("payload", {})
    existing = payload.get(KEY)
    payload[KEY] = (prov.combine(prov.parse(existing), label) if existing is not None
                    else label).value


def worst(events: Iterable[dict[str, Any]]) -> prov.Provenance:
    """``combine`` of every labelled content event (empty history: no taint, UNRESTRICTED)."""
    labels = [event_label(e) for e in events if is_labelled_kind(e)]
    return prov.combine(*labels) if labels else prov.Provenance.UNRESTRICTED


def derived_label(tail: list[dict[str, Any]]) -> prov.Provenance:
    """Label of material that arrives with no turn of its own (a delegation report, a notice).

    Bounded by the session's most recent turn as persisted: ``combine`` of every labelled event
    from the last ``user_message`` on. Nothing stored = UNAVAILABLE. Restart-safe: it reads the
    stored labels, never process state."""
    seen: list[prov.Provenance] = []
    for event in reversed(tail):
        if not is_labelled_kind(event):
            continue
        seen.append(event_label(event))
        if event.get("kind") == "user_message":
            break
    return prov.combine(*seen) if seen else prov.Provenance.UNAVAILABLE


def replayable_remote(message: Any) -> bool:
    return not getattr(message, "restricted", False) and prov.remote_replayable(
        getattr(message, "provenance", None))


def event_replayable_remote(event: dict[str, Any]) -> bool:
    return not is_labelled_kind(event) or event_label(event) is prov.Provenance.UNRESTRICTED


def filter_messages(messages: list[Any]) -> tuple[list[Any], int]:
    """Keep only positively UNRESTRICTED messages for a remote destination.

    Coherence: a tool message whose call was withheld (or the reverse) is dropped/closed so the
    provider never sees an orphan; leading assistant/tool messages (a conversation must open with
    the person) are dropped too. Returns ``(kept, withheld_count)``."""
    kept = [m for m in messages if replayable_remote(m)]
    live_calls = {
        str(b.get("id")) for m in kept if m.role == "assistant" and isinstance(m.content, list)
        for b in m.content if b.get("type") == "tool_use"
    }
    kept = [m for m in kept if not (m.role == "tool" and (m.tool_call_id or "") not in live_calls)]
    answered = {m.tool_call_id for m in kept if m.role == "tool"}
    out: list[Any] = []
    for m in kept:
        if m.role == "assistant" and isinstance(m.content, list):
            # A call whose result was withheld cannot stay open: drop the whole round.
            if any(b.get("type") == "tool_use" and b.get("id") not in answered
                   for b in m.content):
                continue
        out.append(m)
    live = {
        str(b.get("id")) for m in out if m.role == "assistant" and isinstance(m.content, list)
        for b in m.content if b.get("type") == "tool_use"
    }
    out = [m for m in out if not (m.role == "tool" and (m.tool_call_id or "") not in live)]
    while out and out[0].role != "user":
        out.pop(0)
    return out, len(messages) - len(out)


def for_destination(
    messages: list[Any], *, local: bool
) -> tuple[list[Any], int]:
    """History for ONE destination: unchanged when provably local, else only UNRESTRICTED."""
    if local:
        return messages, 0
    return filter_messages(messages)


def withheld_text(count: int) -> str:
    noun = "message" if count == 1 else "messages"
    return (f"{count} earlier {noun} withheld from the remote engine "
            "(they may derive from material that must stay on this computer).")


def system_note(count: int) -> str:
    return ("\n\nNote: " + withheld_text(count) + " The conversation you see is incomplete; "
            "do not guess what was withheld.") if count > 0 else ""


def notice_payload(count: int, destination: str) -> dict[str, Any]:
    return {"kind": "history_withheld", "count": count, "destination": destination,
            "text": withheld_text(count)}


def withheld_count(history: list[dict[str, Any]], *, local: bool) -> int:
    """How many rebuilt messages a destination would NOT be shown (0 for a local one)."""
    if local:
        return 0
    from dodaios.agent_chat.runner_api import messages_from_events  # noqa: PLC0415

    return filter_messages(messages_from_events(history))[1]
