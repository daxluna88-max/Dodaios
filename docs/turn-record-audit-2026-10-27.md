# Turn record audit (plan block 8 "Registro reale dei turni"), 2026-10-27

Scope: what the main screen's "Engine" panel says about the last turn, and whether it is the engine/model that
really ran. Static review and static fixes only: **nothing was run** (no tests, no build, no app). The git branch
name `block8` is the review-fix batch, not this plan block.

Vocabulary used below: PLANNED = decided, no call issued. ATTEMPTED = a call was issued to the engine/model listed.
COMPLETED / FAILED = final. SUPERSEDED = STOP overtook the turn and its reply was dropped.

## Files

| File | Role |
|---|---|
| `dodaios/control/turn_record.py` | data (`TurnRecord`, `EngineUse`, `Flow`, `RecordKey`) and `TurnLedger` (holder, compare-and-set) |
| `dodaios/brain/manager.py` | `_begin_turn_record`, `_note_dispatch`, `_finish_engine_record`, `_StopTurn` (per-turn context), `_run_multi_engine_turn`, the provider loop in `_generate` |
| `dodaios/brain/multi_engine_chat.py` | `_record_turn`, `_close`, `ask` (multi-engine branch records) |
| `dodaios/brain/consumer_orchestrator.py` | branches, `begin_turn` generation, `run_id`, `EgressRecord` (the source of `attempted` flows) |
| `dodaios/brain/engine_routing.py` | `resolve_engine` (task > project > default), `ActiveEngines`, `FallbackRouter` / `Attempt` |
| `dodaios/ui/web/control_strip_routes.py` | `GET /api/control-strip` -> `_engine_payload` (reads `brain._turn_ledger_obj.last()`) |
| `ui/web/frontend/src/lib/controlStrip.ts`, `components/ControlStrip.tsx` | types and rendering |

## Event table

Source of truth column: "dispatch" = the (provider, model) pair taken from the chain entry about to be called (it
already carries the TurnOverride pair or the fallback candidate); "attribute" = a mutable field of the manager.

