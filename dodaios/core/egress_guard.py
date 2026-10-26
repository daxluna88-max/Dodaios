"""Strict offline profile: refuse outbound connections to anything but this machine (ADR-0053).

``JARVIS_NETWORK_POLICY=local-only`` installs a process-wide guard on the Python socket layer: a
connect to a non-local address and a DNS lookup of a non-local name raise :class:`EgressBlocked`
(an ``OSError``, so every caller treats it as an ordinary connection failure) BEFORE any packet
leaves. Allowed: loopback (127.0.0.0/8, ``::1``), Unix sockets, and the hosts the owner lists in
``JARVIS_LOCAL_ALLOWED_HOSTS``.

This is the network-negative proof the product policy (ADR-0049) needs: the provider doors say
which engines Work may choose, this guard shows that nothing else leaves either (tools, update
checks, telemetry, embeddings, a plugin nobody audited). It is opt-in because a strict offline
profile also disables GitHub, browser and mail tools.

What it is NOT: a sandbox. A native extension that opens sockets without Python's ``socket``
module, and any child process that does not install the guard itself, are not covered. Stdlib
only.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
from typing import Any

log = logging.getLogger(__name__)

POLICY_ENV = "JARVIS_NETWORK_POLICY"
ALLOWED_ENV = "JARVIS_LOCAL_ALLOWED_HOSTS"
_MAX_RECORDED = 50


class EgressBlocked(ConnectionRefusedError):
    """The strict offline profile refused a connection or a lookup."""


_lock = threading.Lock()
_installed: dict[str, Any] | None = None
_blocked: list[str] = []
_blocked_count = 0


def _allowed(env: dict[str, str] | None = None) -> frozenset[str]:
    raw = (env if env is not None else os.environ).get(ALLOWED_ENV, "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def host_is_local(host: Any, allowed: frozenset[str] | None = None) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str) or not host:
        return False
    name = host.strip().strip("[]").lower().split("%", 1)[0]
    if name in (allowed if allowed is not None else _allowed()):
        return True
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:  # not an IP literal: any other name is not local
        return False


def _record(target: str) -> None:
    global _blocked_count
    with _lock:
        _blocked_count += 1
        _blocked.append(target)
        del _blocked[:-_MAX_RECORDED]
    log.warning("strict offline profile: blocked %s", target)


def blocked_attempts() -> tuple[int, tuple[str, ...]]:
    """(total blocked since install, the last few targets). Targets only: never any payload."""
    with _lock:
        return _blocked_count, tuple(_blocked)


def _check(address: Any) -> None:
    if isinstance(address, tuple) and address:
        host = address[0]
        if host_is_local(host):
            return
        _record(f"{host}:{address[1] if len(address) > 1 else ''}")
        raise EgressBlocked(f"strict offline profile: connection to {host!r} is not allowed")
    # A str/bytes address is a Unix-socket path: local by construction.


def install(*, allowed_hosts: frozenset[str] | None = None) -> bool:
    """Install the guard once. Returns False when it was already installed."""
    global _installed
    with _lock:
        if _installed is not None:
            return False
        originals = {
            "connect": socket.socket.connect,
            "connect_ex": socket.socket.connect_ex,
            "sendto": socket.socket.sendto,
            "getaddrinfo": socket.getaddrinfo,
            "gethostbyname": socket.gethostbyname,
            "gethostbyname_ex": socket.gethostbyname_ex,
        }
        _installed = originals
    extra = allowed_hosts

    def local(host: Any) -> bool:
        return host_is_local(host, (extra | _allowed()) if extra is not None else None)

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and address and not local(address[0]):
            _record(f"{address[0]}:{address[1] if len(address) > 1 else ''}")
            raise EgressBlocked(
                f"strict offline profile: connection to {address[0]!r} is not allowed"
            )
        return originals["connect"](self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        if isinstance(address, tuple) and address and not local(address[0]):
            _record(f"{address[0]}:{address[1] if len(address) > 1 else ''}")
            return 111  # ECONNREFUSED: the error code a refused connection reports
        return originals["connect_ex"](self, address)

    def guarded_sendto(self: socket.socket, data: Any, *args: Any) -> Any:
        address = args[-1] if args else None
        if isinstance(address, tuple) and address and not local(address[0]):
            _record(f"udp {address[0]}:{address[1] if len(address) > 1 else ''}")
            raise EgressBlocked(
                f"strict offline profile: datagram to {address[0]!r} is not allowed"
            )
        return originals["sendto"](self, data, *args)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and host != "" and not local(host):
            _record(f"dns {host}")
            raise EgressBlocked(f"strict offline profile: lookup of {host!r} is not allowed")
        return originals["getaddrinfo"](host, *args, **kwargs)

    def guarded_gethostbyname(host: str) -> str:
        if not local(host):
            _record(f"dns {host}")
            raise EgressBlocked(f"strict offline profile: lookup of {host!r} is not allowed")
        return originals["gethostbyname"](host)

    def guarded_gethostbyname_ex(host: str) -> Any:
        if not local(host):
            _record(f"dns {host}")
            raise EgressBlocked(f"strict offline profile: lookup of {host!r} is not allowed")
        return originals["gethostbyname_ex"](host)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.socket.sendto = guarded_sendto  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    socket.gethostbyname = guarded_gethostbyname  # type: ignore[assignment]
    socket.gethostbyname_ex = guarded_gethostbyname_ex  # type: ignore[assignment]
    log.info("strict offline profile active: only this machine is reachable")
    return True


def uninstall() -> None:
    """Restore the original socket functions (for tests; the app never uninstalls)."""
    global _installed, _blocked_count
    with _lock:
        originals, _installed = _installed, None
        _blocked.clear()
        _blocked_count = 0
    if originals is None:
        return
    socket.socket.connect = originals["connect"]  # type: ignore[method-assign]
    socket.socket.connect_ex = originals["connect_ex"]  # type: ignore[method-assign]
    socket.socket.sendto = originals["sendto"]  # type: ignore[method-assign]
    socket.getaddrinfo = originals["getaddrinfo"]  # type: ignore[assignment]
    socket.gethostbyname = originals["gethostbyname"]  # type: ignore[assignment]
    socket.gethostbyname_ex = originals["gethostbyname_ex"]  # type: ignore[assignment]


def install_from_env(env: dict[str, str] | None = None) -> bool:
    """Install when ``JARVIS_NETWORK_POLICY=local-only``.

    An unknown non-empty value is an error: a typo must not silently leave the network open.
    """
    value = ((env if env is not None else os.environ).get(POLICY_ENV) or "").strip().lower()
    if not value or value in ("off", "none"):
        return False
    if value != "local-only":
        raise ValueError(f"{POLICY_ENV}={value!r}: the only supported policy is 'local-only'")
    return install()
