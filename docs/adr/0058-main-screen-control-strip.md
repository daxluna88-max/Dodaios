# ADR-0058 — Main screen: a control strip that makes the assistant understandable and stoppable

**Status:** Proposed; implemented locally in `claude/p4-product-ports` (code and tests WRITTEN, not run; `dist` not rebuilt, so nothing is live) · **Date:** 2026-10-27

## Context

Issue 18 left item O11 open: after the guided first run (ADR-0057) the main screen does not guarantee that the owner
can (1) stop everything, (2) see what the assistant is doing, (3) see which engine is active and what it receives (and what it does not prove),
(4) reach and correct what it remembers, (5) see jobs and decisions waiting. The pieces exist, but scattered:

| Need | What exists today (read in code, not run) | Gap |
|---|---|---|
| Stop | `harness.cu_run_registry.cancel_all_runs`, `harness.manager.cancel_all`, `BrainManager._cancel_all_background_tasks`, `POST /api/missions/{id}/cancel`, `HoldToAbortButton` (per mission card) | no single action; the hold button is per mission; nothing stops voice playback and jobs together |
| State | orb states `idle, listening, thinking, speaking, error, paused` (tray), mission statuses | no "waiting for approval" / "working" in the orb; not shown on the main screen |
| Engine | `BrainManager.active_provider`, `engine_policy.current_product()`, `/api/network-policy`, product engine policy (ADR-0049/0053) | the configured default is shown, the engine that actually answered is not; what is sent is not stated |
| Memory | wiki/memory-vault routes (`/api/wiki/*`, memory vault pages: read, put, rename, archive), `[memory.learning]` | no entry point from the main screen; no per-note approval; the vault offers archive, not deletion |
| Jobs and approvals | `/api/missions`, `/api/missions/{id}/tool-approvals` (+ `deny`) | no combined list of what runs and what waits |

## Decision

Add one **control strip** to the main screen of both products, always rendered, never hidden behind a menu, built only
from existing routes plus two thin aggregating endpoints. It is read-mostly; the only write actions are STOP, approve /
deny and memory correct / archive, each already an existing owner action.

### 1. STOP (always visible)

- A fixed button on the strip, reachable by keyboard (a documented shortcut, first in the focus order) and, where the
  voice path has a stop word, by that word (to be checked in the code; not verified). One click, **no hold and no
  confirmation**: stopping must be faster than the harm. (The hold-to-abort of mission cards stays for per-mission
  cancel, where friction is wanted.)
- `POST /api/stop` (new, one handler) works in two phases:
  1. **Close the door, synchronously and before waiting for anything.** Increment the **generation** that `PlanGate`
     (ADR-0056) and the voice use, so a late reply, audio or expression from before STOP is dropped; refuse new
     dispatches, new turns and new approvals; deny every pending approval; set the strip state to *Stopping*. An
     approval or a new turn that races with STOP finds the door closed and cannot restart work.
  2. **Independent shutdowns, each with its own timeout and its own result:** voice playback and the pending turn;
     `cancel_all_runs`; running missions; background agent tasks. A subsystem that hangs or fails does not delay or
     block the others; its timeout is reported, not hidden.
- **Idempotent:** a second STOP while stopping, or after it, returns the current outcome and starts nothing twice.
  Doing something new needs an explicit owner action, which reopens the door; STOP itself never turns anything on.
- **Four outcomes per subsystem, never collapsed into one:** `requested` (the request was sent), `confirmed` (the
  subsystem reports it stopped), `still_active` (it did not stop in time) and `unknown` (no answer, no telemetry).
  The strip shows **Stopped** only when everything reported `confirmed`; otherwise it shows **Stopping** or
  **Partly stopped**, naming what is still active (a worker, audio). The initial idle state and an explicit STOP are
  different states with different text.
- STOP promises to stop work in progress, **not to undo effects already done** (a sent email, a written file); the
  screen says so.
- STOP does not depend on the engine, the mood, the personality, the budget or the network policy. It works with no
  engine connected and offline.

