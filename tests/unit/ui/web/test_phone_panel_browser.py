"""Run the phone panel's browser tests (``tests/browser/phone_panel``) against a real server.

They need Node and the ``playwright`` package (resolved through ``npm root -g`` or NODE_PATH);
without them the test is skipped, never silently passed.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[4] / "tests" / "browser" / "phone_panel"
KEY = "browser-test-control-key-1234"  # mirrors server.py


def _node_env() -> dict[str, str] | None:
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node:
        return None
    env = dict(os.environ)
    roots = [env.get("NODE_PATH", "")]
    if npm:
        probe = subprocess.run([npm, "root", "-g"], capture_output=True, text=True, check=False)
        roots.append(probe.stdout.strip())
    env["NODE_PATH"] = os.pathsep.join(r for r in roots if r)
    check = subprocess.run(
        [node, "-e", "require('playwright')"], env=env, capture_output=True, check=False
    )
    return env if check.returncode == 0 else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_phone_panel_in_a_real_browser() -> None:
    env = _node_env()
    if env is None:
        pytest.skip("node + playwright are not available")
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 90
        ready = False
        while time.monotonic() < deadline and server.poll() is None:
            line = server.stdout.readline()
            if line.startswith("READY"):
                ready = True
                break
        assert ready, "the test server did not start"
        run = subprocess.run(
            ["node", str(HERE / "panel.test.js"), f"http://127.0.0.1:{port}", KEY],
            env=env, capture_output=True, text=True, timeout=240, check=False,
        )
        assert run.returncode == 0, run.stdout + run.stderr
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
