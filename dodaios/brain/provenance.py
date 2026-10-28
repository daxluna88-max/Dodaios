"""Durable "restricted" label for text that came out of local-only material (ADR-0050, block 7).

``ContextItem.local_only`` labels INPUT. The answers derived from it (a local engine's reply, a
local synthesis) are plain strings once they leave the orchestrator, and the doors after it (spoken
by a cloud TTS, written to memory by the curator) only see a string. This registry keeps the label
next to the text: ``mark_restricted(text)`` when a result is tainted, ``is_restricted(text)`` at
each door.

R6 (block 8): the text registry is NOT the boundary. Text matching is not a privacy label (it
ignores short text, expires, evicts, vanishes on restart and fails after any rewrite). The boundary
is a TYPED label: :class:`Provenance` carried by :class:`LabelledText` and by the per-turn channel
(``begin_turn`` / ``set_turn_provenance`` / ``turn_provenance``), which does not look at the text at
all. ``remote_speech_blocked`` fails closed: RESTRICTED and UNAVAILABLE both keep text away from a
remote synthesizer; only a local one is unaffected. The registry remains an extra signal.

Best-effort by construction (registry): matching is on whitespace/case-normalised text, so a
sentence cut out of a marked answer is still recognised, but a paraphrase or a number-spelled
rewrite is not. It is bounded in size and time and never raises. It is a floor, not a proof (see
ADR-0050, block 7).
"""

from __future__ import annotations

import contextvars
import dataclasses
import enum
import threading
import time
from collections import deque
from typing import Any

_MAX_ENTRIES = 256
_TTL_S = 3600.0
_MIN_LEN = 6  # shorter fragments would match innocent text ("ok", "yes")

_lock = threading.Lock()
_entries: deque[tuple[float, str]] = deque(maxlen=_MAX_ENTRIES)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def mark_restricted(text: str) -> None:
    """Remember that ``text`` derives from material that must not reach a remote destination."""
    norm = _norm(text)
    if len(norm) < _MIN_LEN:
        return
    with _lock:
        _entries.append((time.monotonic(), norm))


def is_restricted(text: str) -> bool:
    """True when ``text`` is (a part of, or contains) a marked answer."""
    norm = _norm(text)
    if len(norm) < _MIN_LEN:
        return False
    now = time.monotonic()
    with _lock:
        while _entries and now - _entries[0][0] > _TTL_S:
            _entries.popleft()
        return any(norm in marked or marked in norm for _, marked in _entries)


def clear() -> None:
    with _lock:
        _entries.clear()


def tts_is_local(provider: Any) -> bool:
    """Whether a TTS provider (or every leg of a fallback pair) is a local-only service.

    Unknown means remote. ``provider`` may be wrapped (``_inner``) or a primary/fallback pair.
    """
    from dodaios.brain import engine_policy  # noqa: PLC0415

    seen = 0
    while provider is not None and seen < 4:
        legs = [getattr(provider, "_primary", None), getattr(provider, "_fallback", None)]
        if all(leg is not None for leg in legs):
            return all(tts_is_local(leg) for leg in legs)
        inner = getattr(provider, "_inner", None)
        if inner is None:
            break
        provider, seen = inner, seen + 1
    name = getattr(provider, "name", "")
    return bool(name) and name in engine_policy.LOCAL_SERVICES["tts"]


# --- Typed provenance (R6, block 8) -------------------------------------------------------------


class Provenance(enum.StrEnum):
    """What a piece of text is allowed to do. Ordered by severity in :func:`combine`."""

    UNRESTRICTED = "unrestricted"  # known not to derive from local-only material
    RESTRICTED = "restricted"  # derives from local-only material: never to a remote destination
    UNAVAILABLE = "unavailable"  # nobody could say: treated like RESTRICTED for remote doors


def combine(*labels: Provenance | None) -> Provenance:
    """RESTRICTED beats UNAVAILABLE beats UNRESTRICTED; ``None`` (no label) is UNAVAILABLE."""
    seen = {Provenance.UNAVAILABLE if label is None else label for label in labels}
    if Provenance.RESTRICTED in seen:
        return Provenance.RESTRICTED
    if Provenance.UNAVAILABLE in seen or not seen:
        return Provenance.UNAVAILABLE
    return Provenance.UNRESTRICTED


class LabelledText(str):
    """A ``str`` that carries its :class:`Provenance`.

    Store ``provenance.value`` next to it to keep it across a restart. Any string operation
    returns a plain ``str``: the label is NOT kept by slicing or rewriting, so a path that
    transforms text must pass the label on in parallel (the turn channel below), never rely on
    this object surviving.
    """

    provenance: Provenance

    def __new__(cls, text: str, provenance: Provenance) -> LabelledText:
        obj = super().__new__(cls, text)
        obj.provenance = provenance
        return obj


