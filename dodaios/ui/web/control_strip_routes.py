"""Main screen control strip: STOP, resume and the combined state (ADR-0058).

* ``GET  /api/control-strip`` the one-line state, the simultaneous indicators, the engine(s) of the
  last turn (inference kept apart from network/tools, planned apart from attempted), the jobs that
  run and the decisions that wait. Read-only; assembled from existing sources.
* ``POST /api/stop`` the owner's STOP for THIS product: the door closes first, then every part is
  asked on its own, each with its own outcome (requested / confirmed / still_active / unknown).
* ``POST /api/stop/resume`` the owner's deliberate resume; opens a new generation.

Both write routes sit behind the same Host / Origin / credential checks as every other ``/api``
route (``surface_security``); they are in none of its exception lists and grant nothing: STOP
only removes work, resume only reopens the door that was closed.

Scope is the product process: every session of this product, nothing of the other product or of
another installation. Session overrides (mood, style nudges) are neither read nor reset.

Honest limits, reported rather than hidden: playback / realtime and mission worker processes have
no verified stop here, so they come back ``unknown`` or ``still_active``; a mission whose state was
flipped but whose worker was not confirmed killed is not counted as stopped.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from dodaios.control.stop import (
    StopController,
    Subsystem,
    get_stop_controller,
)
from dodaios.control.strip_state import Observed, Reading, derive

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["control"])

_BG_PREFIX = "jarvis-agent-"
_INSTALLED_ATTR = "stop_subsystems_installed"
#: Missions whose worker was NOT confirmed killed by the last STOP (state flipped only).
_UNCONFIRMED_WORKERS_ATTR = "stop_unconfirmed_workers"


def _background_tasks() -> list[asyncio.Task[Any]]:
    try:
        return [t for t in asyncio.all_tasks() if (t.get_name() or "").startswith(_BG_PREFIX)
                and not t.done()]
    except RuntimeError:  # no running loop here, so there are no tasks to count
        return []


def _brain(request: Any) -> Any:
    return getattr(request.app.state, "brain", None)


# -- parts that STOP asks, each with its own probe -------------------------------------------


def _kill_switch_part() -> Subsystem:
    from dodaios.control.cancel import get_kill_switch  # noqa: PLC0415

    async def request() -> None:
        await get_kill_switch().trip("kill_switch:web")

    async def probe() -> Any:
        return "confirmed" if not list(get_kill_switch().active_tokens()) else "still_active"

    return Subsystem("kill_switch_holders", request, probe)


def _cu_runs_part() -> Subsystem:
    from dodaios.harness import cu_run_registry  # noqa: PLC0415

    async def request() -> None:
        cu_run_registry.cancel_all_runs("stop")

    async def probe() -> Any:
        return "confirmed" if cu_run_registry.active_run_count() == 0 else "still_active"

    return Subsystem("computer_use_runs", request, probe)


def _background_part(ref: Any) -> Subsystem:
    async def request() -> None:
        brain = _brain(ref)
        cancel = getattr(brain, "_cancel_all_background_tasks", None)
        if callable(cancel):
            cancel()
            return
        for task in _background_tasks():
            task.cancel()

    async def probe() -> Any:
        return "confirmed" if not _background_tasks() else "still_active"

    return Subsystem("background_agents", request, probe)


def _approvals_part(ref: Any) -> Subsystem:
    def coordinator() -> Any:
        return getattr(ref.app.state, "mission_tool_approvals", None)

    async def request() -> None:
        coord = coordinator()
        if coord is not None:
            await coord.deny_all(reason="stopped_by_owner")

    async def probe() -> Any:
        coord = coordinator()
        if coord is None:
            return "unknown"
        return "confirmed" if await coord.pending_count() == 0 else "still_active"

    return Subsystem("pending_approvals", request, probe)


def _missions_part(ref: Any) -> Subsystem:
    """Cancel every non-terminal mission through the existing cancel route's own logic.

    The state flip alone is not an arrest: only a mission whose worker the cancel path reports
    killed counts. A mission with no confirmation keeps this part ``unknown``.
    """
    state = ref.app.state

    async def request() -> None:
        from dodaios.ui.web import missions_routes  # noqa: PLC0415

        mgr = getattr(state, "mission_manager", None)
        setattr(state, _UNCONFIRMED_WORKERS_ATTR, set())
        if mgr is None:
            return
        for mission_id, _prompt, _state in await mgr.store.list_non_terminal_missions():
            try:
                result = await missions_routes.cancel_mission(mission_id, ref)
            except HTTPException:
                continue  # already terminal / gone: nothing to stop
            if not result.get("worker_killed"):
                getattr(state, _UNCONFIRMED_WORKERS_ATTR).add(mission_id)

    async def probe() -> Any:
        mgr = getattr(state, "mission_manager", None)
        if mgr is None:
            return "confirmed"
        if await mgr.store.list_non_terminal_missions():
            return "still_active"
        if getattr(state, _UNCONFIRMED_WORKERS_ATTR, set()):
            return "unknown"
        return "confirmed"

    return Subsystem("missions", request, probe)


async def _no_op() -> None:
    return None


def _install(request: Request) -> StopController:
    """Register the parts once per app (idempotent). Voice playback and realtime have no verified
    stop, so they are present with NO probe: their outcome is always ``unknown``."""
    controller = get_stop_controller()
    state = request.app.state
    if getattr(state, _INSTALLED_ATTR, False):
        return controller
    # The parts outlive this request: they hold the app, not the request.
    ref = SimpleNamespace(app=request.app)
    for part in (
        _kill_switch_part(),
        _cu_runs_part(),
        _background_part(ref),
        _approvals_part(ref),
        _missions_part(ref),
        Subsystem("voice_playback", _no_op, None),
        Subsystem("realtime_session", _no_op, None),
    ):
        controller.add(part)
    setattr(state, _INSTALLED_ATTR, True)
    return controller


# -- the combined state ----------------------------------------------------------------------


async def _voice_reading(request: Request) -> tuple[Reading, Reading]:
    """(orb state, audio playing) from the voice REST mirror; unknown when it cannot be read."""
    try:
        from dodaios.ui.web.voice_call_routes import voice_state  # noqa: PLC0415

        answer = await voice_state(request)
    except Exception:  # noqa: BLE001 - an unreadable voice state is "unknown", never "idle"
        log.debug("control strip: the voice state could not be read", exc_info=True)
        return Reading(), Reading()
    if not answer.get("available", False):
        return Reading("idle", 0.0), Reading(False, 0.0)
    orb = str(answer.get("voice_state") or "unknown")
    if orb not in ("idle", "listening", "thinking", "speaking", "error", "paused"):
        return Reading(), Reading()  # connecting / unknown: not a state we can claim
    return Reading(orb, 0.0), Reading(orb == "speaking", 0.0)


async def _pending_decisions(request: Request, mission_ids: list[str]) -> list[dict[str, Any]]:
    coord = getattr(request.app.state, "mission_tool_approvals", None)
    if coord is None:
        return []
    out: list[dict[str, Any]] = []
    for mission_id in mission_ids:
        for item in await coord.list_pending(mission_id):
            out.append(item.to_dict())
    return out


async def _snapshot(request: Request) -> dict[str, Any]:
    from dodaios.harness import cu_run_registry  # noqa: PLC0415

    controller = _install(request)
    app_state = request.app.state
    mgr = getattr(app_state, "mission_manager", None)
    missions: list[tuple[str, str, str]] = []
    missions_ok = mgr is not None
    if mgr is not None:
        try:
            missions = await mgr.store.list_non_terminal_missions()
        except Exception:  # noqa: BLE001 - an unreadable store is unknown, not zero
            log.warning("control strip: the missions could not be listed", exc_info=True)
            missions_ok = False
    decisions = await _pending_decisions(request, [m[0] for m in missions]) if missions_ok else []
    coord = getattr(app_state, "mission_tool_approvals", None)
    approvals_n: int | None = await coord.pending_count() if coord is not None else None
    cu_n = cu_run_registry.active_run_count()
    bg_n = len(_background_tasks())
    orb, audio = await _voice_reading(request)
    jobs = Reading(len(missions) + cu_n + bg_n, 0.0) if missions_ok else Reading()
    last = controller.last_report
    strip = derive(
        Observed(
            orb=orb, audio_playing=audio, jobs_running=jobs,
            approvals_pending=Reading(approvals_n, 0.0) if approvals_n is not None else Reading(),
        ),
        controller.state,
        last.results if last is not None and controller.state != "running" else (),
    )
    brain = _brain(request)
    ledger = getattr(brain, "_turn_ledger_obj", None)
    record = ledger.last() if ledger is not None else None
    return {
        "state": strip.summary,
        "not_confirmed": list(strip.not_confirmed),
        "door_open": controller.door_open,
        "generation": controller.generation,
        "indicators": {
            "audio": strip.indicators.audio,
            "jobs": strip.indicators.jobs,
            "approvals": strip.indicators.approvals,
        },
        "engine": _engine_payload(record),
        "jobs": {
            "missions": [{"id": m[0], "state": m[2]} for m in missions],
            "computer_use_runs": cu_n,
            "background_agents": bg_n,
        },
        "approvals": decisions,
        "stop": _report_payload(last) if controller.state != "running" else None,
    }


def _engine_payload(record: Any) -> dict[str, Any]:
    if record is None:
        return {"turn_id": None, "inference": "none", "engines": [],
                "network_tools": "not_observed", "planned": [], "attempted": [],
                "observability": "none", "refused": []}
    return {
        "turn_id": record.turn_id,
        "inference": record.inference,
        "engines": [{"provider": e.provider, "role": e.role, "locality": e.locality}
                    for e in record.engines],
        "network_tools": record.network_tools,
        "planned": [{"category": f.category, "destination": f.destination}
                    for f in record.planned],
        "attempted": [{"category": f.category, "destination": f.destination}
                      for f in record.attempted],
        "observability": record.observability,
        "refused": list(record.refused),
    }


def _report_payload(report: Any) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        "generation": report.generation,
        "state": report.state,
        "repeated": report.repeated,
        "results": [{"name": r.name, "outcome": r.outcome, "detail": r.detail}
                    for r in report.results],
    }


@router.get("/control-strip", summary="State, engine, jobs and decisions for the main screen")
async def get_control_strip(request: Request) -> dict[str, Any]:
    return await _snapshot(request)


@router.post("/stop", summary="Stop this product: door first, then every part on its own")
async def post_stop(request: Request) -> dict[str, Any]:
    report = await _install(request).stop()
    payload = await _snapshot(request)
    payload["stop"] = _report_payload(report)
    return payload


@router.post("/stop/resume", summary="The owner lets work start again (a new generation)")
async def post_resume(request: Request) -> dict[str, Any]:
    controller = _install(request)
    controller.resume()
    return await _snapshot(request)
