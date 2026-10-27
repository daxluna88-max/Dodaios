"""Block 9: the turn record's identity, lifecycle and compare-and-set (written, NOT run)."""

from __future__ import annotations

from dodaios.control import turn_record as tr


def _open(ledger: tr.TurnLedger, turn: str, gen: int = 1, run: str = "r1") -> tr.RecordKey:
    key = ledger.begin(tr.TurnRecord(
        turn, generation=gen, run_id=run, phase="planned", state="running",
        planned_partial=False))
    assert key is not None
    return key


def test_a_new_run_starts_planned_with_no_engine():
    ledger = tr.TurnLedger()
    _open(ledger, "t")
    last = ledger.last()
    assert last.lifecycle == "planned" and last.engines == () and last.state == "running"


def test_the_engine_and_model_are_the_ones_passed_at_dispatch():
    ledger = tr.TurnLedger()
    key = _open(ledger, "t")
    assert ledger.attempt(key, provider="openai", model="gpt-x", locality="remote",
                          requested="openai")
    last = ledger.last()
    assert last.lifecycle == "attempted"
    assert last.engines == (tr.EngineUse("openai", "main", "remote", "gpt-x"),)
    assert last.planned == (tr.Flow("question", "openai"),) and last.planned_partial


def test_a_fallback_replaces_the_engine_and_remembers_the_failed_one():
    ledger = tr.TurnLedger()
    key = _open(ledger, "t")
    ledger.attempt(key, provider="a", model="m1", locality="remote", requested="a")
    ledger.attempt(key, provider="b", model=None, locality="local", requested="a")
    assert ledger.finish(key, provider="b", locality="local", answered=True)
    last = ledger.last()
    assert last.engines[0].provider == "b" and last.earlier_attempts == ("a:m1",)
    assert last.fell_back and last.lifecycle == "completed" and last.planned == ()


def test_a_fast_path_turn_shows_no_engine_and_no_flow():
    ledger = tr.TurnLedger()
    key = _open(ledger, "t")
    ledger.finish(key, provider=None, locality="unknown", answered=True)
    last = ledger.last()
    assert last.engines == () and last.planned == () and not last.planned_partial
    assert last.lifecycle == "completed" and last.inference == "none"


def test_a_late_writer_of_an_older_turn_is_ignored_on_every_write():
    ledger = tr.TurnLedger()
    old = _open(ledger, "old", run="r-old")
    new = _open(ledger, "new", run="r-new")
    assert not ledger.attempt(old, provider="a", model=None, locality="remote")
    assert not ledger.finish(old, provider="a", locality="remote", answered=True)
    assert not ledger.apply(old, tr.TurnRecord("old", engines=(tr.EngineUse("a"),)))
    assert not ledger.update(old, phase="failed")
    assert ledger.last().key == new and ledger.last().lifecycle == "planned"


def test_the_same_turn_id_with_another_run_id_is_not_the_same_record():
    ledger = tr.TurnLedger()
    first = _open(ledger, "t", run="r1")
    _open(ledger, "t", run="r2")
    assert not ledger.finish(first, provider="a", locality="local", answered=True)
    assert ledger.last().run_id == "r2"


def test_a_run_from_an_older_stop_generation_never_replaces_the_current_record():
    ledger = tr.TurnLedger()
    current = _open(ledger, "new", gen=2)
    assert ledger.begin(tr.TurnRecord("stale", generation=1)) is None
    assert not ledger.record(tr.TurnRecord("stale2", generation=1))
    assert ledger.last().key == current


def test_a_terminal_record_is_not_reopened_by_a_late_attempt_or_finish():
    ledger = tr.TurnLedger()
    key = _open(ledger, "t")
    ledger.attempt(key, provider="a", model=None, locality="remote")
    ledger.finish(key, provider="a", locality="remote", answered=True)
    assert not ledger.attempt(key, provider="b", model=None, locality="remote")
    assert not ledger.finish(key, provider="b", locality="remote", answered=False)
    assert ledger.last().engines[0].provider == "a" and ledger.last().lifecycle == "completed"


def test_superseded_by_stop_is_explicit_even_after_the_engine_finished():
    ledger = tr.TurnLedger()
    key = _open(ledger, "t")
    ledger.attempt(key, provider="a", model=None, locality="remote")
    ledger.finish(key, provider="a", locality="remote", answered=True)
    assert ledger.finish(key, provider=None, locality="unknown", answered=False, superseded=True)
    last = ledger.last()
    assert last.lifecycle == "superseded" and last.state == "superseded"
    assert last.fell_back is False


def test_update_keeps_phase_and_state_consistent():
    ledger = tr.TurnLedger()
    key = _open(ledger, "t")
    ledger.update(key, phase="failed")
    assert ledger.last().state == "failed"


def test_two_concurrent_runs_do_not_swap_engines():
    ledger = tr.TurnLedger()
    a = _open(ledger, "a", run="ra")
    b = _open(ledger, "b", run="rb")
    ledger.attempt(a, provider="from-a", model=None, locality="remote")  # ignored: b is current
    ledger.attempt(b, provider="from-b", model="mb", locality="remote")
    ledger.finish(a, provider="from-a", locality="remote", answered=True)  # ignored
    last = ledger.last()
    assert last.turn_id == "b" and last.engines[0].provider == "from-b"
    assert last.lifecycle == "attempted"
