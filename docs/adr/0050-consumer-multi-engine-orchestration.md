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
