"""Engine overrides, safe fallback and "which engine is running" (ADR-0052).

Pure policy and bookkeeping, no I/O of its own (callers pass async stream factories):

* ``resolve_engine``: default < project < task override. Under the Work product every layer must
  be a local engine; a stale or malicious override is REJECTED with the layer named, never skipped
  silently and never turned into a remote call.
* ``ActiveEngines``: which engine is actually running each turn/job right now (not the default).
* ``FallbackRouter``: an ordered candidate list with capability, egress and budget checks.
  Fallback happens only while NOTHING visible has been produced (no text, no tool call) and only
  for a failure another engine can fix (timeout, connection, overload). Quota/auth failures stop
  (no retry storm, no silent switch to another paid account). A partial answer is never spliced
  with a second engine's answer. Under Work no remote candidate is ever tried.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from dodaios.brain import engine_policy
from dodaios.brain.consumer_orchestrator import Budget
from dodaios.core.protocols import BrainDelta

# -- overrides -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EngineChoice:
    provider: str
    model: str | None
    source: str  # default | project | task


def resolve_engine(
    default: tuple[str, str | None],
    project: tuple[str, str | None] | None = None,
    task: tuple[str, str | None] | None = None,
    *,
    product: str | None = None,
) -> EngineChoice:
    """Task beats project beats default. Every layer is checked, not only the winner, so a poisoned
    lower layer is reported instead of waiting to become effective later."""
    product = product if product is not None else engine_policy.current_product()
    layers = (("default", default), ("project", project), ("task", task))
    for source, layer in layers:
        if layer is None:
            continue
        try:
            engine_policy.check_provider(layer[0], product=product)
        except engine_policy.EnginePolicyError as exc:
            raise engine_policy.EnginePolicyError(
                f"The {source} engine setting is not allowed: {exc}", code="override_rejected"
            ) from exc
    chosen = next((name, value) for name, value in reversed(layers) if value is not None)
    return EngineChoice(chosen[1][0], chosen[1][1], chosen[0])


class ActiveEngines:
    """Thread-safe record of the engine running each turn or job (the UI reads this)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, EngineChoice] = {}

    def set(self, key: str, choice: EngineChoice) -> None:
        with self._lock:
            self._active[key] = choice

    def clear(self, key: str) -> None:
        with self._lock:
            self._active.pop(key, None)

    def get(self, key: str) -> EngineChoice | None:
        with self._lock:
            return self._active.get(key)

    def snapshot(self) -> dict[str, EngineChoice]:
        with self._lock:
            return dict(self._active)


# -- failure classification ------------------------------------------------------------------------

TRANSIENT, QUOTA, AUTH, FATAL = "transient", "quota", "auth", "fatal"
_QUOTA = (
    "429",
    "quota",
    "rate limit",
    "rate_limit",
    "insufficient",
    "billing",
    "credit",
    "exceeded",
)
_AUTH = (
    "401",
    "403",
    "unauthor",
    "forbidden",
    "invalid api key",
    "invalid_api_key",
    "not authenticated",
)


def classify_failure(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, ConnectionError, asyncio.TimeoutError)):
        return TRANSIENT
    text = f"{type(exc).__name__} {exc}".lower()
    if any(marker in text for marker in _QUOTA):
        return QUOTA
    if any(marker in text for marker in _AUTH):
        return AUTH
    if any(marker in text for marker in ("overload", "503", "502", "unavailable", "connection")):
        return TRANSIENT
    return FATAL


# -- fallback router -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    provider: str
    model: str | None
    kind: str  # subscription | api | local
    est_cost: float | None = None
    authenticated: bool = True
    egress_authorized: bool = False  # the privacy gate allowed sending this turn's context there
    tools: bool | None = None  # declared capability; None = unknown
    vision: bool | None = None


@dataclass(slots=True)
class Attempt:
    provider: str
    outcome: str  # ok | skipped | failed:<class> | stopped:<class>
    reason: str = ""


