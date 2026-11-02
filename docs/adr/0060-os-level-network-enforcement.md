# ADR-0060 — OS-level network enforcement for the strict offline profile

**Status:** Proposed (design only) · **Date:** 2026-10-27

**Nothing in this ADR is implemented at operating-system level.** Only the code side described under
"Implemented now" exists, and it has not been run.

## Context

ADR-0053's guard patches Python's `socket` module in one process. It cannot prove "offline": child processes that do
not install it (CLIs, browsers, ffmpeg, MCP servers, installers), native code that opens sockets itself, and non-Python
tools are outside it. A claim of "offline" needs enforcement the app cannot bypass by launching something else.

## Options per platform (all unbuilt)

| Platform | Mechanism | Children | Privileges | Gaps |
|---|---|---|---|---|
| Linux | Run the app (or each child) in a network namespace with only loopback up (`unshare -n`, `ip link set lo up`); or nftables `meta skuid`/cgroup rules dropping non-loopback output | Namespace and cgroup membership are inherited by every child | `CAP_SYS_ADMIN` or unprivileged user namespaces (`unshare -Ur -n`, may be disabled by the distro); nftables needs root | Container/sandbox hosts may forbid it; processes started by other daemons (D-Bus activation, systemd user units) are outside the tree |
| macOS | App Sandbox without `com.apple.security.network.client` (kernel-enforced, inherited by children); or a `pf` anchor | Sandbox is inherited by children; `pf` is per-host, not per-process | Entitlements at signing time; `pf` needs root | Sandboxing the whole app breaks its other features; `pf` rules are system-wide and need admin consent |
| Windows | AppContainer without `internetClient`/`privateNetworkClient` capabilities (children inherit the token); or Windows Firewall/WFP filter bound to the app's package or path | Child processes inherit the AppContainer token; a Job Object alone does not restrict network | Firewall/WFP rules need admin; AppContainer launch needs a manifest/packaging | A Job Object cannot block network; path-based firewall rules miss renamed or copied binaries |

Loopback must stay open (local engines, the app's own UI and voice workers).

## What cannot be enforced

- Hosts where the user denies the privilege, or where the platform forbids it (unprivileged user namespaces off,
  no admin rights, managed Windows).
- Anything outside the enforced tree: other processes on the machine, system services, hardware radios.
- Local-network or loopback proxies that forward elsewhere (a local engine or MCP server that itself calls out). The
  filter sees loopback as allowed; only the destination program's own enforcement stops that.
- Side channels: data written to disk or removable media, or a user copying content out.

## Verification: observed, not assumed

The app may say "network blocked by the operating system" only after a probe it ran itself, in the enforced context,
succeeded in being refused:

1. Start a probe child inside the same enforcement as the real children (same namespace/sandbox/token).
2. From it, try a TCP connect to a public address by IP literal (no DNS) and a UDP send; both must fail with a
   network-level error, not an `EgressBlocked` raised by our own Python guard (the probe runs with the Python guard
   OFF, so a refusal can only come from the OS).
3. The same probe must still reach loopback (otherwise the enforcement is broken, not working).
4. The result is stored with the time, platform and mechanism; it expires at restart. Any failure leaves the flag false.

`egress_guard.os_enforcement_verified()` returns `False` always until this exists.

## What the screen may claim

- Python guard on: "Connections from this app's Python code to other computers are refused." Nothing more.
- Listing the unavailable function classes (shell, command-line tools, browser, agents, web/mail/online services, MCP).
- Only with a passed probe: "The operating system blocked an outside connection from a test process on <date>."
  Never "offline", "air-gapped" or "nothing leaves" without qualification, and never for what the probe did not cover.

## Implemented now (code side, unverified)

`dodaios/core/egress_guard.py`: a registry of function classes and `tool_refusal`, `child_refusal`, `require_child`,
`child_is_covered`, `child_environment`, `coverage()`; `status()` reports `python_guard_enforced`, a constant-false
`os_enforcement_verified`, the unavailable classes and what is unclassified.

Under the profile a function is refused unless proven covered. The only proof accepted is a Python child run as
`python -m <module>` with this interpreter, the module listed in `GUARDED_CHILD_MODULES` (empty today) and
`JARVIS_NETWORK_POLICY=local-only` in its environment.

Wired at: `ToolExecutor.execute` and the skill runner (tools by name), worker subprocesses
(`create_worker_subprocess`), MCP stdio servers, `CliTool`, `run_shell`, agent-runtime warm processes and installers,
the Codex app server.

## Not covered by the code side

Unwired launch points: `agent_runtimes/openclaw.py`, `clis/auth.py`, `clis/installer.py`, `clis/prober.py`,
`terminal/pty_host_client.py`, `ui/browser_embed.py`, `plugins/tool/open_app.py`, `google_calendar_rest.py`
(node bot), git users in `agentic_ide/*` (fetch/push/clone), `computers/*`, `society/browser/*`, `ffmpeg` users,
`mcp/bootstrap.py`. Tool classification is by name, not audited per tool. Plugin/marketplace tools with other names
are covered by the socket guard only. Remote MCP transports (SSE/HTTP) rely on the socket guard.

## Consequences

The profile gets stricter and less convenient; the honest limit stays visible. A later change implements the OS
mechanisms and the probe; until then the screen must keep saying OS enforcement is not verified.
