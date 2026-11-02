"""The owner's personality profile, resolved (issue 17, ADR-0056).

One place says what ``[brain.personality]`` means: the presets, how a preset and the owner's own
values combine, how an old or damaged block is read, and what the profile looks like once resolved.
Pure functions over the validated ``PersonalityConfig``: no I/O, no model call, no clock.

* **Separate from the mood.** This is the stable, owner-chosen part. The temporary, simulated mood
  is ``dodaios.brain.mood``; the plan for text, voice and avatar is built from both.
* **Per product.** The block lives in the product's own config file, so Consumer and Work never
  share a profile. Defaults differ: professional for Work, friendly for Consumer.
* **Never free text.** Every value is an enumeration or a small bounded integer, so nothing the
  owner types can become an instruction or carry personal data to a remote engine.
* **A damaged block never stops the app.** ``parse`` returns the defaults and says why.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from dodaios.core.config import PersonalityConfig

log = logging.getLogger(__name__)

#: The schema this build writes. Older blocks are migrated; newer ones are not guessed at.
CURRENT_SCHEMA_VERSION = 1

# preset -> (tone, formality, warmth, humor, concision)
PRESETS: dict[str, tuple[str, int, int, int, int]] = {
    "professional": ("neutral", 3, 1, 0, 3),
    "friendly": ("friendly", 1, 3, 2, 1),
    "concise": ("neutral", 2, 1, 0, 4),
}
#: Preset taken when the owner has not picked one.
PRODUCT_DEFAULT_PRESET = {"work": "professional", "consumer": "friendly"}
FALLBACK_PRESET = "friendly"


@dataclass(frozen=True, slots=True)
class EffectiveProfile:
    """The stable profile once preset and the owner's own values are combined."""

    mode: str
    preset: str
    tone: str
    formality: int
    warmth: int
    humor: int
    concision: int
    expressive_range: int
    moods_allowed: tuple[str, ...]
    locked_mood: str | None


def default_preset(product: str | None) -> str:
    return PRODUCT_DEFAULT_PRESET.get(product or "", FALLBACK_PRESET)


def effective(cfg: PersonalityConfig, product: str | None = None) -> EffectiveProfile:
    """Preset values, overridden dimension by dimension by what the owner set."""
    preset = cfg.preset or default_preset(product)
    tone, formality, warmth, humor, concision = PRESETS[preset]
    return EffectiveProfile(
        mode=cfg.mode,
        preset=preset,
        tone=cfg.tone or tone,
        formality=formality if cfg.formality is None else cfg.formality,
        warmth=warmth if cfg.warmth is None else cfg.warmth,
        humor=humor if cfg.humor is None else cfg.humor,
        concision=concision if cfg.concision is None else cfg.concision,
        expressive_range=cfg.expressive_range,
        moods_allowed=tuple(cfg.moods_allowed),
        locked_mood=cfg.locked_mood if cfg.locked_mood in cfg.moods_allowed else None,
    )


@dataclass(frozen=True, slots=True)
class ParsedProfile:
    config: PersonalityConfig
    #: ok | migrated | defaults (unreadable) | newer (written by a newer build)
    status: str = "ok"
    reason: str = ""


def migrate(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Bring an older block to the current schema. Missing version means version 0.

    v0 -> v1: no field changes (v0 is a block written before the version existed); the version is
    stamped so the next write is explicit. Each future step goes here, in order, one per version.
    """
    block = dict(raw)
    version = block.get("schema_version", 0)
    if isinstance(version, int) and not isinstance(version, bool) and version < 1:
        block["schema_version"] = 1
    return block


def parse(raw: object) -> ParsedProfile:
    """The profile for a raw ``[brain.personality]`` value, never raising.

    ``None`` or empty = the defaults, status ok. A block of a newer schema is not interpreted
    (fields could mean something else): defaults, status ``newer``. A block that does not validate
    is not partly applied: defaults, status ``defaults`` with the first problem named.
    """
    if raw is None or raw == {}:
        return ParsedProfile(PersonalityConfig())
    if not isinstance(raw, Mapping):
        return ParsedProfile(
            PersonalityConfig(), "defaults", "the personality block is not a table")
    version = raw.get("schema_version", 0)
    is_int = isinstance(version, int) and not isinstance(version, bool)
    if is_int and version > CURRENT_SCHEMA_VERSION:
        return ParsedProfile(
            PersonalityConfig(), "newer",
            f"written by a newer version (schema {version}); using the defaults",
        )
    migrated = migrate(raw)
    try:
        cfg = PersonalityConfig.model_validate(migrated)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ())) or "profile"
        log.warning("personality: the saved profile is not valid (%s); using the defaults", where)
        reason = f"{where}: {first.get('msg', 'invalid')}"
        return ParsedProfile(PersonalityConfig(), "defaults", reason)
    status = "migrated" if migrated.get("schema_version") != raw.get("schema_version") else "ok"
    return ParsedProfile(cfg, status)


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "EffectiveProfile",
    "PRESETS",
    "ParsedProfile",
    "default_preset",
    "effective",
    "migrate",
    "parse",
]
