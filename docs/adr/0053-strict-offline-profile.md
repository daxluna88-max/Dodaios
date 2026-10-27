# ADR-0053 — Strict offline profile: a network-negative guard for the Work product

**Status:** Accepted (opt-in) · **Date:** 2026-10-26

## Context

ADR-0049 limits which engines Work may choose, but "local inference only" is also a statement about what leaves the
machine: tools, update checks, telemetry, embeddings and any plugin nobody audited sit outside the provider doors.
The brief asks for network-negative tests, not hidden UI options.

## Decision

`dodaios/core/egress_guard.py` (stdlib only), enabled by `JARVIS_NETWORK_POLICY=local-only` and installed at the top
of `dodaios.__main__.main`:

1. A connect, `connect_ex` or UDP `sendto` to anything but loopback (127.0.0.0/8, `::1`), a Unix socket or a host in
   `JARVIS_LOCAL_ALLOWED_HOSTS` raises `EgressBlocked` (an `OSError`, so callers see an ordinary connection failure)
   BEFORE any packet is sent. `getaddrinfo` / `gethostbyname(_ex)` of a non-local name are refused too, so names do
   not leak through DNS. Blocked attempts are counted and their targets (never any payload) recorded.
2. Any unknown policy value ends startup with exit code 2: a typo must not leave the network open.
3. It is opt-in: a strict offline profile also disables GitHub, browser and mail tools. The product doors of
   ADR-0049 stay the default protection for Work.

## Settings

The profile can also be chosen without environment variables: `[network] policy = "off" | "local-only"` in the config
file (`config_writer.set_network_policy`), read at process start; the environment value wins. Settings > General >
Network has the switch (`GET/PUT /api/network-policy`). It shows what the running process enforces (never the switch
position alone), the source that won, the blocked attempts, and says when a restart is needed. An unknown value in the
file ends startup like an unknown environment value.

## Not decided here

It is not a sandbox: a native extension that opens sockets without Python's `socket` module and child processes that
do not install the guard are not covered (the voice engine worker only ever talks to loopback by design). The guard
is not installed by default for Work, and no Work install selects it yet; the Local voice card has no switch for it.

See ADR-0060 for the function coverage registry (uncoverable tools and children are refused) and the design of
OS-level enforcement, which is not built.

## Consequences

With the guard on, a representative Work turn (refused remote engine, local engine answering) makes zero outbound
attempts, and any path that would leak fails loudly. Proven with real sockets and the real entry point.

## Block 8: review fixes R7

Review finding (high): under the profile an unclassified tool name returned "allowed", and replaced child environments
dropped product identity and policy. Written, not run: no test, build or service was executed in this block.

1. **Default deny.** `egress_guard.tool_refusal(name, tool=None)` now refuses anything that is not positively
   classified as no-network. A tool passes only when it is in `OFFLINE_SAFE_TOOLS` (first-party local UI/read tools,
   read by hand for network/process imports, not run) or the registry declared it with
   `declare_tool_coverage(name, "none")`, or (first-party class only, module under `dodaios.`) it has
   `network_capability = "none"` / `offline_safe = True`. A name in a refusal class is refused whatever it declares; an
   unknown capability value counts as unknown (refused). `tool_capability()` exposes the verdict.
2. **Harness and skill runner call sites (other agent).** `plugins/harness/mcp_remote.py`, `skills/runner.py` and
   `safety/tool_executor.py` still call `tool_refusal(name)` with the name only: they must pass the tool object
   (`tool_refusal(name, tool)`) so its declared capability is consulted, and the plugin/marketplace registry must call
   `declare_tool_coverage` for tools it vetted. Until then a third-party tool is simply refused under the profile.
3. **Child identity.** New `dodaios/core/child_identity.py` (`forward_identity`) copies `JARVIS_PRODUCT`,
   `JARVIS_PRODUCT_ACTIVE`, `JARVIS_DATA_DIR`, `JARVIS_CONFIG`, `JARVIS_KEYRING_SERVICE`, `JARVIS_NETWORK_POLICY` and
   `JARVIS_LOCAL_ALLOWED_HOSTS` into a replaced environment. Applied in `build_worker_env` (mission workers),
   `agent_runtimes.base.child_env` (agent runtimes, openclaw, hermes), `codex_auth` isolated environment and
   `mcp.env_isolation.isolated_environment` (untrusted servers get product and policy only, not the data layout).
   Environments that copy all of `os.environ` already carry them. Not audited one by one: `agent_chat` CLI runners,
   `voice_engine` worker, terminal/PTY and `agentic_ide` spawns (they inherit or overlay `os.environ`).

Not claimed: this is NOT full offline. A forwarded `JARVIS_NETWORK_POLICY` only helps a child that installs the guard
(`GUARDED_CHILD_MODULES` is still empty); there is still no OS enforcement (ADR-0060 is design only).
