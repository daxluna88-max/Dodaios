# ADR-0057 — Guided first run for Consumer and Work

**Status:** Accepted (implemented, not verified on a PC) · **Date:** 2026-10-27

## Context

Consumer and Work are two independent products (ADR-0054). Both had the same three-step setup window
(name = wake word, connect an AI, voice) followed by the walk through the app. Issue 18 asks for a short guided
first run that differs per product, asks for nothing it does not need, and never claims more than is true.

## Decision

The existing onboarding is extended, not replaced: the gate, the setup window, the walk and the per-product
`setup_state.json` stay. The window now has six steps, `name`, `voice`, `personality`, `connect`, `privacy`,
`summary` (then `tour`); stored older step ids still resume where they were.

1. **Welcome (`name`).** The assistant's name (the wake word, as before) plus the owner's name and, for Work, the
   workspace's. Optional. They stay on this computer: never in a prompt, never sent to an engine.
2. **Interaction (`voice`).** Text, voice or both; text is always available. Choosing voice asks for nothing:
   the microphone is requested when it is first used. The voice settings only show when voice is chosen.
3. **Personality.** Static written previews (no model, no API). «Keep this tone» is the `manual` mode and «Adapt the
   tone to the situation» is the `mixed` mode of ADR-0056; `adaptive` stays an advanced setting. Consumer: Friendly,
   Ironic, Essential; Work: Professional, Collaborative, Essential. «Ironic» and «Collaborative» are presets plus
   dials (`friendly` + humor 3, `friendly` + humor 0 + formality 2), not new modes. Skipping writes nothing, so the
   product default applies (Work professional, Consumer friendly).
4. **Engine.** Consumer: one engine first (the existing connect step), with a note on local / API key / official
   sign-in, and that a subscription is not API credit. Work: a local-only panel; no cloud sign-in is offered and
   nothing falls back to the cloud. Comparing engines stays in Settings.
5. **Privacy and actions.** What stays local and what a remote engine receives, memory as a switch, and the
   approval note. Leaving this step is the one explicit APPLY.
6. **Summary.** «Your Jarvis», read back from the configuration after it was written. «Start» when an engine is
   selected, allowed and authenticated; «Explore the interface» otherwise. No generated answer or audio.

### What the apply step writes

`[brain.personality]` (unless skipped), `[safety] approval_mode = "ask"` and `[memory.learning] enabled`
(off unless the owner turned it on). Only after all of them succeeded is the applied mark stored, so a failure
never leaves the run reported done; the writes are idempotent, so it is simply run again. Changing a choice and
continuing applies again. The route needs a click in the UI (no Bearer, an Origin) and refuses once onboarding is
complete: an installed app's approval mode and memory switch change in Settings, never by replaying the guide. A
replay sends nothing to the first-run endpoints.

### Two deliberate departures from the issue's wording

- **Approval default.** The shipped default is `approval_mode = "bypass"`. A new install gets `ask` written
  explicitly; existing installs are not touched, and the summary shows the real value.
- **Memory «ask before saving».** There is no per-note approval. It is delivered as the learning loop being off
  until the owner turns it on, and the summary says so.

## Not decided here / limits

- «A usable model» is never claimed: nothing is checked without a request, and a local engine's server is not
  probed. The summary says «not verified».
- The avatar is not part of the first run (it is not connected to the runtime yet).
- The owner's name is kept and shown, but the assistant does not use it in conversations yet.
- Nothing here was run on a PC: checklist group O of Jarvis-di-dax issue 16 is NOT RUN.

## Block 11: first run hardening

Nothing was run: no test, no type-check, no build. The frontend `dist` was NOT rebuilt, so the UI changes are
NOT live. Python was only `py_compile`d and `ruff check`ed.

### Gaps found

1. **Stale applied mark after a reset.** `reset_onboarding` removed `first_run_applied_at`, but the real key is
   `first_run_applied`. After a reset the old digest stayed, and a replayed apply returned «already» and wrote nothing.
2. **No record of an interrupted apply.** The three config writes are each atomic, but a crash between them left no
   trace of which had landed.
3. **Silent state-write failures.** `_merge_state` only logs; `save_choices` and the final applied mark could be
   lost while the route answered ok.
