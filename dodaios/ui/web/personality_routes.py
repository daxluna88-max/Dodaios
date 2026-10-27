"""Settings > Assistant > Personality (issue 17): the owner's style and the simulated mood.

* ``GET``    the saved profile, what it resolves to, the presets and the mood right now with its
  reason. Reads nothing from the cloud; the mood is a simulated way of speaking, never a feeling.
* ``PUT``    save the profile (validated first, one atomic write, applied at once, no restart).
* ``DELETE`` reset: the block is removed and the product's defaults apply.
* ``POST /preview`` the style text a profile would produce, without saving.
* ``POST /mood`` an owner request: set a mood, hold it, back to neutral, adaptation on/off. These
  change this session only; nothing here is saved as a permanent preference.

Everything stored is an enumeration or a small bounded integer (``PersonalityConfig``). Style never
grants a tool, an approval, a budget or a job: no route here touches those.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, get_args

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from dodaios.brain import engine_policy, personality, style
from dodaios.brain import mood as mood_mod
from dodaios.core import config as cfg_mod
from dodaios.core import config_writer
from dodaios.core.config import Mood, PersonalityConfig, Tone

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/personality", tags=["settings"])


class MoodBody(BaseModel):
    action: Literal["set", "hold", "neutral", "adaptation"]
    mood: str | None = Field(default=None, max_length=20)
    intensity: int = Field(default=2, ge=1, le=mood_mod.MAX_INTENSITY)
    ttl_s: float = Field(default=mood_mod.OWNER_TTL_S, gt=0, le=mood_mod.MAX_TTL_S)
    enabled: bool = True


def _brain(request: Request) -> Any:
    return getattr(request.app.state, "brain", None)


def _saved() -> personality.ParsedProfile:
    """The profile on disk, read the way startup reads it (a damaged block is reported)."""
    path = cfg_mod.resolve_config_path()
    import tomllib  # noqa: PLC0415

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8")).get("brain", {}).get("personality")
    except (OSError, ValueError):
        raw = None
    return personality.parse(raw)


def _mood_payload(state: mood_mod.MoodState, adaptation_on: bool) -> dict[str, Any]:
    return {
        "mood": state.mood, "intensity": state.intensity, "origin": state.origin,
        "reason": state.reason, "expires_at": state.expires_at,
        "adaptation_on": adaptation_on,
    }


def _payload(request: Request) -> dict[str, Any]:
    parsed = _saved()
    product = engine_policy.current_product()
    brain = _brain(request)
    if brain is not None:
        profile, state, adaptation_on = brain.personality_snapshot()
        live = getattr(brain, "_config", None)
        shown = live.brain.personality if live is not None else parsed.config
    else:
        shown = parsed.config
        profile, state, adaptation_on = personality.effective(shown, product), \
            mood_mod.NEUTRAL_STATE, True
    return {
        "product": product,
        "status": parsed.status,
        "reason": parsed.reason,
        "profile": shown.model_dump(),
        "effective": {
            "mode": profile.mode, "preset": profile.preset, "tone": profile.tone,
            "formality": profile.formality, "warmth": profile.warmth, "humor": profile.humor,
            "concision": profile.concision,
        },
        "presets": {
            name: dict(zip(("tone", "formality", "warmth", "humor", "concision"), values,
                           strict=True))
            for name, values in personality.PRESETS.items()
        },
        "default_preset": personality.default_preset(product),
        "mood": _mood_payload(state, adaptation_on),
        "mood_available": brain is not None,
        "simulated": True,
        "tones": list(get_args(Tone)),
        "moods": list(get_args(Mood)),
    }


@router.get("", summary="Personality profile, resolved style and current simulated mood")
async def get_personality(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_payload, request)


@router.put("", summary="Save the personality profile (applies at once)")
async def put_personality(body: PersonalityConfig, request: Request) -> dict[str, Any]:
    try:
        stored = await asyncio.to_thread(
            config_writer.set_personality, body.model_dump(exclude_none=True),
            path=cfg_mod.resolve_config_path())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    brain = _brain(request)
    if brain is not None:
        brain.set_personality(PersonalityConfig.model_validate(stored))
    return await asyncio.to_thread(_payload, request)


@router.delete("", summary="Reset the personality profile to the product's defaults")
async def reset_personality(request: Request) -> dict[str, Any]:
    await asyncio.to_thread(config_writer.reset_personality, path=cfg_mod.resolve_config_path())
    brain = _brain(request)
    if brain is not None:
        brain.set_personality(PersonalityConfig())
    return await asyncio.to_thread(_payload, request)


@router.post("/preview", summary="The style text a profile would produce (nothing is saved)")
async def preview_personality(body: PersonalityConfig) -> dict[str, str]:
    profile = personality.effective(body, engine_policy.current_product())
    return {"instruction": style.instruction(profile, mood_mod.NEUTRAL_STATE)}


@router.post("/mood", summary="An owner request about the simulated mood (this session only)")
async def post_mood(body: MoodBody, request: Request) -> dict[str, Any]:
    brain = _brain(request)
    if brain is None:
        raise HTTPException(status_code=503, detail="The assistant is still starting.")
    event: mood_mod.Event
    if body.action == "set":
        if not body.mood:
            raise HTTPException(status_code=400, detail="A mood is required.")
        event = mood_mod.OwnerMood(body.mood, body.intensity, body.ttl_s)
    elif body.action == "hold":
        event = mood_mod.OwnerHold()
    elif body.action == "neutral":
        event = mood_mod.OwnerNeutral()
    else:
        event = mood_mod.OwnerAdaptation(body.enabled)
    applied = await asyncio.to_thread(brain.personality_apply, event)
    if applied is not None and not applied.accepted:
        raise HTTPException(status_code=409, detail=applied.refused)
    return await asyncio.to_thread(_payload, request)
