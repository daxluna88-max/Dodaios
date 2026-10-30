# Delivery state of the Jarvis pilot (2026-10-30, ~18:00 CET)

What is verified, what is not, what is blocked. Evidence is in `docs/COLLAUDO-REGISTRO-2026-10-30.md`
(device runs by Dax on an iPhone through the Termius tunnel) and the VPS reports on branch `pilot-results`.
Nothing here is a claim of "ready". The pilot runs upstream `56eb2e4f` plus the patches in
`scripts/pilot/MANIFEST` (last: `it-guard-29`); the frontend on the VPS is bundle `index-D6EYJn6o.js`
built from `504305405` (rebuilt by hand, not automatic).

## By point of the operating order

| # | Point | Done and verified | Not verified / open |
|---|---|---|---|
| 1 | Memory ("Dimentica") | Forget removes the value and its note lines from the profile (IP-8 PASS, new chat does not know it); honest reply about chat history, wiki and backups | The notebook (`MEMORY.md`) copy was not inspected; backups and chat history keep the text by design |
| 2 | Chat | IP-1 lock screen, IP-2 reload reopens, IP-3 draft not overridden, IP-4 Wi-Fi flaps, IP-5b stop keeps the written words after reload | A typed draft is lost on reload (IP-3 note); 320/375 px and keyboard (IP-11) NOT RUN |
| 3 | Italian | IP-6 "Rispondi in italiano" in an English session; IP-7 easy variant "No lo sé"; UI keys complete (12,731, none missing) | IP-7 strict case (first message of a chat) NOT RUN; English narration before a tool call (OBS-1); voice and native speaker review NOT RUN |
| 4 | Privacy | With the switch on, the personal blocks and the personal-read tools are sent; with it off they are withheld in code (tests) | The request body was never inspected (IP-9 NOT PROVEN); consent at first cloud send NOT chosen; chat history and tool results of non-personal tools still go to the provider |
| 5 | AI engines | `GET /api/settings/engines` and `engine_trace()` (names only); Codex reason explained | IP-10 on the VPS NOT RUN; token cost per engine not measured; saving memory depends on the model (FIND-2: a free model did not call the tools; default is now DeepSeek V4.1 Flash, DEF-1 PASS) |
| 6 | Consumer/PMI | Isolation and "Work = local only" exist on unmerged branches; 86 tests passed there; integration plan written (`docs/INTEGRATION-PLAN-2026-10-30.md`) | NOT merged, NOT on the pilot; "Work local only" is a policy gate, not a network sandbox (tools, updates, telemetry, embeddings are outside); network block for Work is for after Hostinger |
| 7 | Collaudo | Checklist and registry exist (`docs/COLLAUDO-2026-10-30.md`); SEC-1 PASS (destructive command shows the card, decline blocks it) | Mac R1–R8 BLOCKED (no Mac, no signed build); blocks 5–7 definition missing; voice BLOCKED (`JARVIS_VOICE=0`); avatar NOT RUN (other repo) |
| 8 | Delivery | This document; patches 1–29 with checked chain on upstream | Guided install, user guide and macOS packaging NOT done |

## Findings fixed today (with the commit that fixed them)

- Stop then reload lost the written text -> `it-partial-27`.
- A reply confirmed a save no tool had made ("Noted.") -> `it-honesty-28`.
- A destructive command ran with no card in the default "bypass" chat -> `it-guard-29`.
- The pilot check failed on a correct 12-hour spoken time -> `check.py`.

## Known small items (not fixed)

- The approval card offers "Consenti sempre" for a guarded command (the guard still asks every time).
- The reason "declined by the person" is shown in English.
- Narration before a tool call can be English and duplicated (DeepSeek).
- A bare "Ricorda…" with a throw-away sentence is rightly refused by the curator.

## Decisions waiting for Dax

1. Consent at the first cloud send (A: account setting plus notice, B: a "no retention" request flag, C: send only the memory that is needed, D: local-only mode).
2. Merge of the product stack ("unisci") and the order of the integration plan.
3. Network block for the PMI product (after Hostinger, together with the move to the real PC).
4. Blocks 5–7: the list and the acceptance criteria.
5. Voice engine and the Italian wake-word model.
