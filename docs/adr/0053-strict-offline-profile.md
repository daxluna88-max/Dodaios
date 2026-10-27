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

## Block 9: GPT round 2 (F1, F2, F4)

F1 is in ADR-0050. Written, NOT run: no test, build, CI or service was executed.

**F2 (R7, allowlist by name is impersonable): fixed in code; partial for callers.**
Files: `dodaios/core/egress_guard.py` (`OFFLINE_SAFE_IMPLEMENTATIONS`, `_first_party_identity`, `tool_capability`,
`declare_tool_coverage`).

* The allowlist is now a table `tool name -> {"module:qualname"}` of first-party classes (`dodaios.plugins.tool.*`,
  `dodaios.agent_screen.tools`). `OFFLINE_SAFE_TOOLS` remains as the name list for documentation and grants nothing.
* `tool_capability(name, tool)` gives `none` only if `tool` is a verified first-party class: its module starts with
  `dodaios.`, the class object IS the object found at that qualname in `sys.modules[module]`, and that module's file lies
  inside the package directory (a plugin that sets `__module__ = "dodaios.x"` fails the identity check), and the identity
  is in the table for that name. A homonymous third-party object (`whoami`, `wiki-list`, `click`) is `unknown` (refused).
* A caller that passes only a name gets NO allowlist credit (unavailable). `declare_tool_coverage(name, "none", tool)` is
  bound to the vetted class identity; without `tool` a `none` declaration is stored as unknown; a different class under the
  same name is refused. Refusal classes by name still win.
* Consequence: `safety/preflight.py` and any other name-only call site now refuse allowlisted tools until they pass the
  tool object (other agents own those files; not touched). This is fail closed, as requested.
* Residual: the table is hand-maintained and the "no network/process" judgement is by reading source, not by running; the
  registry-vetted path for third parties still trusts the registry; a first-party class that later gains network use stays
  allowed until the table is revisited.

**F4 (child identity kept any pre-set value): fixed in code; enforcement is limited.**
Files: `dodaios/core/child_identity.py` (`forward_identity`, `enforce_identity`, `ChildIdentityConflict`) and the launchers
`dodaios/agent_runtimes/base.py`, `dodaios/mcp/env_isolation.py`, `dodaios/terminal/pty_manager.py`
(`_interactive_child_env`), `dodaios/voice_engine/client.py` (`worker_env`), `dodaios/agentic_ide/session.py`
(`_without_parent_agent_session`), `dodaios/agent_chat/runner_cli.py` (CLI turn plan, before spawn).

* A value already in the child env is kept only if equal to the parent's (paths normalised, product/policy
  case-insensitive); otherwise `ChildIdentityConflict` is raised (fail closed). Absent -> set from the parent. Parent
  absent and child set is also a conflict, except a stricter `JARVIS_NETWORK_POLICY`.
* Order fixed: the check runs AFTER the caller's overlay (`child_env(extra)`, MCP `overrides`, voice `extra`), otherwise the
  overlay could still override.
* Launchers: agent_chat CLI runner, voice_engine worker, PTY (replacement env), agentic_ide pane env: applied. Documented as
  NOT applied: `agent_chat/turn_host.py` (receives an env frame in a separate host process; relies on the caller's check),
  the resumed-turn plan built from `{"CODEX_HOME"}`, `pty_host_client` host start (copies `os.environ`, same identity),
  `missions/isolation/env.py` and `codex_auth` (allowlist fill only; no overlay after it), and git helper envs.
* Transferring a variable is not enforcement. A child honours `JARVIS_NETWORK_POLICY` only if it reads it and installs the
  guard (`GUARDED_CHILD_MODULES` is still empty); a CLI vendor binary ignores all of it. There is no OS enforcement
  (ADR-0060). A raised conflict can stop a launch: that is intended.

Controls prepared, NOT RUN (`tests/test_block9_gpt_round2.py`): homonymous plugin objects named `whoami`/`wiki-list`/`click`
refused, name-only refused, real first-party class accepted, spoofed `__module__` refused; env override conflicts raise
for product, data dir and network policy while equal values and absent values pass.
