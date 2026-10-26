"""Strict offline profile (ADR-0053): real sockets; blocked attempts fail before any packet."""

from __future__ import annotations

import asyncio
import http.client
import socket
import threading
import urllib.request

import pytest

from dodaios.core import egress_guard as eg

TEST_NET = "203.0.113.7"  # reserved documentation range: never routable


@pytest.fixture(autouse=True)
def guard():
    eg.uninstall()
    eg.install()
    yield
    eg.uninstall()


def serve_once():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        conn.recv(1024)
        conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok")
        conn.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_loopback_still_works_with_real_http():
    port = serve_once()
    assert urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read() == b"ok"


def test_non_local_connect_is_refused_before_any_packet_and_counted():
    sock = socket.socket()
    with pytest.raises(eg.EgressBlocked):
        sock.connect((TEST_NET, 443))
    assert sock.connect_ex((TEST_NET, 443)) == 111
    count, targets = eg.blocked_attempts()
    assert count == 2 and targets[0] == f"{TEST_NET}:443"
    sock.close()


def test_every_high_level_client_hits_the_same_wall():
    with pytest.raises(OSError, match="not allowed"):
        http.client.HTTPSConnection(TEST_NET, timeout=1).request("GET", "/")
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://{TEST_NET}/", timeout=1)
    with pytest.raises(OSError):
        socket.create_connection((TEST_NET, 80), timeout=1)

    async def go():
        await asyncio.open_connection(TEST_NET, 80)

    with pytest.raises(OSError):
        asyncio.run(go())


def test_dns_lookups_of_non_local_names_are_refused_so_names_do_not_leak():
    for call in (
        lambda: socket.getaddrinfo("api.openai.com", 443),
        lambda: socket.gethostbyname("example.com"),
        lambda: socket.gethostbyname_ex("example.com"),
    ):
        with pytest.raises(eg.EgressBlocked):
            call()
    assert socket.getaddrinfo("localhost", 80)  # local names still resolve


def test_udp_to_a_non_local_address_is_refused():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    with pytest.raises(eg.EgressBlocked):
        sock.sendto(b"x", (TEST_NET, 53))
    sock.sendto(b"x", ("127.0.0.1", 9))  # loopback datagram is fine
    sock.close()


def test_unix_sockets_are_local_by_construction(tmp_path):
    path = str(tmp_path / "s")
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(path)
    srv.listen(1)
    client = socket.socket(socket.AF_UNIX)
    client.connect(path)
    client.close()
    srv.close()


def test_an_owner_allowed_host_passes_the_guard_but_still_fails_to_connect_normally(monkeypatch):
    monkeypatch.setenv(eg.ALLOWED_ENV, TEST_NET)
    assert eg.host_is_local(TEST_NET)
    sock = socket.socket()
    sock.settimeout(0.2)
    with pytest.raises(
        OSError
    ) as exc:  # allowed by the guard: now the real (failing) connect happens
        sock.connect((TEST_NET, 9))
    assert not isinstance(exc.value, eg.EgressBlocked)
    sock.close()


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("10.0.0.2", False),
        ("example.com", False),
        ("127.0.0.1.evil.com", False),
        ("", False),
        (None, False),
    ],
)
def test_host_classification_never_resolves_names(host, local):
    assert eg.host_is_local(host, frozenset()) is local


def test_install_is_idempotent_and_uninstall_restores_the_original_functions():
    assert eg.install() is False
    eg.uninstall()
    original = socket.getaddrinfo
    assert original.__name__ == "getaddrinfo"
    assert socket.socket.connect.__name__ != "guarded_connect"


def test_install_from_env_values(monkeypatch):
    eg.uninstall()
    assert eg.install_from_env({}) is False
    assert eg.install_from_env({eg.POLICY_ENV: "off"}) is False
    with pytest.raises(ValueError, match="only supported"):
        eg.install_from_env(
            {eg.POLICY_ENV: "mostly-local"}
        )  # a typo must not leave the network open
    assert eg.install_from_env({eg.POLICY_ENV: " LOCAL-ONLY "}) is True


def test_a_representative_work_turn_with_fake_local_engine_makes_no_outbound_request(monkeypatch):
    """Provider door + guard together: remote engine refused, local one answers, nothing leaves."""
    from dodaios.brain import engine_policy as ep
    from dodaios.brain.provider_registry import BrainProviderRegistry
    from dodaios.core.protocols import BrainDelta, BrainMessage, BrainRequest

    monkeypatch.setenv("JARVIS_PRODUCT", "work")
    monkeypatch.setattr(ep, "endpoint_problem", lambda provider: None)

    class Local:
        def __init__(self, model=None):
            pass

        def estimate_cost(self, req):
            return 0.0

        async def complete(self, req):
            yield BrainDelta(content="locale")

    class Remote(Local):
        def __init__(self, model=None):
            socket.create_connection((TEST_NET, 443), timeout=1)  # would leak if ever constructed

    reg = BrainProviderRegistry()
    reg._classes = {"ollama": Local, "openai": Remote}
    reg._loaded = True
    with pytest.raises(ep.EnginePolicyError):
        reg.instantiate("openai")
    brain = reg.instantiate("ollama")

    async def go():
        req = BrainRequest(messages=(BrainMessage(role="user", content="ciao"),))
        return [d.content async for d in brain.complete(req)]

    assert asyncio.run(go()) == ["locale"]
    assert eg.blocked_attempts()[0] == 0  # not a single outbound attempt, not even a blocked one


