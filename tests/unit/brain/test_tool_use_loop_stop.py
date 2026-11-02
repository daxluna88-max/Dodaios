"""A turn that started before STOP invokes no more tools and asks the model nothing more."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from dodaios.brain import manager as mgr_mod
from dodaios.brain.tool_use_loop import ToolUseLoop
from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.core.protocols import BrainDelta, BrainMessage, BrainRequest, ToolResult


@pytest.fixture(autouse=True)
def _fresh_controller():
    reset_stop_controller()
    yield
    reset_stop_controller()


class _Tool:
    name = "gmail"
    schema: dict[str, Any] = {}
    description = "mail"


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, output="sent")


class _Brain:
    """Round 1 asks for two tools; STOP lands while that round streams."""

    def __init__(self, stop_during_round: int | None) -> None:
        self.requests: list[BrainRequest] = []
        self._stop_during = stop_during_round

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        n = len(self.requests)
        if self._stop_during == n:
            await get_stop_controller().stop()
        if n <= 2:
            yield BrainDelta(tool_call={"id": f"a{n}", "name": "gmail", "input": {}})
            yield BrainDelta(tool_call={"id": f"b{n}", "name": "gmail", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="done")
        yield BrainDelta(finish_reason="stop")


async def _run(brain: _Brain, executor: _Executor) -> Any:
    token = mgr_mod._STOP_TURN.set(mgr_mod._StopTurn(get_stop_controller().ticket()))
    try:
        loop = ToolUseLoop(brain, {"gmail": _Tool()}, executor)
        return await loop.run([BrainMessage(role="user", content="mail them")])
    finally:
        mgr_mod._STOP_TURN.reset(token)


async def test_no_tool_runs_when_stop_landed_while_the_model_was_answering():
    executor = _Executor()
    brain = _Brain(stop_during_round=1)
    out = await _run(brain, executor)
    assert executor.calls == 0
    assert len(brain.requests) == 1  # and the model is not asked again
    assert out.finish_reason == "stopped_by_owner"


async def test_no_tool_runs_after_stop_and_resume_either():
    executor = _Executor()
    brain = _Brain(stop_during_round=1)
    token = mgr_mod._STOP_TURN.set(mgr_mod._StopTurn(get_stop_controller().ticket()))
    try:
        original = brain.complete

        async def complete(req: BrainRequest) -> AsyncIterator[BrainDelta]:
            async for delta in original(req):
                yield delta
            get_stop_controller().resume()  # reopened before the tools run

        brain.complete = complete  # type: ignore[method-assign]
        await ToolUseLoop(brain, {"gmail": _Tool()}, executor).run(
            [BrainMessage(role="user", content="mail them")]
        )
    finally:
        mgr_mod._STOP_TURN.reset(token)
    assert executor.calls == 0


async def test_a_turn_stopped_between_two_tools_runs_only_the_first():
    executor = _Executor()

    class _StopsAfterFirst(_Executor):
        async def execute(self, tool: Any, args: dict[str, Any], **kw: Any) -> ToolResult:
            result = await super().execute(tool, args, **kw)
            await get_stop_controller().stop()
            return result

    executor = _StopsAfterFirst()
    out = await _run(_Brain(stop_during_round=None), executor)
    assert executor.calls == 1
    assert out.finish_reason == "stopped_by_owner"


async def test_a_turn_that_was_not_stopped_runs_every_tool():
    executor = _Executor()
    out = await _run(_Brain(stop_during_round=None), executor)
    assert executor.calls == 4 and out.text == "done"


async def test_a_loop_outside_any_turn_is_unaffected():
    executor = _Executor()
    await get_stop_controller().stop()
    loop = ToolUseLoop(_Brain(None), {"gmail": _Tool()}, executor)
    out = await loop.run([BrainMessage(role="user", content="x")])
    assert executor.calls == 4 and out.text == "done"
