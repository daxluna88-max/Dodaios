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

Pure data plus a tiny holder; the brain fills it in where a turn is decided.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

Locality = Literal["local", "remote", "unknown"]
Role = Literal["main", "reviewer", "synthesizer"]
#: The categories a turn can send. A fixed list: nothing free-form is ever stored.
CATEGORIES = ("question", "history", "memory", "files", "tool_output")
#: ``declared`` = from the gate's configuration and the calls issued, not from the network.
Observability = Literal["declared", "none"]
#: ``running`` = the engine listed is the one chosen at the start, not yet confirmed to answer.
TurnState = Literal["running", "answered", "failed"]


@dataclass(frozen=True, slots=True)
class EngineUse:
    provider: str
    role: Role = "main"
    locality: Locality = "unknown"


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
    """Holds the record of the latest turn of one product."""

    def __init__(self) -> None:
        self._last: TurnRecord | None = None

    def record(self, record: TurnRecord) -> None:
        self._last = record

    def last(self) -> TurnRecord | None:
        return self._last

    def clear(self) -> None:
        self._last = None

    def finish(
        self, turn_id: str, *, provider: str | None, locality: Locality, answered: bool
    ) -> None:
        """Replace the start-of-turn guess with what actually ran.

        ``provider`` is the engine of the last attempt (the one that answered when ``answered``);
        ``None`` means no model was used (a fast path replied). Only the turn that is still the
        latest is touched: a late finish of an older turn changes nothing.
        """
        last = self._last
        if last is None or last.turn_id != turn_id or last.state != "running":
            return
        if provider is None:
            self._last = replace(last, engines=(), planned=(), state="answered")
            return
        flows = () if locality == "local" else (Flow("question", provider),)
        self._last = replace(
            last,
            engines=(EngineUse(provider, "main", locality),),
            planned=flows,
            state="answered" if answered else "failed",
        )


def sanitize_flows(flows: tuple[Flow, ...]) -> tuple[Flow, ...]:
    """Drop anything whose category is not in the fixed list: a free-form value is never kept."""
    return tuple(f for f in flows if f.category in CATEGORIES)


__all__ = [
    "CATEGORIES",
    "EngineUse",
    "Flow",
    "Locality",
    "Observability",
    "Role",
    "TurnLedger",
    "TurnRecord",
    "TurnState",
    "sanitize_flows",
]
