# ADR-0052 — Engine overrides, safe fallback and measured reporting

**Status:** Accepted (library only; not wired into a user flow) · **Date:** 2026-10-26

## Context

ADR-0049 fixes what the Work product may run; ADR-0050 fans a Consumer task out to several engines. Two gaps from
the brief remain: per-project/per-task engine overrides that cannot reach the cloud from Work, and a fallback
policy that never replays an effect or sends data nowhere the owner did not authorize.

## Decision

`dodaios/brain/engine_routing.py`, no I/O of its own:

1. `resolve_engine(default, project, task)`: task beats project beats default. EVERY layer is checked, not only the
   winner: under Work a remote layer is rejected with the layer named (`override_rejected`), even when a local task
   override would shadow it, so a poisoned setting is reported instead of waiting to become effective.
2. `ActiveEngines`: thread-safe record of the engine running each turn/job, for the UI to show instead of the
   default.
3. `FallbackRouter`: ordered candidates, each checked for authentication, product policy, egress authorization for
   remote engines, declared capability (unknown = unsupported), unknown subscription quota and the aggregate budget.
   Fallback happens ONLY while nothing visible was produced (no text, no tool call) and only after a transient
   failure (timeout, connection, overload). Quota, auth and unclassified failures stop without trying another engine
   (no retry storm, no silent switch to another paid account). A partial answer is never joined with another
   engine's answer. Attempts are bounded; under Work no remote candidate is tried.
4. `consumer_orchestrator.fan_out_report`: per-branch measured first-token and total time, usage and data sent, with
   the cost basis kept apart (`subscription_quota`, `api_charge`, `local_resources`) and never added into one total.
   Unmeasured values are `None`, never zero.

## Not decided here

Wiring into chat/voice/job paths, a settings UI for project/task overrides, real prices and quotas, provider-side
cancellation cost, and live (non-fake) measurements: none were taken. Local runs have resource costs without token
billing; they are reported by basis, not priced.

## Consequences

A caller that adopts these helpers gets fail-closed overrides and a fallback that cannot double an effect. Nothing
changes for existing flows until they call them.
