"""Block 9: GPT round 2 (F3 retry identity + legacy migration, F5 unattended paths).

WRITTEN, NOT RUN (owner order: no test execution in this change). Run before relying on them.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest

from dodaios.safety.effects import LEGACY_ATTEMPT, EffectLedger, LedgerUnavailable
from dodaios.safety.preflight import current_ticket, guarded_call

pytestmark = pytest.mark.asyncio


async def test_failed_no_effect_allows_retry_with_new_attempt_row() -> None:
    led = EffectLedger()
    r1 = led.reserve("s", "k", "t", "fp")
    assert r1.kind == "reserved" and r1.attempt == ""
    led.finish("s", "k", success=False, attempt=r1.attempt, no_effect=True)
    r2 = led.reserve("s", "k", "t", "fp")
    assert r2.kind == "reserved"  # not in_flight: no primary key collision
    assert r2.attempt.startswith("retry-")
    led.finish("s", "k", success=True, attempt=r2.attempt)
    rows = led._db.execute("SELECT attempt, state FROM effect_calls ORDER BY ts").fetchall()
    assert ("", "failed") in rows and (r2.attempt, "done") in rows  # history kept
    assert led.reserve("s", "k", "t", "fp").kind == "replay"


async def test_second_failed_retry_gets_third_identity() -> None:
    led = EffectLedger()
    seen = set()
    for _ in range(3):
        r = led.reserve("s", "k", "t", "fp")
        assert r.kind == "reserved"
        seen.add(r.attempt)
        led.finish("s", "k", success=False, attempt=r.attempt, no_effect=True)
    assert len(seen) == 3


async def test_legacy_effects_table_is_honoured(tmp_path: Any) -> None:
    path = tmp_path / "fx.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE effects (scope TEXT NOT NULL, key TEXT NOT NULL, tool TEXT NOT NULL, "
        "fingerprint TEXT NOT NULL, state TEXT NOT NULL, boot TEXT NOT NULL, success INTEGER, "
        "preview TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', ts REAL NOT NULL, "
        "PRIMARY KEY (scope, key));"
    )
    now = time.time()
    for k, st in (("a", "done"), ("b", "started"), ("c", "unknown"), ("d", "failed")):
        db.execute(
            "INSERT INTO effects (scope,key,tool,fingerprint,state,boot,ts) "
            "VALUES ('s',?,'t',?,?, 'oldboot', ?)", (k, f"fp{k}", st, now),
        )
    db.commit()
    db.close()
    led = EffectLedger(path)
    assert led.reserve("s", "a", "t", "fpa").kind == "replay"
    for k in "bcd":
        assert led.reserve("s", k, "t", f"fp{k}").kind == "unknown"
    row = led.get("s", "a")
    assert row is not None and row.attempt == LEGACY_ATTEMPT
    EffectLedger(path)  # second open: idempotent
    assert led.reserve("s", "b", "t", "fpb").kind == "unknown"


async def test_legacy_migration_failure_fails_closed(tmp_path: Any, monkeypatch: Any) -> None:
    path = tmp_path / "fx.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE effects (scope TEXT)")  # wrong shape: import must fail
    db.commit()
    db.close()
    with pytest.raises(LedgerUnavailable):
        EffectLedger(path)


class _Eff:
    name = "send"
    calls = 0

    async def execute(self, args: dict[str, Any], ctx: Any) -> Any:
        from dodaios.core.protocols import ToolResult

        type(self).calls += 1
        return ToolResult(success=True, output="ok")


async def test_guarded_call_runs_once_then_refuses_duplicate() -> None:
    from dodaios.safety.effects import set_default_ledger

    set_default_ledger(EffectLedger())
    tool = _Eff()
    _Eff.calls = 0

    async def call(a: dict[str, Any]) -> Any:
        return await tool.execute(a, None)

    args = {"to": "x", "nested": {"a": [1]}}
    ran, res, held = await guarded_call(
        "send", args, call, tool=tool, scope="t1", key="k1", ticket=current_ticket()
    )
    assert ran and held is None
    ran2, _, held2 = await guarded_call(
        "send", args, call, tool=tool, scope="t1", key="k2", ticket=current_ticket()
    )
    assert not ran2 and held2 and "effect-" in held2
    assert _Eff.calls == 1
    set_default_ledger(None)


async def test_guarded_call_raise_leaves_unknown() -> None:
    from dodaios.safety.effects import set_default_ledger

    led = EffectLedger()
    set_default_ledger(led)

    async def boom(a: dict[str, Any]) -> Any:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        await guarded_call("send", {}, boom, tool=_Eff(), scope="t", key="k",
                           ticket=current_ticket())
    row = led.get("t", "k")
    assert row is not None and row.state == "unknown"
    set_default_ledger(None)
