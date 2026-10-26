"""Product engine policy: which brain providers a Jarvis product may use (ADR-0049).

Two independent products share this runtime:

* **work** (PMI / Lavoro): LOCAL inference only. A remote provider is refused at the one door every
  provider instance comes through (:meth:`BrainProviderRegistry.instantiate`) and again on every
  ``complete()`` call, so a stale or malicious config cannot route a turn to the cloud. A local
  engine that
  is missing or busy means an error (or a queue), never a cloud fallback.
* **consumer** (or no product set): no restriction from this module. Multi-engine orchestration, the
privacy
  gate and aggregate budgets are NOT implemented here.

The product comes from ``JARVIS_PRODUCT`` (``work`` | ``consumer``), read at every check. A
non-empty value
this module does not know is treated as ``work``: the strictest policy wins when the setting is
garbled.

"Local" means the provider is ``ollama`` or ``local-openai`` AND its resolved endpoint is this
machine
(loopback literal or ``localhost``, no team proxy), or a host the owner listed in
``JARVIS_LOCAL_ALLOWED_HOSTS`` (a LAN GPU box, for example). Host names are never resolved through
DNS.
Unknown providers count as remote.

This is a policy gate on brain providers. It is NOT a network sandbox: tools, update checks,
telemetry, STT/TTS
and embeddings outside the brain registry are separate paths (see docs/engine-inventory.md).
"""

from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from dodaios.core.protocols import BrainDelta, BrainRequest

log = logging.getLogger(__name__)

PRODUCTS = ("work", "consumer")
LOCAL_PROVIDERS = frozenset({"ollama", "local-openai"})
ALLOWED_HOSTS_ENV = "JARVIS_LOCAL_ALLOWED_HOSTS"


class EnginePolicyError(RuntimeError):
    """The selected engine is not allowed (or not usable) for this product."""

    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.code = code


def current_product(env: dict[str, str] | None = None) -> str | None:
    raw = ((env if env is not None else os.environ).get("JARVIS_PRODUCT") or "").strip().lower()
    if not raw:
        return None
    if raw not in PRODUCTS:
        log.error("JARVIS_PRODUCT=%r is unknown: applying the strictest (work) engine policy", raw)
        return "work"
    return raw


