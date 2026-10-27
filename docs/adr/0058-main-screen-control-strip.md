# ADR-0058 — Main screen: a control strip that makes the assistant understandable and stoppable

**Status:** Proposed (design only: nothing implemented, nothing tested) · **Date:** 2026-10-27

## Context

Issue 18 left item O11 open: after the guided first run (ADR-0057) the main screen does not guarantee that the owner
can (1) stop everything, (2) see what the assistant is doing, (3) see which engine is active and what it receives,
(4) reach and correct what it remembers, (5) see jobs and decisions waiting. The pieces exist, but scattered:

| Need | What exists today (read in code, not run) | Gap |
|---|---|---|
| Stop | `harness.cu_run_registry.cancel_all_runs`, `harness.manager.cancel_all`, `BrainManager._cancel_all_background_tasks`, `POST /api/missions/{id}/cancel`, `HoldToAbortButton` (per mission card) | no single action; the hold button is per mission; nothing stops voice playback and jobs together |
| State | orb states `idle, listening, thinking, speaking, error, paused` (tray), mission statuses | no "waiting for approval" / "working" in the orb; not shown on the main screen |
| Engine | `BrainManager.active_provider`, `engine_policy.current_product()`, `/api/network-policy`, product engine policy (ADR-0049/0053) | the configured default is shown, the engine that actually answered is not; what is sent is not stated |
| Memory | wiki/memory-vault routes (`/api/wiki/*`, memory vault pages: read, put, rename, archive), `[memory.learning]` | no entry point from the main screen; no per-note approval; "forget" is archive, not delete |
| Jobs and approvals | `/api/missions`, `/api/missions/{id}/tool-approvals` (+ `deny`) | no combined list of what runs and what waits |

## Decision

Add one **control strip** to the main screen of both products, always rendered, never hidden behind a menu, built only
from existing routes plus two thin aggregating endpoints. It is read-mostly; the only write actions are STOP, approve /
deny and memory correct / forget, each already an existing owner action.

### 1. STOP (always visible)

- A fixed button on the strip, reachable by keyboard (a documented shortcut, first in the focus order) and, where the voice path
  has a stop word, by that word (to be checked in the code; not verified). One click, **no hold and no confirmation**: stopping must be faster than the harm. (The
  hold-to-abort of mission cards stays for per-mission cancel, where friction is wanted.)
- `POST /api/stop` (new, one handler) does, in order, and reports each result: stop voice playback and the pending
  turn; `cancel_all_runs`; cancel running missions; cancel background agent tasks; mark every pending approval denied.
  It increments the **generation** that `PlanGate` (ADR-0056) and the voice use, so a late reply, audio or expression
  from before STOP is dropped.
- The answer is honest: `{stopped: [...], not_stopped: [...]}`. A job that cannot be interrupted is listed, not hidden.
  The strip then shows **Stopped** until the owner starts something; STOP never turns anything on again.
- STOP does not depend on the engine, the mood, the personality, the budget or the network policy. It works with no
  engine connected and offline.

### 2. State (one word, one icon, one text)

Exactly one of: **Stopped** (idle), **Listening**, **Thinking**, **Working** (a job/tool runs), **Waiting for your
decision** (an approval is pending), **Speaking**, **Error** (with the reason). Derived by one pure function from the
orb state, the missions list and the pending approvals, in this priority: Error > Waiting > Working > Speaking >
Thinking > Listening > Stopped. The text is always shown next to the icon (colour is never the only signal). It
never says "ready" for a state it did not observe.

### 3. Active engine and what is sent

- Shows the engine of the **current/last turn**, not the configured default, with a badge **Local** or **Remote**
  and, for Remote, the provider name.
- A "what is sent" line, from facts only: `local, nothing leaves this computer` / `remote: your message and the
  context shown in Settings` / `remote disabled by policy (offline mode)`. The text is a fixed enumeration; it does
  not claim more than the privacy gate enforces (ADR-0050, ADR-0053: the Python guard alone does not prove offline).
- Work shows Local only. A remote engine there is shown as **blocked**, never as a fallback.
- A multi-engine answer (ADR-0055) shows the engines that were asked for that turn.

### 4. Memory

- A **Memory** button opens a panel listing what is remembered, newest first, with the source (which conversation or
  note), a **Correct** action (edit) and a **Forget** action (delete, with an undo window; archive remains available
  for the vault). Global switch for learning (`[memory.learning]`, off by default per ADR-0057), shown with its state.
- Honest limit: there is **no per-note approval flow yet** (a proposed memory waiting for approve/reject). The panel
  shows existing memory only; the approval queue is a follow-up and the screen says "automatic memory is off" while off.
- Per product: Work and Consumer never show each other's memory.

### 5. Jobs and approvals

- A list of what runs (mission, tool, started, engine) with a per-job cancel, and a list of **decisions waiting**
  (what, which tool, with which arguments in short, from which job) with Approve / Deny. Approvals show before
  anything else on the strip when pending, and the page title and the tray carry the count.
- Approval decisions use the existing mission tool-approval routes; nothing here widens what a job may do. The strip
  cannot approve in bulk and cannot "always allow".

### Aggregating endpoints (new, read-only unless noted)

`GET /api/control-strip` returns `{state, engine: {name, locality, sends}, jobs: [...], approvals: [...],
memory: {learning, count}}` from existing sources, assembled in one place so the strip polls (or subscribes) once.
`POST /api/stop` as above. Both are `async` only if they `await`; both are limited to the authenticated owner session (the
exact registration in `surface_security.py` is to be checked when building; not verified).

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
- The remote-access case (phone) is out of scope: STOP from the phone is checked separately (issue 16, F03/F05).
- Budget display and cost are not part of the strip.
