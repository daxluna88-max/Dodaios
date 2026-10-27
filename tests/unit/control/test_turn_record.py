"""Unit tests for the turn record (ADR-0058). Written, not run (issue 16 STOP)."""
from __future__ import annotations

from dodaios.control import turn_record as tr


def test_inference_is_derived_from_the_engines_and_unknown_wins():
    assert tr.TurnRecord("t").inference == "none"
    local = tr.EngineUse("ollama", "main", "local")
    remote = tr.EngineUse("openai", "reviewer", "remote")
    assert tr.TurnRecord("t", engines=(local,)).inference == "local"
    assert tr.TurnRecord("t", engines=(remote,)).inference == "remote"
    assert tr.TurnRecord("t", engines=(local, remote)).inference == "mixed"
    assert tr.TurnRecord("t", engines=(local, tr.EngineUse("x", "main", "unknown"))
                         ).inference == "unknown"


def test_network_and_tools_are_not_observed_unless_recorded():
    record = tr.TurnRecord("t", engines=(tr.EngineUse("ollama", "main", "local"),))
    assert record.inference == "local" and record.network_tools == "not_observed"
    assert record.observability == "none"


def test_remote_destinations_are_listed_once_and_sorted():
    record = tr.TurnRecord("t", engines=(
        tr.EngineUse("b", "reviewer", "remote"), tr.EngineUse("a", "synthesizer", "remote"),
        tr.EngineUse("b", "main", "remote"), tr.EngineUse("ollama", "main", "local")))
    assert record.remote_destinations == ("a", "b")


def test_only_the_fixed_categories_are_ever_kept():
    flows = (tr.Flow("question", "openai"), tr.Flow("my secret text", "openai"),
             tr.Flow("history", "openai"))
    assert [f.category for f in tr.sanitize_flows(flows)] == ["question", "history"]


def test_the_ledger_keeps_only_the_latest_turn():
    ledger = tr.TurnLedger()
    assert ledger.last() is None
    ledger.record(tr.TurnRecord("one"))
    ledger.record(tr.TurnRecord("two"))
    assert ledger.last().turn_id == "two"
    ledger.clear()
    assert ledger.last() is None


def test_finish_replaces_the_start_of_turn_guess_with_the_engine_that_answered():
    ledger = tr.TurnLedger()
    ledger.record(tr.TurnRecord(
        "t", engines=(tr.EngineUse("openai", "main", "remote"),), state="running",
        requested="openai"))
    ledger.finish("t", provider="ollama", locality="local", answered=True)
    got = ledger.last()
    assert got.state == "answered" and got.engines[0].provider == "ollama"
    assert got.inference == "local" and got.fell_back is True and got.planned == ()


def test_no_model_used_means_no_engine_not_the_one_that_was_chosen():
    ledger = tr.TurnLedger()
    ledger.record(tr.TurnRecord(
        "t", engines=(tr.EngineUse("openai", "main", "remote"),), state="running",
        requested="openai"))
    ledger.finish("t", provider=None, locality="unknown", answered=True)
    assert ledger.last().engines == () and ledger.last().inference == "none"


def test_a_failed_turn_is_marked_failed_and_never_reported_as_a_fallback():
    ledger = tr.TurnLedger()
    ledger.record(tr.TurnRecord(
        "t", engines=(tr.EngineUse("a", "main", "remote"),), state="running", requested="a"))
    ledger.finish("t", provider="b", locality="remote", answered=False)
    assert ledger.last().state == "failed" and ledger.last().fell_back is False


def test_a_late_finish_of_an_older_turn_changes_nothing():
    ledger = tr.TurnLedger()
    ledger.record(tr.TurnRecord("old", state="running", requested="a"))
    ledger.record(tr.TurnRecord("new", state="running", requested="b"))
    ledger.finish("old", provider="a", locality="local", answered=True)
    assert ledger.last().turn_id == "new" and ledger.last().state == "running"
