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
    global _installed, _blocked_count, _source
    _source = "none"
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


def policy_from_file(path: Any) -> str | None:
    """``[network] policy`` in the config file: ``local-only``, ``off`` or None (absent/unreadable).

    An unknown value is an error (a typo must not leave the network open), exactly like the env var.
    """
    import tomllib  # noqa: PLC0415 - only needed at start

    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:  # no config file yet: nothing is configured
        return None
    except (OSError, tomllib.TOMLDecodeError):
        log.warning("network policy: config file unreadable; the policy stays off", exc_info=True)
        return None
    section = data.get("network")
    value = str(section.get("policy", "") if isinstance(section, dict) else "").strip().lower()
    if not value or value == "off":
        return value or None
    if value != "local-only":
        raise ValueError(f"[network] policy = {value!r}: the only supported policy is 'local-only'")
    return value


_source: str = "none"


def install_from_settings(path: Any = None, env: dict[str, str] | None = None) -> str:
    """Install the guard from the env var (wins) or the config file. Returns the source used:
    ``env``, ``config`` or ``none`` (guard not installed)."""
    global _source
    environ = env if env is not None else os.environ
    if (environ.get(POLICY_ENV) or "").strip():
        _source = "env" if install_from_env(environ) else "none"
        return _source
    if path is not None and policy_from_file(path) == "local-only":
        install()
        _source = "config"
        return _source
    _source = "none"
    return _source


#: What the guard does not stop, shown with its status so "offline" is never claimed beyond it.
NOT_COVERED = (
    "native extensions that open sockets without Python's socket module",
    "child processes that do not install the guard themselves (other programs, CLIs, browsers)",
    "anything outside this process tree: there is no operating-system enforcement yet (ADR-0060)",
)

# --- Function coverage (ADR-0060) -------------------------------------------------------------
# The socket guard sees only this Python process. Under the profile, a function that starts a
# child process or talks over the network is UNAVAILABLE unless proven covered. R7: the default is
# DENY. A tool passes only when positively classified as no-network (``OFFLINE_SAFE_TOOLS`` or a
# capability declared by the registry via ``declare_tool_coverage``); a name in a refusal class
# below is refused whatever it declares.

#: Python modules (``python -m <module>``) that are verified to call ``install_from_env`` first.
#: Empty on purpose: add one only together with a test that starts it and sees the refusal.
GUARDED_CHILD_MODULES: frozenset[str] = frozenset()

#: key -> (label, tool names, tool-name prefixes, tool-name infixes)
_TOOL_CLASSES: dict[str, tuple[str, frozenset[str], tuple[str, ...], tuple[str, ...]]] = {
    "shell": ("shell commands and verify-by-curl",
              frozenset({"run_shell", "verify_via_curl"}), (), ()),
    "cli": ("coding / third-party command-line tools", frozenset(), ("cli_",), ()),
    "browser": ("browser and desktop-app launching",
                frozenset({"navigate", "open_app", "computer_use"}), (), ()),
    "agents": ("worker and agent processes",
               frozenset({"spawn_worker", "multi_spawn", "spawn_subagents", "dispatch_to_harness",
                          "dispatch_with_review", "dispatch_to_admin", "delegate_to_agent"}),
               (), ()),
    "web_mail_saas": ("web search, mail, calendar, chat, GitHub and other online services",
                      frozenset({"search_web", "gmail", "google_drive", "google_calendar", "slack",
                                 "spotify", "youtube_music", "vercel", "home_assistant"}), (), ()),
    "mcp": ("MCP servers (launched as children or remote)", frozenset(), (), ("/",)),
}

#: What is NOT classified and so only covered by the in-process socket guard.
UNCLASSIFIED = (
    "call sites that pass only a tool NAME (not the tool) cannot see its declared capability: the "
    "MCP harness and skill runner must call tool_refusal(name, tool) (ADR-0053, R7)",
    "git operations run by the code workspace (fetch, push, clone) and the Codex app server",
    "agent runtime installers/gateways, the terminal host, native pickers, media tools (ffmpeg)",
)


class OfflineUnavailable(EgressBlocked):
    """A function that cannot be covered is unavailable under the strict offline profile."""


def _active() -> bool:
    return _installed is not None


def tool_class(tool_name: str) -> str | None:
    """The uncoverable class of a tool name, or None when it is not classified as one."""
    name = (tool_name or "").strip().lower()
    for key, (_label, names, prefixes, infixes) in _TOOL_CLASSES.items():
        hit = name in names or any(name.startswith(p) for p in prefixes)
        if hit or any(i in name for i in infixes):
            return key
    return None


#: Tools POSITIVELY classified as "starts no child process and opens no network connection of
#: its own": first-party local UI-input and read-only local tools (names as the tools declare
#: them). Checked by reading their modules for network/process imports, NOT by running them.
#: Everything that is neither here, nor declared, nor in a refusal class above is UNAVAILABLE
#: under the profile (default deny, R7).
OFFLINE_SAFE_TOOLS: frozenset[str] = frozenset({
    "click", "click_element", "drag", "hotkey", "move_mouse", "scroll", "type_text",
    "switch_window", "wait_for_element", "wait_for_ui_state", "read_visible_ui_state",
    "inspect-pointer", "reset_orb_position", "show_on_screen", "whoami",
    "wiki-list", "wiki-page-read", "product_help",
})

#: Coverage declared at registration time by the tool/plugin registry (it vets the plugin first).
_DECLARED: dict[str, str] = {}

CAPABILITY_NONE = "none"  # no network, no child process: the only value that passes the profile
CAPABILITY_NETWORK = "network"
CAPABILITY_CHILD = "child_process"
CAPABILITY_UNKNOWN = "unknown"
_CAPABILITIES = frozenset({CAPABILITY_NONE, CAPABILITY_NETWORK, CAPABILITY_CHILD})


