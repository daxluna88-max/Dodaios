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
- **Realtime / GPT-Live.** Superseded by the section «Block 12: personality review» below: the style now rides in the realtime and live session instructions, with the limits stated there.
- **Names.** The assistant does not use the owner's name yet.
- **Languages.** Commands: Italian and English only.
- **Tests.** `test_personality`, `test_mood`, `test_style`, `test_expression`, `test_style_commands`,
  `test_personality_routes`, `PersonalitySection.test.tsx` are written and NOT run; only static checks and local
  builds were done. Checklist items of Jarvis-di-dax issue 16 (groups P and O) are NOT RUN.
- **State path.** `setup_state.json` is computed at import in `state.py`; to confirm on a PC that it is imported after
  the product is chosen.

## Block 12: personality review (2026-10-27, local, NOT run)

Nothing was run: no pytest, vitest, tsc, eslint, vite, npm, app or service. The TypeScript was not type-checked and
`dist` was not rebuilt, so **the UI is NOT live**. Checks done: reading, `python3 -m py_compile`, `ruff check` (no new
finding). Tests were written, not run.

### 1. Re-audit of the three earlier corrections (static)

| Earlier finding | State found | Result |
| --- | --- | --- |
| Mood deadline re-anchoring | `MoodReducer.state()` re-anchors `expires_at` at the next fade drop, so reading twice or after persisting gives the same level. Traced by hand for three reads (30 s, 50 s, 70 s after a 3-level owner mood). | Correct; test added. |
| Profile re-applied to the active state | `StyleRuntime.sync` calls `set_profile` when the resolved profile changes; limits apply at once (range, mode, `moods_allowed`, pin); a session request survives unless its mood is no longer allowed. | Correct; one defect found in a neighbour (below). |
| Product identity in `PlanGate` | `accepts` compares product, session, turn and generation. | Correct, but see defects 2 and 3. |

Defects found and fixed:

1. **«Turn adaptation off» left the adaptive mood standing** until it expired. Now it drops a rule- or
   activity-made mood at once; an owner request, a hold and the owner's pin stay (they are not adaptation).
2. **`PlanGate` could not be driven by the STOP ticket.** `begin` refused any generation `<=` the last one, but the STOP
   generation is shared by many turns until a STOP, so the second turn would have been refused. Now a new turn of the
   same generation replaces the previous one (turns begin in order); a cancelled or older generation never returns.
3. **`PlanGate` depended on being told about STOP.** It now takes an optional `admit` predicate
   (`expression.stop_admits`, which asks `StopController.admit`): a plan is refused as soon as its generation is stale
   or the door is closed, even if `cancel` was never called. A fault asking STOP reads as «not admitted».

### 2. Limits of adaptation and the owner's explicit request

Two different things, now stated in code, API and UI:

- **Range of automatic adaptation** (`expressive_range`, 0..2, and `manual` mode): what rules and activity may do on
  their own. Capped, with a cooldown.
- **Explicit owner request** («more serious», a button, `POST /api/personality/mood`): coherent with the range ONLY
  because the range limits adaptation, not the owner. It may go beyond the range, up to `MAX_INTENSITY` (3), for 30
  min by default and this session only. It stays bound by **`moods_allowed`** (a mood not allowed is refused, whoever
  asks, and a later profile that forbids it drops it) and by the maximum intensity (0, 4, negative, bool or float are
  refused as `bad_value`). The owner's pin is likewise bound by `moods_allowed`. Session dial nudges («less irony») move
  a dimension, not a mood, and are clamped to the 0..4 scale.
- `mood.limits(profile)` returns both numbers; `GET /api/personality` carries `limits`, `channels` and
  `realtime_delivery`. The panel `PersonalityLimits.tsx` (new i18n namespace `personality_limits`; en source, de/es/pt/zh
  **unreviewed drafts**, marked in each file's `_status`) shows them as separate blocks, lists the allowed moods and
  repeats that style never changes facts, privacy, approvals, tools, budgets or stopping. The namespace is registered in
  `i18n/index.ts` (one union member and one loader entry); no other namespace was edited.

### 3. Style in the realtime and live paths

- `BrainManager.realtime_style_block()` builds the same profile + mood text as the written chat plus one sentence for a
  speaking model («shapes wording, sentence length and pacing of words only; never describe your voice, face or mood as
  changed or felt»). Enumerations only: no name, notes, reasons or mood history; nothing personal beyond the profile
  fields. It is read fresh at every update (nothing cached), so no plan can go stale; a fault logs a warning without
  values.
- `realtime/session.py`: `_session_instructions(..., style=)` puts it in the dynamic tail, after the voice contract and
  every operational rule (which it never overrides), at the three call sites (open, per-turn update, language retry).
  `live/session.py`: appended to the identity block, read once when the call opens.
- **Which providers can carry it** (`expression.REALTIME_STYLE_DELIVERY`, shown in the UI): `openai-realtime`,
  `local-realtime`, `local-voice` replace their instructions on each update (style applies every turn).
  `gemini-live`, `vertex-live` have no mid-session instruction message (only language, turn and standing directives
  travel), and the `live` module (`openai-live`, `openai-live-subscription`) builds its instructions once: for these
  the style in force when the call opens stays for the whole call; a mood change or a new profile applies to the NEXT
  call. Any other provider is reported `unknown`.
- **Not claimed, not done:** no voice modulation (no shipped TTS or realtime provider takes a per-turn parameter), no
  lip sync, no avatar driving (the avatar is not wired). The mood stays SIMULATED and local: a way of wording things,
  never an emotion claim and never a reading of the person.

### 4. STOP and generation

- While STOP has the door closed `realtime_style_block()` returns `""`, so an update built then carries no style (the
  refusal reply stays neutral). Because the block is rebuilt from the current state at each update, nothing from before
  STOP is replayed after resume.
- The avatar's `PlanGate` is bound to `StopController` through `admit` (defects 2 and 3). The avatar is still not wired,
  so no expression emit exists to guard today.
- Not done: no ticket check AFTER an `update_session` has been handed to the provider; the update is built and sent
  synchronously, and what the provider already received is cleared by the hang-up STOP already performs (ADR-0058).

### Residual limits

- de/es/pt/zh strings are drafts without a native review.
- The realtime style is wording only; whether a given model follows it is not verified.
- Gemini / Vertex / live-module calls do not follow a mid-call mood change.
- Commands remain Italian and English only; the assistant still does not use the owner's name.

### Controls prepared, NOT RUN

`tests/unit/brain/test_personality_limits.py` (owner request vs range, `moods_allowed`, intensity bounds, adaptation off,
fade re-anchoring, realtime text, STOP closing the realtime block, `PlanGate` + `stop_admits`, `_session_instructions`
order), one added case in `tests/unit/ui/web/test_personality_routes.py`, and
`views/assistant/PersonalityLimits.test.tsx`. To run on a PC: those three, plus the existing `test_mood`,
`test_expression`, `test_style`, `PersonalitySection.test.tsx`; then a real call on OpenAI Realtime and on Gemini Live
with a mood set, to hear (not assume) that wording changes and that Gemini keeps the connect-time style.
