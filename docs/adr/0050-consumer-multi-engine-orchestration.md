# ADR-0050 — Consumer multi-engine orchestration: privacy gate, one budget, bounded branches

**Status:** Accepted (library only; not wired into a user flow) · **Date:** 2026-10-26

## Context

The Consumer owner speaks only to Jarvis, which may use several explicitly connected engines at once (a subscription
integration, an API model, a local model). A remote model necessarily receives what is sent to it, so the local
privacy decision and the spend ceiling must sit in front of every call, for the whole fan-out and not per branch.

## Decision

`dodaios/brain/consumer_orchestrator.py`, no I/O of its own (callers pass brain objects):

1. `PrivacyGate.prepare` builds each remote branch prompt from the minimum context: only allowed categories, never
   `local_only` items, identifiers masked by a pluggable redactor (default: emails, key-shaped tokens, long digit
   runs: a floor, not anonymization). A local branch keeps its context. What left is logged as destination,
   categories and counts (`EgressRecord`), never as a second copy of the prompt.
2. `Budget.reserve` is one atomic ceiling for the whole fan-out. An unknown price is refused unless the caller
   opts in, and is never counted as zero.
3. `Branch.kind` is `subscription | api | local`. A branch that needs API access is refused on a subscription
   connection; an unauthenticated branch is refused; nothing falls back to another paid path.
4. `Orchestrator.run`: bounded concurrency, per-branch timeout, partial failure reported per branch,
   `first_success` cancels the losers, results of a cancelled or superseded turn are dropped (`stale`), branches run
   without tools and any tool call they emit is ignored.
5. Under `JARVIS_PRODUCT=work` every non-local branch is refused (ADR-0049).

## Not decided here

Choosing branches, the final synthesis call (it must pass the same gate), provider retention terms, real prices and
subscription entitlements (verify official integrations per provider), wiring into chat or voice, any UI, and
playback ownership (no overlapping spoken answers) for voice.

## Consequences

Safe to build the Consumer flow on top; nothing user-visible changes until a caller uses it.

## Route

`POST /api/consumer/fanout` (`ui/web/consumer_fanout_routes.py`) is the first caller: the request names the branches,
one ceiling for the whole fan-out and, per item of context, `local_only` and a category. Branch kinds come from the
provider (`ollama` / `local-openai` local; `codex` / `claude-cli` / `antigravity` subscription; the rest API), and
"authenticated" is read without contacting the provider (stored key, or the plan-login probe). A provider that is not
installed, not buildable, not authenticated, over budget or without a known price is reported per branch as `refused`;
nothing is charged for it. It returns each branch's answer, the measured report and the egress metadata, never a final
synthesized answer. Under the Work product it answers HTTP 403. `GET /api/consumer/status` lists the engines with kind and authentication
(no provider is contacted). Settings > General > "Ask several engines" is the first screen over both: the owner types a
question, ticks connected engines, optionally estimates each cost and sets one ceiling; an engine without an estimate
is skipped unless the owner allows it; only the typed question leaves this computer. Hidden under Work.

## Block 7: provenance

Static re-examination of the gate at five doors. Nothing was run (no tests, no app, no services); the new tests in
`tests/unit/brain/test_block7_provenance.py` are written, not executed.

Gaps found and what changed:

1. **Derivations could be relabelled as plain.** `ContextItem` only had `category` and `local_only`; a summary or quote
   of a "file" item built as a new `task` item passed the gate. `ContextItem.derived_from` plus
   `ContextItem.derive(sources, text, category)` now carry `local_only` (any source) and every source category; the gate
   and the `tainted` rule use `restricted_for`, so a derivation is as restricted as its sources.
