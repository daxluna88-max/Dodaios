# Integration study: product stack merged into it-complete (2026-10-31)

Study branch `claude/integration-1` = `claude/it-complete` (`19e2f9bac`) + `origin/claude/p4-product-ports` (the whole stack, 72 commits). **Nothing was merged into any main or stack branch. The VPS is untouched.** Moving `it-complete` needs Dax's "unisci".

## Facts measured (sandbox, offline, no cost)

- Merge: no textual conflict (git). Locale JSON of all six languages valid after the merge; the corrected privacy wording (it, en) and `trace_report.reason.declined` survived.
- Frontend: `npx tsc --noEmit -p .` printed nothing (no type errors). `vitest` NOT run on the merged branch.
- Python gate, `tests/unit/brain` + `tests/unit/ui/web` (+ `tests/unit/core/test_product_profile.py` on the study branch only, it does not exist on the parent), same command on both branches in separate worktrees:
  - parent `it-complete`: 133 failed, 5579 passed, 24 errors.
  - merged study branch: 89 failed, 5987 passed, 24 errors (before the fix below).
  - Failures only on the merged branch (4): 3 in `test_work_real_ollama_plugin.py`, all `ModuleNotFoundError: No module named 'openai'` (the sandbox lacks the package, not a regression; those tests exist only on the stack); 1 in `test_mood.py::test_a_faded_state_read_twice_is_the_same`.
  - The mood failure was a wrong TEST (the owner-mood duration has a floor, so `ttl_s=10` expires at 30 s, and `Activity("gaming")` is not a known activity). Test corrected, 28 mood tests pass. The reducer code was right.
  - 48 failures present on the parent are absent on the merged branch (the stack fixes them or replaces the tests); not analysed one by one.
  - The 24 errors are the same on both (missing `rich`, `openai`, `google.genai` in the sandbox venv).
- Policy and privacy tests on the merged branch: 97 pass (`test_engine_policy`, multi-engine chat, consumer orchestrator, consumer fan-out routes, IP-9 request capture, new fan-out capture).

## Privacy of the fan-out (standing priority)

New test `tests/unit/brain/test_multi_engine_privacy_capture.py` (only on this branch): the privacy switch is ON (the pilot setting); the manager holds sentinels in profile, learned notebook, contacts, per-turn wiki text, external turn memory and chat history; a "Chiedi a più modelli" turn is run with two fake engines. Result: three requests reach the providers (two branches and the synthesizer), none contains any sentinel, all run without tools. Positive control: the same manager's normal prompt does contain the profile, contacts and notebook sentinels. Reason in the code: `multi_engine_chat.ask` builds the only context item as the question (`ContextItem(task, "task")`), the gate allows only the `task` category for remote engines, history is not sent, and the synthesizer receives an enumerated style sentence.

Limits of this finding:
- It proves the personal blocks do not leave through the fan-out. It does not make the fan-out private: the QUESTION itself goes to several providers, with a redactor that masks identifiers (existing test: e-mail masked).
- Several providers receiving one question is a path Dax has NOT authorized for his data. Keep `[brain.multi_engine] enabled = false` until he decides.
- The OpenRouter `data_collection=deny` rule lives in `plugins/brain/openrouter.py`, so it covers an OpenRouter branch; other providers keep their own terms.
- Not examined: the stack's other new entry points (`/api/consumer/fanout` with caller-supplied context, which is the caller's choice), the realtime path, the learning loop.

## What this study does NOT show

No real provider, no real device, no GitHub Actions, no macOS, no voice. `dist` not rebuilt from the merged branch. A clean merge plus a green subset is not a release.

## Suggested order (unchanged from the plan), with this evidence

1. Step 1 (#6 -> #8 -> #9 -> #10) and the rest of the stack are mergeable with no new Python failure beyond sandbox dependencies.
2. Before moving `it-complete`: Dax decides on multi-provider sending; `vitest` on the merged frontend; rebuild `dist` with `scripts/pilot/rebuild-ui.sh` (dry run first); keep multi-engine OFF by default on the pilot.

## Update: full frontend gate on the study branch (commit `554aab98a`)

- `npx vitest run` on the study branch (it-complete `faab8f044` + the stack): **7728 passed, 0 failed, 0 test files failed to load**. The same command on `it-complete`: 7670 passed, 0 failed, 0 failed files. `npx tsc --noEmit`: no output (no type errors). Backend gate: see the section above (not rerun after these frontend-only commits).
- First merged run had 15 failures. 12 already failed on the stack branch alone (tests or code never run before); 3 appeared only after the merge (Italian dictionary parity). Fixes, all in this branch:
  - 101 Italian strings added for keys the stack introduced (control strip, personality, first-run guide, network, ask-several-engines, local voice); placeholders checked mechanically. These are my own translations: not reviewed by a native speaker other than Dax.
  - Two guide strings per language named "Jarvis"; a stack test requires the assistant to be named only by the `{name}` token. Replaced with "the Work edition" / "your assistant" in en, de, es, pt, zh, it.
  - `PersonalitySection` now translates only the 11 mood reasons the server can send (checked against `dodaios/brain/mood.py`) and shows any other code as is.
  - Four tests corrected: `ControlStrip` (counted indicators before the reading loaded), `SetupTour` replay (now walks the steps and still asserts no non-GET call to the first-run endpoints), `PersonalitySection` (a text that appears twice), `App.detached` (the shell now hosts the control strip, which needs a react-query provider).
- A measuring mistake of mine, kept here so it is not repeated: a first count ignored three test files that failed to load because my worktree linked `node_modules` through a symlink (Vite denies files outside the root). Count failed FILES as well as failed tests.
- Still NOT proven: a real browser, the iPhone, `dist` built from this branch, GitHub Actions, macOS. The 78 "missing tests" seen earlier were the three unloaded files plus renamed tests in `SetupTour`/`setupSteps`; with the files loading the study branch has 58 more tests than `it-complete`.
