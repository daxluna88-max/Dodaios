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

    def stale(self) -> bool:
        if self.exempt:
            return False
        from dodaios.control.stop import get_stop_controller  # noqa: PLC0415

        return not get_stop_controller().admit(self.ticket).allowed


_TURN: ContextVar[VoiceTurnTicket | None] = ContextVar("dodaios_voice_turn_ticket", default=None)


def new_ticket() -> VoiceTurnTicket:
    from dodaios.control.stop import get_stop_controller  # noqa: PLC0415

    controller = get_stop_controller()
    return VoiceTurnTicket(controller.ticket(), exempt=not controller.door_open)


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


__all__ = [
    "VoiceTurnTicket",
    "current_turn",
    "current_turn_stale",
    "new_ticket",
    "session_ticket",
    "not_stale",
    "voice_turn",
]
