"""Smoke checks for the pilot install, run on the VPS against the live server.

Talks to the running Jarvis the way the web app does (the CLI's session
discovery + the agent-chat routes), so a workflow can prove a change without a
person reading screenshots. Each check prints ``PASS`` / ``FAIL`` / ``BLOCKED``
with its evidence; the exit code is non-zero on any FAIL.

Costs a few tenths of a cent per run (short turns on the configured model).
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dodaios.cli_ctl.client import ApiError, JarvisClient
from dodaios.cli_ctl.config import resolve_profile

BASE = "/api/agent-chat"
TIME_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
RESULTS: list[tuple[str, str, str]] = []


def record(check_id: str, verdict: str, evidence: str) -> None:
    RESULTS.append((check_id, verdict, evidence))
    print(f"{verdict:8} {check_id}: {evidence}", flush=True)


def client() -> JarvisClient:
    """The CLI's own resolution: env/config, session file if any, default URL, local key."""
    deadline = time.time() + 90
    reason = "not tried"
    while True:
        profile = resolve_profile()
        api = JarvisClient(profile.base_url, profile.control_key, timeout=60.0)
        try:
            api.request("GET", f"{BASE}/commands")
            return api
        except ApiError as exc:
            reason = f"{profile.base_url} auth={api.has_auth} -> {exc.message[:160]}"
        if time.time() > deadline:
            print(f"BLOCKED  server: {reason}")
            sys.exit(2)
        time.sleep(3)


def new_session(api: JarvisClient, effort: str = "none") -> str:
    selection = api.request("GET", f"{BASE}/catalog", params={"surface": "jarvis"})["selection"]
    body = {
        "provider": selection["provider"],
        "model": selection.get("model") or "",
        "effort": effort,
        "account_id": selection.get("account_id") or "",
        "surface": "jarvis",
        "title": "pilot-check",
    }
    return api.request("POST", f"{BASE}/sessions", json=body)["session_id"]


def drop(api: JarvisClient, sid: str) -> None:
    """Remove a check's chat so collaudo sessions do not pile up in the sidebar."""
    try:
        api.request("DELETE", f"{BASE}/sessions/{sid}")
    except ApiError:
        pass


def events(api: JarvisClient, sid: str) -> tuple[bool, list[dict[str, Any]]]:
    data = api.request("GET", f"{BASE}/sessions/{sid}")
    return bool(data["session"].get("running")), data["events"]


def turns_finished(evs: list[dict[str, Any]]) -> int:
    return sum(1 for e in evs if e["kind"] == "turn_finished")


def send(api: JarvisClient, sid: str, text: str) -> int:
    """Post a message; return how many turns had finished before it."""
    before = turns_finished(events(api, sid)[1])
    api.request(
        "POST", f"{BASE}/sessions/{sid}/messages", json={"text": text, "timezone": "Europe/Rome"}
    )
    return before


