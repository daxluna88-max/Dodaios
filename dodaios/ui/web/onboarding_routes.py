"""FastAPI routes for the first-time onboarding guide.

All reads fail open (never 5xx): a missing/corrupt state file or a failing
first-run probe reports a safe "incomplete" default so the UI gate can decide
its own behaviour. There is NO server-side wake-word trademark rejection —
the legal posture rests on the accepted Terms + the wake-word acknowledgment,
not on blocking.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from dodaios.setup import first_run as fr
from dodaios.setup import state as st
from dodaios.setup.onboarding_fastpath import state_payload as _shared_state_payload
from dodaios.setup.onboarding_meta import CURRENT_TERMS_VERSION, read_terms_text

from .lifecycle_guard import require_interactive_desktop_action

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# Tests override this to redirect the state file; production leaves it None
# (state.py resolves the default data/setup_state.json).
_STATE_PATH_OVERRIDE: Path | None = None
_COMPLETE_LOCK = threading.Lock()


def _path() -> Path | None:
    return _STATE_PATH_OVERRIDE


class StepBody(BaseModel):
    step: str
    skipped: list[str] | None = None


def _safe_state_payload() -> dict:
    """Build the GET /state payload — shared with the fast-boot handler
    (dodaios.setup.onboarding_fastpath) so the warming window and the real app
    can never disagree. Never raises — fails open to 'incomplete'."""
    return _shared_state_payload(_path())


@router.get("/state")
def get_state(response: Response) -> dict:
    # Same no-cache guarantee the fast-boot handler gives: the gate must never
    # act on a cached completed/incomplete verdict.
    response.headers["cache-control"] = "no-store, max-age=0"
    return _safe_state_payload()


@router.get("/terms")
def get_terms() -> dict:
    return {"version": CURRENT_TERMS_VERSION, "text": read_terms_text()}


@router.post("/step")
def post_step(body: StepBody) -> dict:
    st.set_onboarding_step(body.step, skipped=body.skipped, path=_path())
    return {"ok": True}


@router.post("/accept-terms")
def post_accept_terms() -> dict:
    st.accept_terms(CURRENT_TERMS_VERSION, path=_path())
    return {"ok": True, "version": CURRENT_TERMS_VERSION}


def _schedule_app_shutdown(request: Request) -> None:
    """Quit the whole app shortly after the decline response flushes.

    Desktop hosts get the clean ``DesktopApp.request_quit()`` sequence (mark
    quit → destroy window → hard-exit backstop, same as the restart path but
    without a relauncher). Headless hosts have no window to destroy, so a
    short-delayed hard exit is the honest equivalent — the HTTP 200 reaches
    the browser first, then the server is gone.
    """
    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "request_quit", None)
    if callable(fn):
        try:
            if fn():
                return
        except Exception:  # noqa: BLE001 — a decline must still end the process
            log.warning("desktop quit failed; falling back to hard exit", exc_info=True)
    threading.Timer(0.8, os._exit, args=(0,)).start()


@router.post("/decline-terms")
def post_decline_terms(request: Request) -> dict:
    """Declining the Terms quits the app (design 2026-07-09).

    The install one-liner never asks anything, so the Terms gate on first
    launch is the ONE consent moment — declining it must not leave a
    half-running assistant behind. Nothing is persisted on decline: the next
    start shows the gate again. 409 once the Terms are accepted — the gate no
    longer exists then, and this endpoint must not double as a kill switch.
    """
    require_interactive_desktop_action(
        request, action="quit", require_origin=True
    )
    if _safe_state_payload()["terms"]["accepted"]:
        raise HTTPException(status_code=409, detail="terms already accepted")
    _schedule_app_shutdown(request)
    return {"ok": True, "quitting": True}


@router.post("/acknowledge-wake-word")
def post_ack_wake_word() -> dict:
    st.acknowledge_wake_word(_path())
    return {"ok": True}


def _schedule_fresh_restart(request: Request) -> bool:
    """Restart the app in a fresh process right after onboarding completes.

    The first launch happens straight out of the installer: the process the
    user just walked through onboarding has been running since BEFORE the
    language, wake word, and providers were configured. A relauncher restart
    (``DesktopApp.request_restart()`` — the same detached helper the
    self-update uses) re-initializes every subsystem from the now-complete
    config, so the assistant never lingers half-warm. The completion marker
    is persisted before this is called, so the fresh instance reads
    completed=true and can never re-open the gate. Headless hosts have no
    window — ``request_restart`` reports False and onboarding simply
    completes in place. Best-effort: a restart failure never fails the
    completion request.
    """
    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "request_restart", None)
    if not callable(fn):
        return False
    try:
        log.info("Desktop restart requested by onboarding completion")
        return bool(fn())
    except Exception:  # noqa: BLE001 — completing onboarding must never 500 here
        log.warning("post-onboarding fresh restart failed; staying up", exc_info=True)
        return False


@router.post("/complete")
def post_complete(request: Request) -> dict:
    require_interactive_desktop_action(
        request, action="restart", require_origin=True
    )
    # Sync routes run in a thread pool: serialize the check and transition so
    # simultaneous clicks cannot each schedule a restart.
    with _COMPLETE_LOCK:
        if st.is_onboarding_complete(_path()):
            return {"ok": True, "restarting": False}
        st.mark_onboarding_complete(_path())
        restarting = _schedule_fresh_restart(request)
    return {"ok": True, "restarting": restarting}


@router.post("/tour-complete")
def post_tour_complete() -> dict:
    """The guided tour of the real app was finished or skipped.

    No restart and no desktop guard: it only records that the one-time tour
    has been seen, exactly like the step progress route.
    """
    st.mark_tour_complete(_path())
    return {"ok": True}


# -- guided first run of Consumer / Work (issue 18) ------------------------------------------------


def _require_ui_click(request: Request) -> None:
    """Like the completion route: a Bearer client or a request without Origin is not a click."""
    headers = request.headers
    if headers.get("authorization", "").strip() or not headers.get("origin", "").strip():
        raise HTTPException(status_code=403, detail="the first run is applied from the app's UI")


def _first_run_payload() -> dict:
    """Saved choices, what is applied, and the summary read from the REAL configuration."""
    from dodaios.brain import engine_policy
    from dodaios.core import config as cfg_mod

    product = engine_policy.current_product()
    key = fr.product_key(product)
    choices = fr.load_choices(_path())
    try:
        summary = fr.summary(product, choices, cfg_mod.load_config())
    except Exception:  # noqa: BLE001 - the screen still opens; the summary says it is unavailable
        log.warning("first run: the summary could not be built", exc_info=True)
        summary = None
    return {
        "product": key,
        "choices": choices.model_dump(),
        "applied": fr.is_applied(product, choices, _path()),
        "presets": list(fr.PRESET_SETTINGS[key]),
        "default_preset": fr.DEFAULT_PRESET[key],
        "summary": summary,
    }


@router.get("/first-run")
def get_first_run(response: Response) -> dict:
    response.headers["cache-control"] = "no-store, max-age=0"
    return _first_run_payload()


@router.put("/first-run")
def put_first_run(body: fr.FirstRunChoices) -> dict:
    """Save the choices made so far (progress only: nothing is written to the configuration)."""
    fr.save_choices(body, _path())
    return _first_run_payload()


@router.post("/first-run/apply")
def post_first_run_apply(request: Request) -> dict:
    """Write the saved choices to the configuration; ok only when every write succeeded."""
    from dodaios.brain import engine_policy

    _require_ui_click(request)
    if st.is_onboarding_complete(_path()):
        # An installed app's approval mode and memory switch change in Settings,
        # never by replaying the guide.
        raise HTTPException(status_code=409, detail="onboarding is already complete; use Settings")
    try:
        fr.apply(engine_policy.current_product(), fr.load_choices(_path()), state_path=_path())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except fr.FirstRunError as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "first_run_not_applied", "step": exc.step, "written": exc.done},
        ) from exc
    return _first_run_payload()


@router.post("/first-run/reset")
def post_first_run_reset(request: Request) -> dict:
    """Forget the saved choices (explicit). The configuration already written is not reverted."""
    _require_ui_click(request)
    fr.reset_choices(_path())
    return _first_run_payload()


__all__ = ["router"]
