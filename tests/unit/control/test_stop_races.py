"""STOP / resume / new work races (ADR-0058, block 2). Simulated parts, no real workers."""
from __future__ import annotations

from dodaios.control.stop import StopController, Subsystem


def controller(*parts: Subsystem) -> StopController:
    return StopController(parts, request_timeout_s=0.2, probe_timeout_s=0.2, settle_s=0.3)


def part(name: str, calls: list[str] | None = None) -> Subsystem:
    async def request() -> None:
        if calls is not None:
            calls.append(name)

    async def probe() -> str:
        return "confirmed"

    return Subsystem(name, request, probe)


async def test_a_repeated_stop_checks_again_a_part_that_was_confirmed_before():
    """Work that slipped past the door after a confirmed first STOP is not forgotten."""
    state = {"alive": 0}
    calls: list[str] = []

    async def request() -> None:
        calls.append("req")
        state["alive"] = 0

    async def probe() -> str:
        return "still_active" if state["alive"] else "confirmed"

    ctl = controller(Subsystem("w", request, probe))
    first = await ctl.stop()
    assert first.state == "stopped" and calls == ["req"]
    state["alive"] = 1  # something started anyway (a path that does not use the door)
    second = await ctl.stop()
    assert second.repeated and second.state == "stopped"
    assert calls == ["req", "req"]  # asked again, because the probe no longer confirmed


async def test_a_repeated_stop_does_not_ask_a_part_that_still_confirms():
    calls: list[str] = []
    ctl = controller(part("a", calls))
    await ctl.stop()
    await ctl.stop()
    assert calls == ["a"]


async def test_an_unconfirmed_part_survives_stop_resume_stop_and_is_asked_again():
    calls: list[str] = []
    state = {"ok": False}

    async def request() -> None:
        calls.append("w")

    async def probe() -> str:
        return "confirmed" if state["ok"] else "still_active"

    ctl = controller(Subsystem("w", request, probe))
    first = await ctl.stop()
    assert first.state == "partly_stopped"
    ctl.resume()  # allowed: the STOP itself has finished
    late = ctl.ticket()
    second = await ctl.stop()  # a NEW generation: the old worker is asked about again
    assert not second.repeated and second.generation > first.generation
    assert second.results[0].outcome == "still_active" and calls == ["w", "w"]
    assert ctl.admit(late).reason == "stopped"
    state["ok"] = True
    third = await ctl.stop()
    assert third.state == "stopped" and third.generation == second.generation


async def test_work_created_while_the_door_is_closed_never_runs_after_resume():
    ctl = controller(part("a"))
    await ctl.stop()
    created_during_stop = ctl.ticket()  # taken at creation, door closed
    assert ctl.admit(created_during_stop).reason == "stopped"
    ctl.resume()
    assert ctl.admit(created_during_stop).reason == "stale_generation"