| Event | Written by | Source of truth (before -> after block 9) | Persisted / shown | Gap found -> state now |
|---|---|---|---|---|
| Turn start | before: nothing for cancel / style / screen-confirm fast paths; `_record_single_engine_turn` after the multi-engine check. After: `_begin_turn_record` right after the STOP door check | -> `(turn id, STOP ticket, new run id)` | in memory only (`TurnLedger._last`, latest turn only); shown by `/api/control-strip` | Fast paths ahead of the old call left the PREVIOUS turn on screen as "last turn". Now every admitted run opens a record (phase `planned`, no engine). A refused (door closed) turn writes nothing |
| Engine chosen | before: `self.active_provider` read before any fast path and before the chain | attribute -> dispatch | shown as "chosen at the start" | Wrong with a TurnOverride (its provider/model is not `active_provider`), wrong when the chain skips dead/cooldown entries, and concurrent turns read the same attribute. Now no engine is written at start |
| Override applied | `generate(turn_override=...)` -> `_TURN_OVERRIDE`; chain built from it | -> dispatch (the chain entry) | before: not recorded at all, the strip showed `active_provider` | Now the override pair is what `_note_dispatch` receives. `requested` = first chain entry (`chain[0][0]`). Gap: the record does not say that an override (or `resolve_engine` source default/project/task) was the reason; `EngineChoice.source` and `ActiveEngines` are not wired to the ledger |
| Attempt start | `_note_dispatch(prov_name, model, requested)` in the provider loop, next to `_active_turn_identity` | dispatch | phase `attempted`, `engines[0] = (provider, "main", locality, model)`, `planned` = question flow if remote | Before: identity (provider, model) was kept per turn but model was dropped and nothing reached the record until the end. Skipped entries (dead, cooldown, init failure, vision) never reach dispatch so are never listed |
| Attempt fail | provider loop (`provider_errors`, `continue`) | -> next `_note_dispatch` | the failed engine is kept in `earlier_attempts` as `provider[:model]` | Before: lost. Gap: failure reason/class is not stored in the record (only the manager's `provider_errors`, health ledger); an attempt that fails and is NOT followed by another dispatch is only visible as the final `failed` |
| Attempt success / completion | `_finish_engine_record` in `generate`'s `finally` | before: `turn.identity` + `ledger_turn_id` match | after: key match (CAS) + phase `completed` / `failed` | `answered` = not crashed and not `_last_turn_all_failed` (per-turn flag). A fallback win shows `fell_back` |
| Fallback | same loop; `fell_back` property compares answering provider with `requested` | -> as above | `requested`, `fell_back`, `earlier_attempts` | `FallbackRouter` (engine_routing.py) has its own `Attempt` list (`ok/skipped/failed/stopped`) and is not the router `_generate` uses; it writes nothing to the ledger (gap) |
| Fast path (cancel, style, screen confirm, society, switch, language, depth, mission command) | `_record_response_side_effects` + return | before: record said "running" with a guessed engine until finish blanked it (cancel/style never recorded) | after: finish with `identity None` -> `engines=()`, `planned=()`, `planned_partial=False`, phase `completed` | No engine shown, as required. The panel says "no model used" |
| Multi-engine branch start | `ask()` -> `_record_turn(..., phase="planned")` after branches are built | branch list (provider, brain model) | reviewers with role `reviewer`, locality, model when the engine object exposes `.model` | Before: nothing until the very end. Unbuildable branches are only in `refusals` text |
| Multi-engine branch end | `_record_turn(..., phase="attempted")` once the synthesizer is built | orchestrator `BranchResult.egress` (metadata only) | `attempted` flows from egress | Per-branch ok/failed status is not in the record, only `refused` providers |
| Synthesis | `_record_turn(..., phase="completed"/"failed")` after `orchestrator.synthesize` | synth egress | synthesizer engine + flows | `planned` for the synthesizer: question flow only; the engine answers it also receives are not a listed category, hence `planned_partial` |
| Multi-engine early exits (work product, off, no engines) | `_close` | none | phase `completed`, no engine | Before: the previous record stayed on screen |
| Multi-engine crash | `_run_multi_engine_turn` except branch | none | `update(phase="failed")` | |
| STOP / stale drop | `_finish_engine_record` -> `turn.stale()` | STOP controller `admit(ticket)` | phase/state `superseded` (any earlier phase) | Before: the stale turn's finish still wrote its engine as if answered. Frontend shows "STOP came before this turn finished" |
| Late writer, older turn | any of the above | before: multi-engine `ledger.record()` at the end replaced whatever was current, no order/generation check; `finish` compared only the turn id | after: every write carries `RecordKey(turn_id, generation, run_id)`; `TurnLedger` applies it only if it is the current key; `begin` from an older STOP generation is refused | This was GPT's second finding. An old multi-engine answer finishing late can no longer replace the new turn's record |

## Findings of GPT, status

1. `_record_single_engine_turn` read `self.active_provider` before the fast paths: fixed. The method is gone; the
   engine is written at dispatch from the chain entry, passed as arguments. No instance attribute is read.
2. Multi-engine ledger written at the end without order/generation check: fixed (key + CAS, three phased writes).
3. `planned` is not a complete inventory: made explicit with `planned_partial` (true whenever a model ran), shown
   in the panel. It is not made complete: history, memory, files and tool output sent by the single-engine path are
   still not enumerated (they are not tracked per category at dispatch).

## Sources of truth after block 9

- Identity: `RecordKey(turn id, STOP generation = `_StopTurn.ticket`, run id = fresh per `_generate`)`. The orchestrator
  has its own `run_id` (`turn/gen/hex`) for its fan-out; it is not copied into the record.
- Engine/model: the chain entry at dispatch (single engine) or the branch (multi-engine; model only if the brain
  object exposes `.model`).
- Locality: `engine_policy.destination_of(provider).local` (loopback endpoint only).
- State: `phase` (planned/attempted/completed/failed/superseded) with `state` kept in step for older readers.
- Persistence: none. The ledger holds the latest turn in memory; a restart empties it. History is not kept.

## Residual gaps (not fixed)

- No persistence and no history of turns; only the latest run is visible. A concurrent newer turn hides the older.
- Failure reasons, `EngineChoice.source`, the `FallbackRouter` attempt list and skipped chain entries are not in the
  record.
- Tools used (`tools_used`) and `network_tools` are still never filled (`not_observed`). Tool calls of a stale turn
  already started are out of scope (ADR-0058).
- `run_task` (scheduled/background tasks) opens no record at all.
- Multi-engine branch models are unknown unless the brain exposes `.model`; per-branch status is not recorded.
- Voice/realtime turns that do not go through `BrainManager._generate` are not recorded.
- Locale files other than `en` carry the two new strings in English.
- Everything above and below is unverified by execution: no test, lint-format, build or app run was done.
