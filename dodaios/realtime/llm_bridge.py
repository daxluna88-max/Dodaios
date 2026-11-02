"""App side of the voice engine's LLM bridge: answer ``llm.request`` with a brain provider
(ADR-0051).

The engine worker (``dodaios.voice_engine.bridge_llm``) sends each model call to the app. This
module builds the provider through the normal registry (so the product policy of ADR-0049
applies: the Work product can only reach a local server), streams the answer back as
``llm.delta`` frames and cancels on ``llm.cancel``. VAD, STT, TTS, barge-in and the tool loop
stay in the engine; tool calls come back as data and the engine still routes them through
``tool.call`` and the ToolExecutor gate, so a provider switch never approves or executes
anything.

Per call: one task keyed by the request id. A cancelled call never sends another frame (the
stream is closed and nothing is written after the cancel), and a frame is only ever sent for a
registered call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from dodaios.core.protocols import BrainMessage, BrainRequest

log = logging.getLogger(__name__)

Send = Callable[[dict[str, Any]], Awaitable[None]]
Resolve = Callable[[], Any]


def to_brain_request(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
) -> BrainRequest:
    """The engine's chat-format history as a normalized ``BrainRequest`` (system text split out)."""
    system: list[str] = []
    out: list[BrainMessage] = []
    open_calls: dict[str, list[str]] = {}  # tool name -> ids of calls still waiting for a result
    counter = 0
    for message in messages:
        role = str(message.get("role") or "")
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        if role == "system":
            system.append(text)
        elif role == "assistant" and message.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if text:
                blocks.append({"type": "text", "text": text})
            for call in message["tool_calls"]:
                function = call.get("function") or {}
                counter += 1
                call_id = f"call_{counter}"
                name = str(function.get("name") or "")
                open_calls.setdefault(name, []).append(call_id)
                arguments = function.get("arguments")
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": arguments if isinstance(arguments, dict) else {},
                    }
                )
            out.append(BrainMessage(role="assistant", content=blocks))
        elif role == "tool":
            name = str(message.get("tool_name") or "")
            pending = open_calls.get(name) or []
            call_id = pending.pop(0) if pending else f"call_orphan_{len(out)}"
            out.append(
                BrainMessage(
                    role="tool",
                    tool_call_id=call_id,
                    name=name or None,
                    content=[{"type": "tool_result", "tool_use_id": call_id, "content": text}],
                )
            )
        elif role in ("user", "assistant"):
            out.append(BrainMessage(role=role, content=text))  # type: ignore[arg-type]
    return BrainRequest(
        messages=tuple(out),
        tools=tuple(_tool_spec(t) for t in tools or ()),
        system="\n\n".join(system) or None,
    )


def _tool_spec(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    return {
        "name": function.get("name", ""),
        "description": function.get("description", ""),
        "input_schema": function.get("parameters")
        or function.get("input_schema")
        or {"type": "object", "properties": {}},
    }


class BrainLlmBridge:
    def __init__(self, send: Send, resolve: Resolve) -> None:
        self._send = send
        self._resolve = resolve
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def route(self, message: dict[str, Any]) -> None:
        """Dispatch an ``llm.request`` / ``llm.cancel`` frame from the engine."""
        if message.get("type") == "llm.cancel":
            self.cancel(message)
        else:
            self.handle(message)

    def handle(self, message: dict[str, Any]) -> None:
        call_id = str(message.get("id") or "")
        if not call_id or call_id in self._tasks:
            return
        task = asyncio.get_running_loop().create_task(self._serve(call_id, message))
        self._tasks[call_id] = task
        task.add_done_callback(lambda _t, cid=call_id: self._tasks.pop(cid, None))

    def cancel(self, message: dict[str, Any]) -> None:
        task = self._tasks.get(str(message.get("id") or ""))
        if task is not None:
            task.cancel()

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _frame(self, call_id: str, **fields: Any) -> None:
        await self._send({"type": "llm.delta", "id": call_id, **fields})

    async def _serve(self, call_id: str, message: dict[str, Any]) -> None:
        stream: Any = None
        try:
            brain = self._resolve()
            request = to_brain_request(
                list(message.get("messages") or []), list(message.get("tools") or [])
            )
            stream = brain.complete(request)
            async for delta in stream:
                if delta.content:
                    await self._frame(call_id, content=delta.content)
                if delta.tool_call:
                    call = delta.tool_call
                    arguments = call.get("input", call.get("arguments"))
                    await self._frame(
                        call_id,
                        tool_call={
                            "name": call.get("name", ""),
                            "arguments": arguments if isinstance(arguments, dict) else {},
                        },
                    )
                if delta.usage:
                    await self._frame(call_id, usage=dict(delta.usage))
            await self._frame(call_id, finish=True)
        except asyncio.CancelledError:
            raise  # the engine cancelled: it expects nothing more for this call
        except Exception as exc:  # noqa: BLE001 - the engine gets the reason, the app stays up
            log.warning("voice LLM bridge call failed: %s", type(exc).__name__)
            with contextlib.suppress(Exception):
                await self._frame(call_id, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    await close()


def brain_resolver(provider: str, model: str | None) -> Resolve:
    """Builds the provider through the registry, where the product policy applies."""

    def resolve() -> Any:
        from dodaios.brain.provider_registry import BrainProviderRegistry  # noqa: PLC0415

        registry = BrainProviderRegistry()
        return (
            registry.instantiate(provider, model=model) if model else registry.instantiate(provider)
        )

    return resolve