2. **Synthesis dropped the label.** `SynthesisResult` had no `tainted`: a local synthesis over restricted answers came
   back as plain text. It now has `tainted` (restricted answers or task items, or the branch's own taint), and
   `MultiEngineOutcome.tainted` carries it to the caller.
3. **Final answer spoken or remembered.** New `dodaios/brain/provenance.py`: a bounded, 1 h, best-effort registry of
   restricted answer text. `multi_engine_chat.ask` marks a tainted answer; `MeteredTTS.synthesize` (the one wrapper around
   the pipeline's TTS) skips a remote TTS for restricted text (text stays on screen; fallback pairs count as remote
   unless both legs are local); `BrainManager._record_response_side_effects` does not hand a restricted answer to the
   curator (no memory note derived from it).
4. **Memory in a remote request.** `BrainManager.generate` attached `recall_block` to the shared turn context for every
   engine in the chain. `recall_block(..., remote=True)` is now used when any engine in the chain is not provably
   loopback (fails closed): only notes a provider labels via the optional `recall_notes` (`MemoryNote.local_only`) are
   returned; a provider with only `recall` (unlabeled) contributes nothing to a remote chain. Local use is unchanged.

Fan-out branches and the route already withheld `local_only`/unlisted categories; that is unchanged. Branches have no
tools, so tool output enters a fan-out only as a `ContextItem` and an unlisted category (e.g. `tool_output`) is withheld.

NOT covered:

* Tool outputs in the normal chat tool loop (`tool_use_loop`): results of tools that read private files, mail or screens
  carry no label, and a fallback to a remote engine after a local one ran such a tool replays them. Needs a label on
  `BrainMessage` and a check at fallback; left alone because it overlaps the fallback-replay work.
* The wiki injector and `_upcoming_block`/screen/pointer context in the same turn context are unlabeled and still go to
  remote engines; only the turn-memory block is gated.
* The registry matches normalised text: a paraphrase, a model rewrite or text altered before TTS (number spelling) is not
  recognised. Realtime/native voice connections that speak by themselves, and `live/native.py` `set_turn_context`
  (local engine only), are not gated. Existing memory providers must implement `recall_notes` to reach remote engines.
* Embeddings: no remote embedding call was found in `dodaios/brain` or the speech path; not exhaustively searched.
* Durable storage of the label (it is in-process and lost on restart); labels on stored history/curator output.

## Block 8: review fixes R6

Review finding (high): text matching is not a privacy label. The registry of block 7 ignores text under 6 characters,
expires after 1 h, evicts after 256 entries, vanishes on restart and fails after any transformation, so a short
restricted answer ("42") reached a remote TTS. Written, not run: no test, build or service was executed in this block.

What changed:

1. **Typed label.** `dodaios/brain/provenance.py` has `Provenance` (`unrestricted` | `restricted` | `unavailable`),
   `LabelledText` (a `str` that carries the label) and a per-turn channel (`begin_turn`, `set_turn_provenance`,
   `turn_provenance`) that does not read the text at all. The label only gets more restrictive within a turn.
   `remote_speech_blocked(text)` fails closed: restricted AND unavailable both keep text off a remote synthesizer; a
   local one is unaffected. The text-match registry stays only as an extra signal.
2. **Carried through the turn.** `MultiEngineOutcome.provenance` (default `unavailable`; explicit `restricted` /
   `unrestricted` only where known) and `SynthesisResult.provenance`. `BrainManager._run_multi_engine_turn` sets
   `unavailable` before the engines run (an exception leaves it so), then replaces it with the outcome's label and
   returns a `LabelledText`. `_generate_untagged` calls `begin_turn()` first.
3. **Doors.** `MeteredTTS.synthesize` uses `remote_speech_blocked` (also for 1-5 character text and rewritten text).
   `_record_response_side_effects` skips the curator for a restricted/unavailable response and marks the history pair
   `BrainMessage.restricted`; the per-engine dispatch uses `_history_for_engine`, which drops restricted messages for any
   engine not provably local (unknown = remote).

Remaining gaps (NOT closed):

* Normal-chat tool output (`tool_use_loop`) carries no label, nor does the wiki injector, screen context, pointer
  context or `_upcoming_block`; those still reach remote engines. Their material is labelled `unrestricted` by default
  because nobody labels it, which is a gap, not a proof.
* The turn channel is process-wide and resets at the next turn: speech of a restricted answer still being synthesized
  when the next turn begins is covered only by the text registry. Concurrent sessions share it (conservative).
* Native/realtime voice connections that speak by themselves, `live/native.py` `set_turn_context`, and other speech
  that does not pass `MeteredTTS` are not gated. Ack/phrase TTS during a restricted turn is also suppressed for remote.
* `turn_memory.py` is unchanged: `MemoryNote.local_only` still decides, and a provider without `recall_notes` still
  contributes nothing to a remote chain; notes are not yet typed `Provenance`.
* The label is in process memory: it is lost on restart and not stored with history on disk.

## Block 9: GPT round 2 (F1, F2, F4)

Static review fix, F1 only here (F2/F4 are in ADR-0053). Written, NOT run: no test, build, CI or service was executed.

**F1 (R6, fail-open for unlabelled text): fixed in code, with residual limits (partial for coverage).**
Files: `dodaios/brain/provenance.py`, `dodaios/brain/manager.py` (`_generate_untagged`, `_run_multi_engine_turn`,
`_record_response_side_effects`), `dodaios/speech/usage_meter.py` (unchanged call, new semantics).

* **Absence of provenance = UNAVAILABLE.** `remote_speech_blocked(text)` now refuses every plain `str`; only a
  `LabelledText` that positively carries `UNRESTRICTED` can go to a remote synthesizer, and then only if the turn is not
  restricted/unavailable and the text registry does not match. `label_of(plain)` is `None` and is refused.
* **Per turn, not process-global.** The turn state is a `contextvars.ContextVar` holding a `_TurnState` (declared /
  unavailable / restricted flags). `begin_turn(turn_id)` starts it UNAVAILABLE (not unrestricted). A concurrent turn runs
  in its own context and cannot reset or relax this one. No turn state at all (speech from memory, wiki, tool output,
  screen, an alternative voice, a task that does not inherit the context) reads as UNAVAILABLE. RESTRICTED and
  UNAVAILABLE are sticky inside a turn; `UNRESTRICTED` is only a positive declaration (`attach_unrestricted()`) and can
  never lift either.
* **Per chunk.** The label rides on the text object (`LabelledText`). A transformation (normalisation, splitting,
  translation) yields a plain `str` and is therefore blocked unless the path re-attaches the label with
  `provenance.label_like(new_text, source)`. Fail-closed on purpose.
* **Multi-engine.** `_run_multi_engine_turn` no longer calls `begin_turn()` mid-turn (that reset the channel) and no longer
  pre-sets UNAVAILABLE (the default already is). It merges the outcome's label and returns a `LabelledText`.
* **Storage is a separate question.** `withheld_from_remote_store()` keeps the block-8 meaning (blocked only on a positive
  restricted/unavailable signal) for the curator and remote history replay, so ordinary chat history is not silently
  disabled. Residual: an unlabelled answer of an unmarked path is still stored/replayed.

Paths that positively attach UNRESTRICTED today: only the multi-engine outcomes that are known to be clean
(`work`, `off`, `no_engines` status results and an untainted synthesis), returned as `LabelledText`. The API
`attach_unrestricted()` / `label(text, UNRESTRICTED)` exists for a pure local-origin typed user prompt answered by a fully
local engine, but NO such path in `manager.py` calls it yet: ordinary chat is therefore BLOCKED for remote TTS by default.
This is the intended default and a visible functional consequence (a remote voice stays silent on normal replies until a
path proves the turn clean). Blocked on purpose: memory/wiki/tool output/screen context/pointer context, alternative
voice, ack/phrase TTS, any text that lost its label.

Residual limits: the pipeline must not strip the label between the manager and `MeteredTTS` (if it does, speech is
blocked, not leaked); native/realtime voice that speaks by itself and other speech that bypasses `MeteredTTS` are still
not gated; the label lives in process memory; the text registry is still a floor, not a proof.

Controls prepared, NOT RUN (`tests/test_block9_gpt_round2.py`, updated `tests/test_block8_review_r6_r7.py`): plain and
short text ("42", "ok") is refused for a remote TTS; a transformed plain string is refused; a labelled UNRESTRICTED chunk
passes; two turns in two asyncio tasks do not reset each other; an UNAVAILABLE merge cannot be lifted by UNRESTRICTED.

## Block 13: GPT round 3 (G2)

Finding G2 (HIGH): unknown provenance still became remote-replayable history and curator input. State: **fixed for the
history, replay, curator and persisted-chat boundaries named below; partial overall** (uncovered paths listed).

Cause: `withheld_from_remote_store` blocked only on a POSITIVE restricted/unavailable signal, and a turn that no path
labelled starts in the undeclared state (`label()` is UNAVAILABLE but neither flag is set). `_record_response_side_effects`
therefore wrote both messages with `restricted=False` and invoked the curator, so ordinary unlabelled paths (paths, tool,
wiki, screen-derived data) were not fail-closed for later remote replay or curation.

Implementation choice (typed label stored WITH each message, missing = UNAVAILABLE):

* `brain/provenance.py`: `storage_label(text)` returns RESTRICTED on any positive signal, UNRESTRICTED only on a positive
  declaration (turn channel `attach_unrestricted`, text label not contradicting, registry not matching), else UNAVAILABLE.
  `withheld_from_remote_store` is now fail closed (`storage_label != UNRESTRICTED`). New `parse` (None, empty, unknown or
  garbage = UNAVAILABLE), `remote_replayable` (only a stored UNRESTRICTED) and `local_only_signal` (the old block-8
  "positive evidence" test, kept for callers that tag rather than permit).
* `core/protocols.py`: `BrainMessage.provenance: str = "unavailable"` (the legacy `restricted` flag still vetoes).
* `brain/manager.py`: `_record_response_side_effects` and the second history append in `generate` store the
  `storage_label` value on the user and assistant messages; the curator runs only when the stored label is UNRESTRICTED
  (the second path used to call it unconditionally). `_history_for_engine` sends a REMOTE (or unknown) engine only messages
  whose label is positively UNRESTRICTED; a local engine still sees all history, unchanged and not relabelled.
  `_seedable` keeps the label of a seeded dict/`BrainMessage` and gives a bare `(role, text)` pair UNAVAILABLE. Drop-context
  notes, background notes and any other `BrainMessage` built without a label are UNAVAILABLE by default.
* Durable: `state/chat_store.py` adds a nullable `chat_messages.provenance` column (`_COLUMN_MIGRATIONS`, applied on open);
  `add_message(provenance=...)` stores it, `get_thread` returns it, NULL (every old row) reads as `unavailable`.
  `ui/web/chats_routes.py`: `ChatTurn.provenance` and `_seed_messages` carry the stored label into `seed_history` on resume
  (`_seed_pairs` is kept for the other callers). `memory/control/proposals.py` now tags with `local_only_signal` so its
  "restricted/unlabelled" tag keeps its previous meaning.

Functional consequence (intended, visible): with no path declaring UNRESTRICTED, a REMOTE engine receives no prior-turn
history, and the curator learns nothing from ordinary turns, until a path proves a turn clean. Only the multi-engine
outcomes already known clean declare UNRESTRICTED. History was NOT promoted to unrestricted to keep either feature working.

Residual limits / open:
* The chat writers in `ui/web/launcher.py` and `ui/desktop_app.py` call `add_message` outside the turn's context (the reply
  comes back through an executor/task), so they do not pass a label: persisted ordinary rows are stored NULL = UNAVAILABLE.
  After a restart a resumed chat is therefore local-engine-only for replay. Passing a real label needs the brain to hand it
  back with the reply; not done (open).
