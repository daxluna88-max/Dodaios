"""Which worker processes were started and are still alive (ADR-0058, STOP confirmation).

``cancel_running_mission`` only says that a cancel request reached the run task; it does not say
that the worker processes are gone. This registry lets STOP ask the operating system instead:
every pid a job object assigned is remembered with its start time, and :func:`live_worker_pids`
reports which of them still exist. A pid that now belongs to another process (reused) does not
count as alive.

Honest limits: a process that was never assigned to a job object is not tracked; without ``psutil``
the answer is ``None`` (cannot tell), never "none alive".
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
#: pid -> process start time (seconds since epoch) as read at registration; 0.0 when unreadable.
_TRACKED: dict[int, float] = {}
_START_TOLERANCE_S = 2.0


def _start_time(pid: int) -> float | None:
    try:
        import psutil  # noqa: PLC0415

        return float(psutil.Process(pid).create_time())
    except ImportError:  # psutil is optional: the start time is then simply unknown
        return None
    except Exception:  # noqa: BLE001 - gone, or not readable: the caller decides what that means
        log.debug("worker registry: start time unreadable", exc_info=True)
        return None


def register(pid: int) -> None:
    """Remember a worker process the moment a job object takes it. Never raises."""
    try:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return
        started = _start_time(pid) or 0.0
        with _LOCK:
            _TRACKED[pid] = started
    except Exception:  # noqa: BLE001 - tracking must never stop a worker from starting
        log.debug("worker registry: could not remember pid", exc_info=True)


def live_worker_pids() -> list[int] | None:
    """Pids of tracked workers that still exist; ``None`` when the OS cannot be asked.

    Entries for processes that are gone are dropped as a side effect.
    """
    try:
        import psutil  # noqa: PLC0415
    except ImportError:  # psutil is optional: "cannot tell" is the honest answer, as None
        return None
    alive: list[int] = []
    with _LOCK:
        for pid, started in list(_TRACKED.items()):
            try:
                proc = psutil.Process(pid)
                current = float(proc.create_time())
                zombie = proc.status() == psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:  # the process is gone: dropping it is the answer
                _TRACKED.pop(pid, None)
                continue
            except Exception:  # noqa: BLE001 - unreadable: cannot say it is gone
                log.debug("worker registry: pid state unreadable", exc_info=True)
                return None
            if started and abs(current - started) > _START_TOLERANCE_S:
                _TRACKED.pop(pid, None)  # the pid now belongs to a different process
                continue
            if zombie:
                continue  # exited, only waiting to be reaped
            alive.append(pid)
    return alive


def clear() -> None:
    """Forget every tracked pid (tests)."""
    with _LOCK:
        _TRACKED.clear()


__all__ = ["clear", "live_worker_pids", "register"]
