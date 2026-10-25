# ADR-0046 — Optional boot hook

**Status:** Accepted · **Date:** 2026-10-25

## Context

The tool gate (ADR-0044) and the per-turn memory (ADR-0045) are both installed by an
external integration that needs the event bus and the config. Without one place to do it,
each integration would patch the app's startup.

## Decision

1. `DODAIOS_BOOT_HOOK="package.module:function"` names a callable `(bus, config)`.
2. `_phase2_full_brain()` calls it once, just before returning the manager.
3. Unset: nothing happens. A failing hook is logged at ERROR and does not stop startup,
   but what it would have installed (including the gate) is then absent.
4. `DODAIOS_BOOT_HOOK_REQUIRED=1` makes the integration mandatory: if the hook is unset,
   raises or returns `None`, a `FailClosedGate` refuses every tool call until the app is
   restarted with a working hook.

## Consequences

- The hook runs arbitrary code named by an environment variable: only the owner of the
  process environment can set it.