def label_of(text: Any) -> Provenance | None:
    """The label ``text`` carries, or None when it is a plain string (no label)."""
    return text.provenance if isinstance(text, LabelledText) else None


def label(text: str, provenance: Provenance) -> LabelledText:
    return LabelledText(text, provenance)


@dataclasses.dataclass
class _TurnState:
    """One turn's label. ``declared`` is a POSITIVE claim; absence of any claim is UNAVAILABLE."""

    turn_id: str
    declared: bool = False  # a path positively attached UNRESTRICTED
    unavailable: bool = False  # a path said "cannot tell" (sticky)
    restricted: bool = False  # a path said "local-only material" (sticky)

    def label(self) -> Provenance:
        if self.restricted:
            return Provenance.RESTRICTED
        if self.declared and not self.unavailable:
            return Provenance.UNRESTRICTED
        return Provenance.UNAVAILABLE


# Per-turn context (R6, block 9): a ContextVar, never a process-global. A concurrent turn runs in
# its own task/context and cannot reset or relax this turn's state. No state at all (code that is
# not inside a turn, e.g. memory/wiki/tool/screen speech) reads as UNAVAILABLE.
_turn_var: contextvars.ContextVar[_TurnState | None] = contextvars.ContextVar(
    "dodaios_turn_provenance", default=None)


def begin_turn(turn_id: str | None = None) -> None:
    """A new turn starts in THIS context. Block 9: it starts UNAVAILABLE, not UNRESTRICTED; a
    path must positively attach UNRESTRICTED (:func:`attach_unrestricted`) to unlock remote
    speech.

    Block 18 (I1): the input label the dispatcher declared for this message (see
    :func:`declare_input`) is INHERITED, never dropped: the fresh state is merged with it, so a
    turn that carries a restricted/unavailable attachment starts at least as restricted."""
    _turn_var.set(_TurnState(turn_id=turn_id or ""))
    carried = _input_var.get()
    if carried is not None and carried.turn is not Provenance.UNRESTRICTED:
        set_turn_provenance(carried.turn)


# --- Immutable input label (block 18, I1) -------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class InputItem:
    """One attachment's own label: ``kind`` is image / text / pdf / other."""

    kind: str
    name: str
    label: Provenance


@dataclasses.dataclass(frozen=True, slots=True)
class InputLabel:
    """What the CURRENT message (prompt text + images) is allowed to do. Frozen: it is replaced
    only by a more restrictive one (:func:`declare_input`). ``turn`` is the most restrictive of
    ``items`` and of the message's own declared label."""

    turn: Provenance
    items: tuple[InputItem, ...] = ()


_input_var: contextvars.ContextVar[InputLabel | None] = contextvars.ContextVar(
    "dodaios_input_label", default=None)


def declare_input(
    turn: Provenance | str | None, items: tuple[InputItem, ...] = ()
) -> InputLabel:
    """Declare (in THIS context) the label of the message about to be dispatched.

    Merges with any label already declared: the result is ``combine`` of both, so a second call
    can only keep or tighten, never loosen (queued turns, retries, continuations, nested
    dispatches all inherit). A ``None``/unknown ``turn`` reads as UNAVAILABLE (fail closed)."""
    new = parse(turn)
    old = _input_var.get()
    merged = InputLabel(
        combine(new, *(item.label for item in items), *([old.turn] if old else [])),
        (old.items if old else ()) + tuple(items),
    )
    _input_var.set(merged)
    if merged.turn is not Provenance.UNRESTRICTED:  # never a positive claim from here
        set_turn_provenance(merged.turn)
    return merged


def input_label() -> InputLabel | None:
    """The declared label of the current message, or None when it carries no attachment."""
    return _input_var.get()


def input_requires_local() -> bool:
    """True when the current message must NOT reach a destination that is not provably local."""
    carried = _input_var.get()
    return carried is not None and carried.turn is not Provenance.UNRESTRICTED


def set_turn_provenance(provenance: Provenance) -> None:
    """Merge ``provenance`` into this turn. RESTRICTED and UNAVAILABLE are sticky; UNRESTRICTED
    is a positive declaration that can never lift either of them."""
    state = _turn_var.get()
    if state is None:
        state = _TurnState(turn_id="")
        _turn_var.set(state)
    if provenance is Provenance.RESTRICTED:
        state.restricted = True
    elif provenance is Provenance.UNAVAILABLE:
        state.unavailable = True
    else:
        state.declared = True


