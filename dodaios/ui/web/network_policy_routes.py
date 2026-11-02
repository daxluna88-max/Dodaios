"""Settings > General > Network: the opt-in strict offline profile (ADR-0053).

``GET`` reports what THIS process enforces and what the config file says; ``PUT`` writes
``[network] policy``. The guard is installed at process start, so a change takes effect after a
restart (``restart_required``); a ``JARVIS_NETWORK_POLICY`` environment value overrides the file
and is reported as the source. Nothing here opens a connection.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dodaios.core import config as cfg_mod
from dodaios.core import config_writer, egress_guard

router = APIRouter(prefix="/api/network-policy", tags=["settings"])


class PolicyBody(BaseModel):
    policy: str


def _configured() -> str:
    try:
        return cfg_mod.load_config().network.policy
    except Exception:  # noqa: BLE001 - an unreadable config reads as the default
        return "off"


def _payload() -> dict[str, Any]:
    running = egress_guard.status()
    configured = _configured()
    env = (os.environ.get(egress_guard.POLICY_ENV) or "").strip().lower()
    wanted = env or configured
    return {
        "configured": configured,
        "env_override": env or None,
        "active": running["active"],
        "source": running["source"],
        "python_guard_enforced": running["python_guard_enforced"],
        "os_enforcement_verified": running["os_enforcement_verified"],
        "unavailable": running["unavailable"],
        "unclassified": running["unclassified"],
        "blocked": running["blocked"],
        "recent": running["recent"],
        "restart_required": (wanted == "local-only") != running["active"],
    }


@router.get("", summary="Strict offline profile: configured vs enforced")
async def get_network_policy() -> dict[str, Any]:
    return await asyncio.to_thread(_payload)


@router.put("", summary="Choose the strict offline profile (next start)")
async def put_network_policy(body: PolicyBody) -> dict[str, Any]:
    try:
        await asyncio.to_thread(config_writer.set_network_policy, body.policy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await asyncio.to_thread(_payload)
