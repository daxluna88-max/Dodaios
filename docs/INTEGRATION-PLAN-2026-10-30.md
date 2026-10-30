# Integration plan for the product stack (2026-10-30)

Plan only. **Nothing here is executed; no merge happens without Dax's explicit "unisci".**
Facts come from git (`git rev-list`, `git merge-tree`), not from CI. Checks marked NOT RUN have no evidence.

## State read on 2026-10-30

| Branch | Ahead of `claude/it-complete` | Behind | Dry-run merge into `it-complete` |
|---|---|---|---|
| `claude/engine-selection` (#6) | 18 | 85 | no conflict reported |
| `claude/p4-product-ports` (whole stack, 72) | 72 | 85 | no conflict reported |
| `claude/p2-final-answer-ui` (#12) | 36 | 85 | no conflict reported (the map expects `dist/assets/*` conflicts when merged onto `p4-product-ports`) |
| `claude/mobile-panel` (#4) | 3 | 97 | no conflict reported |

`git merge-tree` finding no textual conflict does not mean the result builds or passes tests.

## Order (each step stacked on the previous one; retarget the base after each)

1. #6 `engine-selection` -> #8 `p0-destination-locality` -> #9 `p1-orchestrator-hardening` -> #10 `p2-final-synthesis`
2. #11 `p4-independent-products` -> #15 `p4-data-isolation`
3. PR for `p4-product-ports` (none exists yet): ports per product, multi-engine chat, personality, first run, STOP + turn record.
4. #12 `p2-final-answer-ui`, after rebuilding `dist` (never resolve `dist/assets/*` by hand).
5. #4 `mobile-panel` last, after phone STOP/approvals go through the ADR-0058 door (block 14).

## Gate after every step (before the next one)

- Python: `PYTHONPATH=. python -m pytest <touched test dirs> -q -p no:cacheprovider`, compared with the same run on the parent commit (the sandbox has known failures from missing dependencies; only NEW failures count).
- Frontend: `npx tsc --noEmit -p .` and `npx vitest run` in `dodaios/ui/web/frontend`.
- Policy: with `JARVIS_PRODUCT=work`, a remote provider is refused (tests in `tests/unit/brain/test_engine_policy.py`, 86 tests passed on `claude/stop-late-output` on 2026-10-30).
- Isolation: `tests/unit/core/test_product_profile.py` (data dir, vault service, launch lock, ownership marker).
- Not provable here, record as NOT RUN: GitHub Actions (fork quota is used up until 1 Nov), macOS, a real iPhone, voice.

## What merging into `it-complete` does and does not do on the pilot VPS

- The VPS runs upstream `56eb2e4f` plus the patches listed in `scripts/pilot/MANIFEST`. A merge into `it-complete` does not change the VPS Python unless a patch is added.
- The cron restarts Jarvis on every new `it-complete` commit, so a merge restarts it.
- The frontend on the VPS changes only when `dist` is rebuilt by hand.
- Therefore: merge on a separate branch first (e.g. `claude/integration-1`), run the gate, and only then move `it-complete`.

## Known gaps to carry forward (not closed by merging)

- The Work "local only" gate covers brain, STT, TTS and realtime plugins. It is a policy gate, not a network sandbox: tools, update checks, telemetry, embeddings and reranking are outside it (`docs/engine-inventory.md`). Dax's decision (2026-10-30): the network block for Work is developed after Jarvis on Hostinger is finished, together with the move to the real PC.
- Phone STOP/approvals do not yet use the ADR-0058 door.
- Nothing in the stack has pipeline evidence.

## Rollback

Each step is a merge commit on its own branch: undo with `git revert -m 1 <merge>` on that branch, or drop the branch. Never rewrite `it-complete` history.