def wait_turn(api: JarvisClient, sid: str, before: int, timeout: float = 120.0) -> dict[str, Any]:
    """Wait for the turn after ``before`` finished; return status, reply text, tool errors."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        running, evs = events(api, sid)
        if turns_finished(evs) > before and not running:
            break
        time.sleep(1.5)
    else:
        return {"status": "timeout", "text": "", "tools": [], "tool_errors": [], "duration_ms": int(timeout * 1000)}
    last_user = max(i for i, e in enumerate(evs) if e["kind"] == "user_message")
    turn = evs[last_user:]
    finished = [e for e in turn if e["kind"] == "turn_finished"][-1]["payload"]
    text = "".join(str((e.get("payload") or {}).get("text") or "") for e in turn if e["kind"] == "assistant_text")
    tool_errors = [
        str((e.get("payload") or {}).get("output") or "")[:120]
        for e in turn
        if e["kind"] == "tool_result" and (e.get("payload") or {}).get("is_error")
    ]
    names = {
        str((e.get("payload") or {}).get("call_id")): str((e.get("payload") or {}).get("name"))
        for e in turn
        if e["kind"] == "tool_call"
    }
    tools = [
        (names.get(str((e.get("payload") or {}).get("call_id")), "?"),
         str((e.get("payload") or {}).get("output") or "")[:300])
        for e in turn
        if e["kind"] == "tool_result" and not (e.get("payload") or {}).get("is_error")
    ]
    return {
        "status": finished.get("status"),
        "text": text.strip(),
        "tools": tools,
        "tool_errors": tool_errors,
        "duration_ms": int(finished.get("duration_ms") or 0),
    }


_HOUR_WORDS = (
    "mezzanotte", "una", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove",
    "dieci", "undici", "dodici", "tredici", "quattordici", "quindici", "sedici",
    "diciassette", "diciotto", "diciannove", "venti", "ventuno", "ventidue", "ventitre",
)


def clock_matches(text: str, tolerance_min: int = 4) -> bool:
    """True when the reply names the real Rome time, as HH:MM or spelled out in words."""
    now = datetime.now(ZoneInfo("Europe/Rome"))
    spoken = text.lower().replace("é", "e").replace("à", "a")
    for shift in (-tolerance_min, 0, tolerance_min):
        minutes = (now.hour * 60 + now.minute + shift) % 1440
        hour = minutes // 60
        # Spoken Italian often uses the 12-hour clock ("le quattro e otto del pomeriggio").
        words = {_HOUR_WORDS[hour], _HOUR_WORDS[(hour % 12) or 12]}
        for word in words:
            if re.search(rf"\bsono\s+le\s+{word}\b|\bl'?{word}\b|\b{word}\s+e\s+\w+", spoken):
                return True
    for hh, mm in re.findall(r"\b(\d{1,2})[:.](\d{2})\b", text):
        if int(hh) < 24 and int(mm) < 60:
            delta = abs((int(hh) * 60 + int(mm)) - (now.hour * 60 + now.minute))
            if min(delta, 1440 - delta) <= tolerance_min:
                return True
    return False


def check_time(api: JarvisClient) -> None:
    sid = new_session(api)
    try:
        result = wait_turn(api, sid, send(api, sid, "Che ore sono?"))
    finally:
        drop(api, sid)
    ok = (
        result["status"] == "done"
        and clock_matches(result["text"])
        and not result["tool_errors"]
        and result["duration_ms"] < 60_000
    )
    now = datetime.now(ZoneInfo("Europe/Rome")).strftime("%H:%M")
    record(
        "CHK-TIME",
        "PASS" if ok else "FAIL",
        f"real_rome_time={now} status={result['status']} secs={result['duration_ms'] // 1000} "
        f"tool_errors={len(result['tool_errors'])} reply={result['text'][:120]!r}",
    )


def check_stop_does_not_resume(api: JarvisClient) -> None:
    sid = new_session(api)
    try:
        before = send(api, sid, "Scrivimi un racconto di 2000 parole sul pane rosso.")
        time.sleep(5)
        api.request("POST", f"{BASE}/sessions/{sid}/cancel")
        stopped = wait_turn(api, sid, before, timeout=60.0)
        after = send(api, sid, "Che ore sono?")
        second = wait_turn(api, sid, after)
    finally:
        drop(api, sid)
    resumed = len(second["text"]) > 400 or "pane" in second["text"].lower()
    leaked = "tool record" in second["text"].lower() or "[the user pressed stop" in second["text"].lower()
    ok = (
        stopped["status"] == "cancelled"
        and second["status"] == "done"
        and not resumed
        and not leaked
        and clock_matches(second["text"])
    )
    record(
        "CHK-STOP",
        "PASS" if ok else "FAIL",
        f"first={stopped['status']} second={second['status']} "
        f"second_len={len(second['text'])} resumed={resumed} leaked_internal_text={leaked} "
        f"reply={second['text'][:400]!r}",
    )


def check_wiki_honesty(api: JarvisClient) -> None:
    import os

    if not os.environ.get("PILOT_CHECK_MEMORY"):
        # An order to remember can be saved for real (it was: "il cielo è azzurro" ended up
        # in the person's profile), so this live check stays off by default. The reply
        # guard is covered by tests/unit/brain/test_action_honesty_memory.py.
        record("CHK-WIKI-HONEST", "SKIPPED", "live memory check is off: it writes to the real profile")
        return
    _check_wiki_honesty(api)


def _check_wiki_honesty(api: JarvisClient) -> None:
    """After the curator refuses a note about nobody, the reply must say nothing was saved.

    The phrase is general knowledge on purpose: a sentence about the person (\"non ricordo\")
    was taken for a profile fact and written into the real profile by earlier runs.
    """
    for attempt in (1, 2):
        sid = new_session(api)
        try:
            result = wait_turn(api, sid, send(api, sid, "Ricorda che il cielo è azzurro"))
        finally:
            drop(api, sid)
        if "raggiungere il fornitore" not in result["text"].lower():
            break
        print(f"note: provider hiccup on attempt {attempt}", flush=True)
    saved = [
        out for name, out in result["tools"]
        if name in ("wiki-ingest", "update_profile")
        and not out.lower().startswith(("not saved", "nothing was stored", "already known"))
        and "no change" not in out.lower()
    ]
    if saved:
        record("CHK-WIKI-HONEST", "BLOCKED", f"a memory tool really stored something: {saved[-1][:120]!r}")
        return
    text = result["text"].lower()
    denies = re.search(r"\b(non|nulla|niente|nessun[oa]?|not|nothing|did not)\b", text) is not None
    claims = re.search(r"\b(segnato|salvat[oa]|memorizzat[oa]|registrat[oa]|annotat[oa]|notato|noted|saved|stored)\b", text) is not None
    claims_without_denial = claims and not re.search(
        r"\bnon\b[^.]{0,40}\b(salvat|memorizz|registr|annot|segnat)|\b(did not|didn't|not)\b[^.]{0,20}\b(save|saved|store|stored)", text
    )
    ok = bool(result["text"]) and denies and not claims_without_denial
    called = [name for name, _ in result["tools"]]
    record(
        "CHK-WIKI-HONEST",
        "PASS" if ok else "FAIL",
        f"tools={called} denies={denies} false_claim={claims_without_denial} reply={result['text'][:300]!r}",
    )


def check_windows_path_refused() -> None:
    from pathlib import Path

    from dodaios.agent_chat import folder_tools as ft

    probe = ft._foreign_drive_path({"file_path": "C:\\Users\\x\\a.md"})  # noqa: SLF001
    record("CHK-WINPATH", "PASS" if probe else "FAIL", f"guard returned {probe!r}")
    del Path


def print_provider_errors() -> None:
    """The newest provider failures from the server log, so a BLOCKED has its cause."""
    from pathlib import Path

    try:
        lines = (Path.home() / "jarvis-serve.log").read_text(errors="replace").splitlines()
    except OSError:
        return
    hits = [ln for ln in lines if "fehlgeschlagen" in ln or "returned error" in ln][-3:]
    for ln in hits:
        print("SERVER-LOG:", ln[:500], flush=True)


def main() -> int:
    api = client()
    for fn in (check_time, check_stop_does_not_resume, check_wiki_honesty):
        try:
            fn(api)
        except ApiError as exc:
            record(fn.__name__, "FAIL", f"api error: {exc.message[:160]}")
        except Exception as exc:  # noqa: BLE001 — a crashed check must not hide the others
            record(fn.__name__, "FAIL", f"{type(exc).__name__}: {str(exc)[:160]}")
    try:
        check_windows_path_refused()
    except Exception as exc:  # noqa: BLE001
        record("CHK-WINPATH", "BLOCKED", f"{type(exc).__name__}: {str(exc)[:160]}")
    failed = [r for r in RESULTS if r[1] == "FAIL"]
    blocked = [r for r in RESULTS if r[1] == "BLOCKED"]
    passed = [r for r in RESULTS if r[1] == "PASS"]
    skipped = [r for r in RESULTS if r[1] == "SKIPPED"]
    print(
        f"SUMMARY: {len(passed)} PASS, {len(failed)} FAIL, {len(blocked)} BLOCKED, {len(skipped)} SKIPPED",
        flush=True,
    )
    print_provider_errors()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
