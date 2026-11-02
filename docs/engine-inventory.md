# Engine inventory: where a model gets called (2026-10-26, main at 8730d9a8)

Purpose: the starting point for interchangeable engines (Consumer) and local-only inference (Work, ADR-0049).
Read from the code; nothing here was exercised against a real provider.

## Gated by the product policy (ADR-0049)

| Path | How it gets a brain | Gate |
|---|---|---|
| Chat / `BrainManager` | `BrainProviderRegistry.instantiate` | registry check + per-call `PolicyBrain` |
| Resolver, wiki curator, bio brain, self-build | `registry.instantiate` | same |
| Agent chat API runner | `agent_chat.runner_api.build_brain` (direct class) | `check_provider` |
| Agent runtime gateway | `agent_runtimes.gateway._new_brain` (direct class) | `check_provider` |
| Critic runner | `BrainProviderRegistry().get_class` + `cls(...)` | `check_provider` |

Also gated (same product check): `core.registry.load` for `jarvis.stt` / `jarvis.tts` / `jarvis.realtime`
(cloud plugins refused under Work), the STT class lookup (falls back to the local recognizer), dictation polish
(on-device families only), and the local voice engine's Ollama client (non-local host refused).

## NOT gated (open work; Work must not be called fully local until these are)

- Plugins loaded by a path other than `core.registry.load` / the STT lookup (none found, not exhaustively audited).
- `local-realtime` (self-hosted URL) is refused under Work until its endpoint is checked.
- Subscription launchers and CLIs (`codex_app_server.py`, `claude-cli` runners) outside the brain registry were not
  audited one by one.
- Tools (browser, GitHub, email), update checks, telemetry, embeddings, reranking and vision clients outside the
  brain registry are NOT individually gated. The opt-in strict offline profile (`JARVIS_NETWORK_POLICY=local-only`,
  ADR-0053) refuses every non-local connection and DNS lookup at the socket layer instead; it is not on by default.
- `get_class` callers that only probe metadata (`subscription_connected`, signatures) do not run inference.

## Local live voice

`voice_engine/llm.py` streams from a local Ollama server directly (default, unchanged). ADR-0051 adds an
optional bridge: with `[voice_engine].answer_source = "brain"` the app answers each model call with a registry
provider (policy applies there; Work reaches only local providers). Selectable from the Local voice card (ADR-0051), now mounted on Settings > API Keys > Live calls under "On this machine (experimental)" (it had no
screen before this change); no
mid-session switch, no measured latency, no capability check at selection time.

## Reuse points for the next steps

`BrainProviderRegistry` (entry points `jarvis.brain`: claude-api, openai, gemini, vertex, openrouter, nvidia,
codex, antigravity, claude-cli, grok, ollama, local-openai), `BrainRequest`/`BrainDelta`, `MeteredBrain`
(usage ledger), `core.config.resolve_provider_endpoint`, protected credential storage. Consumer orchestration, privacy gate and aggregate budget: `consumer_orchestrator.py` (ADR-0050), library only.
Overrides, fallback and per-branch reporting: `engine_routing.py` (ADR-0052), library only. Not built: a normalized
capability catalog beyond what plugins declare (`engine_policy.capabilities_of`), wiring into chat/voice/jobs, UI.

## Work policy coverage (static audit, 2026-10-27)

Block 6 part A. Read from the code; NOTHING was run (no tests, no app, no network). "Gate" is the
`engine_policy` check that runs before any credential read or connection. Line numbers are approximate
(file as of the block 6 commit).

