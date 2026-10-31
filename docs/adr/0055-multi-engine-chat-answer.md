# ADR-0055 — Ask several engines from the chat, one answer, one voice

**Status:** Accepted (opt-in) · **Date:** 2026-10-27

## Context

ADR-0050 built the orchestrator (privacy gate, one budget, no-tool branches, synthesis) and a Settings screen over it, but
nothing connected it to the chat turn: the owner had to use a separate screen, and its answer never reached the chat or
the voice.

## Decision

`dodaios/brain/multi_engine_chat.py` turns `[brain.multi_engine]` into branches and runs them through the existing
`Orchestrator`; `BrainManager.generate` calls it when the message itself asks for it ("ask several engines: ...",
"chiedi a più modelli: ...", and the de/es/pt forms).

1. **Explicit per message, off by default.** A branch has no tools, so a setting alone must never route "open the browser"
   there. `enabled = false` is the default; with it off, an explicit request is answered with a plain notice, not a silent
   normal turn.
2. **One answer, one response event.** The final synthesis is published once through `_record_response_side_effects`, the
   same path as any reply, so the voice speaks it once. Branch answers stay inside the module: never published, never in
   history.
3. **Same gate for everything.** Only the question is sent (category `task`); history is not. Identifiers are masked, the
   budget is one ceiling for branches plus synthesis (default 0: nothing remote runs until the owner sets one), an unknown
   price is refused unless `allow_unknown_cost`, a subscription connection is not API access.
4. **The synthesizer is `synthesizer`, else the first engine that actually answered.** A failed synthesis says so; the
   branch answers are never spliced together.
5. **Work refuses it** with a notice (ADR-0049). Cancelling the turn task cancels every running engine.

## Not decided here / limits

- No settings screen for `[brain.multi_engine]` yet (edit the TOML); no always-on mode.
- Reached by the voice only where the turn goes through `generate` (classic pipeline, delegated realtime turns); a
  realtime model that answers by itself without delegating does not pass here. Not verified with a real microphone.
- Only tested with fake providers. No real engine, price, subscription or terms of service was involved (checklist D01–D08
  of Jarvis-di-dax issue 16 are NOT RUN).
- A newer turn does not interrupt an engine already running from an earlier one (no turn supersede across calls).