4. **Reset was all-or-nothing and unexplained.** It only forgot the choices, did not clear the resume step, and told
   the owner nothing about what stays.
5. **No isolation check.** The first-run state and config paths were trusted; nothing verified they sat in this
   product's own directory (ADR-0049 `check_product_root`).
6. **A local engine was «ready» only because it needs no credential.** `authenticated` is True for every local
   engine, so a missing `ollama` still gave «Start».
7. **Summary lacked levels and permissions.** It could not tell configured / detected / reachable / verified apart,
   and did not list what was enabled and what was left off.

### Fixes

- `state._ONBOARDING_KEYS` now includes `first_run_applied` and `first_run_journal`; new `state.drop_state_keys`
  removes only named keys (atomic), keeping the terms acceptance.
- Step journal (`first_run_journal`): written before the first write and after each step, removed after the applied
  mark was read back. `first_run.journal()` reports `done` and `resume` (first planned step not done); a re-run
  continues from it. Correctness does not depend on it: the writes stay idempotent and only the applied mark means
  «done».
- `save_choices` and `apply` read back what they wrote and raise `FirstRunError("save"|"mark")` otherwise. The
  routes answer 500 (not saved) instead of ok.
- `check_isolation`: for a product launch (`JARVIS_PRODUCT` set) the state file's folder must pass
  `product_profile.check_product_root`, carry this product's `.product` marker, the config file must sit in it, and
  the run's product must equal the process's. Checked before any write; a failure is `IsolationError` (HTTP 409).
  The unscoped app and tests are not checked.
- Reset: `reset_plan` (reads only) and `reset_choices(include=...)` remove only this product's first-run state
  (choices, applied mark, journal, resume step). Optional, only when ticked: `personality_config` (back to the
  product default), `learning_switch` (memory learning off). Never offered: credentials, memory content, terms,
  the other product; the approval mode is not reverted. Routes: `GET /first-run/reset-plan`, `POST /first-run/reset`
  with `{include: [...]}`; the answer carries the plan that was carried out.
- Engine levels `configured / detected / reachable / verified` in `engine_status`. `detected` for a local engine is
  `shutil.which` of its program (`ollama`); a generic local server (`local-openai`) is «cannot be told» and is NOT
  ready. `reachable` is always `not_checked` and `verified` always `not_run`. Work still refuses every non-local
  provider (`work_local_only`).
- `components()` reads whether the speech package is installed (`find_spec`, no import, no model, no device).
- `permissions()` lists microphone, screen, files, network, tools, memory learning, each OFF unless the owner chose
  it (microphone: «asked at first use»; tools: «asks first»; memory: the real configuration). The summary shows
  «Turned on» and «Left off» lists.
- Strings: new chunk `first_run_hardening` (en/de/es/pt/zh; de/es/pt/zh are unreviewed drafts, marked by
  `review_status`), registered in `i18n/index.ts`. No other namespace was edited.

### Residual limits

- A damaged `setup_state.json` is still read as empty and overwritten by the next write (terms acceptance and all
  flags are lost, the owner sees the guide again). No quarantine copy, no cross-process lock.
- The three config writes are not one transaction: after a crash the config can hold the personality but not yet
  the approval mode. The journal names it and the re-run finishes it; until then the product default applies.
- `reachable` and `verified` are never produced by the first run; a local server being down is only found at the
  first message. A generic local server shows «not ready» even if it runs.
- The reset UI control is NOT built (only the API and the strings); the summary does not yet offer «resume»
  as a button, it only states the unfinished steps.
- The `ollama` probe is a PATH lookup: a binary outside PATH reads as not found.
- Isolation is enforced only when `JARVIS_PRODUCT` is set; the installed-app flow outside a product launch is
  unchanged.

### Controls prepared, NOT RUN

- `tests/unit/setup/test_first_run_hardening.py` (written, never executed): stale-mark regression, journal after an
  interrupted apply and re-run, lost mark / lost save detected, reset keeps terms and config, reset options,
  isolation against the other product's folder and a stray config, Work refuses cloud, local engine levels,
  permissions all OFF, voice component probe.
- Still to do on a PC: kill the app between the apply steps and relaunch; run Work and Consumer side by side and
  reset one; confirm the summary text in all five languages; type-check and rebuild the frontend.
