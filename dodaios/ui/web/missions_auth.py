"""Process-local tokens for authenticated UI and mission WebSocket sessions.

Tokens are minted only behind the global Host/Origin/credential boundary, live
in memory, and disappear on restart. Mission sockets keep their narrower
hello-frame check as defense in depth; the main UI session itself is carried in
an HttpOnly cookie managed by :mod:`dodaios.ui.web.surface_security`.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Final

from fastapi import APIRouter
from fastapi.responses import JSONResponse

# Module-global token store. A set for O(1) lookup. Reset on
# restart — sufficient for a process-scoped browser session.
_TOKENS: set[str] = set()
_TOKEN_BYTES: Final[int] = 32  # 256 bits of entropy

# Opt-in persistence (``JARVIS_SESSION_PERSIST=1``): browser sessions survive a restart for
# ``SESSION_TTL_SECONDS``. Only SHA-256 digests reach disk, in a 0600 file next to the control key.
SESSION_TTL_SECONDS: Final[int] = 30 * 24 * 3600
_MAX_PERSISTED: Final[int] = 64
_PERSISTED: dict[str, float] | None = None


def persistence_enabled() -> bool:
    return os.environ.get("JARVIS_SESSION_PERSIST", "").strip().lower() in {"1", "true", "yes"}


def _store_path() -> Path:
    from dodaios.core.control_key import control_key_file

    return control_key_file().with_name(".session_tokens")


def _digest(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def _persisted() -> dict[str, float]:
    global _PERSISTED
    if _PERSISTED is None:
        _PERSISTED = {}
        try:
            raw = json.loads(_store_path().read_text(encoding="utf-8"))
            now = time.time()
            _PERSISTED = {
                str(k): float(v) for k, v in raw.items() if now - float(v) < SESSION_TTL_SECONDS
            }
        except (OSError, ValueError, AttributeError):
            _PERSISTED = {}
    return _PERSISTED


def _save_persisted() -> None:
    store = _persisted()
    for stale in sorted(store, key=store.__getitem__)[:-_MAX_PERSISTED]:
        del store[stale]
    try:
        path = _store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(store, handle)
        os.replace(tmp, path)
    except OSError:
        pass  # best effort: the in-memory session still works until restart


def issue_token() -> str:
    """Generates a URL-safe token and stores it as valid."""
    tok = secrets.token_urlsafe(_TOKEN_BYTES)
    register_token(tok)
    return tok


def validate_token(tok: str) -> bool:
    """True if the token is in the store (no drift, no expiration)."""
    if not tok:
        return False
    if tok in _TOKENS:
        return True
    if not persistence_enabled():
        return False
    issued = _persisted().get(_digest(tok))
    return issued is not None and time.time() - issued < SESSION_TTL_SECONDS


def register_token(tok: str) -> None:
    """Register an externally-minted token as valid. Idempotent.

    The fast-boot boundary can mint an authenticated HttpOnly session before the
    full app exists. Registering that issued session preserves it at handoff.
    """
    if tok:
        _TOKENS.add(tok)
        if persistence_enabled() and _digest(tok) not in _persisted():
            _persisted()[_digest(tok)] = time.time()
            _save_persisted()


def revoke_token(tok: str) -> None:
    """Best-effort removal of the token. Idempotent."""
    _TOKENS.discard(tok)
    if tok and persistence_enabled() and _persisted().pop(_digest(tok), None) is not None:
        _save_persisted()


def reset_tokens() -> None:
    """Test hook: clears the store entirely."""
    global _PERSISTED
    _TOKENS.clear()
    _PERSISTED = None


router = APIRouter(prefix="/api/missions/auth", tags=["missions-auth"])


@router.get("/token")
async def get_token() -> JSONResponse:
    """Return a fresh in-memory mission token without allowing caching."""
    return JSONResponse(
        {"token": issue_token()},
        headers={"Cache-Control": "no-store"},
    )