def test_the_app_entry_point_refuses_an_unknown_network_policy_and_accepts_local_only():
    import os
    import subprocess
    import sys

    def run(value):
        env = {**os.environ, eg.POLICY_ENV: value}
        return subprocess.run([sys.executable, "-m", "dodaios", "--version"], env=env,
                              capture_output=True, text=True, timeout=120, check=False)

    bad = run("mostly-local")
    assert bad.returncode == 2 and "only supported policy" in bad.stderr
    assert run("local-only").returncode == 0


# -- the config file (settings screen) -----------------------------------------------------


def test_policy_from_file_values(tmp_path):
    f = tmp_path / "jarvis.toml"
    assert eg.policy_from_file(f) is None  # no file yet
    f.write_text('[network]\npolicy = "local-only"\n', encoding="utf-8")
    assert eg.policy_from_file(f) == "local-only"
    f.write_text('[network]\npolicy = "off"\n', encoding="utf-8")
    assert eg.policy_from_file(f) == "off"
    f.write_text('[brain]\nprimary = "ollama"\n', encoding="utf-8")
    assert eg.policy_from_file(f) is None
    f.write_text('[network]\npolicy = "mostly"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="only supported"):
        eg.policy_from_file(f)  # a typo in the file must not leave the network open either
    f.write_text("[network\nbroken", encoding="utf-8")
    assert eg.policy_from_file(f) is None  # unreadable file: logged, policy stays off


def test_install_from_settings_env_wins_over_the_file(tmp_path):
    eg.uninstall()
    f = tmp_path / "jarvis.toml"
    f.write_text('[network]\npolicy = "local-only"\n', encoding="utf-8")
    # env says off: the file is ignored
    assert eg.install_from_settings(f, {eg.POLICY_ENV: "off"}) == "none"
    assert eg.status()["active"] is False
    assert eg.install_from_settings(f, {}) == "config"
    st = eg.status()
    assert st["active"] is True and st["source"] == "config"
    with pytest.raises(eg.EgressBlocked):
        socket.socket().connect((TEST_NET, 80))
    assert eg.status()["blocked"] == 1 and eg.status()["recent"] == [f"{TEST_NET}:80"]
    eg.uninstall()
    assert eg.install_from_settings(f, {eg.POLICY_ENV: "local-only"}) == "env"
    assert eg.status()["source"] == "env"


def test_install_from_settings_with_no_file_and_no_env_installs_nothing(tmp_path):
    eg.uninstall()
    assert eg.install_from_settings(tmp_path / "missing.toml", {}) == "none"
    status = eg.status()
    assert {k: v for k, v in status.items() if k != "not_covered"} == {
        "active": False, "source": "none", "blocked": 0, "recent": []}


def test_the_app_entry_point_reads_the_policy_from_the_config_file(tmp_path, monkeypatch, capsys):
    from dodaios import __main__ as entry
    from dodaios.core import config as cfg_mod

    f = tmp_path / "dodaios.toml"
    f.write_text('[network]\npolicy = "local-only"\n', encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_FILE", f)
    monkeypatch.delenv(eg.POLICY_ENV, raising=False)
    eg.uninstall()
    try:
        try:
            entry.main(["--version"])
        except SystemExit:
            pass
        assert eg.status()["active"] is True and eg.status()["source"] == "config"
        with pytest.raises(eg.EgressBlocked):
            socket.create_connection((TEST_NET, 443), timeout=1)
    finally:
        eg.uninstall()
    f.write_text('[network]\npolicy = "mostly"\n', encoding="utf-8")
    assert entry.main(["--version"]) == 2  # a typo in the file ends startup, network untouched
    assert "only supported policy" in capsys.readouterr().err


def test_python_child_with_the_env_policy_blocks_an_outside_host():
    """Negative-network evidence for a child: it inherits the policy through the environment and
    installs the guard itself; nothing is sent (the address is a reserved documentation range)."""
    import os
    import subprocess
    import sys

    code = (
        "from dodaios.core import egress_guard as g; g.install_from_env(); import socket\n"
        "try:\n"
        "    socket.create_connection(('203.0.113.7', 80), timeout=1)\n"
        "except g.EgressBlocked:\n"
        "    print('BLOCKED')\n"
    )
    out = subprocess.run(  # noqa: S603 - fixed interpreter and inline code
        [sys.executable, "-c", code],
        env={**os.environ, "JARVIS_NETWORK_POLICY": "local-only"},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert "BLOCKED" in out.stdout, out.stderr[-400:]


def test_status_names_what_is_not_covered():
    from dodaios.core import egress_guard

    assert egress_guard.status()["not_covered"]
