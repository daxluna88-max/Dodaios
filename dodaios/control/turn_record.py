"""What the last turn actually used, for the main screen (ADR-0058).

Honest by construction:

* **Inference is not the network.** An engine's *locality* says where the model ran (a loopback
  endpoint is ``local``; an unreadable or non-loopback one is ``remote``). It says nothing about
  tools, synthesis, embeddings or child processes, so ``network_tools`` is recorded separately and
  stays ``not_observed`` unless something recorded it.
* **Planned is not sent.** ``planned`` is what the privacy gate was set to allow; ``attempted`` is
  what a call was actually issued for. Neither is a transport-level observation, and the record
  says so with ``observability``.
* **Categories and destinations only.** Never the text, never an identifier, never a key.

* **Identity and lifecycle (block 9).** A record belongs to one run of one turn:
  ``(turn_id, STOP generation, run_id)``. Every later write names that key and is applied only if
  it is still the current one (compare-and-set), so an older turn finishing late can never replace
  the newer turn's record, and concurrent turns never swap engines. ``phase`` is explicit:
  ``planned`` (decided, nothing issued) -> ``attempted`` (a call was issued to the engine/model
  listed) -> ``completed`` / ``failed``; ``superseded`` = STOP overtook it and its reply was
  dropped. The engine and model are the ones resolved AT DISPATCH, passed in by the caller.
* **``planned`` is never a full inventory.** ``planned_partial`` says so unless no model is used.

Pure data plus a tiny holder; the brain fills it in where a turn is decided.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from typing import Any, Literal

Locality = Literal["local", "remote", "unknown"]
Role = Literal["main", "reviewer", "synthesizer"]
#: The categories a turn can send. A fixed list: nothing free-form is ever stored.
CATEGORIES = ("question", "history", "memory", "files", "tool_output")
#: ``declared`` = from the gate's configuration and the calls issued, not from the network.
Observability = Literal["declared", "none"]
#: ``running`` = the engine listed is the one chosen at the start, not yet confirmed to answer.
TurnState = Literal["running", "answered", "failed", "superseded"]
#: Explicit lifecycle. ``state`` is kept (older readers) and always agrees with it.
Phase = Literal["planned", "attempted", "completed", "failed", "superseded"]
_STATE_OF_PHASE: dict[str, str] = {
    "planned": "running", "attempted": "running", "completed": "answered",
    "failed": "failed", "superseded": "superseded",
}
_PHASE_OF_STATE: dict[str, str] = {
    "running": "attempted", "answered": "completed", "failed": "failed",
    "superseded": "superseded",
}


@dataclass(frozen=True, slots=True)
class RecordKey:
    """Whose record this is: one run of one turn under one STOP generation."""

    turn_id: str
    generation: int = 0
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class EngineUse:
    provider: str
    role: Role = "main"
    locality: Locality = "unknown"
    #: The model resolved at dispatch (override / fallback attempt); ``None`` = not known.
    model: str | None = None


@dataclass(frozen=True, slots=True)
class Flow:
    """A category going to a destination (a provider id or ``local``), never its content."""

    category: str
    destination: str


@dataclass(frozen=True, slots=True)
class TurnRecord:
    turn_id: str
    engines: tuple[EngineUse, ...] = ()
    planned: tuple[Flow, ...] = ()
    attempted: tuple[Flow, ...] = ()
    tools_used: tuple[str, ...] = ()
    #: ``not_observed`` unless a component recorded network use for this turn.
    network_tools: Literal["not_observed", "observed"] = "not_observed"
    observability: Observability = "none"
    refused: tuple[str, ...] = field(default=())
    state: TurnState = "answered"
    #: The provider chosen at the start; differs from the one that answered after a fallback.
    requested: str | None = None
    #: STOP generation and run the record belongs to (with ``turn_id`` it is the identity).
    generation: int = 0
    run_id: str = ""
    #: Lifecycle; ``None`` = derive it from ``state`` (records built the older way).
    phase: Phase | None = None
    #: ``planned`` lists only what the app declared (the question to a remote engine), not a
    #: complete inventory of history, memory, files or tool output: partial unless no model ran.
    planned_partial: bool = True
    #: Engines (``provider`` or ``provider:model``) tried and failed before the current one.
    earlier_attempts: tuple[str, ...] = ()

    @property
    def lifecycle(self) -> Phase:
        return self.phase or _PHASE_OF_STATE.get(self.state, "completed")  # type: ignore[return-value]

    @property
    def key(self) -> RecordKey:
        return RecordKey(self.turn_id, self.generation, self.run_id)

    @property
    def fell_back(self) -> bool:
        """The engine that answered is not the one chosen at the start."""
        if self.requested is None or self.state != "answered" or not self.engines:
            return False
        return self.engines[0].provider != self.requested

    @property
    def inference(self) -> Literal["local", "remote", "mixed", "unknown", "none"]:
        if not self.engines:
            return "none"
        kinds = {e.locality for e in self.engines}
        if "unknown" in kinds:
            return "unknown"
        if kinds == {"local"}:
            return "local"
        if kinds == {"remote"}:
            return "remote"
        return "mixed"

    @property
    def remote_destinations(self) -> tuple[str, ...]:
        return tuple(sorted({e.provider for e in self.engines if e.locality != "local"}))


class TurnLedger:
    """Holds the record of the latest turn of one product.

    Writers name the ``RecordKey`` they were given by ``begin``; a write whose key is not the
    current one is ignored and reported as ``False`` (compare-and-set). A turn that starts under an
    older STOP generation than the current record never replaces it.
    """

    def __init__(self) -> None:
        self._last: TurnRecord | None = None
        self._lock = threading.Lock()

    def _current(self, key: RecordKey | str) -> TurnRecord | None:
        last = self._last
        if last is None:
            return None
        if isinstance(key, str):  # older callers: the turn id alone
            return last if last.turn_id == key else None
        return last if last.key == key else None

    def record(self, record: TurnRecord) -> bool:
        """Install ``record`` as the latest, unless it is from an older STOP generation."""
        with self._lock:
            last = self._last
            if last is not None and record.generation < last.generation:
                return False
            self._last = record
            return True

    def begin(self, record: TurnRecord) -> RecordKey | None:
        """Open the record of a new run; the returned key is what its later writes must carry."""
        return record.key if self.record(record) else None

    def apply(self, key: RecordKey, record: TurnRecord) -> bool:
        """Replace the record of ``key`` (same identity) if it is still the current one."""
        with self._lock:
            if self._current(key) is None:
                return False
            self._last = replace(
                record, turn_id=key.turn_id, generation=key.generation, run_id=key.run_id)
            return True

    def attempt(
        self, key: RecordKey, *, provider: str, model: str | None, locality: Locality,
        requested: str | None = None,
    ) -> bool:
        """A call is about to be issued to ``provider``/``model`` (resolved at dispatch).

        A later attempt of the same run (a fallback) replaces the engine and remembers the one
        before it as an earlier attempt.
        """
        with self._lock:
            last = self._current(key)
            if last is None or last.lifecycle not in ("planned", "attempted"):
                return False
            earlier = last.earlier_attempts
            if last.lifecycle == "attempted" and last.engines:
                prev = last.engines[0]
                earlier += (f"{prev.provider}:{prev.model}" if prev.model else prev.provider,)
            flows = () if locality == "local" else (Flow("question", provider),)
            self._last = replace(
                last, engines=(EngineUse(provider, "main", locality, model),), planned=flows,
                planned_partial=True, phase="attempted", state="running",
                requested=requested if requested is not None else (last.requested or provider),
                earlier_attempts=earlier, observability="declared",
            )
            return True

    def update(self, key: RecordKey, **changes: Any) -> bool:
        """Change fields of the record of ``key`` (phase/state kept consistent), if current."""
        with self._lock:
            last = self._current(key)
            if last is None:
                return False
            phase = changes.get("phase")
            if phase is not None and "state" not in changes:
                changes["state"] = _STATE_OF_PHASE[phase]
            self._last = replace(last, **changes)
            return True

    def last(self) -> TurnRecord | None:
        return self._last

    def clear(self) -> None:
        with self._lock:
            self._last = None

    def finish(
        self, turn: RecordKey | str, *, provider: str | None, locality: Locality, answered: bool,
        model: str | None = None, superseded: bool = False,
    ) -> bool:
        """Replace the start-of-turn guess with what actually ran.

        ``provider`` is the engine of the last attempt (the one that answered when ``answered``);
        ``None`` means no model was used (a fast path replied). Only the run that is still the
        latest is touched: a late finish of an older turn changes nothing. ``superseded`` (STOP
        overtook the turn and its reply was dropped) is applied even after a terminal phase.
        """
        with self._lock:
            last = self._current(turn)
            if last is None:
                return False
            if superseded:
                self._last = replace(last, phase="superseded", state="superseded")
                return True
            if last.state != "running":
                return False
            if provider is None:
                self._last = replace(
                    last, engines=(), planned=(), planned_partial=False,
                    phase="completed" if answered else "failed",
                    state="answered" if answered else "failed",
                )
                return True
            flows = () if locality == "local" else (Flow("question", provider),)
            self._last = replace(
                last,
                engines=(EngineUse(provider, "main", locality, model),),
                planned=flows, planned_partial=True,
                phase="completed" if answered else "failed",
                state="answered" if answered else "failed",
            )
            return True


def sanitize_flows(flows: tuple[Flow, ...]) -> tuple[Flow, ...]:
    """Drop anything whose category is not in the fixed list: a free-form value is never kept."""
    return tuple(f for f in flows if f.category in CATEGORIES)


__all__ = [
    "CATEGORIES",
    "EngineUse",
    "Flow",
    "Locality",
    "Observability",
    "Phase",
    "RecordKey",
    "Role",
    "TurnLedger",
    "TurnRecord",
    "TurnState",
    "sanitize_flows",
]
