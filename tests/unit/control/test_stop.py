"""Unit tests for the STOP coordinator (ADR-0058). Written, not run (issue 16 STOP)."""
from __future__ import annotations

import asyncio

from dodaios.control.stop import StopController, Subsystem


def controller(*parts: Subsystem) -> StopController:
    return StopController(parts, request_timeout_s=0.2, probe_timeout_s=0.2, settle_s=0.3)


def part(
    name: str, *, probe: str | None = "confirmed", calls: list[str] | None = None
) -> Subsystem:
    async def request() -> None:
        if calls is not None:
            calls.append(name)

    async def answer() -> str:
        return probe  # type: ignore[return-value]

    return Subsystem(name, request, None if probe is None else answer)


async def test_the_door_is_closed_before_anything_is_awaited():
    seen: dict[str, bool] = {}
    ctl: StopController

    async def request() -> None:
        seen["open_while_stopping"] = ctl.door_open
        seen["admit"] = ctl.admit().allowed

    async def probe() -> str:
        return "confirmed"

    ctl = controller(Subsystem("x", request, probe))
    await ctl.stop()
    assert seen == {"open_while_stopping": False, "admit": False}


async def test_all_confirmed_is_stopped_and_anything_else_is_partly_stopped():
    report = await controller(part("a"), part("b")).stop()
    assert report.state == "stopped" and {r.outcome for r in report.results} == {"confirmed"}
    report = await controller(part("a"), part("b", probe="still_active")).stop()
    assert report.state == "partly_stopped"
    assert {r.name: r.outcome for r in report.results} == {"a": "confirmed", "b": "still_active"}


async def test_a_part_without_a_probe_is_unknown_never_confirmed():
    report = await controller(part("voice", probe=None)).stop()
    assert report.results[0].outcome == "unknown" and report.results[0].detail == "no_probe"
    assert report.state == "partly_stopped"


async def test_a_hanging_or_failing_part_does_not_block_the_others():
    async def hang() -> None:
        await asyncio.sleep(10)

    async def boom() -> None:
        raise RuntimeError("no")

    async def probe_ok() -> str:
        return "confirmed"

    async def probe_slow() -> str:
        await asyncio.sleep(10)
        return "confirmed"

    ctl = controller(
        part("good"), Subsystem("hang", hang, probe_slow), Subsystem("boom", boom, probe_ok))
    report = await asyncio.wait_for(ctl.stop(), 5)
    got = {r.name: (r.outcome, r.detail) for r in report.results}
    assert got["good"] == ("confirmed", "ok")
    assert got["boom"] == ("unknown", "request_failed")
    assert got["hang"][0] == "unknown"
    assert report.state == "partly_stopped"


async def test_a_probe_that_raises_is_unknown():
    async def request() -> None:
        return None

    async def probe() -> str:
        raise RuntimeError("no")

    report = await controller(Subsystem("p", request, probe)).stop()
    assert report.results[0].outcome == "unknown" and report.state == "partly_stopped"


async def test_a_part_that_settles_during_the_window_is_confirmed():
    answers = iter(["still_active", "still_active", "confirmed"])

    async def request() -> None:
        return None

    async def probe() -> str:
        return next(answers)

    report = await controller(Subsystem("slow", request, probe)).stop()
    assert report.results[0].outcome == "confirmed"


async def test_a_repeated_stop_only_asks_the_unconfirmed_parts_and_keeps_the_generation():
    calls: list[str] = []
    state = {"ok": False}

    async def request() -> None:
        calls.append("flaky")

    async def probe() -> str:
        return "confirmed" if state["ok"] else "still_active"

    ctl = controller(part("steady", calls=calls), Subsystem("flaky", request, probe))
    first = await ctl.stop()
    generation = ctl.generation
    state["ok"] = True
    second = await ctl.stop()
    assert first.state == "partly_stopped" and second.state == "stopped" and second.repeated
    assert ctl.generation == generation
    assert calls.count("steady") == 1 and calls.count("flaky") == 2


async def test_two_stops_at_once_share_one_run():
    calls: list[str] = []
    ctl = controller(part("a", calls=calls))
    one, two = await asyncio.gather(ctl.stop(), ctl.stop())
    assert calls == ["a"] and one.generation == two.generation == 1


async def test_a_turn_queued_before_stop_stays_refused_after_resume():
    ctl = controller(part("a"))
    queued = ctl.ticket()
    assert ctl.admit(queued).allowed
    await ctl.stop()
    assert ctl.admit(queued).reason == "stopped" and not ctl.admit().allowed
    ctl.resume()
    assert ctl.admit(queued).reason == "stale_generation"
    assert ctl.admit().allowed and ctl.admit(ctl.ticket()).allowed


async def test_resume_is_explicit_and_a_no_op_when_the_door_is_open():
    ctl = controller(part("a"))
    before = ctl.generation
    assert ctl.resume() == before and ctl.door_open
    await ctl.stop()
    assert ctl.state == "stopped" and not ctl.door_open
    new = ctl.resume()
    assert new == ctl.generation > before and ctl.state == "running"


async def test_stop_works_with_no_parts_registered():
    report = await controller().stop()
    assert report.state == "stopped" and report.results == ()
