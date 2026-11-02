"""Durable "restricted" label for text that came out of local-only material (ADR-0050, block 7).

``ContextItem.local_only`` labels INPUT. The answers derived from it (a local engine's reply, a
local synthesis) are plain strings once they leave the orchestrator, and the doors after it (spoken
by a cloud TTS, written to memory by the curator) only see a string. This registry keeps the label
next to the text: ``mark_restricted(text)`` when a result is tainted, ``is_restricted(text)`` at
each door.

Best-effort by construction: matching is on whitespace/case-normalised text, so a sentence cut out
of a marked answer is still recognised, but a paraphrase or a number-spelled rewrite is not. It is
bounded in size and time and never raises. It is a floor, not a proof (see ADR-0050, block 7).
"""

from __future__ import annotations

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