### 2. State

One **summary line** (one word, one icon, one text) chosen by priority, plus **always-visible indicators** that
do not disappear when the summary changes.

- Summary, exactly one of: **Error** (with the reason) > **Waiting for your decision** > **Working** > **Speaking** >
  **Thinking** > **Listening** > **Idle** (nothing observed running) / **Stopped** (after an explicit STOP, fully
  confirmed) / **Stopping / Partly stopped** / **Unknown**.
- The summary never hides concurrent activity: separate indicators for **audio**, **jobs (count)** and **approvals
  (count)** stay visible even when an error wins the summary line.
- Missing, disconnected or stale telemetry is **Unknown**, never Idle or Stopped. "Ready" is never shown for a state
  that was not observed.
- Derived by one pure function from the orb state, the missions list, the pending approvals and the STOP outcomes. The
  text is always shown next to the icon (colour is never the only signal).

### 3. Active engine and what is sent

- Shows the engine of the **current/last turn**, not the configured default, with a badge **Local inference** or
  **Remote** and, for Remote, the provider name.
- **No sentence promises that nothing leaves the computer.** A local engine proves where the inference ran, not that
  tools, synthesis, embeddings or child processes use no network. The strip shows two separate lines:
  1. *Inference:* `local` / `remote (provider)` / `remote blocked by policy`.
  2. *Network and tools:* what the turn actually used (tools called, categories and destinations of anything sent),
     or `not observed`. "Fully offline" is shown only where strict offline is enforced and verified at OS level
     (ADR-0053: the Python guard alone does not prove it); otherwise the line says what is and is not enforced.
- For a remote engine, the line lists **categories and destinations**, not a generic "context shown in Settings", and
  separates **planned** (what the privacy gate would send) from **actually sent** (what the turn recorded). Content
  is never written to logs or to the screen's telemetry; only categories, counts and destinations.
- Work shows local inference only. A remote engine there is shown as **blocked**, never as a fallback.
- A multi-engine answer (ADR-0055) shows the engines asked for that turn and the synthesizer.

### 4. Memory

- A **Memory** button opens a panel listing what is remembered, newest first, with the source (which conversation or
  note), a **Correct** action (edit) and an **Archive** action (the existing vault behaviour: the page is moved
  aside and can be restored). Global switch for learning (`[memory.learning]`, off by default per ADR-0057), shown
  with its state.
- **Archive is not deletion and the screen never calls it deletion.** A real **Delete** is added only after this is
  specified and agreed: which stores it covers (pages, search and embedding indexes, caches, backups, exports), what
  stays behind (backups until they rotate), whether there is an undo window, and the difference between a *logical*
  deletion (hidden, recoverable) and a *permanent removal*. Until then the panel offers Archive and says what stays.
- Honest limit: there is **no per-note approval flow yet** (a proposed memory waiting for approve/reject). The panel
  shows existing memory only; the screen says "automatic memory is off" while off.
- Per product: Work and Consumer never show each other's memory.

### 5. Jobs and approvals

- A list of what runs (mission, tool, started, engine) with a per-job cancel, and a list of **decisions waiting**
  (what, which tool, with which arguments in short, from which job) with Approve / Deny. Approvals show before
  anything else on the strip when pending, and the page title and the tray carry the count.
- Approval decisions use the existing mission tool-approval routes; nothing here widens what a job may do. The strip
  cannot approve in bulk and cannot "always allow". After a STOP, an approval that was pending is denied, and a late
  Approve for it is refused.

### Aggregating endpoints

`GET /api/control-strip` (read-only) returns `{state, indicators, engine: {inference, network_tools, planned, sent},
jobs: [...], approvals: [...], memory: {learning, count}}` from existing sources, assembled in one place so the strip
polls (or subscribes) once. `POST /api/stop` as above. Both are `async` only if they `await`.

