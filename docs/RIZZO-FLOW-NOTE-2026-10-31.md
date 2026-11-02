# Rizzo Flow: kept for later (Dax, 2026-10-31: "tieni in memoria Rizzo flow, lo sviluppiamo più tardi")

NOT built, NOT installed, NOT measured. This note is only so the work can restart from facts.

## What it is
[Rizzo-AI-Academy/rizzo-flow](https://github.com/Rizzo-AI-Academy/rizzo-flow): a local decision model on llama.cpp. It returns typed answers with probabilities (boolean, choice, score, numeric) in one forward pass, no text generation. Apache-2.0; weights Apache-2.0 (XHToken Spark-X2.5), llama.cpp MIT. Not in Dax's GitHub account (public repo). The Jarvis repo (`jarvis-di-dax`, `dax_controller/sidecar.py`) already has a client for `POST /v1/decisions` (port 8017); from reading the real `request.schema.json` and `response.schema.json` it looks compatible, NOT tested against a live server (`tests/test_compat_rizzo_reale.py` exists for that).

## Facts from its README (not verified by us)
- Models: 4B Q4_K_M 2.6 GB (ties Q8_0 on its own test), 4B Q8_0 4.4 GB, 1.7B Q4_K_M 1.1 GB (loses about 5 points). KV cache about 144 KiB per token (1.4 GiB at the default 8192 context).
- About 50 ms per decision on an NVIDIA RTX 5060 Ti. CPU latency: NOT stated. "A GPU-free host has not been tested." Maintainers tested Windows + NVIDIA only; Linux not tried.
- Linux x64 CPU runtime exists (`llama-b11081-bin-ubuntu-x64`, pinned build). One resident model, requests serialized. Probabilities uncalibrated ("status ok does not mean correct"). Localhost only, not hardened for public exposure.

## Fit for the Hostinger VPS (2 vCPU, 7.7 GiB RAM, 96 GB disk, Ubuntu 26.04, Python 3.14.4)
- Memory: our ESTIMATE about 3.3-3.5 GB for 4B Q4_K_M with a 2048 context; it cannot run together with the UI rebuild (5 GB).
- Speed: NOT known. Expectation (not a measurement): seconds per decision on 2 vCPU. Fine for shadow mode (verdict logged beside the rule's, nothing acts on it); possibly too slow to decide inside every turn.
- Unproven: `uv sync --locked` on Python 3.14, the CPU runtime on this VPS's CPU (needs `lscpu`), real latency.
- Alternative: the PC with an NVIDIA GPU, reached over Tailscale (needs the PC on).

## Design agreed in principle
Rules stay the floor (cheap, deterministic, already shipping). Rizzo handles only the ambiguous middle, in SHADOW mode first (log its verdict next to the rule's for some days, act on nothing), promote only after measuring latency, locality and precision. If Rizzo is down or uncertain, the rule decides (the safe side). It must run LOCAL only: it reads the owner's messages (standing privacy priority). First candidate use: "is this message an order to save to memory?" (the FIND-1/FIND-4 class of bug).

## "Training" it
Start with the question text and few-shot examples, then calibration (`--calibration`). A LoRA fine-tune needs a GPU and costs money: not now. A labelled set of Dax's real messages (including wrong voice transcripts) is needed either way; the test register is the source.

## Needs Dax before any step
Authorization to download a model (free, 2.6 GB) for a CPU speed test on the Claude sandbox; or only `lscpu` and `free -h` from the VPS. Nothing downloaded so far.

## Decisions of Dax, 2026-10-31 (evening)
- Sending the same question to several providers (multi-engine fan-out): **stays OFF** ("Sì spento").
- Rizzo Flow: **later, when everything else works** ("Rizzo quando tutto funziona"). No download, no install until then.
