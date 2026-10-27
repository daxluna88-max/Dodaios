"""Product identity and offline policy for child processes that get a REPLACED environment (R7).

Several launch points build a child environment from an allowlist instead of inheriting
``os.environ`` (mission workers, agent runtimes, isolated MCP servers, the isolated Codex login).
Those children used to lose ``JARVIS_PRODUCT`` / ``JARVIS_PRODUCT_ACTIVE`` / ``JARVIS_DATA_DIR`` /
``JARVIS_CONFIG`` / ``JARVIS_KEYRING_SERVICE`` and ``JARVIS_NETWORK_POLICY``: a Jarvis-aware child
then ran as the default product (shared data dir, shared keys, no offline policy).

``forward_identity(env)`` copies those variables from the parent into ``env`` when the parent has
them. It never invents a value; since block 9 a conflicting value already in ``env`` raises
``ChildIdentityConflict`` instead of being kept. Stdlib only.

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


class ChildIdentityConflict(RuntimeError):
    """The child's environment names a different identity/policy than the parent (fail closed)."""


def _same(name: str, a: str, b: str) -> bool:
    if name in ("JARVIS_DATA_DIR", "JARVIS_CONFIG"):
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
    if name in ("JARVIS_NETWORK_POLICY", "JARVIS_PRODUCT", "JARVIS_PRODUCT_ACTIVE"):
        return a.strip().lower() == b.strip().lower()
    return a.strip() == b.strip()


def forward_identity(
    env: MutableMapping[str, str],
    *,
    source: dict[str, str] | None = None,
    untrusted: bool = False,
) -> MutableMapping[str, str]:
    """Set the parent's identity and policy variables in ``env`` (in place; also returned).

    Block 9 (F4): fail closed. A value already in ``env`` is kept ONLY when it equals the
    parent's; a different one raises :class:`ChildIdentityConflict` (it is never presumed right).
    An absent one is set from the parent. When the parent has none, a child value is a conflict,
    except a stricter ``JARVIS_NETWORK_POLICY`` (the child may add the offline profile).
    Transferring a variable is not enforcement: only a child that reads it and installs the guard
    obeys it.
    """
    parent = source if source is not None else os.environ
    names = UNTRUSTED_VARS if untrusted else (*IDENTITY_VARS, *POLICY_VARS)
    for name in names:
        value = parent.get(name)
        own = env.get(name)
        parent_has = value is not None and value != ""
        child_has = own is not None and own != ""
        if parent_has and not child_has:
            env[name] = str(value)
        elif parent_has and child_has:
            if not _same(name, str(own), str(value)):
                raise ChildIdentityConflict(
                    f"child environment sets {name} to a value that differs from the parent's; "
                    "refusing to start the child under another identity or policy"
                )
        elif child_has and name != "JARVIS_NETWORK_POLICY":
            raise ChildIdentityConflict(
                f"child environment sets {name} but the parent has none; refusing to presume it"
            )
    return env


def enforce_identity(env: MutableMapping[str, str]) -> MutableMapping[str, str]:
    """For launchers that copy ``os.environ`` and then overlay caller keys: validate + fill."""
    return forward_identity(env)


__all__ = [
    "IDENTITY_VARS",
    "POLICY_VARS",
    "UNTRUSTED_VARS",
    "ChildIdentityConflict",
    "enforce_identity",
    "forward_identity",
]