def declare_tool_coverage(tool_name: str, capability: str) -> None:
    """Called by the tool/plugin registry for a tool it has vetted. An unknown value is recorded
    as UNKNOWN (so it is refused): a typo must not open a tool."""
    name = (tool_name or "").strip().lower()
    if name:
        _DECLARED[name] = capability if capability in _CAPABILITIES else CAPABILITY_UNKNOWN


def tool_capability(tool_name: str, tool: Any = None) -> str:
    """``none`` only when positively classified; everything else is ``network``/``child_process``
    (declared or by refusal class) or ``unknown`` (refused under the profile).

    A refusal class by NAME always wins: a tool cannot declare itself offline-safe to leave one.
    The ``network_capability`` attribute (or ``offline_safe is True``) on a tool object is honoured
    only for first-party classes (module under ``dodaios.``); a third-party plugin must be declared
    through :func:`declare_tool_coverage` by the registry.
    """
    name = (tool_name or "").strip().lower()
    if tool_class(name) is not None:
        return CAPABILITY_CHILD
    if name in _DECLARED:
        return _DECLARED[name]
    if name in OFFLINE_SAFE_TOOLS:
        return CAPABILITY_NONE
    if tool is not None and str(getattr(type(tool), "__module__", "")).startswith("dodaios."):
        declared = getattr(tool, "network_capability", None)
        if declared in _CAPABILITIES:
            return str(declared)
        if getattr(tool, "offline_safe", None) is True:
            return CAPABILITY_NONE
    return CAPABILITY_UNKNOWN


def tool_refusal(tool_name: str, tool: Any = None) -> str | None:
    """A plain refusal message when the profile is active and the tool is not positively covered.

    Default deny (R7): an unclassified name is UNAVAILABLE, not allowed. Call sites that hold the
    tool object (the MCP harness, the skill runner) must pass it as ``tool`` so its declared
    capability is consulted; ``tool_name`` alone only matches the built-in lists.
    """
    if not _active():
        return None
    key = tool_class(tool_name)
    if key is not None:
        label = _TOOL_CLASSES[key][0]
        return (
            f"Unavailable in the strict offline profile: {tool_name!r} ({label}) cannot be kept "
            "offline. Turn the profile off in Settings > General > Network to use it."
        )
    capability = tool_capability(tool_name, tool)
    if capability == CAPABILITY_NONE:
        return None
    why = ("declared as using the network or a child process" if capability != CAPABILITY_UNKNOWN
           else "not classified as offline-safe")
    return (
        f"Unavailable in the strict offline profile: {tool_name!r} is {why}, so it cannot be "
        "kept offline. Turn the profile off in Settings > General > Network to use it."
    )


def child_is_covered(argv: Any, env: dict[str, str] | None = None) -> bool:
    """True only for a Python child that installs the guard and inherits the policy.

    Proof, not hope: the interpreter is this one, the module is in ``GUARDED_CHILD_MODULES`` and
    the environment it will get carries ``JARVIS_NETWORK_POLICY=local-only``.
    """
    import sys  # noqa: PLC0415

    try:
        args = [str(a) for a in argv]
    except TypeError:
        return False
    if len(args) < 3 or os.path.realpath(args[0]) != os.path.realpath(sys.executable):
        return False
    if args[1] != "-m" or args[2] not in GUARDED_CHILD_MODULES:
        return False
    value = ((env if env is not None else os.environ).get(POLICY_ENV) or "").strip().lower()
    return value == "local-only"


def child_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of ``env`` that makes a guarded child install the same policy and allow-list."""
    out = dict(env if env is not None else os.environ)
    if _active():
        out[POLICY_ENV] = "local-only"
    return out


def child_refusal(what: str, argv: Any = None, env: dict[str, str] | None = None) -> str | None:
    """A refusal message for launching a child process, or None when allowed/covered."""
    if not _active() or (argv is not None and child_is_covered(argv, env)):
        return None
    return (
        f"Unavailable in the strict offline profile: starting {what} would run a program the "
        "network guard cannot cover. Turn the profile off in Settings > General > Network."
    )


def require_child(what: str, argv: Any = None, env: dict[str, str] | None = None) -> None:
    """Raise :class:`OfflineUnavailable` BEFORE a child process starts, when it is not covered."""
    message = child_refusal(what, argv, env)
    if message is not None:
        _record(f"child {what}")
        raise OfflineUnavailable(message)


def os_enforcement_verified() -> bool:
    """Always False: no operating-system enforcement exists or is observed yet (ADR-0060)."""
    return False


def coverage() -> dict[str, Any]:
    """Per function class: available or not under the running profile, and why."""
    active = _active()
    classes = [
        {"key": key, "label": label, "available": not active,
         "why": "" if not active else "cannot be proven offline; refused"}
        for key, (label, *_rest) in _TOOL_CLASSES.items()
    ]
    return {"python_guard_enforced": active, "os_enforcement_verified": os_enforcement_verified(),
            "classes": classes, "unclassified": list(UNCLASSIFIED)}


def status() -> dict[str, Any]:
    """What this process enforces, for the settings screen. Targets only, never any payload."""
    count, targets = blocked_attempts()
    active = _installed is not None
    cov = coverage()
    return {"active": active, "source": _source if active else "none",
            "blocked": count, "recent": list(targets)[-5:], "not_covered": list(NOT_COVERED),
            "python_guard_enforced": active,
            "os_enforcement_verified": cov["os_enforcement_verified"],
            "unavailable": [c["label"] for c in cov["classes"] if not c["available"]],
            "unclassified": cov["unclassified"]}


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
