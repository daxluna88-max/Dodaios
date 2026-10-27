"""Product identity and offline policy for child processes that get a REPLACED environment (R7).

Several launch points build a child environment from an allowlist instead of inheriting
``os.environ`` (mission workers, agent runtimes, isolated MCP servers, the isolated Codex login).
Those children used to lose ``JARVIS_PRODUCT`` / ``JARVIS_PRODUCT_ACTIVE`` / ``JARVIS_DATA_DIR`` /
``JARVIS_CONFIG`` / ``JARVIS_KEYRING_SERVICE`` and ``JARVIS_NETWORK_POLICY``: a Jarvis-aware child
then ran as the default product (shared data dir, shared keys, no offline policy).

``forward_identity(env)`` copies those variables from the parent into ``env`` when the parent has
them. It never invents a value and never overrides one the caller already set. Stdlib only.

Honest limit: forwarding ``JARVIS_NETWORK_POLICY`` only helps a child that installs the guard
(``egress_guard.GUARDED_CHILD_MODULES``); it is not OS enforcement (ADR-0060).
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

#: Who the child is (product, scoped data, config, key service).
IDENTITY_VARS: tuple[str, ...] = (
    "JARVIS_PRODUCT",
    "JARVIS_PRODUCT_ACTIVE",
    "JARVIS_DATA_DIR",
    "JARVIS_CONFIG",
    "JARVIS_KEYRING_SERVICE",
)
#: What the child must obey (strict offline profile and its allow-list).
POLICY_VARS: tuple[str, ...] = ("JARVIS_NETWORK_POLICY", "JARVIS_LOCAL_ALLOWED_HOSTS")
#: Subset safe for UNTRUSTED children (no filesystem layout of the owner's data).
UNTRUSTED_VARS: tuple[str, ...] = ("JARVIS_PRODUCT", "JARVIS_PRODUCT_ACTIVE", *POLICY_VARS)


def forward_identity(
    env: MutableMapping[str, str],
    *,
    source: dict[str, str] | None = None,
    untrusted: bool = False,
) -> MutableMapping[str, str]:
    """Add the parent's identity and policy variables to ``env`` (in place; also returned)."""
    parent = source if source is not None else os.environ
    names = UNTRUSTED_VARS if untrusted else (*IDENTITY_VARS, *POLICY_VARS)
    for name in names:
        value = parent.get(name)
        if value is not None and value != "" and name not in env:
            env[name] = value
    return env


__all__ = ["IDENTITY_VARS", "POLICY_VARS", "UNTRUSTED_VARS", "forward_identity"]
