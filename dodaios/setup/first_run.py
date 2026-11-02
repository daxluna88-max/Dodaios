"""The guided first run of Consumer and Work, applied to the REAL config (issue 18, ADR-0057).

It extends the onboarding that already exists (``dodaios.setup.state``, ``/api/onboarding``): the
choices are kept in the same per-product state file, and nothing here starts a second system.

What it does, and what it deliberately does not:

* **Choices are saved as the owner goes** (back and resume lose nothing) and are only APPLIED to
  the configuration in one explicit step. The step writes up to three things, in order: the
  personality block (``[brain.personality]``, unless skipped), ``[safety] approval_mode = "ask"``
  and ``[memory.learning] enabled``. Only after every write succeeded is the applied mark stored;
  a failure in the middle leaves it unset, so the first run is never reported done on a
  half-written config. The writes are idempotent, so the step can simply be run again.
* **Only new installs.** An install that already finished onboarding never sees this flow, so its
  approval mode and memory switch are not changed behind its owner's back.
* **No permission, download, connection or paid call.** The summary reads configuration and the
  same no-contact credential check the fan-out screen uses. «Usable engine» is never claimed: a
  model is not verified without a request, so the summary says it is not verified.
* **The owner's name stays on this computer.** It is shown in the app and never put in a prompt
  or sent to an engine.
* **Memory.** There is no per-note approval in the app. «Ask before saving» is therefore delivered
  as the learning loop being OFF until the owner turns it on, and the summary says exactly that.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from dodaios.setup import state as st

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STATE_KEY = "first_run"
APPLIED_KEY = "first_run_applied"

Interaction = Literal["text", "voice", "both"]
ToneChoice = Literal["keep", "adapt"]
MemoryChoice = Literal["off", "on"]

#: What each product offers as a personality preview -> the real settings it stands for.
#: ``keep``/``adapt`` choose the mode; the preset and the dials come from here, never a fourth mode.
PRESET_SETTINGS: dict[str, dict[str, dict[str, Any]]] = {
    "consumer": {
        "friendly": {"preset": "friendly"},
        "ironic": {"preset": "friendly", "humor": 3},
        "essential": {"preset": "concise"},
    },
    "work": {
        "professional": {"preset": "professional"},
        "collaborative": {"preset": "friendly", "humor": 0, "formality": 2},
        "essential": {"preset": "concise"},
    },
}
DEFAULT_PRESET = {"consumer": "friendly", "work": "professional"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def product_key(product: str | None) -> str:
    """The first-run flow of a product; the unscoped app follows Consumer."""
    return "work" if product == "work" else "consumer"


class FirstRunChoices(BaseModel):
    """What the owner chose so far. Every field has a safe default: skipping changes nothing."""

    owner_name: str | None = Field(default=None, max_length=60)
    space_name: str | None = Field(default=None, max_length=60)
    #: Text is always available; voice is only a preference, the microphone is asked for at its use.
    interaction: Interaction = "text"
    personality_skip: bool = False
    personality_tone: ToneChoice = "adapt"
    personality_preset: str | None = Field(default=None, max_length=20)
    memory: MemoryChoice = "off"

    @field_validator("owner_name", "space_name", mode="after")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _CONTROL.sub("", value).strip()
        return cleaned or None


class FirstRunError(RuntimeError):
    """A write of the apply step failed; ``done`` lists what was written before it."""

    def __init__(self, step: str, done: list[str]) -> None:
        super().__init__(f"could not apply the first-run choices at {step!r}")
        self.step, self.done = step, done


@dataclass(slots=True)
class ApplyResult:
    applied: list[str] = field(default_factory=list)
    #: Nothing to do: the same choices were already applied.
    already: bool = False


# -- saved choices -------------------------------------------------------------------------------


def load_choices(path: Path | None = None) -> FirstRunChoices:
    """The saved choices, or the defaults when there are none or the entry is damaged."""
    raw = st.load_setup_state(path).get(STATE_KEY)
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return FirstRunChoices()
    try:
        return FirstRunChoices.model_validate(raw.get("choices") or {})
    except ValueError:
        log.warning("first run: the saved choices are not valid; starting from the defaults")
        return FirstRunChoices()


def save_choices(choices: FirstRunChoices, path: Path | None = None) -> None:
    st._merge_state(  # noqa: SLF001 - same package, same atomic writer
        {STATE_KEY: {"schema_version": SCHEMA_VERSION, "choices": choices.model_dump()}}, path)


def _digest(product: str, choices: FirstRunChoices) -> str:
    blob = json.dumps([product, choices.model_dump(exclude={"owner_name", "space_name"})],
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def reset_choices(path: Path | None = None) -> None:
    """Forget the saved choices and the applied mark. The configuration already written stays as
    it is: the summary always reads it back, so nothing is hidden by the reset."""
    st._merge_state({STATE_KEY: None, APPLIED_KEY: None}, path)  # noqa: SLF001


def is_applied(product: str | None, choices: FirstRunChoices, path: Path | None = None) -> bool:
    """The configuration holds exactly these choices (an edited choice is not applied yet)."""
    return applied_digest(path) == _digest(product_key(product), choices)


def applied_digest(path: Path | None = None) -> str | None:
    raw = st.load_setup_state(path).get(APPLIED_KEY)
    return raw.get("digest") if isinstance(raw, dict) else None


# -- what the choices mean -----------------------------------------------------------------------


def personality_block(product: str, choices: FirstRunChoices) -> dict[str, Any] | None:
    """The ``[brain.personality]`` block the choices stand for; None when the step was skipped.

    «Keep this tone» is the manual mode, «adapt the tone to the situation» is the mixed mode (the
    default #17 recommends). ``adaptive`` stays an advanced setting.
    """
    if choices.personality_skip:
        return None
    key = product_key(product)
    preset = choices.personality_preset or DEFAULT_PRESET[key]
    if preset not in PRESET_SETTINGS[key]:
        raise ValueError(f"{preset!r} is not a personality for {key}")
    return {"mode": "manual" if choices.personality_tone == "keep" else "mixed",
            **PRESET_SETTINGS[key][preset]}


def apply(
    product: str | None,
    choices: FirstRunChoices,
    *,
    state_path: Path | None = None,
    config_path: Path | None = None,
    clock: Callable[[], str] = st._now_iso,  # noqa: SLF001
) -> ApplyResult:
    """Write the choices to the configuration; mark them applied only when ALL writes worked.

    ``ValueError`` (a personality that does not belong to the product) is raised before anything is
    written. A write failure raises ``FirstRunError`` and leaves the applied mark unset.
    """
    from dodaios.core import config as cfg_mod  # noqa: PLC0415
    from dodaios.core import config_writer  # noqa: PLC0415

    key = product_key(product)
    block = personality_block(key, choices)  # validates before any write
    digest = _digest(key, choices)
    if applied_digest(state_path) == digest:
        return ApplyResult(already=True)
    cfg_path = config_path or cfg_mod.resolve_config_path()
    steps: list[tuple[str, Callable[[], object]]] = []
    if block is not None:
        steps.append(("personality", lambda: config_writer.set_personality(block, path=cfg_path)))
    elif _has_personality(cfg_path):
        # Skipped after a first choice: back to the product default.
        steps.append(("personality", lambda: config_writer.reset_personality(path=cfg_path)))
    steps.append(("approval", lambda: config_writer.set_safety_approval_mode("ask", path=cfg_path)))
    steps.append(("memory", lambda: config_writer.set_learning_enabled(
        choices.memory == "on", path=cfg_path)))
    done: list[str] = []
    for name, write in steps:
        try:
            write()
        except Exception as exc:
            log.warning("first run: writing %s failed; nothing is marked applied", name,
                        exc_info=True)
            raise FirstRunError(name, done) from exc
        done.append(name)
    st._merge_state(  # noqa: SLF001
        {APPLIED_KEY: {"schema_version": SCHEMA_VERSION, "digest": digest, "at": clock()}},
        state_path)
    return ApplyResult(applied=done)


def _has_personality(config_path: Path) -> bool:
    import tomllib  # noqa: PLC0415

    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # no readable file means no block to take away
        return False
    return "personality" in data.get("brain", {})


# -- the summary, from the real configuration ----------------------------------------------------


def engine_status(product: str | None, cfg: Any, registry: Any | None = None) -> dict[str, Any]:
    """The selected engine split into installed / allowed / authenticated, and never «verified».

    No provider is contacted: authentication is read from a stored credential or the plan-login
    probe, and a local engine's server is not checked. ``ready`` means «a conversation can be
    attempted», not «it was seen to answer».
    """
    from dodaios.brain import engine_policy  # noqa: PLC0415
    from dodaios.brain import multi_engine_chat as mec  # noqa: PLC0415
    from dodaios.brain.provider_registry import BrainProviderRegistry  # noqa: PLC0415

    provider = str(getattr(cfg.brain, "primary", "") or "")
    out: dict[str, Any] = {"provider": provider, "kind": None, "local": None, "installed": False,
                           "allowed": False, "authenticated": False, "ready": False,
                           "verified": False, "reason": ""}
    if not provider:
        out["reason"] = "no_engine"
        return out
    registry = registry or BrainProviderRegistry()
    out["installed"] = provider in set(registry.available())
    kind = mec.connection_kind(provider)
    out["kind"], out["local"] = kind, engine_policy.destination_of(provider).local
    try:
        engine_policy.check_provider(provider, product=product)
        out["allowed"] = True
    except engine_policy.EnginePolicyError:  # said to the owner through ``reason``
        out["reason"] = "work_local_only"
        return out
    if not out["installed"]:
        out["reason"] = "not_installed"
        return out
    out["authenticated"] = mec.authenticated(provider, kind, registry)
    if not out["authenticated"]:
        out["reason"] = "not_connected"
        return out
    out["ready"] = True
    out["reason"] = "local_server_not_checked" if kind == "local" else "not_verified_with_a_request"
    return out


def summary(
    product: str | None, choices: FirstRunChoices, cfg: Any, registry: Any | None = None
) -> dict[str, Any]:
    """«Your Jarvis»: the choices as they are in the configuration now, not as they were hoped."""
    from dodaios.brain import personality  # noqa: PLC0415

    key = product_key(product)
    profile = personality.effective(cfg.brain.personality, key)
    engine = engine_status(product, cfg, registry)
    return {
        "product": key,
        "interaction": choices.interaction,
        "personality": {"preset": profile.preset, "mode": profile.mode},
        "engine": engine,
        "memory": {"learning_enabled": bool(cfg.memory.learning.enabled)},
        "actions": {"approval_mode": str(cfg.safety.approval_mode)},
        "start": "start" if engine["ready"] else "explore",
    }


__all__ = [
    "ApplyResult",
    "DEFAULT_PRESET",
    "FirstRunChoices",
    "FirstRunError",
    "PRESET_SETTINGS",
    "apply",
    "applied_digest",
    "is_applied",
    "engine_status",
    "load_choices",
    "personality_block",
    "product_key",
    "reset_choices",
    "save_choices",
    "summary",
]
