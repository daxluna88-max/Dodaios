# ADR-0048 — Per-turn context for the local voice engine

**Status:** Accepted · **Date:** 2026-10-25

## Context

ADR-0045 lets an external memory add a block to a `BrainManager` turn. Native live voice (the local engine) does not
go through `BrainManager.generate`: the engine's own LLM answers, and the app asks for each response
(`creates_responses_automatically = False`). `update_session(turn_directive=...)` cannot carry a memory block:
the local adapter folds it into `instructions` and the engine REPLACES its instructions with it, then re-primes.

## Decision

1. New protocol message `turn.context` (`{session, content}`) and `ConversationSession.on_turn_context`.
2. The text rides on the user message of the NEXT model call only: it is not stored in the history, does not touch
   the system prompt (the primed prefix stays valid), is capped at 2000 characters, and is dropped when it is used,
   when the user starts speaking again, or when an empty text arrives.
3. `LocalVoiceSession.set_turn_context(text)` sends it. `NativeLiveVoiceSession._request_native_response` calls
   `recall_block` (ADR-0045, 200 ms budget) and sends the result before `request_response`, on connections that
   have `set_turn_context`. It sends an empty block when there is nothing, so a stale context can never stand in for
   the current turn. A slow or failing memory yields no block and never blocks the answer.
4. Connections that answer by themselves (Gemini Live) cannot receive context before they speak and are untouched.

## Consequences

- With no provider installed the engine behaves as before (an empty context message is sent and ignored).
- A turn may wait up to 200 ms for the memory.
- Tool calls from voice already run through `ToolExecutor`, so the tool gate (ADR-0044) applies to them.
