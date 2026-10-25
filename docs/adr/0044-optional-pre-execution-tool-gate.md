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
