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

### Corrections after the second review (2026-10-27, local, not run)

- **Races between STOP, resume and starting work.** `resume` is refused (`409 stopping`) while a STOP is still
  shutting parts down, so new work can never be started only to be cancelled by the shutdown still running.
  Gates that must move with the door (today: the mission tool-approval coordinator) register as *door listeners* and
  are flipped in the same synchronous step; the coordinator then refuses approvals and denies requests that arrive
  while closed (`stopped_by_owner`). `dispatch` and `rerun` take a ticket before creating the mission and re-check it
  afterwards: a mission created while STOP landed is cancelled and the call answers `409`.
- **Late replies of turns already started.** Every turn carries the STOP generation it started under (a context
  variable). If STOP, or STOP and a resume, happened since, its response event, streamed text and history entry are
  dropped and `generate` returns an empty reply. The refusal reply and a stop phrase are exempt. Not covered: tools a
  stale turn already invoked in the brain's own dispatcher (only background agents, computer-use runs and missions are
  cancelled); concurrent turns share `_last_turn_all_failed`.
- **Stale state after a lost connection.** The screen treats a failed read, no read, or one older than 9 s as stale:
  the summary shows *Unknown* with the time of the last reading, indicators and decisions are not shown as current,
  Approve / Deny / Resume are not offered from stale data. STOP stays available.
- **Worker stop is confirmed from the operating system.** Job objects register the pid of every worker they take; the
  missions part is `confirmed` only if no mission is non-terminal, no run task is in flight and none of the tracked
  pids is alive (zombies and reused pids do not count). Without `psutil` the answer is `unknown`. A cancel request
  (`worker_killed`) is no longer treated as a confirmation. Limit: a process never assigned to a job object is not
  tracked.
- **Effective engine.** The turn record starts `running` with the engine chosen, then is finished with the engine of
  the last attempt: a fallback is flagged (`fell_back`, `requested`), a turn where nothing answered is `failed`, and a
  turn answered by a fast path shows no engine. It is per turn (carried in the turn state), but the engine identity is
  still read from the manager's per-instance attribute set during the attempt.

### Tickets at creation, re-verified before every side effect (2026-10-27, local, blocks 2 and 3)

Rule: a unit of work takes the STOP generation (`ticket()`) when it is **created** (queued, scheduled,
dispatched), and re-checks it (`admit(ticket)`) at start and right before each side effect. After STOP, and after
STOP and a resume, an old ticket is refused. Where a ticket cannot travel in a signature (fakes, many layers), the
creator sets the context variable `control.stop.work_ticket` (the only new shared primitive, backwards compatible) and
the code below re-verifies it.

- **STOP / repeated STOP.** A repeated STOP re-probes the parts the previous STOP had confirmed and asks again any
  that no longer confirm (work that slipped past the door is not forgotten). Unconfirmed parts are asked again by
  every later STOP, also across stop, resume, stop (new generation). Tests: `tests/unit/control/test_stop_races.py`.
- **Tool approvals.** `ApprovalWorkflow.arm()` takes the ticket; `wait()` / `peek()` turn an approval into a denial
  (`stopped_by_owner`) if STOP, or STOP and a resume, came after the request was armed. One choke point for the UI,
  the coordinator and both auto-approvers.
- **Brain.** `ToolUseLoop` re-verifies before every model round and every tool call (the tools a stale turn had
  already asked for no longer run). `_last_turn_all_failed` is per turn (context variable); the value of the last
  finished turn stays readable for callers that read it after `generate()`. `run_task` takes `stop_ticket` / reads
  `work_ticket`, does not start on a stale one and drops a reply that finished after STOP.
- **Chat.** `AgentChatService.send` takes the ticket on entry and passes it to the brain turn; a message queued in
  `send_queue` carries its ticket and is dropped (failed notice) if STOP came after it was queued, not sent after a
  resume. Parked background results (`parked_work`) and the fleet-briefing task are re-verified the same way.
- **Missions.** `run_mission` / `resume_mission` take or are given a ticket, refuse a stale one (mission CANCELLED) and
  re-check before every worker spawn; the dispatch / rerun routes re-check when the background start begins;
  `resume_waiting_missions` does nothing while the door is closed. `spawn_worker` (voice) takes its ticket when the
  background dispatch is created; a mission persisted just before STOP is cancelled, not run.
- **Scheduled tasks.** The scheduler takes the ticket when it creates the run (also manual run and hook delivery);
  the runner's existing cancel points re-verify it. A hook delivery overtaken by STOP is `interrupted` (never
  replayed, never "done"); a recurring routine overtaken mid-run goes back to `scheduled`.

Tests (written first, failing before the fix): `tests/unit/control/test_stop_races.py`,
`tests/unit/safety/test_approval_stop_ticket.py`, `tests/unit/brain/test_tool_use_loop_stop.py`,
`test_turn_all_failed_per_turn.py`, `test_run_task_stop.py`, `tests/unit/agent_chat/test_chat_stop_ticket.py`,
`test_background_stop_ticket.py`, `tests/missions/test_mission_stop_ticket.py`,
`tests/missions/api/test_missions_routes_stop.py`, `tests/unit/plugins/tool/test_spawn_worker_stop.py`,
`tests/unit/tasks/test_tasks_stop_ticket.py`.

