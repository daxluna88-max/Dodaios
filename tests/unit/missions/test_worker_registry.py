"""Live worker processes as the OS sees them (ADR-0058). Written, not run (issue 16 STOP)."""
from __future__ import annotations

import subprocess
import sys

import pytest

from dodaios.missions.isolation import worker_registry as wr

psutil = pytest.importorskip("psutil")


@pytest.fixture(autouse=True)
def _clean():
    wr.clear()
    yield
    wr.clear()


def test_a_running_process_is_alive_and_a_finished_one_is_not():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])  # noqa: S603
    try:
        wr.register(proc.pid)
        assert proc.pid in (wr.live_worker_pids() or [])
    finally:
        proc.kill()
        proc.wait()
    assert proc.pid not in (wr.live_worker_pids() or [])


def test_a_pid_that_never_existed_is_not_reported():
    wr.register(2**22 + 12345)
    assert wr.live_worker_pids() == []


def test_a_reused_pid_does_not_count_as_the_worker():
    wr._TRACKED[proc_pid := psutil.Process().pid] = 1.0  # registered with a start time long ago
    assert proc_pid not in (wr.live_worker_pids() or [])


def test_bad_pids_are_ignored_and_never_raise():
    for bad in (0, -1, True, "x", None):
        wr.register(bad)  # type: ignore[arg-type]
    assert wr.live_worker_pids() == []


def test_without_psutil_the_answer_is_unknown_not_none_alive(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert wr.live_worker_pids() is None
