"""Final dispatch decision for the CURRENT message (ADR-0062, block 18, I1).

``provenance.declare_input`` records, at ingestion, the immutable label of the message being
dispatched (per attachment item and the turn's most restrictive). This module ENFORCES it at every
real dispatch (brain fallback chain, dispatcher/tool loop, API runner, CLI/ACP runner, control
runner): when the label is UNAVAILABLE or RESTRICTED (a missing label with attachments reads as
UNAVAILABLE) and the destination is not PROVABLY on this machine (``engine_policy.destination_of``:
loopback only; an unknown name, unreadable endpoint or vendor CLI is remote), the request is refused
with a user-visible message. Nothing is dropped silently and nothing is sent. Under Work the same
loopback proof is required. A message that carries no attachment has no input label and is not
touched by this module.
"""

from __future__ import annotations

import logging
from typing import Any

from dodaios.brain import provenance

log = logging.getLogger(__name__)

CODE = "attachment_label_remote_refused"


class InputLabelRefused(RuntimeError):
    """The current message may not be sent to this (not provably local) destination."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = CODE


def user_message(destination: str) -> str:
    return (
        "This message carries an attachment (text, PDF or image) that must stay on this "
        f"computer, so it was not sent to {destination or 'a remote engine'}. Choose a local "
        "engine (Ollama or a local OpenAI-compatible server on this machine) or send it without "
        "the attachment."
    )


def provider_of(brain: Any) -> str:
    """The provider name a (possibly wrapped) brain reports; '' when it cannot be told."""
    for attr in ("_provider_name", "name", "provider"):
        value = getattr(brain, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def is_local(provider: str, brain: Any = None) -> bool:
    """Provably local (loopback). Any failure or empty name is remote."""
    if not provider:
        return False
    from dodaios.brain import engine_policy  # noqa: PLC0415

    try:
        return bool(engine_policy.destination_of(provider, brain).local)
    except Exception:  # noqa: BLE001 - unprovable means remote
        log.debug("locality of %s unreadable: treated as remote", provider)
        return False


def refusal(provider: str, brain: Any = None, *, local: bool | None = None) -> str | None:
    """The user-visible refusal when the current message may not go to ``provider``, else None.

    ``local`` overrides the lookup for runners whose locality is known by construction (a vendor
    CLI is remote unless proven otherwise)."""
    if not provenance.input_requires_local():
        return None
    ok = is_local(provider, brain) if local is None else local
    return None if ok else user_message(provider)


def check(provider: str, brain: Any = None, *, local: bool | None = None) -> None:
    """Raise :class:`InputLabelRefused` when the current message may not go to ``provider``."""
    message = refusal(provider, brain, local=local)
    if message is not None:
        raise InputLabelRefused(message)


def check_brain(brain: Any) -> None:
    """Dispatcher-level check, from the brain object alone (name looked through wrappers)."""
    if provenance.input_requires_local():
        check(provider_of(brain), brain)
