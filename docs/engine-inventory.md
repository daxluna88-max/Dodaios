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
optional bridge: with `[voice_engine].llm_source = "brain"` the app answers each model call with a registry
provider (policy applies there; Work reaches only local providers). Configuration is a config key only: no UI, no
mid-session switch, no measured latency, no capability check at selection time.

## Reuse points for the next steps

`BrainProviderRegistry` (entry points `jarvis.brain`: claude-api, openai, gemini, vertex, openrouter, nvidia,
codex, antigravity, claude-cli, grok, ollama, local-openai), `BrainRequest`/`BrainDelta`, `MeteredBrain`
(usage ledger), `core.config.resolve_provider_endpoint`, protected credential storage. Consumer orchestration, privacy gate and aggregate budget: `consumer_orchestrator.py` (ADR-0050), library only.
Overrides, fallback and per-branch reporting: `engine_routing.py` (ADR-0052), library only. Not built: a normalized
capability catalog beyond what plugins declare (`engine_policy.capabilities_of`), wiring into chat/voice/jobs, UI.
