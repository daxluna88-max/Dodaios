"""Unit tests for the main-screen state (ADR-0058). Written, not run (issue 16 STOP)."""
from __future__ import annotations

from dodaios.control.stop import SubsystemResult
from dodaios.control.strip_state import STALE_AFTER_S, Observed, Reading, derive


def fresh(value):
    return Reading(value, 1.0)


def quiet(**kw) -> Observed:
    base = {
        "orb": fresh("idle"), "audio_playing": fresh(False),
        "jobs_running": fresh(0), "approvals_pending": fresh(0),
    }
    base.update(kw)
    return Observed(**base)


def test_nothing_going_on_with_fresh_readings_is_idle():
    assert derive(quiet()).summary == "idle"


def test_priority_error_waiting_working_speaking_thinking_listening():
    assert derive(quiet(error="engine_failed", approvals_pending=fresh(1))).summary == "error"
    assert derive(quiet(approvals_pending=fresh(1), jobs_running=fresh(2))).summary == "waiting"
    assert derive(quiet(jobs_running=fresh(2), audio_playing=fresh(True))).summary == "working"
    assert derive(quiet(audio_playing=fresh(True), orb=fresh("thinking"))).summary == "speaking"
    assert derive(quiet(orb=fresh("thinking"))).summary == "thinking"
    assert derive(quiet(orb=fresh("listening"))).summary == "listening"


def test_the_summary_never_hides_simultaneous_activity():
    state = derive(quiet(error="engine_failed", jobs_running=fresh(3),
                         approvals_pending=fresh(1), audio_playing=fresh(True)))
    assert state.summary == "error" and state.error == "engine_failed"
    assert (state.indicators.audio, state.indicators.jobs, state.indicators.approvals) == (
        "active", 3, 1)


def test_missing_or_old_telemetry_is_unknown_never_idle():
    assert derive(Observed()).summary == "unknown"
    old = Reading(0, STALE_AFTER_S + 1)
    assert derive(quiet(jobs_running=old)).summary == "unknown"
    assert derive(quiet(jobs_running=old)).indicators.jobs is None
    assert derive(quiet(orb=Reading("idle", None))).summary == "unknown"


def test_a_known_activity_still_shows_when_other_sources_are_unknown():
    assert derive(quiet(approvals_pending=Reading(), jobs_running=fresh(1))).summary == "working"


def test_stopped_only_after_an_explicit_stop_with_everything_quiet():
    ok = (SubsystemResult("a", "confirmed"),)
    assert derive(quiet(), "stopped", ok).summary == "stopped"
    assert derive(quiet(), "running").summary == "idle"


def test_never_stopped_while_something_observed_is_still_active():
    ok = (SubsystemResult("a", "confirmed"),)
    for kw in ({"jobs_running": fresh(1)}, {"audio_playing": fresh(True)},
               {"approvals_pending": fresh(1)}, {"orb": fresh("speaking")}):
        assert derive(quiet(**kw), "stopped", ok).summary == "partly_stopped"


def test_stopping_and_partly_stopped_name_what_is_not_confirmed():
    results = (SubsystemResult("a", "confirmed"), SubsystemResult("voice", "unknown", "no_probe"),
               SubsystemResult("worker", "still_active", "still_running"))
    state = derive(quiet(), "partly_stopped", results)
    assert state.summary == "partly_stopped" and state.not_confirmed == ("voice", "worker")
    assert derive(quiet(), "stopping").summary == "stopping"


def test_a_stop_in_progress_wins_over_an_error_but_the_error_is_kept():
    state = derive(quiet(error="engine_failed"), "stopping")
    assert state.summary == "stopping" and state.error == "engine_failed"


def test_bad_values_are_treated_as_unknown():
    assert derive(quiet(jobs_running=fresh(-1))).indicators.jobs is None
    assert derive(quiet(jobs_running=fresh(True))).indicators.jobs is None
    assert derive(quiet(orb=fresh("dancing"))).summary == "unknown"
