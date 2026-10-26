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

## NOT gated (open work; Work must not be called fully local until these are)

- Realtime/live voice plugins that talk to a vendor directly: `plugins/realtime/openai_live.py`,
  `openai_subscription_live.py`, Gemini Live (`core/google_genai.py`, `live/subscription_auth.py`).
- Cloud STT (`plugins/stt/openai_api.py`) and any cloud TTS/embedding client outside the brain registry.
- Dictation polish (`dictation/polish_client.py`): it has its own local-only chain; not verified against this policy.
- Subscription CLIs (`codex`, `claude-cli`, `antigravity`) are classified remote and refused through the registry;
  `codex_app_server.py` and other direct launchers were not audited.
- Tools (browser, GitHub, email), update checks and telemetry: a strict offline profile is a separate item.
- `get_class` callers that only probe metadata (`subscription_connected`, signatures) do not run inference.

## Local live voice

`voice_engine/llm.py` streams from a local Ollama server over HTTP directly (`DEFAULT_BASE_URL`
`http://127.0.0.1:11434`), bypassing `BrainManager`. A chat provider dropdown therefore does NOT drive local live
voice. Routing live voice through a selectable provider adapter (and checking its base URL against the policy)
is not done.

## Reuse points for the next steps

`BrainProviderRegistry` (entry points `jarvis.brain`: claude-api, openai, gemini, vertex, openrouter, nvidia,
codex, antigravity, claude-cli, grok, ollama, local-openai), `BrainRequest`/`BrainDelta`, `MeteredBrain`
(usage ledger), `core.config.resolve_provider_endpoint`, protected credential storage. Not built: a normalized
capability catalog beyond what plugins declare (`engine_policy.capabilities_of`), Consumer parallel
orchestration, privacy gate, aggregate budget reservation.