Still NOT verified or NOT done (all tests use fakes; nothing ran against a real provider, real worker process, the
real app or Windows):
- real worker processes after STOP (the OS-level confirmation is unchanged and only simulated here); no real
  mission, computer-use run or coding CLI was stopped end to end;
- `_last_turn_all_failed` is still read after `generate()` by the voice pipeline and the realtime session (not
  touched, block 4): two voice turns that finish at the same moment can still see each other's value there;
  `_last_turn_suppressed` and `_last_turn_executed_action_tool` are still shared per manager;
- turns of coding CLIs / ACP runners in the agent chat have no ticket check inside the runner (only the brain
  runner passes it); the voice pipeline, realtime, TTS, avatar and streamed output were not touched;
- paths that start work without going through these doors (other `asyncio.create_task` background jobs not listed
  above, phone companion STOP/approval) are not covered; the re-probe in a repeated STOP is what catches them;
- a spoken "stop" still does not close the door.

### STOP reaches the voice output paths (2026-10-27, local, block 4)

- **Per-turn ticket in the voice pipeline** (`speech/stop_gate.py`). `_handle_utterance` takes the STOP generation when
  the turn is created and keeps it in a context variable, so look-ahead synthesis, playback and the completion-buffer
  / continuation timers scheduled from inside the turn inherit it. It is checked before: a streamed sentence is
  scrubbed and queued, each synthesized chunk, each chunk handed to the player, the on-screen reply delta, `_speak`,
  and the `SpeechSpoken` event (`_emit_spoken`). The brain stream is wrapped in `guard_stream` (the existing
  engine_policy pattern), so a late delta of a stopped or resumed generation ends the stream and closes the source.
  A turn created while the door is closed is exempt (only the refusal and a stop phrase can come out of it).
- **Spoken stop phrase.** Before the brain is called, a stop phrase (the kill-intent regex or the voice-command gate's
  `cancel`) starts `StopController.stop()`: the door closes before the turn goes on, then the parts run. The voice
  parts are registered by the pipeline itself, so this works even if the strip was never opened; the other parts are
  those `_install` has registered (strip opened at least once).
- **Probes** (`speech/stop_parts.py`). `voice_playback`: request = `AudioPlayer.stop()`; probe = new
  `AudioPlayer.playback_in_flight` (open output stream or a `play_chunks` call holding the play lock). A player
  without it, or no pipeline, stays `unknown`. `realtime_session`: request = the existing voice hangup, only while a
  duplex handle exists; probe = that handle (cleared after the lifecycle unwound), no pipeline = `unknown`.
- **Realtime audio.** `DesktopRealtimePlayback` takes a `stale` predicate (the session's ticket) and drops provider
  PCM after STOP / resume.
- **Multi-engine / final answer / expressions.** The turn-level drop in `BrainManager.generate` (response event,
  history, returned text) already covers the multi-engine synthesis result. The avatar is not wired to the runtime
  (ADR-0056), so there is no expression emit to guard; `ExpressionGate.cancel(generation)` is unchanged.
- **Not verified (simulated only).** All tests use fakes: no real audio device, TTS provider, realtime provider or
  browser call was run. `confirmed` means this process holds no output stream / no session handle, not that sound
  stopped at the speaker or that the provider closed its side. A browser-held call (`run_browser_call`) and the
  `DesktopRealtimeBargeInDetector` are not ticketed. Realtime relies on the hangup to end the session; only provider
  PCM delivered through `DesktopRealtimePlayback` is gated. `control/wiring.wire_voice_kill_switch` is not wired in
  the app and was not changed. No lip sync or voice modulation exists or is claimed.

## Block 9: turn record (2026-10-27, local, not run)

The turn record now says what actually ran, and who it belongs to. Full event table, sources of truth and gaps:
`docs/turn-record-audit-2026-10-27.md`.

- **Identity.** A record is `(turn id, STOP generation, run id)`. `TurnLedger.begin` opens it when the turn passes
  the STOP door (before any fast path); every later write (`attempt`, `apply`, `update`, `finish`) carries that key
  and is applied only while it is still the current one (compare-and-set). A late writer of an older turn, or of an
  older STOP generation, changes nothing. The multi-engine path writes with the same key, so an old answer finishing
  late cannot replace the new turn's record.
- **Engine and model at dispatch.** `BrainManager._note_dispatch(provider, model, requested)` is called with the chain
  entry about to be called (the TurnOverride's pair or the fallback attempt's). The manager's `active_provider` is no
  longer read. A fallback replaces the engine and keeps the failed one in `earlier_attempts`. A turn answered by a
  fast path has no engine.
- **States.** `phase`: planned, attempted, completed, failed, superseded (STOP overtook the turn). `state` stays for
  older readers and agrees with it (`superseded` is new).
- **Planned is partial.** `planned_partial` is true whenever a model ran: `planned` lists the question to a remote
  engine, not history, memory, files or tool output. The panel says so.
- **Not done.** Persistence and history; failure reasons; `resolve_engine` source and `FallbackRouter` attempts are
  not wired to the record; `run_task` and non-`_generate` voice paths record nothing; `tools_used` / `network_tools`
  still not observed. Tests were written, not run: `tests/unit/control/test_turn_record_identity.py`,
  `tests/unit/brain/test_turn_record_dispatch.py`.
