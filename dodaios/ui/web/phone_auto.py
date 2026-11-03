"""Timed "approve always" for a chat opened from the phone.

The chat's permission mode is switched to ``auto`` and put back to what it was when the time
is up (or on request). The deadline is written to disk so a restart still puts it back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from dodaios.agent_chat.events import make_event

log = logging.getLogger(__name__)

#: Choices the phone offers, in seconds; ``None`` = until the chat is over (no deadline).
WINDOWS: dict[str, int | None] = {"30m": 30 * 60, "1h": 3600, "chat": None}


class AutoGrants:
    def __init__(self, root: Path) -> None:
        self._path = Path(root) / "auto-grants.json"
        self._data: dict[str, dict[str, Any]] | None = None
        self._timers: dict[str, asyncio.TimerHandle] = {}

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._data is None:
            try:
                data = json.loads(self._path.read_text("utf-8"))
                self._data = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh)
        os.replace(tmp, self._path)

    def state(self, session_id: str) -> dict[str, Any] | None:
        entry = self._load().get(session_id)
        return None if entry is None else {"until": entry.get("until")}

    async def grant(self, svc: Any, session_id: str, window: str) -> dict[str, Any]:
        if window not in WINDOWS:
            raise ValueError("unknown window")
        session = svc.store.get_session(session_id)
        if session is None:
            raise LookupError(session_id)
        data = self._load()
        seconds = WINDOWS[window]
        until = None if seconds is None else time.time() + seconds
        previous = data[session_id]["previous"] if session_id in data else session.permission_mode
        data[session_id] = {"until": until, "previous": previous}
        self._save()
        if session.permission_mode != "auto":
            svc.store.update_session(session_id, permission_mode="auto")
            await svc._emit(  # noqa: SLF001 - the service's own event path
                session_id, make_event("session_updated", {"permission_mode": "auto"})
            )
        self._arm(svc, session_id, until)
        return {"until": until}

    def _arm(self, svc: Any, session_id: str, until: float | None) -> None:
        old = self._timers.pop(session_id, None)
        if old is not None:
            old.cancel()
        if until is None:
            return
        loop = asyncio.get_running_loop()
        self._timers[session_id] = loop.call_later(
            max(0.0, until - time.time()),
            lambda: loop.create_task(self.revoke(svc, session_id)),
        )

    async def revoke(self, svc: Any, session_id: str) -> bool:
        timer = self._timers.pop(session_id, None)
        if timer is not None:
            timer.cancel()
        data = self._load()
        entry = data.pop(session_id, None)
        if entry is None:
            return False
        self._save()
        previous = str(entry.get("previous") or "default")
        try:
            session = svc.store.get_session(session_id)
            if session is not None and session.permission_mode == "auto":
                svc.store.update_session(session_id, permission_mode=previous)
                await svc._emit(  # noqa: SLF001
                    session_id, make_event("session_updated", {"permission_mode": previous})
                )
        except Exception:  # noqa: BLE001
            log.warning("phone auto: could not restore chat %s", session_id, exc_info=True)
        return True

    async def restore(self, svc: Any) -> None:
        """After a restart: put back the chats whose time ran out, re-arm the others."""
        now = time.time()
        for session_id, entry in list(self._load().items()):
            until = entry.get("until")
            if until is None:
                continue
            if until <= now:
                await self.revoke(svc, session_id)
            else:
                self._arm(svc, session_id, until)
