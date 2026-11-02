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
