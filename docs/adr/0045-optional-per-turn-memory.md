# ADR-0045 — Optional per-turn memory provider

**Status:** Accepted · **Date:** 2026-10-25

## Context

The wiki injector is tied to the vault search and its keyword relevance gates. An
external memory (Jarvis' own: episodic log, link graph, learning loop) needs to add
its own context to a turn without pretending to be a vault.

## Decision

1. `dodaios/brain/turn_memory.py` holds a process-wide `TurnMemory` provider
   (`set_turn_memory`), absent by default.
2. `BrainManager.generate()` appends `recall_block(user_text)` to the per-turn
   context suffix, after the wiki injector and never for society turns.
3. The call is bounded by `budget_ms` (default 250 ms); a provider that is slow,
   empty or raising yields no block and never breaks the turn.
4. The block is framed as possibly irrelevant notes, never as instructions.
5. With no provider installed behaviour is unchanged.

## Consequences

- Worst case a turn waits `budget_ms` for the memory.
- The provider sees the user's utterance; it must not send it elsewhere.