* Not covered: voice native/realtime (`take_voice_history_seed`, realtime context, the engine speaking by itself), the
  `_previous_user_turn_text` follow-up detector (local use), and the memory labels of `turn_memory` (a provider that labels
  a note `local_only=False` is still trusted as a positive "shareable" claim; unlabelled `recall()` text is already withheld
  from remote). Summaries (`BrainManager.summarize`) take caller-supplied text and were not changed here. None of these is
  claimed closed.
* The label lives next to the text; text rewritten by a path that drops it becomes UNAVAILABLE, not leaked. No OS-level
  enforcement exists.

Controls prepared, NOT RUN (`tests/test_block13_gpt_round3.py`, updated `tests/test_block8_review_r6_r7.py`): an undeclared
turn stores `unavailable` on both messages and does not call the curator; a declared-clean turn stores `unrestricted` and
calls it once; a remote engine's history view contains only `unrestricted` messages; a local engine keeps all; seeded
unlabelled pairs stay `unavailable`; a chat DB created before the column reads old rows as `unavailable`, and a label written
before a "restart" (second `ChatStore` on the same file) is read back. Nothing was run: no test, no CI, no build, no app.
Block 17 (GPT round 4, H1): phone attachment label is set before analysis and travels with the queued record; see ADR-0062 "Block 17: GPT round 4 (H1)".

