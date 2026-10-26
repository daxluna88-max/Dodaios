# ADR-0051 — Live voice answered by an app-side provider (LLM bridge)

**Status:** Accepted (config key only; no UI) · **Date:** 2026-10-26

## Context

The local voice engine (ADR-0037) answers with a local Ollama server reached from its own worker process. A chat
provider dropdown therefore never drove live voice, and the Work policy (ADR-0049) could only check the Ollama
host. The Consumer product needs to choose another engine for voice while VAD, STT, TTS, barge-in and the tool
loop stay local.

## Decision

1. Three additive frames on protocol v1: worker -> app `llm.request` (`id`, `session`, `messages`, `tools`) and
   `llm.cancel`; app -> worker `llm.delta` (`id`, then `content` / `tool_call` / `usage` / `finish` / `error`).
2. Worker: `voice_engine/bridge_llm.BridgeChat` implements the engine's `ChatModel` over those frames (stdlib
   only). It cuts text into clauses as deltas arrive (first word does not wait for the whole answer), returns
   tool calls as data, sends `llm.cancel` on stop or timeout (first delta 60 s, idle 30 s, total 180 s) and accepts
   a delta only for a pending call id: a late frame of a cancelled or finished call is counted and dropped.
3. App: `realtime/llm_bridge.BrainLlmBridge` answers each request with a provider built by
   `BrainProviderRegistry.instantiate`, so the product policy of ADR-0049 applies there (Work reaches only local
   providers; a refused provider returns an error delta, never a connection). The engine's chat history is
   converted to a normalized `BrainRequest`; one task per call id; cancel closes the stream and sends nothing more.
4. Tool calls still go through the engine's `tool.call` and the ToolExecutor gate; a provider switch never
   approves or executes anything. The engine passes `session=` only to models that ask (`needs_session`).
5. Config: `[voice_engine].llm_source = "ollama"` (default, unchanged behaviour) or `"brain"` with `llm_provider`
   and `llm_provider_model`. `EngineSettings` forwards `llm.source = "bridge"` in `configure`.

## Not decided here

A settings UI and translations for the source, a provider capability check at selection time (a text-only provider
with tools fails at call time), mid-session switching (the source is fixed at engine start; a change needs an
engine restart), measured latency of cloud providers for voice, audio-native providers, and the privacy gate
(ADR-0050) in front of a remote provider chosen for voice.

## Consequences

Default installs behave exactly as before. A Consumer install can point voice at any registry provider; a Work
install can only point it at a local one. The extra hop (worker -> app -> provider) adds a pipe round trip per
model call that has not been measured.
