"""Optional boot hook for external integrations (ADR-0046).

``DODAIOS_BOOT_HOOK="package.module:function"`` names a callable that receives
``(bus, config)`` once, when the full brain is built. An integration uses it to
install the tool gate (ADR-0044) and the per-turn memory (ADR-0045) from one
place. Unset, nothing happens.
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

ENV_VAR = "DODAIOS_BOOT_HOOK"
REQUIRED_VAR = "DODAIOS_BOOT_HOOK_REQUIRED"


def _required() -> bool:
    return os.environ.get(REQUIRED_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def _fail_closed(why: str) -> None:
    """Required integration is not running: refuse every tool call until it is."""
    from dodaios.safety.gate import FailClosedGate, set_default_gate

    log.error("required boot hook is not active (%s): ALL tool calls are refused", why)
    set_default_gate(FailClosedGate(f"integration not ready: {why}"))


def run_boot_hook(bus: Any, config: Any) -> Any:
    """Run the configured hook; returns its result, or None when unset or failing.

    With ``DODAIOS_BOOT_HOOK_REQUIRED=1`` an unset, failing or ``None``-returning
    hook installs a gate that refuses every tool call (fail closed). Without it a
    failure is logged at ERROR and startup continues, without what the hook
    would have installed.
    """
    spec = os.environ.get(ENV_VAR, "").strip()
    if not spec:
        if _required():
            _fail_closed(f"{ENV_VAR} is not set")
        return None
    try:
        module_name, _, func_name = spec.partition(":")
        if not module_name or not func_name:
            raise ValueError(f"{ENV_VAR} must look like 'package.module:function', got {spec!r}")
        func = getattr(importlib.import_module(module_name), func_name)
        result = func(bus, config)
    except Exception as exc:  # noqa: BLE001 - an integration must not stop the app
        log.error("boot hook %s failed: what it installs is NOT active", spec, exc_info=True)
        if _required():
            _fail_closed(f"{spec} raised {type(exc).__name__}")
        return None
    if result is None and _required():
        _fail_closed(f"{spec} returned nothing")
    return result