Block 18 (I1): the current attachment-bearing prompt/images are now label-gated at every dispatch (brain chain, dispatcher, API/CLI runners); see ADR-0062 'Block 18: GPT round 5 (I1)'. Not run.

## Block 19: GPT round 6 (J1)

Finding J1 (HIGH): the explicit "ask several engines" path built `[ContextItem(task, "task")]` (`local_only=False`) and
called the orchestrator directly, so the Block 18 input label (attachment text / PDF / image) never reached remote
reviewers or the synthesizer. State: **fixed in code (statically); NOT executed.**

Choice (label -> ContextItem, enforced twice, refusal not leak):
* `brain/multi_engine_chat.py`: `context_items(task)` derives the item from `provenance.input_label()`: no label = no
  attachment = ordinary `task`; any non-UNRESTRICTED label (a missing label reads UNAVAILABLE) = `local_only=True` with
  `derived_from=("attachment_<kind>", ...)`, so the gate withholds it by flag AND by category and the kinds survive into
  the egress record. `ask` then drops every branch that is not provably local (`dispatch_gate.is_local`, kind local)
  BEFORE the fan-out; a restricted input with no permitted engine returns the new outcome `refused` (user-visible
  message from `dispatch_gate.user_message`, nothing sent). The synthesizer is chosen the same way: a remote configured
  synthesizer is replaced by a local engine that answered, or the outcome is `refused`; reviewer answers tainted by the
  material keep the existing `tainted` provenance (outcome RESTRICTED).
