"""What the phone's voice uses, counted here (the one cost the server cannot always see).

Cartesia bills credits, one per character of text spoken; the phone counts the characters it
asks the server to voice, per day and per month, and compares them with a limit the owner sets.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


class VoiceUsage:
    def __init__(self, root: Path) -> None:
        self._path = Path(root) / "voice-usage.json"
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                raw = json.loads(self._path.read_text("utf-8"))
                self._data = raw if isinstance(raw, dict) else {}
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

    @staticmethod
    def _keys() -> tuple[str, str]:
        now = time.localtime()
        return time.strftime("%Y-%m", now), time.strftime("%Y-%m-%d", now)

    def add(self, chars: int) -> None:
        month, day = self._keys()
        with self._lock:
            data = self._load()
            months = data.setdefault("months", {})
            days = data.setdefault("days", {})
            months[month] = int(months.get(month, 0)) + chars
            days[day] = int(days.get(day, 0)) + chars
            for old in sorted(days)[:-62]:  # two months of days are plenty
                del days[old]
            self._save()

    def snapshot(self) -> dict[str, Any]:
        month, day = self._keys()
        with self._lock:
            data = self._load()
            limit = data.get("limit_month")
            return {
                "chars_day": int(data.get("days", {}).get(day, 0)),
                "chars_month": int(data.get("months", {}).get(month, 0)),
                "limit_month": int(limit) if isinstance(limit, int) and limit > 0 else None,
            }

    def set_limit(self, limit: int | None) -> None:
        with self._lock:
            data = self._load()
            if limit and limit > 0:
                data["limit_month"] = int(limit)
            else:
                data.pop("limit_month", None)
            self._save()

    def level(self) -> str:
        """``ok`` | ``warn`` (80% of the limit) | ``bad`` (over it), for the status line."""
        snap = self.snapshot()
        limit = snap["limit_month"]
        if not limit:
            return "ok"
        used = snap["chars_month"]
        return "bad" if used >= limit else "warn" if used >= 0.8 * limit else "ok"
