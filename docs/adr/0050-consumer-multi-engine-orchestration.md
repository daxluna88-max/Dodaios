# ADR-0050 — Consumer multi-engine orchestration: privacy gate, one budget, bounded branches

**Status:** Accepted (library only; not wired into a user flow) · **Date:** 2026-10-26

## Context

The Consumer owner speaks only to Jarvis, which may use several explicitly connected engines at once (a subscription
integration, an API model, a local model). A remote model necessarily receives what is sent to it, so the local
privacy decision and the spend ceiling must sit in front of every call, for the whole fan-out and not per branch.

## Decision

`dodaios/brain/consumer_orchestrator.py`, no I/O of its own (callers pass brain objects):

1. `PrivacyGate.prepare` builds each remote branch prompt from the minimum context: only allowed categories, never
   `local_only` items, identifiers masked by a pluggable redactor (default: emails, key-shaped tokens, long digit
   runs: a floor, not anonymization). A local branch keeps its context. What left is logged as destination,
   categories and counts (`EgressRecord`), never as a second copy of the prompt.
2. `Budget.reserve` is one atomic ceiling for the whole fan-out. An unknown price is refused unless the caller
   opts in, and is never counted as zero.
3. `Branch.kind` is `subscription | api | local`. A branch that needs API access is refused on a subscription
   connection; an unauthenticated branch is refused; nothing falls back to another paid path.
4. `Orchestrator.run`: bounded concurrency, per-branch timeout, partial failure reported per branch,
   `first_success` cancels the losers, results of a cancelled or superseded turn are dropped (`stale`), branches run
   without tools and any tool call they emit is ignored.
5. Under `JARVIS_PRODUCT=work` every non-local branch is refused (ADR-0049).

## Not decided here

Choosing branches, the final synthesis call (it must pass the same gate), provider retention terms, real prices and
subscription entitlements (verify official integrations per provider), wiring into chat or voice, any UI, and
playback ownership (no overlapping spoken answers) for voice.

## Consequences

Safe to build the Consumer flow on top; nothing user-visible changes until a caller uses it.
