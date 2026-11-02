# ADR-0056 — Personality profile and simulated mood

**Status:** Accepted (implemented, tests written but NOT run, not verified on a PC) · **Date:** 2026-10-27

## Context

Consumer and Work are two independent products (ADR-0054). Issue 17 asks for an assistant whose way of speaking the
owner can choose, that can adapt a little to the situation, and whose text, voice and avatar do not contradict each
other. The risks are the usual ones: free text that becomes an instruction, a "mood" that is mistaken for a feeling
or used to nudge the owner, and a style that leaks into permissions, tools, budgets or stopping.

## Decision

Two separate things, both pure and deterministic (no I/O, no model call, no hidden pass, no microphone or camera
analysis):

1. **The profile** (`dodaios/brain/personality.py`): the stable, owner-chosen part, stored per product in
   `[brain.personality]` (`PersonalityConfig`).
   - Only enumerations and small bounded integers: `mode` (`manual`, `adaptive`, `mixed`; default `mixed`), `preset`
     (`professional`, `friendly`, `concise`), `tone`, four 0..4 dials (`formality`, `warmth`, `humor`, `concision`),
     `expressive_range` (0..2), `moods_allowed`, `locked_mood`. Nothing the owner types can become an instruction or
     carry personal data to an engine.
   - A preset gives the base values; each dial the owner set overrides it. Unset preset = the product default
     (Work `professional`, Consumer `friendly`).
   - Initiative is not here: `[brain] proactivity` already is that setting.
   - `schema_version` is stamped; an older block is migrated, a newer one is not interpreted (defaults, status
     `newer`), a block that does not validate is not partly applied (defaults, first problem named). A damaged block
     never stops the app.
2. **The mood** (`dodaios/brain/mood.py`): a small reducer, temporary, bounded, reversible and explainable. It is an
   expressive state of the assistant, never a claim of feeling and never a reading of the person.
   - Moods: `neutral`, `cheerful`, `calm`, `focused`, `serious`; intensity 0..3.
   - Drivers, and nothing else: the owner's explicit request, the activity kind the owner picked (`coding`,
     `writing`, `sensitive`, `casual`), and explicit dialogue signals the app already has (`task_failed`,
     `task_done`).
   - Priority: owner request > activity > adaptation from dialogue signals; a lower one never replaces a higher one
     that is still active. Adaptation has a 60 s cooldown, lasts 5 min, and is capped by `expressive_range`; the owner
     may go up to intensity 3 (30 min by default).
   - After expiry the intensity fades one level per 20 s to neutral; time never builds a mood up. `manual` mode or
     «adaptation off» for the session blocks every driven change. `locked_mood` (owner pin) survives expiry.
   - State is in memory, per conversation: neutral after a restart unless pinned; never written as a memory.

### How it reaches the assistant

- **Text.** `dodaios/brain/style.py` builds a short instruction from enumerations only (no notes, no reasons, no mood
  history), so it is safe to send to any engine the owner chose. It always carries the rules: a style, not a feeling;
  no claim of emotions or of what the user feels; style never changes facts, accuracy, privacy, approvals, tools or
  stopping; no guilt, pressure or flattery; sober on errors and sensitive tasks (humor forced to 0 under `serious`).
  `BrainManager._style_instruction` adds it to the prompt; a fault logs a warning and the turn continues without it.
- **Chat commands** (`style_commands.py`): «more serious», «less irony», «stay like this», «back to neutral», «turn off
  adaptation» (Italian and English). Anchored: only a short message that is the command; a longer request that merely
  contains such words is a normal turn. A clear command changes THIS SESSION only (a mood event or a session nudge of
  one dial, never written to the profile; «always» does not change that, the reply points to Settings). An unclear
  one gets a clarifying question and changes nothing. No model decides what was meant.
- **One plan for text, voice, avatar** (`expression.py`): derived from the same resolved profile and mood, once per
  turn, tagged with the turn identity and a generation; a plan from an old turn or generation is refused, so after
  STOP or a new turn nothing from the previous one moves the face or the voice. The avatar gets an expression only
  from those it declares (`neutral`, `smile`, `curious`, `surprised`): only `cheerful` maps (to `smile`); other moods
  stay neutral; `curious`/`surprised` are never taken from a mood; unknown support is neutral.
- **Settings.** `GET/PUT/DELETE /api/personality`, `POST /api/personality/preview` (nothing saved),
  `POST /api/personality/mood` (session only), and a section in the assistant profile. A saved profile applies at
  once. The first-run window (ADR-0057) writes only presets plus dials, never a new mode.

### What style never does

It does not read or change permissions, tools, approvals, budgets, accuracy, privacy gates or STOP: those modules do
not import it.

## Not decided here / limits

- **Voice.** No shipped TTS provider accepts per-turn parameters (`TTSProvider.synthesize` takes text, voice and
  language; speed is fixed at construction), so the voice part of the plan is reported `unavailable`. No emotional
  voice is promised.
- **Avatar.** The plan exists, but the avatar is not wired to the runtime.
- **Realtime / GPT-Live.** The style is not yet in the realtime instructions.
- **Names.** The assistant does not use the owner's name yet.
- **Languages.** Commands: Italian and English only.
- **Tests.** `test_personality`, `test_mood`, `test_style`, `test_expression`, `test_style_commands`,
  `test_personality_routes`, `PersonalitySection.test.tsx` are written and NOT run; only static checks and local
  builds were done. Checklist items of Jarvis-di-dax issue 16 (groups P and O) are NOT RUN.
- **State path.** `setup_state.json` is computed at import in `state.py`; to confirm on a PC that it is imported after
  the product is chosen.
