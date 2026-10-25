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


def run_boot_hook(bus: Any, config: Any) -> Any:
    """Run the configured hook; returns its result, or None when unset or failing.

    A failing hook is logged at ERROR and never stops the app from starting: but
    note that the gate it would have installed is then absent.
    """
    spec = os.environ.get(ENV_VAR, "").strip()
    if not spec:
        return None
    try:
        module_name, _, func_name = spec.partition(":")
        if not module_name or not func_name:
            raise ValueError(f"{ENV_VAR} must look like 'package.module:function', got {spec!r}")
        func = getattr(importlib.import_module(module_name), func_name)
        return func(bus, config)
    except Exception:  # noqa: BLE001 - an integration must not stop the app
        log.error("boot hook %s failed: gate and memory it installs are NOT active", spec, exc_info=True)
        return None
