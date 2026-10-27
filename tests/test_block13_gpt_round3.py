"""Block 13, GPT round 3 (G2 + egress identity). WRITTEN, NOT RUN by the author (owner order).

G2: unknown provenance is not remote-replayable history / curator input.
Egress: a third-party ``none`` declaration is bound to the vetted class OBJECT, not a string.
"""
from __future__ import annotations

import asyncio
import sqlite3
import types

import pytest

from dodaios.brain import provenance as prov
from dodaios.core import egress_guard as eg
from dodaios.core.protocols import BrainMessage


@pytest.fixture(autouse=True)
def _fresh_turn():
    prov.clear()
    prov.begin_turn("t")
    yield
    prov.clear()


# --- G2: storage_label ------------------------------------------------------------------------


def test_undeclared_turn_is_unavailable_and_withheld():
    assert prov.storage_label("an ordinary unlabelled answer") is prov.Provenance.UNAVAILABLE
    assert prov.withheld_from_remote_store("an ordinary unlabelled answer") is True


def test_only_a_positive_declaration_makes_text_storable():
    prov.attach_unrestricted()
    assert prov.storage_label("known clean answer") is prov.Provenance.UNRESTRICTED
    assert prov.withheld_from_remote_store("known clean answer") is False


def test_declaration_never_lifts_restricted_or_unavailable():
    prov.attach_unrestricted()
    prov.set_turn_provenance(prov.Provenance.UNAVAILABLE)
    assert prov.storage_label("x answer text") is prov.Provenance.UNAVAILABLE
    prov.begin_turn("t2")
    prov.attach_unrestricted()
    prov.set_turn_provenance(prov.Provenance.RESTRICTED)
    assert prov.storage_label("x answer text") is prov.Provenance.RESTRICTED


def test_conflicting_text_label_blocks():
    prov.attach_unrestricted()
    assert prov.storage_label(
        prov.label("t", prov.Provenance.UNAVAILABLE)) is prov.Provenance.UNAVAILABLE


def test_parse_missing_or_garbage_is_unavailable():
    for raw in (None, "", "garbage", 7):
        assert prov.parse(raw) is prov.Provenance.UNAVAILABLE
        assert prov.remote_replayable(raw) is False
    assert prov.remote_replayable("unrestricted") is True
    assert prov.remote_replayable("restricted") is False


def test_local_only_signal_keeps_positive_evidence_semantics():
    assert prov.local_only_signal("plain text here") is False  # absence is not evidence
    prov.mark_restricted("secret local sentence")
    assert prov.local_only_signal("secret local sentence") is True


# --- G2: replay boundary ----------------------------------------------------------------------


def test_remote_engine_gets_only_positively_unrestricted_history():
    from dodaios.brain.manager import _history_for_engine

    hist = [
        BrainMessage(role="user", content="no label"),  # default = unavailable
        BrainMessage(role="assistant", content="clean", provenance="unrestricted"),
        BrainMessage(role="assistant", content="tainted", provenance="restricted"),
        BrainMessage(role="user", content="flag", provenance="unrestricted", restricted=True),
    ]
    assert [m.content for m in _history_for_engine(hist, "openai")] == ["clean"]


def test_local_engine_keeps_all_history_unchanged():
    from dodaios.brain import engine_policy
    from dodaios.brain.manager import _history_for_engine

    local = next(iter(engine_policy.LOCAL_SERVICES.get("brain", ()) or ()), None)
    if local is None:
        pytest.skip("no local brain engine name registered")
    hist = [BrainMessage(role="user", content="no label")]
    assert list(_history_for_engine(hist, local)) == hist


def _bare_manager():
    from dodaios.brain.manager import BrainManager

    mgr = BrainManager.__new__(BrainManager)
    mgr._history = []
    mgr._curator = types.SimpleNamespace(calls=[])

    async def process_turn(u, a):
        mgr._curator.calls.append((u, a))

    mgr._curator.process_turn = process_turn

    async def _publish(**_kw):
        return None

    mgr._publish_response_generated = _publish
    return mgr


def test_side_effects_store_unavailable_and_skip_curator_when_undeclared():
    mgr = _bare_manager()

    async def run():
        await mgr._record_response_side_effects(
            user_text="my path is /home/x", response_text="an unlabelled reply", use_history=True)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert [m.provenance for m in mgr._history] == ["unavailable", "unavailable"]
    assert mgr._curator.calls == []


