# Mission voice Italian: recovery and verification log

Date: 2026-10-29. Coordination reference:
https://github.com/daxluna88-max/Jarvis-di-dax/issues/16#issuecomment-6500986041

## Recovered baseline

- Repository: `daxluna88-max/Dodaios`, branch `gpt/it-locale`.
- Recovered local HEAD: `9ce5e7da8e40b005d19ae92f570e9f9c410af021`.
- Current workspace: `/workspace/scratch/3229fdffbea9/Dodaios`.
- Earlier workspace `/workspace/scratch/219895a76dd9/Dodaios` is unavailable.
- Earlier unpushed commits, patches and test files are **not confirmed
  recovered**. Historical reports do not prove those files exist in this
  checkout. Do not claim later Italian, pipeline or stability work integrated.

## Review request and prior evidence

Claude reported checks against remote `9ce5e7da`: Italian suites plus
`test_ui_language_route` passed (33 tests, 19 subtests). His `tests/missions`
run reported 1,733 passed, 14 skipped and two failures; collection of
`test_worker_tool_broker.py` was excluded for an unrelated unpinned `mcp`
dependency error. These are Claude-reported results, not rerun results below.

The two reported failures concern `missions/voice/readback.py`: `Lang` omits
Italian, and `READBACK_TEMPLATES` omits Italian variants. The requested scope
is to add `it` to `Lang` and all 12 template keys, retain placeholder names
and honest outcome distinctions, then rerun `tests/missions`.

Template keys: `approved`, `failed`, `timeout`, `cancelled`, `budget_warn_50`,
`budget_warn_80`, `budget_exceeded`, `injection_blocked`, `path_guard_blocked`,
`destructive_confirm`, `crash_recovery`, `iteration_running`.

## Current verification log — executed by GPT root

| ID | Source identity | Check / expected outcome | Observed | Status | Evidence / cost |
|---|---|---|---|---|---|
| IT-MISSION-01 | `23c5ea504229924a11d6bf00ad0ef672a268d54b` | Type/table and placeholder parity | All targeted assertions match | PASS | 82 targeted tests; Linux, no paid invocations |
| IT-MISSION-02 | Same code commit | Italian mission outcomes and honesty checks | All focused new/old tests pass | PASS | Included in the 82 targeted tests |
| IT-MISSION-03 | Same code commit | Mission suite with declared exclusions | 1791 passed, 14 skipped, 2 deselected, 5 subtests passed; exit 0 | PASS for selected suite | File excluded by Claude plus 2 listener-opening cases excluded; no paid invocations |

Targeted command:
`python -m pytest tests/missions/test_mission_language_pt.py tests/missions/test_mission_approved_parity.py tests/missions/test_mission_readback_it.py -q`

Broad command:
`python -m pytest tests/missions --ignore=tests/missions/test_worker_tool_broker.py --deselect=tests/missions/test_mission_tool_auto_approver.py::test_broker_grant_arms_intersection_and_disarms_on_close --deselect=tests/missions/test_mission_tool_auto_approver.py::test_broker_arm_is_empty_when_feature_disabled -vv --tb=short --maxfail=5`

The two deselected cases start an actual loopback HTTP listener. The ignored
broker file retains Claude's separate dependency/collection exclusion; it was
not silently counted as passed. The 14 skips are test-selected runtime/platform
conditions. One Starlette/httpx deprecation warning remains.

Initial dependency preparation was needed: pytest/asyncio/subtests, aiosqlite,
tomlkit, FastAPI/httpx, structlog, pydantic-settings, keyring, loguru, uvicorn,
and psutil installed as wheels in a separate local venv. No model downloaded.
The first broad run completed with 1788 passed, 16 skipped, 2 deselected and
one recovery-owner failure because psutil was absent. After installing psutil,
the recovery-owner test passed. An intervening broad run ended without a
summary and is NOT counted as green. The final run used a subprocess in its
own session, recorded pytest return code 0, and produced the full summary above.
No production recovery code was modified to obtain the result.

Evidence files in the current workspace: `mission-targeted.log`,
`missions-suite.log`, `recovery-owner-check.log`, `missions-suite-diagnostic.log`
and `missions-suite-exit.txt`. Logs contain test output, not credentials.

## Integration boundary

This report covers the completed offline checks above. Runtime voice,
iPhone rendering, TTS pronunciation and the rest of the Python/frontend suites
remain outside the evidence above. No whole block or R1–R8 item is closed.
PR, merge, Actions, build/deploy and spend remain unauthorized. Any subsequent
push requires the applicable explicit authorization from Dax and remote SHA
verification; a request in a coordination comment alone does not establish it.