**Security.** Both endpoints are registered explicitly in the existing owner / authentication / Origin / CSRF controls
(the exact registration in `surface_security.py` and the control-key checks is to be done when building; not verified
here), reachable by the authenticated owner session only. They grant no extra access to tools, to other products or to
other installations, and a request without the owner's authentication is refused, including STOP.

### Frontend

A single `ControlStrip` component in the main layout (above the content, not a modal), following the existing
design-system components: no native `<select>`, keyboard order STOP → state → engine → approvals → jobs → memory,
visible focus, `aria-live="polite"` for state changes and `assertive` only for approval requests and errors, text
in the 5 interface languages (no raw keys), reduced-motion respected. Italian is not an interface language today.

## Not decided here / limits

- Nothing is implemented. This is structure and behaviour only; the build, tests and the real check on a PC come on
  Dax's instruction (issue 16, new group).
- Whether the mission worker processes (separate processes) obey `POST /api/stop` immediately is **not verified**;
  `kill/{worker}` is described in `missions_routes.py` as a stub. `not_stopped` exists for this reason.
- Voice playback stop and turn cancellation across the realtime path (a model that answers without delegating) are
  not verified.
- Review by GPT on 2026-10-27 (issue 16): the six findings it raised on the first draft (STOP ordering, outcomes,
  simultaneous activity, privacy wording, archive vs delete, endpoint security) are reflected above. Controls S01–S12.
- The remote-access case (phone) is out of scope: STOP from the phone is checked separately (issue 16, F03/F05).
- Budget display and cost are not part of the strip.

## Implementation notes (2026-10-27, local)

- **Existing emergency stop.** `dodaios/control/cancel.py` already has `KillSwitch` / `KillRequested` (tray, hotkey,
  voice). It only reaches holders that registered a `CancelToken` (the computer-use loop); a search of `speech/`,
  `audio/` and `realtime/` found no subscriber, so playback and realtime have no verified stop. `POST /api/stop` trips
  the same switch (`kill_switch_holders`) and adds the parts that were missing.
- **Scope.** One `StopController` per product process (`get_stop_controller()`): STOP reaches every session of that
  product and nothing of the other product or of another installation. Session overrides (mood, style nudges) are
  neither read nor reset. This is different from data isolation, which is a separate concern.
- **Door and resume.** `stop()` bumps the generation and closes the door before any await; new turns, mission
  dispatch / rerun, tool approvals and capacity decisions then answer `409 stopped`. A turn queued before STOP carries
  its `stop_ticket` (the generation at enqueue); after `POST /api/stop/resume` (a NEW generation) it is still refused
  as `stale_generation`. A typed or spoken stop phrase still passes the door. The queued voice path does not pass a
  ticket yet (limit).
- **Outcomes.** Parts: `kill_switch_holders`, `computer_use_runs`, `background_agents`, `pending_approvals`,
  `missions`, `voice_playback`, `realtime_session`. The last two have no probe, so they are always `unknown` and the
  summary is `partly_stopped`: "Stopped" cannot be shown today whenever voice or realtime exist. Missions count as
  stopped only if the cancel path reports the worker killed; a state flip alone leaves them `unknown`.
- **State.** `strip_state.derive` (pure). The voice state comes from `/api/voice/state`; where it cannot be read the
  summary is `unknown`, never `idle`.
- **Engine.** `TurnLedger` is filled by the multi-engine path (from the orchestrator's own egress metadata) and by
  ordinary turns (main engine only). Locality is the endpoint's (loopback only). Tools and network use are not
  observed: `network_tools` stays `not_observed`. "Planned" and "attempted" come from the app's records, not from the
  wire.
- **Memory.** The strip links to the existing Memory view (view, correct, archive). No deletion and no per-note
  approval were added.
- **Not done.** Per-session ticket in the voice pipeline; verified stop of playback / realtime / worker processes;
  a spoken "stop" does not close the door (the existing background-task cancel still answers it); the new routes'
  Origin/CSRF behaviour is covered by the existing middleware by default and by a written test, not by a run.
