# ADR-0049 — Product engine policy: Work runs local models only

**Status:** Accepted · **Date:** 2026-10-26

## Context

The owner runs two independent Jarvis products on this runtime. **Work** (PMI) must use locally running models
only, for every turn, helper and fallback. **Consumer** may use several connected engines. A dropdown cannot
guarantee that: provider instances are built in several places, a stale config can still name a remote provider,
and a local provider's endpoint can point at another host or the team proxy.

## Decision

1. `dodaios/brain/engine_policy.py` reads `JARVIS_PRODUCT` (`work` | `consumer`) at every check. An unknown
   non-empty value is treated as `work` (strictest wins).
2. Under `work`, only `ollama` and `local-openai` are allowed, and only with an endpoint on this machine
   (loopback literal or `localhost`, no team proxy) or on a host listed in `JARVIS_LOCAL_ALLOWED_HOSTS`.
   Host names are never resolved. Unknown providers count as remote.
3. The check runs in `BrainProviderRegistry.instantiate` BEFORE the class is built (no credential read, no
   connection) and again on every `complete()` through `PolicyBrain`, so a config changed after construction
   cannot reroute a turn. The wrapper keeps `isinstance` answers and attributes of the plugin.
4. Three paths that build a remote client without the registry now call the same check: `build_brain`
   (agent chat), `agent_runtimes.gateway._new_brain` and the critic runner.
5. A missing local engine or model is an error (`unavailable`), never a cloud fallback.
6. `select_engine` is the explicit provider/model selection: it fails with an actionable reason and treats an
   undeclared capability as unsupported (`capability_unknown`), never inferring it from the vendor name.
7. `guard_stream` drops deltas (text and tool calls) of a superseded generation and closes the source stream.

8. The same product check guards the other model-calling doors: `core.registry.load` refuses cloud `jarvis.stt`,
   `jarvis.tts` and `jarvis.realtime` plugins under Work (allowed: `faster-whisper`, `nemotron-local`, `piper-local`,
   `local-voice`); the STT class lookup returns None so the builder falls back to the local recognizer; dictation
   polish returns on-device families only and ignores a cloud pin; the local voice engine's Ollama client refuses a
   non-local host (stdlib-only copy of the rule: the engine may not import the app).

Consumer and an unset product are untouched: no restriction and no wrapper.

## Not decided here (open, see docs/engine-inventory.md)

Consumer orchestration is ADR-0050. Still open: live voice routed through a selectable provider adapter,
`local-realtime` (self-hosted URL, endpoint not checked, so it is refused under Work), tool/update/telemetry egress,
embeddings and vision paths not reached through these doors, and a network-negative end-to-end offline profile.
Work is therefore NOT yet certified offline: it is enforced at the doors listed above, and each has a test.

## Consequences

- Under `work`, a remote provider selected by stale config fails with `EnginePolicyError` and opens no connection.
- The per-call check is cheap for the common case (a set lookup; the endpoint is resolved from cached config).