def attach_unrestricted() -> None:
    """Positively declare that this turn's output derives only from non-local-only material
    (e.g. a typed local-origin prompt answered by a fully local engine). Never overrides
    RESTRICTED/UNAVAILABLE."""
    set_turn_provenance(Provenance.UNRESTRICTED)


def turn_provenance() -> Provenance:
    """The label of THIS context's turn, independent of any text. No turn state = UNAVAILABLE."""
    state = _turn_var.get()
    return Provenance.UNAVAILABLE if state is None else state.label()


def label_like(text: str, source: Any) -> LabelledText:
    """Carry ``source``'s label (None -> UNAVAILABLE) over to ``text`` after a transformation
    (normalisation, chunking, translation), so the label travels per chunk."""
    return LabelledText(text, combine(label_of(source)))


def remote_speech_blocked(text: str) -> bool:
    """Whether ``text`` must NOT go to a remote synthesizer. Fail closed (block 9): a plain
    ``str`` has no provenance and is refused; only a text object that positively carries
    UNRESTRICTED, in a turn not marked restricted/unavailable, and not matching a restricted
    answer, may go remote."""
    if label_of(text) is not Provenance.UNRESTRICTED:
        return True
    state = _turn_var.get()
    if state is not None and state.label() is not Provenance.UNRESTRICTED:
        return True
    return is_restricted(text)


def local_only_signal(text: str) -> bool:
    """A POSITIVE local-only / cannot-tell signal on ``text`` (block-8 meaning).

    True only when a label, the turn channel or the text registry says RESTRICTED/UNAVAILABLE.
    Absence of any signal is False here: this is a "known tainted" test, NOT a permission. Use it
    where the question is "is there evidence of taint" (e.g. tagging a memory proposal); use
    :func:`storage_label` / :func:`withheld_from_remote_store` where the question is "may this go
    to a remote destination" (fail closed).
    """
    if label_of(text) in (Provenance.RESTRICTED, Provenance.UNAVAILABLE):
        return True
    state = _turn_var.get()
    if state is not None and (state.restricted or state.unavailable):
        return True
    return is_restricted(text)


def parse(value: Any) -> Provenance:
    """A stored/serialised label back to :class:`Provenance`. Missing, empty, unknown or
    malformed (an old row, a migration default, a hand-edited value) is UNAVAILABLE (block 13)."""
    if isinstance(value, Provenance):
        return value
    try:
        return Provenance(str(value).strip().lower()) if value is not None else (
            Provenance.UNAVAILABLE)
    except ValueError:
        return Provenance.UNAVAILABLE


def storage_label(text: str) -> Provenance:
    """The label to STORE next to ``text`` that was just produced in THIS turn (block 13, G2).

    RESTRICTED on any positive local-only signal. UNRESTRICTED only on a POSITIVE declaration:
    the turn channel says so (``attach_unrestricted``), the text, when it carries a label, agrees,
    and the text registry does not match. Everything else, including the initial undeclared state
    of a turn that no path labelled, is UNAVAILABLE. Local engines still see UNAVAILABLE history;
    remote ones do not (:func:`remote_replayable`).
    """
    if local_only_signal(text):
        own = label_of(text)
        state = _turn_var.get()
        if own is Provenance.RESTRICTED or (state is not None and state.restricted) or (
                is_restricted(text)):
            return Provenance.RESTRICTED
        return Provenance.UNAVAILABLE
    state = _turn_var.get()
    if state is None or state.label() is not Provenance.UNRESTRICTED:
        return Provenance.UNAVAILABLE
    if label_of(text) not in (None, Provenance.UNRESTRICTED):
        return Provenance.UNAVAILABLE
    return Provenance.UNRESTRICTED


def remote_replayable(value: Any) -> bool:
    """Whether stored material with this label may be replayed/summarised/curated by a REMOTE
    destination. Only a stored UNRESTRICTED qualifies; a missing label is UNAVAILABLE."""
    return parse(value) is Provenance.UNRESTRICTED


def withheld_from_remote_store(text: str) -> bool:
    """Whether ``text`` must stay out of a remote engine's replay / the curator (history, notes).

    Block 13 (G2): FAIL CLOSED. The block-8/9 version blocked only on a positive restricted
    signal and let an unlabelled answer through (the turn's initial undeclared state), which made
    ordinary unlabelled paths remote-replayable. Now only :func:`storage_label` ==
    UNRESTRICTED (a positive declaration) is storable for remote use.
    """
    return storage_label(text) is not Provenance.UNRESTRICTED
