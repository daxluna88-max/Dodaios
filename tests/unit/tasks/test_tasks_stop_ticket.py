"""Scheduled and background tasks take their STOP ticket at creation and re-verify it (ADR-0058)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dodaios.control.cancel import CancelToken
from dodaios.control.stop import get_stop_controller, reset_stop_controller, work_ticket
from dodaios.core.bus import EventBus
from dodaios.tasks.runner import TaskRunner
from dodaios.tasks.scheduler import TaskScheduler
from dodaios.tasks.schema import (
    AgentAction,
    SpeakAction,
    TaskSpec,
    TriggerAfterDelay,
    TriggerEvery,
)
from dodaios.tasks.store import TaskStore


@pytest.fixture(autouse=True)
def _fresh_controller():
    reset_stop_controller()
    yield
    reset_stop_controller()


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "stop.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


class _Runner:
    def __init__(self) -> None:
        self.runs: list[str] = []
        self.tickets: list[int | None] = []

    async def run(self, task_id: str, cancel_token: Any = None, **_kw: Any) -> None:
        self.runs.append(task_id)
        self.tickets.append(work_ticket.get())


async def _task(store: TaskStore) -> str:
    return await store.insert(TaskSpec(
        title="t", trigger=TriggerAfterDelay(delay_seconds=0.01), action=SpeakAction(text="x")))


# ---- scheduler -------------------------------------------------------------------------


async def test_a_run_created_before_stop_does_not_start_after_resume(store: TaskStore):
    runner = _Runner()
    scheduler = TaskScheduler(store=store, bus=EventBus(), runner=runner)
    tid = await _task(store)
    ticket = get_stop_controller().ticket()  # taken when the run was created
    await get_stop_controller().stop()
    get_stop_controller().resume()
    assert await scheduler._safe_run(tid, None, stop_ticket=ticket) is False
    assert runner.runs == []
    row = await store.get(tid)
    assert row is not None and row["state"] == "scheduled"  # untouched, not "running"


async def test_a_run_dispatched_while_stop_lands_never_reaches_the_runner(store: TaskStore):
    runner = _Runner()
    scheduler = TaskScheduler(store=store, bus=EventBus(), runner=runner)
    tid = await _task(store)
    await scheduler._dispatch_runner(tid)  # the asyncio task exists, but has not run yet
    await get_stop_controller().stop()  # the door closes before that task gets to run
    await scheduler.shutdown()
    assert runner.runs == []


async def test_a_run_started_normally_hands_its_ticket_down(store: TaskStore):
    runner = _Runner()
    scheduler = TaskScheduler(store=store, bus=EventBus(), runner=runner)
    tid = await _task(store)
    ticket = get_stop_controller().ticket()
    assert await scheduler._safe_run(tid, None) is not False
    assert runner.runs == [tid] and runner.tickets == [ticket]
    assert work_ticket.get() is None  # nothing leaks out of the call


async def test_a_hook_delivery_stopped_before_it_starts_is_interrupted_not_done():
    marks: list[str] = []

    async def get(_tid: str) -> dict[str, Any]:
        return {"state": "scheduled", "last_error": None}

    async def mark(_tid: str, _delivery: str, outcome: str) -> None:
        marks.append(outcome)

    store = SimpleNamespace(
        get=get, hooks=SimpleNamespace(mark=mark),
        append_step=lambda *a, **k: asyncio.sleep(0),
    )
    scheduler = TaskScheduler(store=store, bus=EventBus(), runner=_Runner())  # type: ignore[arg-type]
    row = {"task_id": "t1", "delivery_id": "d1", "payload_json": "{}", "lineage_json": "[]"}
    ticket = get_stop_controller().ticket()
    await get_stop_controller().stop()
    get_stop_controller().resume()
    await scheduler._run_hook(row, stop_ticket=ticket)
    assert marks == ["interrupted"]  # never "done": the stale delivery is not replayed later


# ---- runner ----------------------------------------------------------------------------


class _Brain:
    def __init__(self, stop_midway: bool = False) -> None:
        self.prompts: list[str] = []
        self.seen_ticket: list[int | None] = []
        self._stop = stop_midway

    async def run_task(self, *, prompt: str, **_kw: Any) -> str:
        self.prompts.append(prompt)
        self.seen_ticket.append(work_ticket.get())
        if self._stop:
            await get_stop_controller().stop()
        return "result"


def _agent_spec() -> TaskSpec:
    return TaskSpec(
        title="[agent:Mailbox] sweep",
        trigger=TriggerAfterDelay(delay_seconds=0.01),
        action=AgentAction(prompt="Sort the inbox."),
        tags=("society", "agent:mailbox"),
    )


async def test_the_runner_does_not_start_a_run_while_the_door_is_closed(store: TaskStore):
    brain = _Brain()
    runner = TaskRunner(store, EventBus(), agent_brain=brain)
    tid = await store.insert(_agent_spec())
    await get_stop_controller().stop()
    await runner.run(tid, CancelToken())
    assert brain.prompts == []
    row = await store.get(tid)
    assert row is not None and row["state"] == "scheduled"


async def test_a_run_stopped_midway_delivers_nothing(store: TaskStore):
    delivered: list[str] = []

    async def sink(tags: Any, text: str, status: str) -> None:
        delivered.append(status)

    brain = _Brain(stop_midway=True)
    runner = TaskRunner(store, EventBus(), agent_brain=brain, result_sink=sink)
    tid = await store.insert(_agent_spec())
    await runner.run(tid, CancelToken())
    assert brain.prompts == ["Sort the inbox."] and delivered == []
    row = await store.get(tid)
    assert row is not None and row["state"] == "cancelled"


async def test_the_ticket_reaches_the_brain_turn_of_an_agent_run(store: TaskStore):
    brain = _Brain()
    runner = TaskRunner(store, EventBus(), agent_brain=brain)
    tid = await store.insert(_agent_spec())
    ticket = get_stop_controller().ticket()
    await runner.run(tid, CancelToken())
    assert brain.seen_ticket == [ticket]


async def test_a_recurring_routine_overtaken_by_stop_keeps_its_schedule(store: TaskStore):
    brain = _Brain(stop_midway=True)
    runner = TaskRunner(store, EventBus(), agent_brain=brain)
    spec = TaskSpec(
        title="every hour",
        trigger=TriggerEvery(interval_seconds=3600),
        action=AgentAction(prompt="Sort the inbox."),
    )
    tid = await store.insert(spec)
    await runner.run(tid, CancelToken())
    row = await store.get(tid)
    assert row is not None and row["state"] == "scheduled"
