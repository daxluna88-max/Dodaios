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

## Consequences

With the guard on, a representative Work turn (refused remote engine, local engine answering) makes zero outbound
attempts, and any path that would leak fails loudly. Proven with real sockets and the real entry point.
