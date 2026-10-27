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

from dataclasses import dataclass, field
from typing import Literal

Locality = Literal["local", "remote", "unknown"]
Role = Literal["main", "reviewer", "synthesizer"]
#: The categories a turn can send. A fixed list: nothing free-form is ever stored.
CATEGORIES = ("question", "history", "memory", "files", "tool_output")
#: ``declared`` = from the gate's configuration and the calls issued, not from the network.
Observability = Literal["declared", "none"]


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
    "sanitize_flows",
]
