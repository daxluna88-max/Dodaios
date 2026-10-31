"""Product engine policy: which providers a Jarvis product may use (ADR-0049).

Two independent products share this runtime:

* work (PMI / Lavoro): LOCAL inference only. A remote provider is refused at the one door every
  provider instance comes through (``BrainProviderRegistry.instantiate``) and again on every
  ``complete()`` call, so a stale or malicious config cannot route a turn to the cloud. A local
  engine that is missing or busy means an error (or a queue), never a cloud fallback. The same
  door check covers STT/TTS/realtime plugins (``check_service``).
* consumer (or no product set): no restriction from this module. Multi-engine orchestration lives
  in ``dodaios.brain.consumer_orchestrator``.

The product comes from ``JARVIS_PRODUCT`` (``work`` | ``consumer``), read at every check. A
non-empty value this module does not know is treated as ``work``: the strictest policy wins when
the setting is garbled.

"Local" means the provider is ``ollama`` or ``local-openai`` AND its resolved endpoint is this
machine (loopback literal or ``localhost``, no team proxy), or a host the owner listed in
``JARVIS_LOCAL_ALLOWED_HOSTS`` (a LAN GPU box, for example). Host names are never resolved through
DNS. Unknown providers count as remote.

This is a policy gate, NOT a network sandbox: tools, update checks, telemetry and embeddings
outside the brain registry are separate paths (see docs/engine-inventory.md).
"""

from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:  # keep this module stdlib-only at import time: the voice worker imports it too
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
    """Why this LOCAL provider's endpoint is not local; None when it is (or cannot be told)."""
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


# -- destination locality: where a call REALLY goes (Consumer privacy) ----------------------
#
# ``check_provider`` answers "may this product use the provider" and, for Work, accepts a LAN
# host the owner listed. Consumer privacy asks a stricter question: does the context stay on THIS
# machine? Only a loopback destination counts. A provider NAME proves nothing (``ollama`` and
# ``local-openai`` accept any endpoint), a LAN allowlist does not make a host private, and an
# unknown or malformed endpoint is remote.


@dataclass(frozen=True, slots=True)
class Destination:
    host: str | None
    local: bool
    reason: str


def is_loopback_host(host: str | None) -> bool:
    """Loopback literal or ``localhost`` only. Ignores ``JARVIS_LOCAL_ALLOWED_HOSTS``."""
    if not host:
        return False
    host = host.strip().strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:  # any other name is never resolved, so never considered local
        return False


def _instance_root(brain: Any) -> str | None:
    """The endpoint the built adapter really uses (looked through policy/metering wrappers)."""
    seen = 0
    while brain is not None and seen < 4:
        root = getattr(brain, "_server_root", None)
        if isinstance(root, str) and root:
            return root
        brain = getattr(brain, "_inner", None) or getattr(brain, "inner", None)
        seen += 1
    return None


def _configured_root(provider: str) -> tuple[str | None, bool]:
    from dodaios.core import config as cfg  # noqa: PLC0415 - heavy, imported on use

    default = None
    if provider == "ollama":
        from dodaios.plugins.brain.ollama import default_server_root  # noqa: PLC0415

        default = default_server_root()
    ep = cfg.resolve_provider_endpoint(provider, vendor_default_base_url=default)
    return (ep.base_url or None), bool(getattr(ep, "via_proxy", False))


def destination_of(provider: str, brain: Any = None) -> Destination:
    """Whether a call to ``provider`` stays on this machine. Fails closed (remote)."""
    if provider not in LOCAL_PROVIDERS:
        return Destination(None, False, f"{provider} is a remote provider")
    try:
        configured, via_proxy = _configured_root(provider)
    except Exception as exc:  # noqa: BLE001 - an unreadable endpoint is an unknown one: remote
        log.debug("endpoint of %s could not be read: %s", provider, type(exc).__name__)
        return Destination(None, False, "the endpoint could not be read")
    if via_proxy:
        return Destination(None, False, f"{provider} is routed through the team proxy")
    root = _instance_root(brain) or configured
    if not root:
        return Destination(None, False, "no endpoint is configured")
    try:
        host = urlsplit(root if "//" in root else f"//{root}").hostname
    except ValueError:
        log.debug("endpoint of %s is not a valid URL: treated as remote", provider)
        return Destination(None, False, "the endpoint is not a valid URL")
    if not is_loopback_host(host):
        return Destination(host, False, f"endpoint host {host!r} is not this machine")
    bound = _instance_root(brain)
    if bound and configured and bound.rstrip("/") != configured.rstrip("/"):
        return Destination(host, False, "the endpoint changed after the engine was built")
    return Destination(host, True, "loopback")


# -- speech and realtime plugins: the same door for the Work product ----------------------

#: Local-only speech/realtime plugins, by group slot. Anything else in these groups is a
#: cloud service. ``local-realtime`` (a self-hosted URL the owner types in) is deliberately
#: not listed: its endpoint is not checked here, so it stays off until that check exists.
LOCAL_SERVICES: dict[str, frozenset[str]] = {
    "stt": frozenset({"faster-whisper", "nemotron-local"}),
    "tts": frozenset({"piper-local"}),
    "realtime": frozenset({"local-voice"}),
}


def check_service(group: str, name: str, *, product: str | None = None) -> None:
    """Raise when a speech/realtime plugin is a cloud service and the product is Work.

    ``group`` is the entry-point group under either spelling (``jarvis.stt`` / ``dodaios.stt``).
    Groups other than stt/tts/realtime are not services and pass.
    """
    product = product if product is not None else current_product()
    slot = group.partition(".")[2]
    if product != "work" or slot not in LOCAL_SERVICES:
        return
    if name not in LOCAL_SERVICES[slot]:
        raise EnginePolicyError(
            f"{slot.upper()} plugin {name!r} is a cloud service: the Work product allows only "
            f"{', '.join(sorted(LOCAL_SERVICES[slot]))}.",
            code="remote_service_in_local_only",
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
    "Destination",
    "EngineSelection",
    "LOCAL_PROVIDERS",
    "PolicyBrain",
    "capabilities_of",
    "LOCAL_SERVICES",
    "check_provider",
    "check_service",
    "current_product",
    "destination_of",
    "endpoint_problem",
    "enforce",
    "guard_stream",
    "host_is_local",
    "is_loopback_host",
    "select_engine",
]