| Path | Gate | Status |
|---|---|---|
| Chat / BrainManager, resolver chains, wiki curator, bio, self-build | `provider_registry.py` `instantiate` -> `check_provider`; `PolicyBrain` per call | covered (ADR-0049) |
| Resolver credential probes (`_provider_reachable`, subscription candidates) | `resolver.py` `provider_blocked_reason` / `work_active` before any key read or login probe | covered (block 6) |
| Agent chat API runner, runtime gateway, critic API path | `check_provider` in `runner_api.build_brain`, `gateway._new_brain`, `critic/runner.py` | covered |
| Agent chat vendor CLI / external runtime / control runner | `agent_chat/service.py` `run_attempt` error turn; `runner_cli.run_cli_turn` `require_local_inference` | covered (block 6) |
| Missions: worker choice | `missions/init.py` `_worker_factory` returns only the in-process worker with a local engine | covered (block 6) |
| Missions: in-process API worker | `api_agent_worker.py` `check_provider` before the secret read and in `_build_brain` | covered (block 6) |
| Missions: critic | `critic/runner.py` `_invoke_once` local in-process critic only | covered (block 6) |
| Jobs / background work (wiki, rollup, bio, learning) | `background_policy.background_providers` drops remote candidates first; `provider_chain.credential_ready_wiki_providers` | covered (block 6) |
| Summaries, titles, briefs, IDE writers | via `resolver.py` (registry); `resolve_subscription_brain` returns None under Work | covered (block 6) |
| Society browser agent model | `society/browser/llm.py` `llm_spec_for` before key lookup | covered (block 6) |
| Society seats / learning review | `society/seat_brain.py` `_build` `check_provider`; `GrokBrowserModel.__init__` | covered (block 6) |
| Flash ack / spawn announcer / readback LLM | `brain/factory.py` `_ack_provider_allowed` (Ollama on a local endpoint only), incl. fallbacks; per-call check in `ack_brain/providers/ollama.py` | covered (block 6) |
| Gemini / Vertex clients (TTS, STT, polish, computer use, voice compare, ack) | `core/google_genai.py` all four builders refuse; `gemini_flash_tts._ensure_client`; `native_computer_use` | covered (block 6) |
| TTS | `plugins/tts/__init__.py` `_build_provider` (only piper-local), `_tts_has_credential`, surface TTS; `pipeline._default_tts_for_pipeline` no Gemini default | covered (block 6) |
| STT, cross-family fallback | `plugins/stt/__init__.py` `_load_provider_class`; `_stt_family_has_key` / `_stt_has_credential` now policy-first | covered (block 6) |
| Realtime / live voice | `core.registry.load` -> `check_service`; `subscription_voice_selected` False and `CodexSubscriptionVoiceBrain` refuses; `live_routes` profile | covered (block 6) |
| Voice bridge, local voice engine | `llm_bridge` via registry; `voice_engine/llm.py` host check | covered (ADR-0049/0051) |
| Dictation polish | `polish_client.py` chain on-device only; endpoint check in `_post`; `polish_probe` | covered (block 6) |
| Provider Test and realtime voice preview routes | `provider_routes._work_refusal`; `provider_test.run_provider_test` | covered (block 6) |
| Ollama embed / chat probes | `ollama_inventory.embed_probe`, `health_monitor._chat_probe` endpoint check | covered (block 6) |
| Memory index / wiki semantic embeddings | local ONNX encoder (`semantic_model.py`); no hosted embedder exists | local by construction; model download (Hugging Face) is setup egress, NOT gated |
| Vision / OCR / computer use | brain registry; native Gemini CU refused | covered |
| Reranking, classifiers, routers | brain registry or deterministic code | covered / no model |
| Wake word | local ONNX / Whisper | local by construction |

### Not covered (honest list)

- Process launching: mission CLI workers (`claude_direct`, `codex_direct`, `gemini`, grok), `harness/base.py`,
  `agent_runtimes` (Hermes/OpenClaw processes), IDE panes, `codex_app_server`: selection is gated above, spawn code
  was deliberately not touched (another agent owns it).
- Tool plugins that call hosted APIs and tool gating; the strict offline profile (ADR-0053) is the only net there.
- Model downloads (semantic model, wake models, Ollama pull), model-list/usage/quota fetches with credentials
  (`model_catalog`, `frontier_resolver`, `agent_usage`): metadata egress, not inference.
- Credential PRESENCE reads in `BrainManager` pre-boot checks for remote providers (no connection; instantiate refuses).
- `local-realtime` and `realtime/local_server/smoke.py`: endpoint not checked.
- A brain cached before `JARVIS_PRODUCT` changes at runtime stays unwrapped (the product is a start-up setting).
- Nothing in this table was executed.
