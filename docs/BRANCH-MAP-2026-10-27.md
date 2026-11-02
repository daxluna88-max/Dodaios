# Branch map and integration sequence (2026-10-27)

Read from git (`git log`, `git merge-base`, `git merge-tree`), not from CI. **Nothing here is merged; no merge is
performed by this document.** "Published" does not mean "integrated": `main` is still `01cdb725`.

## Stack (each branch contains the ones above it)

| Order | Branch | PR | Base | Commits over `main` | Content |
|---|---|---|---|---|---|
| 1 | `claude/engine-selection` | #6 | `main` | 18 | Work local-only policy, Consumer fan-out + privacy gate, voice LLM bridge, overrides/fallback, strict offline profile (ADR-0049..0053) |
| 2 | `claude/p0-destination-locality` | #8 | #6 | 22 | local vs remote decided by the real endpoint |
| 3 | `claude/p1-orchestrator-hardening` | #9 | #8 | 26 | cancellation while queued, finite numbers, provider cleanup |
| 4 | `claude/p2-final-synthesis` | #10 | #9 | 30 | one final answer through the gate |
| 5 | `claude/p4-independent-products` | #11 | #10 | 36 | `--product work|consumer`: data dir, vault, lock (ADR-0054) |
| 6 | `claude/p4-data-isolation` | #15 | #11 | 38 | `user_data_dir()` follows the product |
| 7 | `claude/p4-product-ports` | none | #15 | 71 | ports per product; chat multi-engine (ADR-0055); personality (ADR-0056); guided first run (ADR-0057); **STOP + control strip + turn record (ADR-0058)**; hosted Consumer design (ADR-0059) |

Side branches:

- `claude/p2-final-answer-ui` (#12): final-answer picker UI, based on #10. Not contained in `p4-product-ports`.
  Merging it into `p4-product-ports` conflicts **only** in `dodaios/ui/web/dist/assets/*` (renamed hashed chunks):
  resolve by rebuilding `dist`, never by hand.
- `claude/mobile-panel` (#4): phone companion, based on `main`, independent of the stack. Merges into
  `p4-product-ports` without conflicts (git, not tests). STOP / approvals from the phone do not go through the new
  ADR-0058 door yet: block 14.
- Jarvis repo #12 (`claude/consumer-telemetry`, avatar CPU/RAM): separate repo, base `base`.
- Already contained, 0 commits over `main` (merged or empty): `boot-hook`, `turn-memory`, `tool-gate`,
  `italian-voice`, `piper-licences`, `ci-baseline`, `audio-stub-followup`, `anthropic-sdk-2-test-fix`,
  `uninstall-multicall-test`.

## Where each area lives

| Area | Branch that introduces it |
|---|---|
| Engines, fallback, fan-out | #6, #8, #9, #10 |
| Privacy gate / destination | #6 (ADR-0049/0050), #8 |
| Products / isolation | #11, #15, `p4-product-ports` (ports) |
| Phone | #4 |
| Avatar | Jarvis #12 (+ `dodaios/live`, `plugins/realtime` on `main`) |
| New UI (control strip, setup, personality) | `p4-product-ports`; final-answer picker #12 |
| STOP / generations / turn record | `p4-product-ports` (ADR-0058) |

## Integration sequence (no merge without Dax's explicit "unisci")

1. #6 → #8 → #9 → #10 → #11 → #15 into `main`, in this order (each PR is stacked; retarget the base after each).
2. Open a PR for `p4-product-ports` (nothing exists yet) once the stack is in.
3. #12 after rebuilding `dist` on top of 2.
4. #4 last, after block 14 puts the phone STOP/approval doors behind ADR-0058.
5. Fork Actions quota is used up until 1 Nov: **no pipeline evidence for any of this**. All checks NOT RUN.

## Work blocks 2 → 3 → 4 (this order)

Block 2 (races STOP/resume, no old STOP on new work, unconfirmed workers across repeated STOPs) and part of 8/9 are
**written, not run** in `p4-product-ports` (ADR-0058 corrections). Open per ADR-0058 "Not done": a ticket in the voice
pipeline, no probe for playback/realtime, tools already invoked by a stale turn, a spoken stop that does not close
the door. Blocks 3 and 4 close those.
