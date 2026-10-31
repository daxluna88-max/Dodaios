"""A chat model that lives in the APP: the worker asks, the app streams the answer back (ADR-0051).

The default engine LLM is a local Ollama server reached from this process. This alternative sends
each model call to the app as ``llm.request`` and receives ``llm.delta`` frames, so any provider the
app can build (a local server, or for the Consumer product a connected cloud engine) can drive live
voice while VAD, STT, TTS, clause chunking, barge-in and the tool loop stay in the engine. The app
decides which provider answers and enforces the product policy there; this module only moves frames.

Rules: every request has its own id and only deltas carrying a pending id are accepted, so a late
frame of a cancelled or finished call is dropped; a stop or a timeout sends ``llm.cancel`` and
returns what was produced so far with an error; text is cut into clauses as it arrives, exactly like
the Ollama client, so the first spoken word does not wait for the whole answer. Stdlib only (the
engine may not import the app).
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from dodaios.voice_engine import protocol as p
from dodaios.voice_engine.chunker import ClauseChunker
from dodaios.voice_engine.llm import LlmResult

_POLL_S = 0.05


class BridgeChat:
    """Implements the engine's ``ChatModel`` protocol over the worker<->app frames."""

    # The engine passes ``session=`` so the app can route and cancel per session.
    needs_session = True

    def __init__(self, *, first_delta_timeout_s: float = 60.0, idle_timeout_s: float = 30.0,
                 total_timeout_s: float = 180.0) -> None:
        self._emit: Any = None
        self._pending: dict[str, queue.SimpleQueue[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._first = first_delta_timeout_s
        self._idle = idle_timeout_s
        self._total = total_timeout_s
        self.dropped_stale = 0
        self.model = "bridge"

    def attach(self, emit: Any) -> None:
        self._emit = emit

    def deliver(self, message: dict[str, Any]) -> bool:
        """Hand an ``llm.delta`` frame to the waiting call. False when no call is waiting for it."""
        call_id = str(message.get("id") or "")
        with self._lock:
            inbox = self._pending.get(call_id)
            if inbox is None:
                self.dropped_stale += 1
                return False
        inbox.put(message)
        return True

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        chunker: ClauseChunker | None = None,
        on_clause: Callable[[str, float], None] | None = None,
        stop: threading.Event | None = None,
        session: str = "",
    ) -> LlmResult:
        started = time.perf_counter()
        result = LlmResult()
        if self._emit is None:
            result.error = "the bridge is not attached to the engine"
            return result
        chunker = chunker or ClauseChunker()
        call_id = f"llm_{uuid.uuid4().hex[:12]}"
        inbox: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        with self._lock:
            self._pending[call_id] = inbox
        self._emit.json({"type": p.LLM_REQUEST, "id": call_id, "session": session,
                         "messages": messages, "tools": tools or []})
        last = time.monotonic()
        got_first = False
        try:
            while True:
                if stop is not None and stop.is_set():
                    self._cancel(call_id, session)
                    result.error = "cancelled"
                    break
                now = time.monotonic()
                limit = self._idle if got_first else self._first
                if now - last > limit or time.perf_counter() - started > self._total:
                    self._cancel(call_id, session)
                    result.error = "the model did not answer in time"
                    break
                try:
                    frame = inbox.get(timeout=_POLL_S)
                except queue.Empty:  # nothing yet: loop to re-check stop and the timeouts
                    continue
                last = time.monotonic()
                if frame.get("error"):
                    result.error = str(frame["error"])[:300]
                    break
                at = time.perf_counter() - started
                content = frame.get("content")
                if content:
                    got_first = True
                    if result.t_first_token is None:
                        result.t_first_token = at
                    result.text += str(content)
                    for clause in chunker.feed(str(content)):
                        self._clause(result, clause, at, on_clause)
                call = frame.get("tool_call")
                if isinstance(call, dict) and call.get("name"):
                    got_first = True
                    if result.t_first_token is None:
                        result.t_first_token = at
                    arguments = call.get("arguments", call.get("input"))
                    result.tool_calls.append(
                        {"name": str(call["name"]),
                         "arguments": arguments if isinstance(arguments, dict) else {}}
                    )
                usage = frame.get("usage")
                if isinstance(usage, dict):
                    result.prompt_tokens = int(usage.get("input_tokens") or 0)
                    result.eval_tokens = int(usage.get("output_tokens") or 0)
                if frame.get("finish"):
                    break
        finally:
            with self._lock:
                self._pending.pop(call_id, None)
        end = time.perf_counter() - started
        if not result.error:
            for clause in chunker.flush():
                self._clause(result, clause, end, on_clause)
        result.t_done = end
        return result

    def _cancel(self, call_id: str, session: str) -> None:
        self._emit.json({"type": p.LLM_CANCEL, "id": call_id, "session": session})

    @staticmethod
    def _clause(result: LlmResult, clause: str, at: float,
                on_clause: Callable[[str, float], None] | None) -> None:
        if result.t_first_clause is None:
            result.t_first_clause = at
        result.clauses.append(clause)
        if on_clause is not None:
            on_clause(clause, at)
