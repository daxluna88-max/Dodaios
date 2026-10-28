"""Per-turn STOP ticket for the voice path (ADR-0058).

A voice turn takes the STOP generation when it is created (``begin_voice_turn``). Every emit of
that turn (streamed sentence, synthesized chunk, played audio, spoken-transcript event) asks
``current_turn_stale()`` first: after a STOP, or a STOP and a resume, the answer is yes and the
output is dropped. The ticket lives in a context variable, so work scheduled from inside the turn
(look-ahead synthesis, playback, the completion-buffer timer) inherits it without extra plumbing.

A turn created while the door is already closed is ``exempt``: only the refusal reply and a stop
phrase can come out of it (the brain refuses everything else), and they must still be heard.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(slots=True)
class VoiceTurnTicket:
    ticket: int
    exempt: bool = False
    #: Admission order / identity of a classic turn (ADR-0058 block 18). Filled by ``new_ticket``
    #: only: ``seq`` from ``core.events.next_turn_seq()``, a fresh ``turn_id`` and the pipeline's
    #: ``session_id``. A call ticket (``session_ticket``) leaves them empty: a call has its own
    #: session / turn ids and takes its order per turn. 0 / "" = unknown.
    seq: int = 0
    turn_id: str = ""
    session_id: str = ""

    def stale(self) -> bool:
        if self.exempt:
            return False
        from dodaios.control.stop import get_stop_controller  # noqa: PLC0415

        return not get_stop_controller().admit(self.ticket).allowed


_TURN: ContextVar[VoiceTurnTicket | None] = ContextVar("dodaios_voice_turn_ticket", default=None)


#: Ticket of the fenced send currently in flight in THIS task (block 18). A raw sender deep in the
#: chain (after its own lock / state awaits) asks ``send_allowed()`` right before its final write.
_SEND_FENCE: ContextVar[VoiceTurnTicket | None] = ContextVar(
    "dodaios_send_fence", default=None
)


def send_allowed() -> bool:
    """Synchronous re-check for a raw sender, right before it writes / enqueues.

    True outside a fenced send (no ticket in this context) and for control frames, which the fence
    does not cover. False when the fenced send's ORIGINAL ticket went stale in the meantime.
    """
    ticket = _SEND_FENCE.get()
    return ticket is None or not ticket.stale()


def new_ticket() -> VoiceTurnTicket:
    from uuid import uuid4  # noqa: PLC0415

    from dodaios.control.stop import get_stop_controller  # noqa: PLC0415
    from dodaios.core.events import next_turn_seq  # noqa: PLC0415

    controller = get_stop_controller()
    return VoiceTurnTicket(
        controller.ticket(), exempt=not controller.door_open,
        seq=next_turn_seq(), turn_id=uuid4().hex, session_id="speech.pipeline",
    )


def session_ticket() -> VoiceTurnTicket:
    """Ticket for a whole call (realtime, live/native, browser-held call).

    Unlike a turn, a call is never ``exempt``: opened while the door is closed it is stale at
    once (nothing it produces is emitted), and it goes stale on STOP or on STOP and resume.
    """
    from dodaios.control.stop import get_stop_controller  # noqa: PLC0415

    return VoiceTurnTicket(get_stop_controller().ticket(), exempt=False)


@contextmanager
def voice_turn() -> Iterator[VoiceTurnTicket]:
    """Take the ticket for one turn; the previous one (if any) comes back afterwards."""
    ticket = new_ticket()
    token = _TURN.set(ticket)
    try:
        yield ticket
    finally:
        _TURN.reset(token)


def current_turn() -> VoiceTurnTicket | None:
    return _TURN.get()


def current_turn_stale() -> bool:
    """True when the turn running in this context started before a STOP."""
    turn = _TURN.get()
    return turn is not None and turn.stale()


def not_stale(turn: VoiceTurnTicket | None = None) -> Callable[[], bool]:
    """A predicate for ``guard_stream``: the turn is still current."""
    held = turn if turn is not None else _TURN.get()
    return (lambda: True) if held is None else (lambda: not held.stale())


#: JSON frame types that carry CONTENT toward the person (text, captions, timed audio, speech
#: timing, spoken fallback). Control frames (``audio_clear``, ``audio_closed``, ``tts_cancel``,
#: ``provider_error``, indicators) are NOT in the set: after STOP they must still go out.
STOPPABLE_JSON_TYPES = frozenset({
    "transcript", "audio_timed", "speech_timing", "error_spoken", "caption",
})


def fenced_sender(
    ticket: VoiceTurnTicket,
    raw: Callable[..., object],
    *,
    content_types: frozenset[str] | None = None,
    on_drop: Callable[[], object] | None = None,
) -> Callable[..., object]:
    """Wrap an output callable so the ORIGINAL ticket is checked at the write itself.

    ``stale()`` is evaluated synchronously and ``raw(payload)`` is invoked in the same step: a
    coroutine runs inline up to its first suspension point, and nothing is awaited in between,
    so no STOP can land between the final check and the hand-over to ``raw`` (an entry check
    earlier in the call chain, followed by awaits, cannot promise that). ``content_types`` limits
    the fence to JSON payloads of those types (``None`` = every payload, for binary audio).

    Scope: this suppresses LOCAL sends (this process stops handing data to the transport). It
    does not recall bytes the transport already queued, does not stop browser / WebRTC playback
    and does not cancel the provider's response; none of that is demonstrated here.
    """

    async def send(payload: object, *args: object, **kwargs: object) -> object:
        if content_types is not None and not (
            isinstance(payload, dict)
            and payload.get("type") in content_types
            and payload.get("role") != "user"  # the person's own words are not old OUTPUT
        ):
            return await raw(payload, *args, **kwargs)  # type: ignore[misc]
        if ticket.stale():
            if on_drop is not None:
                on_drop()
            return None
        # Publish the ticket to the raw sender's own awaits (lock, state change) so it can
        # re-check synchronously right before its final write (``send_allowed``).
        token = _SEND_FENCE.set(ticket)
        try:
            return await raw(payload, *args, **kwargs)  # type: ignore[misc]
        finally:
            _SEND_FENCE.reset(token)

    return send


__all__ = [
    "STOPPABLE_JSON_TYPES",
    "fenced_sender",
    "VoiceTurnTicket",
    "current_turn",
    "current_turn_stale",
    "new_ticket",
    "session_ticket",
    "not_stale",
    "send_allowed",
    "voice_turn",
]