* `brain/consumer_orchestrator.py`: defense in depth at the direct brain calls: `_fan.serve` calls
  `dispatch_gate.refusal(...)` (locality from the real destination) and returns `refused` before preparing the prompt;
  `synthesize` returns `refused` for a non-local synthesizer under a restricted input. No redaction and no output-side
  TTS denial is relied on.
* `brain/manager.py` `_run_multi_engine_turn`: the `refused` outcome returns its reason as the reply.

Residual limits: the composed prompt is one string, so typed text and attachment text cannot be split back; the whole
question carries the turn's most restrictive label (typed text of a mixed message stays local too). Per-item labels
are kept as category kinds only. Locality is loopback only (Block 18 rule). No localized catalog string for the refusal
(English `dispatch_gate` text). A message without a declared input label is treated as ordinary typed text, as before.

Controls prepared, NOT RUN: `tests/unit/test_block19_multi_engine_input_label.py` (text/PDF/image labels map to
ContextItem fields; unlabelled = restricted; remote-only engines -> refused, nothing sent; mixed engines -> only the local
one is called and the answer is RESTRICTED; local reviewer + remote synthesizer -> local synthesis, remote sees nothing;
unrestricted input unchanged; orchestrator refuses a remote branch/synthesizer with clean-looking items). Nothing was run:
no pytest, no CI, no app, no model call; only `py_compile` and `ruff check` on the touched modules and the new test.