def test_side_effects_store_unrestricted_and_call_curator_when_declared():
    mgr = _bare_manager()

    async def run():
        prov.begin_turn("t3")
        prov.attach_unrestricted()
        await mgr._record_response_side_effects(
            user_text="remember I like tea", response_text="noted, tea", use_history=True)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert [m.provenance for m in mgr._history] == ["unrestricted", "unrestricted"]
    assert len(mgr._curator.calls) == 1


def test_seed_history_never_promotes_unlabelled_pairs():
    mgr = _bare_manager()
    mgr.seed_history([("user", "old"), {"role": "assistant", "content": "a",
                                        "provenance": "unrestricted"},
                      {"role": "user", "content": "b", "provenance": "bogus"}])
    assert [m.provenance for m in mgr._history] == ["unavailable", "unrestricted", "unavailable"]


# --- G2: durable store ------------------------------------------------------------------------


def test_chat_store_persists_provenance_and_old_rows_read_unavailable(tmp_path):
    from dodaios.state.chat_store import ChatStore

    db = tmp_path / "chats.db"
    # an old database without the column
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE chat_threads (thread_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',"
        " kind TEXT NOT NULL DEFAULT 'text', created_at_ns INTEGER NOT NULL,"
        " updated_at_ns INTEGER NOT NULL);"
        "CREATE TABLE chat_messages (message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,"
        " role TEXT NOT NULL, text TEXT NOT NULL, timestamp_ns INTEGER NOT NULL, trace_json TEXT);"
        "INSERT INTO chat_threads VALUES ('t','x','text',1,1);"
        "INSERT INTO chat_messages VALUES ('m1','t','user','old row',1,NULL);")
    con.commit()
    con.close()

    class _Bus:
        async def publish(self, _e):
            return None

    async def run():
        store = ChatStore(bus=_Bus(), db_path=db)
        await store.add_message(thread_id="t", role="assistant", text="new clean",
                                provenance="unrestricted", publish_event=False)
        await store.add_message(thread_id="t", role="assistant", text="new unlabelled",
                                publish_event=False)
        store.close()
        # restart: a second instance on the same file
        store2 = ChatStore(bus=_Bus(), db_path=db)
        msgs = store2.get_thread("t")["messages"]
        store2.close()
        return [(m["text"], m["provenance"]) for m in msgs]

    assert asyncio.run(run()) == [("old row", "unavailable"), ("new clean", "unrestricted"),
                                  ("new unlabelled", "unavailable")]


# --- Egress: identity bound to the class object ----------------------------------------------


def _make_cls(name="Vetted", module="plugin.mod"):
    cls = type(name, (), {})
    cls.__module__ = module
    cls.__qualname__ = name
    return cls


def test_same_string_identity_does_not_match_vetted_declaration():
    vetted_cls = _make_cls()
    imposter_cls = _make_cls()  # same module + qualname strings, different class object
    assert (vetted_cls.__module__, vetted_cls.__qualname__) == (
        imposter_cls.__module__, imposter_cls.__qualname__)
    eg.install()
    try:
        eg.declare_tool_coverage("b13_tool", eg.CAPABILITY_NONE, vetted_cls())
        assert eg.tool_capability("b13_tool", vetted_cls()) == eg.CAPABILITY_NONE
        assert eg.tool_capability("b13_tool", imposter_cls()) == eg.CAPABILITY_UNKNOWN
        assert eg.tool_refusal("b13_tool", imposter_cls()) is not None
        assert eg.tool_refusal("b13_tool") is not None  # name only: no credit
    finally:
        eg._DECLARED.pop("b13_tool", None)
        eg._DECLARED_IMPL.pop("b13_tool", None)


def test_subclass_of_vetted_class_is_not_the_vetted_identity():
    base = _make_cls("Base")
    sub = type("Base", (base,), {})
    sub.__module__, sub.__qualname__ = base.__module__, base.__qualname__
    try:
        eg.declare_tool_coverage("b13_sub", eg.CAPABILITY_NONE, base())
        assert eg.tool_capability("b13_sub", sub()) == eg.CAPABILITY_UNKNOWN
    finally:
        eg._DECLARED.pop("b13_sub", None)
        eg._DECLARED_IMPL.pop("b13_sub", None)


def test_impl_identity_is_the_class_object_not_a_string():
    cls = _make_cls()
    assert eg._impl_identity(cls()) is cls
    assert eg._impl_identity(None) is None
    assert not isinstance(eg._impl_identity(cls()), str)
