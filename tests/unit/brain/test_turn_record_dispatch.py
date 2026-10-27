"""Block 9: the manager writes the engine from the dispatch, not from an attribute, and the
multi-engine path writes with its run key (written, NOT run)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from dodaios.brain import manager as mgr
from dodaios.brain import multi_engine_chat as mec
from dodaios.control import turn_record as tr


class _FakeBrain:
    """Just enough of BrainManager for the record helpers (they use no other state)."""

    _begin_turn_record = mgr.BrainManager._begin_turn_record
    _note_dispatch = mgr.BrainManager._note_dispatch
    _finish_engine_record = mgr.BrainManager._finish_engine_record
    _turn_ledger = mgr.BrainManager._turn_ledger
    _last_turn_all_failed = False

    def __init__(self) -> None:
        self.active_provider = "WRONG-MUTABLE-ATTRIBUTE"


class _Turn(mgr._StopTurn):  # a plain subclass: gets a __dict__, STOP is simulated by a flag
    is_stale = False

    def stale(self) -> bool:
        return self.is_stale


def _turn(ticket: int = 0) -> _Turn:
    return _Turn(ticket)


def test_begin_opens_a_record_without_any_engine():
    brain, turn = _FakeBrain(), _turn()
    token = mgr._STOP_TURN.set(turn)
    try:
        brain._begin_turn_record(uuid4())
    finally:
        mgr._STOP_TURN.reset(token)
    last = brain._turn_ledger().last()
    assert last.engines == () and turn.record_key == last.key


def test_dispatch_records_the_pair_it_is_given_not_active_provider():
    brain, turn = _FakeBrain(), _turn()
    token = mgr._STOP_TURN.set(turn)
    try:
        brain._begin_turn_record(uuid4())
        brain._note_dispatch("ollama", "llama3", "ollama")
        brain._finish_engine_record(turn)
    finally:
        mgr._STOP_TURN.reset(token)
    last = brain._turn_ledger().last()
    assert last.engines[0].provider == "ollama" and last.engines[0].model == "llama3"
    assert last.lifecycle == "completed"


def test_a_turn_with_no_dispatch_finishes_with_no_engine():
    brain, turn = _FakeBrain(), _turn()
    token = mgr._STOP_TURN.set(turn)
    try:
        brain._begin_turn_record(uuid4())
        brain._finish_engine_record(turn)
    finally:
        mgr._STOP_TURN.reset(token)
    assert brain._turn_ledger().last().engines == ()


def test_a_stale_turn_is_marked_superseded():
    brain, turn = _FakeBrain(), _turn()
    token = mgr._STOP_TURN.set(turn)
    try:
        brain._begin_turn_record(uuid4())
        brain._note_dispatch("ollama", None, "ollama")
        turn.is_stale = True
        brain._finish_engine_record(turn)
    finally:
        mgr._STOP_TURN.reset(token)
    assert brain._turn_ledger().last().lifecycle == "superseded"


def test_an_old_turn_finishing_late_does_not_replace_the_new_one():
    brain = _FakeBrain()
    old, new = _turn(), _turn()
    token = mgr._STOP_TURN.set(old)
    try:
        brain._begin_turn_record(uuid4())
        brain._note_dispatch("old-engine", None, "old-engine")
    finally:
        mgr._STOP_TURN.reset(token)
    token = mgr._STOP_TURN.set(new)
    try:
        brain._begin_turn_record(uuid4())
        brain._note_dispatch("new-engine", "m", "new-engine")
    finally:
        mgr._STOP_TURN.reset(token)
    brain._finish_engine_record(old)  # the old answer lands late
    last = brain._turn_ledger().last()
    assert last.engines[0].provider == "new-engine" and last.lifecycle == "attempted"


def test_multi_engine_record_is_written_with_the_key_and_ignored_when_superseded():
    ledger = tr.TurnLedger()
    key = ledger.begin(tr.TurnRecord("t", generation=1, run_id="r1", phase="planned",
                                     state="running"))
    branch = SimpleNamespace(provider="ollama", brain=SimpleNamespace(model="m"))
    mec._record_turn(ledger, "t", [branch], [], None, None, key=key, phase="planned")
    assert ledger.last().engines[0].model == "m" and ledger.last().lifecycle == "planned"
    ledger.begin(tr.TurnRecord("newer", generation=1, run_id="r2"))
    mec._record_turn(ledger, "t", [branch], [], None, None, key=key, phase="completed")
    assert ledger.last().turn_id == "newer"  # the late multi-engine write changed nothing


def test_close_marks_a_run_that_used_no_engine():
    ledger = tr.TurnLedger()
    key = ledger.begin(tr.TurnRecord("t", generation=1, run_id="r", phase="planned",
                                     state="running"))
    mec._close(ledger, key, "completed")
    assert ledger.last().lifecycle == "completed" and not ledger.last().planned_partial
