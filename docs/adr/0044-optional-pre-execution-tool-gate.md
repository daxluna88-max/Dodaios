# ADR-0044 — Optional pre-execution gate on tool calls

**Status:** Accepted · **Date:** 2026-10-24

## Context

`ToolExecutor.execute()` pauses a call for confirmation only when its risk tier
is `ask` (or plausibility flags it). `safe` and `monitor` calls, which include
every MCP tool, publish no approval request, and `ActionProposed` is only a
notification. An external policy (for example a decision controller that learns
from the owner's past approvals) therefore cannot stop them.

## Decision

1. `ToolExecutor` accepts an optional `gate` (`dodaios/safety/gate.py`) with
   `async review(GateRequest) -> GateDecision`. It runs after the target origin
   is resolved, before the confirmation logic.
2. The gate can only tighten: `allow` changes nothing; `ask` forces a
   confirmation even on `safe`/`monitor` tiers, after the explicit-intent waiver
   and in bypass mode, with `ActionApprovalRequired.reason == "gate"`; `deny`
   publishes `ActionDenied` and returns an error result without running the tool.
3. A gate that raises, times out (`gate_timeout_s`, default 5 s) or returns an
   invalid value counts as `ask`, never `allow`.
4. `MissionToolAutoApprover` only answers `risk_tier` and `plausibility`, so it
   never auto-approves a `gate` confirmation.
5. `set_default_gate()` installs one process-wide gate used by every executor without its own, so the several construction sites need no patching.
6. With no gate installed behaviour is unchanged.

## Consequences

- A slow gate delays every tool call by up to `gate_timeout_s`.
- The gate sees tool arguments; it must not log or send them elsewhere.
- Not covered: a UI string for the `gate` reason (the raw value is shown).

## Block 7: approvals and effects

Status of this section: written by static reading only. Nothing was run: no tests, no app, no CI.

### Where a side-effecting tool can run, and through which decision point

| Path | Reaches the tool through | Same approval / deny / STOP logic |
|---|---|---|
| Typed chat, voice, realtime, phone (telephony goes through the brain), missions (`WorkerToolBroker` -> supervisor gateway), scheduled tasks, workflows, self-build, computer use, MCP server (`dodaios_tools_server` -> gateway) | `ToolExecutor.execute` | yes, one function |
| Two-turn voice/chat confirmation ("ja") | `ToolExecutor.execute_confirmed` | now re-checks (see below); before it skipped STOP, plan mode, offline guard, blacklist and gate |
| Skill runner (`skills/runner.py`) | `tool.execute` directly, own risk check | NOT through the executor. Now also honours STOP and the process-wide gate and refuses on `ask`/`deny` (no approver exists there) |
| Remote MCP harness (`plugins/harness/mcp_remote.py`) | `client.call_tool` directly | NOT through the executor. Same preflight added |
| Fan-out / review / synthesis branches | `brain.complete(tools=())` | no tools; see below |
| Read-only folder tools in `society/coding_threads.py`, wiki note write in `society/learning.py` | direct | not covered (reads / the agent's own memory page) |

### Changes

1. **STOP lives in the executor.** Before this change only an approved call was checked against the STOP generation; a `safe`/`monitor` call (no approval) ran after STOP. `execute` now refuses everything except declared reads while the door is closed, and re-checks the generation after an approval wait.
2. **Forced confirmations cannot be settled by a stance.** The chat bridge (`ChatApprovalBridge`) approved `gate` confirmations under its bypass/always-allowed stance, against point 2 of this ADR. It now asks the person for any reason other than `risk_tier`/`plausibility`. `TaskAutoApprover` answered every reason; it now answers only those two, like `MissionToolAutoApprover`.
3. **Approval bound to the exact call.** `call_fingerprint(tool, args)` (sha256 of the canonical call) is carried on `ActionApprovalRequired` and echoed by every answerer (chat card, mission deck, task and mission auto-approvers). `ApprovalWorkflow` ignores an `ActionApproved` that names another tool or carries another fingerprint (a denial is always honoured). Changed arguments therefore need a new request, and a new request needs a new approval. The chat bridge no longer shows cached arguments of a different proposal. Voice-confirm stores the exact arguments, a fingerprint, the STOP generation and a 120 s lifetime; a new question for the same trace supersedes (and audits) the old one; `execute_confirmed` can be given the expected fingerprint (the manager passes it).
4. **Effect ledger** (`dodaios/safety/effects.py`, used inside `ToolExecutor`): one row per `(trace_id, call id)` with the call fingerprint, written as `started` BEFORE the tool runs.
   - Same call id again after `done`: the stored outcome is returned, the tool is not run (`already_executed`). Covers a replayed stream, a retry after the result was lost, a redelivered turn that keeps its trace and call ids.
   - Same call id while running: `effect-in-flight`.
   - Same tool and arguments under a new id in the same turn (e.g. another engine or a retry re-issued it): forced confirmation, reason `duplicate_effect`.
   - Outcome unknown (the tool raised, the task was cancelled mid-run, or a `started` row from another process boot): never retried on its own; forced confirmation, reason `effect_unknown`. Unattended surfaces answer "approval unavailable". A tool that returned an error result is a known failure and may be retried through the normal approval.
   - Forced confirmations are not approved by bypass mode or by the pre-authorization bridges.
   - Persistence: `configure_default_ledger(user_data_dir()/effects.sqlite3)` is called when the default brain is built (skipped under pytest); otherwise the ledger is per process. Rows are kept 7 days.
   - A ledger that cannot record the claim blocks the run rather than running an unrecorded effect.
5. **Fallback.** `FallbackRouter` never executes tools; it already refuses to fall back once a tool call was produced. The ledger covers the layer where tools actually run, so a second engine re-issuing the call is caught as a duplicate.
6. **Fan-out has no tools by construction, not only by convention.** Branches and the synthesizer already send `tools=()` and skip tool-call deltas; the skipped calls are now counted (`BranchResult.dropped_tool_calls`) and a brain that declares `executes_tools_itself = True` is refused as a branch.

### NOT covered

- External systems: the ledger records what this app issued, not what the far side did. A mail sent whose confirmation was lost is `unknown` and goes to the owner; nothing can know whether it arrived. No end-to-end idempotency key is sent to remote APIs.
- A restart only helps if the redelivered turn keeps its `trace_id` and the provider's call ids. A turn that restarts with a new trace id, or an engine that re-issues the call with fresh ids AND different arguments, is a new call (arguments differ, so a new approval is asked anyway).
- A `safe`-tier tool without `read_only` is treated as a read: no replay guard for it.
- Legacy approvers that publish `ActionApproved` without a fingerprint (desktop UI/WebSocket clients not updated) still count; only tool-name and fingerprint mismatches are rejected. The UI should echo `call_fingerprint`.
- Plausibility-forced and tier confirmations on an `interactive` surface still wait for a decision by `trace_id`; two approvals for different calls of one trace cannot be in flight (`arm` refuses a second), which is intended.
- The skill runner and remote MCP harness are guarded by STOP and the gate only; they have no per-call approval, no ledger and no plan-mode check. Moving them behind `ToolExecutor` is the proper fix.
- `FallbackRouter` and the multi-engine orchestrator are not yet called by the live chat path; their rules are tested in isolation only.
- Nothing here was executed. Tests are in `tests/contract/test_block7_approvals_effects.py` (written, not run).

## Block 8: review fixes R1-R5

Status of this section: written by static reading only. Nothing was run: no tests, no app, no CI, no build. `py_compile` and `ruff check` only. These fix the static review findings on block 7.

### Changes

1. **R1, a failed effect is unknown, not retryable.** `EffectLedger.finish` records `done` only for success. A failed result becomes `failed` (retryable through the normal approval) only when the tool vouched that nothing was applied: `ToolResult.no_effect_guaranteed=True` (new field, default `False`) or a tool attribute `failure_means_no_effect = True`. Every other failure, and every raise or cancellation, is `unknown`: the next attempt is a forced `effect_unknown` confirmation and an unattended surface refuses. Known success replay (`done` -> `already_executed`) and ambiguous failure stay different states. `is_side_effecting` no longer treats a `safe`-tier tool as a read: only a tool that declares `read_only` (`allows_read`, the standard STOP and plan mode use) skips the ledger; an unknown or broken capability counts as an effect. The discovery tool (`tool_shortlist`) now declares `read_only = True` so it keeps skipping.
2. **R2, atomic admission.** `EffectLedger.reserve` checks the call id `(scope, key)` AND the content `(scope, tool, fingerprint)` and writes `started` in ONE `BEGIN IMMEDIATE` transaction (cross-process: SQLite's write lock). No `INSERT OR REPLACE` anywhere; the table is now `effect_calls` with primary key `(scope, key, attempt)`, so another process's reservation, a finished call and an unknown one are never overwritten. A deliberate re-execution of an `unknown` or `duplicate` call is allowed only with an `attempt`: the one-use approval id of the explicit confirmation (the ticket's `approval_id`, or the stashed voice question's). A finished or live call is never overridden. `inspect` stays as an advisory read to decide whether to force a confirmation; the executor reserves again at the last moment and refuses (`effect-<kind>`, new confirmation needed) if the state changed meanwhile. `claim` remains as a thin wrapper of `reserve`. The old `effects` table of block 7 is left untouched and ignored (block 7 was not released); its in-flight rows no longer protect.
3. **R3, no silent degradation.** `configure_default_ledger(path, allow_reduced_protection=False)`: when the file cannot be opened the process state becomes `unavailable` and `get_default_ledger()` raises `LedgerUnavailable`; the executor then refuses every side-effecting call with `effect-ledger-unavailable` (before any approval is asked, also on `execute_confirmed`). Reads, chat and declared read-only tools keep working. The in-memory fallback exists only when the owner sets `safety.effect_ledger_allow_reduced_protection = true`; `effects.protection_state()` then reports mode `reduced` with a plain description of what is not covered. A failure to even attempt configuration marks `unavailable` (`mark_unavailable`). Without any configuration (tests, factories that skip it) the ledger is in-memory and reported as mode `memory`.
4. **R4, STOP at every boundary.** The originating STOP ticket is captured first and rechecked after the gate review (`execute`, `execute_confirmed`), after the approval wait, and at the final execution boundary inside `_invoke`, with no `await` between the ledger reservation and the tool call. `_invoke` requires the ticket (a missing one is no longer a possible argument): after STOP and a resume the generation differs, so the call is refused. The same boundary re-checks the offline profile and that the arguments still hash to the approved fingerprint. `preflight.unattended_refusal` captures the generation on entry (or takes the caller's `ticket`), rechecks STOP and the offline profile after `gate.review`, and gives the gate a deep copy; `final_refusal(tool, ticket)` is the mandatory check right before invoking. `skills/runner.py` and `plugins/harness/mcp_remote.py` capture the ticket before the gate, run `unattended_refusal` then `final_refusal`, and execute a deep copy of the reviewed arguments.
5. **R5, bound, one-use approvals.** Each armed approval gets its own `approval_id` (`ApprovalTicket.approval_id`, carried on `ActionApprovalRequired.approval_id`). For a call-bound ticket an `ActionApproved` counts only if it carries the approval id, the tool name AND the fingerprint, all equal to what was armed; missing or different is ignored and the ticket stays open (a denial is always honoured). The first answer spends the ticket; a later ticket for the same trace has a new id. Producers updated: executor bypass answer, chat bridge (both answer paths), task bridge, mission coordinator (id kept in `PendingMissionToolApproval`; SUPERSEDED by block 13: it is now exposed as the opaque `request_id` and required on approve), mission auto-approver. Voice: the deferral output now carries `approval_id` besides `call_fingerprint`; `execute_confirmed(expected_fingerprint, expected_approval_id)` requires both for anything that is not a declared read, otherwise it refuses and keeps the question. The identity travels through the tool-use loop descriptor, the brain manager, `SupervisorToolRequest.call_fingerprint/approval_id`, the supervisor gateway, the realtime bridge and the live bridge. Arguments are deep-copied once at the start of `execute` (`copy.deepcopy`); that private copy is fingerprinted, published, reviewed (gate gets another copy), stashed and executed (the tool gets another copy), so a later mutation by the caller or the tool cannot make the executed call differ from the approved one.

### Frontend

No TypeScript was changed and `dist` was not rebuilt. The UIs never publish `ActionApproved` themselves: the chat card and the mission deck answer through Python (`ChatApprovalBridge`, `MissionToolApprovalCoordinator`), which now echo the identity they hold server-side. A third-party or legacy client that publishes `ActionApproved` directly (without tool, fingerprint and approval id) is now ignored for executor-armed calls.

### NOT covered

- Nothing here was executed. New tests: `tests/contract/test_block8_review_fixes.py`; block 7 test file and five other existing tests that answer approvals or confirmations were adapted to the new identity (all written, not run). Test doubles that fake `execute_confirmed` of the executor with a fixed signature may need the two new keywords.
- `no_effect_guaranteed` / `failure_means_no_effect` are opt-in and no shipped tool sets them yet, so every failed side-effecting call is `unknown` until tools declare it. This is conservative on purpose (more confirmations after errors).
- Declaring `safe` tools without `read_only` side-effecting means a repeated identical call in one turn under a new call id now asks for a confirmation (`duplicate_effect`). Tools that really are reads should declare `read_only`.
- The reduced-protection state is exposed as a function (`protection_state()`); no UI surface shows it yet.
- A reserved row whose process dies stays `started` and is unknown after restart, as in block 7. The ledger still records only what this app issued, not what the far side applied.
- The skill runner and remote MCP harness still have no per-call approval, no ledger and no plan-mode check; they only honour STOP (now generation-bound), the offline profile and the gate.
- Cross-process atomicity relies on SQLite locking with a 2 s busy timeout; contention beyond that surfaces as "ledger unavailable" and the call is refused, not run.
- Approval identity is not a signature: any in-process code that can read the bus can read `approval_id` from `ActionApprovalRequired`. It prevents stale, mismatched and context-free answers, not a hostile in-process subscriber.

## Block 9: GPT round 2 (F3, F5)

Status of this section: written by static reading only. Nothing was run: no tests, no app, no CI, no build. `py_compile` and `ruff check` only. Test file `tests/contract/test_block9_round2.py` is written, NOT run.

### F3: failed-row retry collision and legacy `effects` table (state: fixed, unverified)

- **Files / functions:** `safety/effects.py` (`EffectLedger.reserve`, `_free_attempt`, `_import_legacy`, `configure_default_ledger`), `safety/tool_executor.py` (`_invoke` now uses `reservation.attempt` for `finish` / `mark_unknown`).
- **Choice (retry):** the primary key `(scope, key, attempt)` is kept. `_decide` still returns `new` after a guaranteed-no-effect `failed` row, but `reserve` now writes the new row under its own identity: the first free `retry-N` attempt (or the owner's approval id if one was given). The `failed` row is never touched, so outcome history is preserved; no `IntegrityError`, no false `in_flight`. `Reservation.attempt` carries the identity and the executor uses it to finish or mark the right row. An integrity error that remains is still reported as `in_flight`, but only on a real concurrent insert.
- **Choice (migration):** opening a persistent ledger reads the block 7 `effects` table once (guarded by `effect_meta`) in one `BEGIN IMMEDIATE` transaction and copies each row into `effect_calls` with `attempt = 'legacy-block7'`. `done` stays `done` (replay), and `started`, `unknown` and also the old `failed` become `unknown` (block 7 did not prove a failure side-effect free), so none can be silently re-executed; a forced confirmation is needed. Rows older than the retention window are skipped. The old table is left unchanged. If the import fails, `EffectLedger` raises `LedgerUnavailable` with a clear message; `configure_default_ledger` then makes the state `unavailable` (effectful calls refused, message shown in `protection_state()`), or `reduced` only if the owner enabled it (in-memory, legacy rows NOT covered, said in the detail).
- **Residual limits:** old `failed` rows are stricter than before (unknown); `retry-N` is chosen per `(scope,key)` and retries are unbounded; a ledger file written by a different process between import and first use is only covered by the normal reserve logic; in-memory ledgers do not migrate.
- **Controls prepared, NOT RUN:** `test_failed_no_effect_allows_retry_with_new_attempt_row`, `test_second_failed_retry_gets_third_identity`, `test_legacy_effects_table_is_honoured`, `test_legacy_migration_failure_fails_closed`.

### F5: skill runner and MCP harness (state: partial)

- **Files / functions:** `safety/preflight.py` (new `guarded_call`; `offline_refusal`, `final_refusal`, `unattended_refusal` accept the tool object and pass it to `egress_guard.tool_refusal(name, tool)`), `skills/runner.py` (`SkillRunner.run` step execution), `plugins/harness/mcp_remote.py` (`MCPRemoteHarness.invoke`).
- **Choice:** not routed through `ToolExecutor` (it needs an approver, bus tickets and a brain context that these paths do not have; wiring them is a large change). Instead both paths call `guarded_call`: deep snapshot of the arguments once (reviewed copy, fingerprinted copy and executed copy are separate), STOP ticket captured first, offline profile with the TOOL OBJECT (the skill's resolved tool; the MCP harness passes the server's tool descriptor), gate, final STOP / offline recheck, then `EffectLedger.reserve` (same atomic reserve as the executor; scope = run trace id, key = skill name + step index, or qualified tool name) with no await between reserve and call. Unknown, duplicate, in-flight, replay and unavailable ledger all REFUSE (no approver to confirm). A raise or cancellation marks `unknown`; a failure is `unknown` unless the tool vouches `no_effect`. The skill runner honours `read_only` (read tools skip the ledger); the MCP harness treats the remote call as always effectful.
- **Residual limits:** still no per-call interactive approval and no plan-mode check on these two paths (an `ask`/`deny` gate or risk tier refuses); the scope is a fresh id per run, so the ledger guards repeats inside one run and process, not a redelivered run; the same tool with identical arguments twice in one skill run now refuses the second call as a duplicate; the MCP descriptor carries no declared offline capability, so under the strict offline profile it is refused (conservative).
- **Controls prepared, NOT RUN:** `test_guarded_call_runs_once_then_refuses_duplicate`, `test_guarded_call_raise_leaves_unknown`. Existing tests that mock `tool.execute` of the skill runner may need an in-memory default ledger.

### Audit of `ActionApprovalRequired` / `ActionApproved` producers

| Producer | Event | approval_id + fingerprint |
|---|---|---|
| `safety/tool_executor.py` (arming, `ActionApprovalRequired`) | required | creates both |
| `safety/tool_executor.py` (bypass answer) | approved | both, plus tool name |
| `safety/approval.py` `ApprovalWorkflow.approve` (helper, no production caller found) | approved | optional keyword args, defaults empty: a call-bound ticket ignores it unless the caller passes them |
| `agent_chat/approval_bridge.py` (inline decision and user answer) | approved | both, echoed from the event |
| `tasks/approval_bridge.py` | approved | both |
| `missions/tool_approvals.py` coordinator `approve` | approved | both (block 13: published as the opaque `request_id`, required on the HTTP approve) |
| `missions/tool_approvals.py` auto-approver | approved | both |
| `voice/tool_confirmation.py` | none published (two-turn: `execute_confirmed` with both) | n/a |
| Frontend `hooks/useWebSocket.ts` | CONSUMES `ActionApprovalRequired` (toast, `jarvis:mission-tool-approval` event); publishes nothing | n/a |
| Frontend `hooks/useRuns.ts` | lists `ActionApproved` as an event name for filtering only | n/a |
| Mission HTTP routes (`ui/web/missions_routes.py`) | call the coordinator, which publishes | via coordinator |

No TypeScript source publishes `ActionApproved`, so no TS edit was needed or made; `dist` was NOT rebuilt and no UI flow is declared available. All Python producers already carry the identity; no change was necessary in them. `ApprovalWorkflow.approve` is the only one that can omit it (by design: a missing identity is ignored by a call-bound ticket).

### Not run / unverified

Nothing here was executed. Unverified: the SQL of the migration against a real block 7 file, the `retry-N` selection under concurrent processes, the new `guarded_call` paths, existing tests of the skill runner and the MCP harness (they may need the default ledger set in memory).

## Block 13: GPT round 3 (G1, G3)

Status of this section: written by static reading only. Nothing was run: no tests, no app, no CI, no build. TypeScript was NOT type-checked and `dist` was NOT rebuilt, so the UI flow is NOT declared available. `py_compile` and `ruff check` only. Test file `tests/contract/test_block13_round3.py` is written, NOT run. Existing tests that approve through the coordinator or the HTTP route without an identity (`tests/missions/api/test_mission_tool_approval_routes.py`, `tests/unit/ui/web/test_control_strip_*.py`, `tests/unit/cli_ctl/test_commands_missions.py`, `ControlStrip*.test.tsx`) were NOT adapted and will need the new fields.

### Correction to block 8 / 9 statements

Earlier text said the `approval_id` is a server-only secret that the browser never sees. That is withdrawn. The id is an OPAQUE ONE-USE REQUEST IDENTITY, not a secret: it is exposed in the pending-approval payload as `request_id` and the client echoes it. Security does not rest on hiding it; it rests on the server matching it exactly and spending it once (`ApprovalTicket` and the coordinator's atomic take).

### G1: HTTP approval bound to one request instance (state: fixed, unverified; UI not available)

- **Files / functions:** `missions/tool_approvals.py` (`PendingMissionToolApproval.to_dict` / `matches_identity`, `MissionToolApprovalCoordinator.approve`, `deny`, `_take_live`, `_on_resolved`), `ui/web/missions_routes.py` (`ApproveToolApprovalBody`, `DenyToolApprovalBody.request_id`, `approve_mission_tool_call`, `deny_mission_tool_call`), `cli_ctl/commands/missions.py` (`approve-tool`), frontend source `lib/controlStrip.ts` (`PendingDecision.request_id`, `useDecide`), `components/missions/api.ts`, `ToolApprovalPanel.tsx`, `types/missions.ts`. `ControlStrip.tsx` needed no edit: it hands the whole `PendingDecision` to `useDecide`.
- **Choice:** the payload (`to_dict`, used by the missions list and the control strip) now carries `request_id` (= the executor-armed `approval_id`, new per `arm`). The approve body is REQUIRED and strict: `request_id`, `tool_name`, `call_fingerprint` (non-empty) and `generation` (int); anything missing is a 422 before the coordinator is touched. `approve()` refuses (returns None, HTTP 404, nothing consumed) unless id, tool and fingerprint are all given AND equal the pending request's, checked inside `_take_live` under the same lock that pops it (match and consume are one atomic step); a pending request that itself has no id/fingerprint (legacy publisher) cannot be approved by HTTP at all. The `ActionApproved` published carries the pending request's id, tool and fingerprint, so `ApprovalWorkflow` (which already requires them) spends the ticket once. Two identical proposals reusing a trace in one generation now differ by `request_id`: the old id cannot approve the newer pending one. Deny works with or without identity; an identity that is given must match (otherwise it is not taken). `_on_resolved` now removes the pending entry on `ActionApproved` only when the event matches the pending identity (`matches_identity`); a legacy or mismatched approval that the workflow ignores no longer hides the real pending decision. `ActionDenied` and `ActionExecuted` still end the request by trace.
- **Residual limits:** the id is an identity, not a signature: anyone who can read the list can approve (same trust as the local UI session; STOP generation, door check and fingerprint remain). `ActionExecuted` carries no id, so a stale executed event for the same trace can still clear a newer pending entry (display only; the workflow ticket is unaffected, and the effect ledger blocks a re-run). The mission `ToolApprovalPanel` takes the generation from the control strip query and refuses client-side when unknown. The CLI needs the four values passed by hand (`--request-id --tool --fingerprint --generation`). `MissionToolAutoApprover` is unchanged (it answers with the id from the event, as before). Dist stale.
- **Controls prepared, NOT RUN:** `test_pending_payload_exposes_request_id`, `test_approve_without_exact_identity_consumes_nothing` (missing/late/wrong id, fingerprint, tool), `test_exact_identity_approves_once_and_is_spent`, `test_late_old_id_does_not_approve_second_identical_proposal_same_trace`, `test_request_without_identity_cannot_be_approved`, `test_deny_works_with_and_without_identity`, `test_rejected_legacy_approval_does_not_hide_pending_decision` (invalid event removal), `test_workflow_still_rejects_legacy_event_and_coordinator_agrees`, `test_http_approve_without_identity_is_4xx_and_consumes_nothing`, `test_http_deny_without_body_identity_still_works`.

### G3: unattended paths enforce the real policy (state: fixed for deny/ask/plan-mode; partial overall)

- **Files / functions:** new `safety/decision.py` (`decide_call`, `plan_mode_refusal`, `CallDecision`), `safety/tool_executor.py` (`execute` now calls `decide_call` for blacklist/tier/ASK and `plan_mode_refusal` for plan mode), `safety/preflight.py` (`_unattended_decision`, `unattended_refusal`, `guarded_call`), `skills/runner.py` (passes its evaluator), `plugins/harness/mcp_remote.py` (unchanged call site; now subject to the default evaluator), `core/tool_read_only.py` (`any_chat_read_only`).
- **Choice:** one function, `decide_call(evaluator, tool, args, plan_mode)`: plan mode (reads only) -> blacklist/whitelist/tier default via `RiskTierEvaluator.evaluate` -> `needs_user_confirmation`; result `deny` / `ask` / `allow`; a broken evaluator denies. The executor uses it at its evaluate step (ordering of its other checks is unchanged; `execute_confirmed` still evaluates directly, untouched). `unattended_refusal` / `guarded_call` run, after STOP and the offline profile, the same function: `deny` refuses, `ask` REFUSES because no approver exists; no gate, or a gate that says `allow`, is not an approval. The gate still runs afterwards and still can only tighten; it now receives the evaluated tier instead of a fixed "ask". Only `granted_tools` (exact tool names handed in by a caller from validated policy, default none, nothing from model arguments) can let an ASK through; no production caller passes it. The `tier` parameter of both functions was removed (no caller used it). The ledger, STOP re-checks and deep copies are unchanged. The skill runner keeps its frontmatter-tier check and now ALSO applies the real tool object's tier (the stricter wins). The MCP harness has no tool object, so the descriptor's `risk_tier` (usually absent) falls back to the configured default tier: with the default `ask` every remote MCP call is refused unless the whitelist or config marks it safe. Plan mode is per chat session and these paths carry no session id, so ANY session in plan mode makes them refuse non-read calls (conservative).
- **Residual limits:** behaviour change by design: skills whose real tool is `ask` and the remote MCP harness are refused unless whitelisted or granted; nothing grants today. `execute_confirmed` and the executor's duplicated plan-mode early block are not folded into the single function. A skill step reaching a tool the registry cannot resolve is unchanged. The policy treats `read_only` only through the shared `allows_read`; the MCP descriptor's `read_only` is whatever it declares.
- **Controls prepared, NOT RUN:** `test_decide_call_ask_deny_allow_and_plan_mode`, `test_unattended_ask_with_no_gate_refuses`, `test_unattended_deny_and_plan_mode_refuse_even_for_safe_tier`, `test_unattended_safe_proceeds_and_exact_grant_lets_ask_through`, `test_guarded_call_ask_does_not_run_the_tool`. Existing block 9 `guarded_call` tests use fake tools and will need `evaluator=` or a safe default tier.

### Not run / unverified

Nothing was executed: no pytest, vitest, tsc, eslint, build or app. TypeScript is not type-checked, `dist` not rebuilt, UI flow not declared available. Unverified: pydantic 422 behaviour for the strict body, the lock/atomic match under concurrency, the executor after the `decide_call` swap (mocked evaluators in existing tests), and every test above.