@dataclass(slots=True)
class RoutedResult:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    error: str = ""


class FallbackRouter:
    def __init__(
        self,
        budget: Budget,
        *,
        max_attempts: int = 3,
        backoff_s: float = 0.0,
        allow_unknown_cost: bool = False,
    ) -> None:
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be an integer >= 1")
        self.budget, self.max_attempts, self.backoff_s = budget, max_attempts, backoff_s
        self.allow_unknown_cost = allow_unknown_cost

    def _skip_reason(
        self, c: Candidate, index: int, needs_tools: bool, needs_vision: bool
    ) -> str | None:
        if not c.authenticated:
            return "not authenticated"
        try:
            engine_policy.check_provider(c.provider)
        except engine_policy.EnginePolicyError as exc:  # reason returned as the skip reason
            return str(exc)
        if c.kind != "local" and not c.egress_authorized:
            return "sending this context to a remote engine is not authorized"
        if needs_tools and c.tools is not True:
            return "tool support is not declared"
        if needs_vision and c.vision is not True:
            return "image support is not declared"
        if index > 0 and c.kind == "subscription" and c.est_cost is None:
            return "unknown subscription quota"
        return None

    async def run(
        self,
        candidates: Sequence[Candidate],
        stream: Callable[[Candidate], AsyncIterator[BrainDelta]],
        *,
        needs_tools: bool = False,
        needs_vision: bool = False,
    ) -> RoutedResult:
        result = RoutedResult()
        tried = 0
        for index, cand in enumerate(candidates):
            if tried >= self.max_attempts:
                break
            skip = self._skip_reason(cand, index, needs_tools, needs_vision)
            if skip:
                result.attempts.append(Attempt(cand.provider, "skipped", skip))
                continue
            if not self.budget.reserve(
                0.0 if cand.kind == "local" and cand.est_cost is None else cand.est_cost,
                allow_unknown=self.allow_unknown_cost,
            ):
                result.attempts.append(
                    Attempt(
                        cand.provider,
                        "skipped",
                        "aggregate budget would be exceeded or price unknown",
                    )
                )
                continue
            tried += 1
            visible = False
            text: list[str] = []
            calls: list[dict[str, Any]] = []
            try:
                async for delta in stream(cand):
                    if delta.content:
                        visible = True
                        text.append(delta.content)
                    if delta.tool_call:
                        visible = (
                            True  # a tool call may already have had an effect: no replay elsewhere
                        )
                        calls.append(dict(delta.tool_call))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - classified and reported below
                kind = classify_failure(exc)
                if visible:
                    result.attempts.append(
                        Attempt(
                            cand.provider, f"stopped:{kind}", "output already produced: no fallback"
                        )
                    )
                    result.text, result.tool_calls, result.provider = (
                        "".join(text),
                        calls,
                        cand.provider,
                    )
                    result.error = f"{cand.provider} failed after producing output ({kind})"
                    return result
                if kind in (QUOTA, AUTH, FATAL):
                    result.attempts.append(
                        Attempt(
                            cand.provider,
                            f"stopped:{kind}",
                            "this failure is not fixed by another engine",
                        )
                    )
                    result.error = f"{cand.provider} failed ({kind})"
                    return result
                result.attempts.append(Attempt(cand.provider, f"failed:{kind}", type(exc).__name__))
                if self.backoff_s:
                    await asyncio.sleep(self.backoff_s * tried)
                continue
            result.attempts.append(Attempt(cand.provider, "ok"))
            result.text, result.tool_calls, result.provider = "".join(text), calls, cand.provider
            return result
        result.error = result.error or "no engine could answer"
        return result


__all__ = [
    "ActiveEngines",
    "Attempt",
    "Candidate",
    "EngineChoice",
    "FallbackRouter",
    "RoutedResult",
    "classify_failure",
    "resolve_engine",
]