def _allowed_hosts(env: dict[str, str] | None = None) -> frozenset[str]:
    raw = (env if env is not None else os.environ).get(ALLOWED_HOSTS_ENV, "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def host_is_local(host: str | None, env: dict[str, str] | None = None) -> bool:
    """Loopback literal, ``localhost`` or an explicitly allowed host. No DNS lookups."""
    if not host:
        return False
    host = host.strip().strip("[]").lower()
    if host in _allowed_hosts(env):
        return True
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:  # not an IP literal: any other name is never considered local
        return False


def endpoint_problem(provider: str) -> str | None:
    """Why this LOCAL provider's configured endpoint is not local; None when it is (or cannot be
    told)."""
    from dodaios.core import config as cfg  # noqa: PLC0415 - heavy, imported on use

    default = None
    if provider == "ollama":
        from dodaios.plugins.brain.ollama import default_server_root  # noqa: PLC0415

        default = default_server_root()
    ep = cfg.resolve_provider_endpoint(provider, vendor_default_base_url=default)
    if getattr(ep, "via_proxy", False):
        return f"{provider} is routed through the team proxy"
    if not ep.base_url:
        return None  # nothing configured: the plugin itself refuses to run without an endpoint
    host = urlsplit(ep.base_url if "//" in ep.base_url else f"//{ep.base_url}").hostname
    if not host_is_local(host):
        return f"{provider} endpoint host {host!r} is not this machine"
    return None


def check_provider(
    provider: str, *, product: str | None = None, check_endpoint: bool = True
) -> None:
    """Raise :class:`EnginePolicyError` when ``provider`` is not allowed for the product."""
    product = product if product is not None else current_product()
    if product != "work":
        return
    if provider not in LOCAL_PROVIDERS:
        raise EnginePolicyError(
            f"Provider {provider!r} is remote: the Work product runs local models only "
            f"({', '.join(sorted(LOCAL_PROVIDERS))}). Choose a local engine.",
            code="remote_in_local_only",
        )
    if check_endpoint:
        problem = endpoint_problem(provider)
        if problem:
            raise EnginePolicyError(
                f"{problem}: the Work product accepts only an endpoint on this machine "
                f"(or a host listed in {ALLOWED_HOSTS_ENV}).",
                code="remote_endpoint",
            )


# -- capabilities: unknown means unknown, never "supported"
# -------------------------------------------------


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    streaming: bool | None = None
    tools: bool | None = None
    vision: bool | None = None
    structured_output: bool | None = None
    context_window: int | None = None
    max_output: int | None = None


def _flag(obj: Any, *names: str) -> bool | None:
    for name in names:
        value = getattr(obj, name, None)
        if callable(value):
            try:
                value = value()
            except Exception:  # noqa: BLE001 - a broken probe means "unknown"
                log.debug("capability probe %s failed: treated as unknown", name, exc_info=True)
                value = None
        if isinstance(value, bool):
            return value
    return None


def _number(obj: Any, *names: str) -> int | None:
    for name in names:
        value = getattr(obj, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def capabilities_of(brain: Any) -> EngineCapabilities:
    """What the provider object DECLARES. Vendor identity alone establishes nothing."""
    return EngineCapabilities(
        streaming=_flag(brain, "supports_streaming"),
        tools=_flag(brain, "can_call_tools", "supports_tools"),
        vision=_flag(brain, "supports_vision"),
        structured_output=_flag(brain, "supports_structured_output"),
        context_window=_number(brain, "context_window"),
        max_output=_number(brain, "max_output_tokens"),
    )


@dataclass(frozen=True, slots=True)
class EngineSelection:
    provider: str
    model: str | None
    brain: Any
    capabilities: EngineCapabilities
    local: bool


def select_engine(
    registry: Any,
    provider: str,
    model: str | None = None,
    *,
    needs_tools: bool = False,
    needs_vision: bool = False,
    product: str | None = None,
) -> EngineSelection:
    """Explicit provider/model selection. Fails with an actionable reason; never falls back
    silently."""
    product = product if product is not None else current_product()
    check_provider(provider, product=product)
    if provider not in registry.available():
        failed = registry.failed().get(provider)
        raise EnginePolicyError(
            f"Provider {provider!r} is not available" + (f" ({failed})" if failed else "") + ".",
            code="unavailable",
        )
    brain = registry.instantiate(provider, model=model) if model else registry.instantiate(provider)
    caps = capabilities_of(brain)
    for needed, value, label in (
        (needs_tools, caps.tools, "tool calls"),
        (needs_vision, caps.vision, "images"),
    ):
        if needed and value is not True:
            state = "does not support" if value is False else "has not declared support for"
            raise EnginePolicyError(
                f"Engine {provider!r}{f' / {model}' if model else ''} {state} {label}; "
                "pick another engine for this work.",
                code="capability_missing" if value is False else "capability_unknown",
            )
    return EngineSelection(provider, model, brain, caps, provider in LOCAL_PROVIDERS)


# -- turn ownership: late output from an old generation must not reach anyone
# --------------------------------


async def guard_stream(
    stream: AsyncIterator[BrainDelta], is_current: Callable[[], bool]
) -> AsyncIterator[BrainDelta]:
    """Yield deltas only while ``is_current()``. The first stale chunk ends the stream and closes
    the source,
    so a slow, cancelled provider cannot speak, animate, change history or trigger tools
    afterwards."""
    try:
        async for delta in stream:
            if not is_current():
                return
            yield delta
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


class PolicyBrain:
    """Re-checks the product policy on EVERY call, so a config changed after construction cannot
    reroute."""

    __slots__ = ("_inner", "_provider_name")

    def __init__(self, inner: Any, provider_name: str) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_provider_name", provider_name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)

    @property
    def unwrapped(self) -> Any:
        return self._inner

    @property  # type: ignore[misc]
    def __class__(self) -> type:  # type: ignore[override]
        return (
            self._inner.__class__
        )  # the metered wrapper reports the plugin class: keep that answer

    def estimate_cost(self, req: BrainRequest) -> float:
        return float(self._inner.estimate_cost(req))

    def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        check_provider(self._provider_name)
        return self._inner.complete(req)


def enforce(brain: Any, provider_name: str) -> Any:
    """Wrap only when a restrictive product policy is active, so other installs keep their exact
    objects."""
    return PolicyBrain(brain, provider_name) if current_product() == "work" else brain


__all__ = [
    "EngineCapabilities",
    "EnginePolicyError",
    "EngineSelection",
    "LOCAL_PROVIDERS",
    "PolicyBrain",
    "capabilities_of",
    "check_provider",
    "current_product",
    "endpoint_problem",
    "enforce",
    "guard_stream",
    "host_is_local",
    "select_engine",
]
