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
import importlib.util
import json
import logging
import os
import re
import shutil
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
#: Step journal of the apply step (block 11): which writes of the CURRENT choices are done.
JOURNAL_KEY = "first_run_journal"

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


class IsolationError(FirstRunError):
    """State or config file outside THIS product's own data directory: nothing is written."""

    def __init__(self, reason: str) -> None:
        super().__init__("isolation", [])
        self.reason = reason


def check_isolation(
    product: str | None, state_path: Path | None = None, config_path: Path | None = None
) -> None:
    """Refuse to touch first-run files that are not in the running product's own directory.

    Only for a product launch (``JARVIS_PRODUCT`` set): the state file's folder must pass
    ``product_profile.check_product_root`` (a real directory, no overlap with the shared root or
    the OTHER product's root), carry this product's ownership marker, and the config file must
    sit in that same folder. The unscoped app and tests have no product root and are not checked.
    Reads only.
    """
    from dodaios.core import product_profile as pp  # noqa: PLC0415

    scoped = (os.environ.get(pp.PRODUCT_ENV) or "").strip().lower()
    if not scoped:
        return
    if scoped != product_key(product) or scoped not in pp.PRODUCTS:
        raise IsolationError(f"the run is for {product_key(product)} but the process is {scoped}")
    folder = st.state_path(state_path).parent
    try:
        root = pp.check_product_root(scoped, folder)
        owner = pp._read_owner(root / pp.MARKER_NAME)  # noqa: SLF001 - same codebase, read only
    except pp.ProductError as exc:
        raise IsolationError(str(exc)) from exc
    if owner != scoped:
        raise IsolationError(f"{root} is not claimed by the {scoped} product")
    if config_path is not None and Path(config_path).resolve().parent != root:
        raise IsolationError(f"{config_path} is not inside the {scoped} directory")


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


def save_choices(
    choices: FirstRunChoices, path: Path | None = None, product: str | None = None
) -> None:
    """Save the choices atomically; raises ``FirstRunError("save")`` when they did not land
    (the writer itself only logs), so the window never shows progress that is not on disk."""
    check_isolation(product if product is not None else os.environ.get("JARVIS_PRODUCT"), path)
    st._merge_state(  # noqa: SLF001 - same package, same atomic writer
        {STATE_KEY: {"schema_version": SCHEMA_VERSION, "choices": choices.model_dump()}}, path)
    if load_choices(path) != choices:
        raise FirstRunError("save", [])


def _digest(product: str, choices: FirstRunChoices) -> str:
    blob = json.dumps([product, choices.model_dump(exclude={"owner_name", "space_name"})],
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


#: What a reset can ALSO take away, only when the owner picks it. Credentials and the memory
#: content are never offered here: they have their own removal in Settings.
RESET_OPTIONS = ("personality_config", "learning_switch")
#: Always kept by a first-run reset.
RESET_KEEPS = ("terms_acceptance", "credentials", "memory_content", "other_product",
               "approval_mode")
_RESET_STATE_KEYS = (STATE_KEY, APPLIED_KEY, JOURNAL_KEY, "first_run_applied_at", "onboarding_step",
                     "onboarding_skipped_steps")


def reset_plan(path: Path | None = None, include: tuple[str, ...] = ()) -> dict[str, Any]:
    """What a reset would remove and keep, before anything is removed. Reads only."""
    bad = [i for i in include if i not in RESET_OPTIONS]
    if bad:
        raise ValueError(f"unknown reset option(s): {', '.join(bad)}")
    present = st.load_setup_state(path)
    return {"removes_state": [k for k in _RESET_STATE_KEYS if k in present],
            "removes_config": list(include), "keeps": list(RESET_KEEPS),
            "options": list(RESET_OPTIONS)}


def reset_choices(
    path: Path | None = None,
    *,
    product: str | None = None,
    include: tuple[str, ...] = (),
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Forget THIS product's first-run state (choices, applied mark, journal, resume step).

    Only the product's own state file is touched (isolation checked first). The configuration
    already written stays as it is unless the owner picked ``personality_config`` (back to the
    product default) and/or ``learning_switch`` (memory learning OFF). Terms, credentials, memory
    content, the approval mode and the other product are never touched. Returns the plan that was
    carried out, for the owner to read.
    """
    plan = reset_plan(path, include)
    check_isolation(product if product is not None else os.environ.get("JARVIS_PRODUCT"), path,
                    config_path if include else None)
    if include:
        from dodaios.core import config as cfg_mod  # noqa: PLC0415
        from dodaios.core import config_writer  # noqa: PLC0415

        cfg_path = config_path or cfg_mod.resolve_config_path()
        if "personality_config" in include and _has_personality(cfg_path):
            config_writer.reset_personality(path=cfg_path)
        if "learning_switch" in include:
            config_writer.set_learning_enabled(False, path=cfg_path)
    st.drop_state_keys(_RESET_STATE_KEYS, path)
    return plan


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
    check_isolation(product, state_path, cfg_path)  # before ANY write
    steps: list[tuple[str, Callable[[], object]]] = []
    if block is not None:
        steps.append(("personality", lambda: config_writer.set_personality(block, path=cfg_path)))
    elif _has_personality(cfg_path):
        # Skipped after a first choice: back to the product default.
        steps.append(("personality", lambda: config_writer.reset_personality(path=cfg_path)))
    steps.append(("approval", lambda: config_writer.set_safety_approval_mode("ask", path=cfg_path)))
    steps.append(("memory", lambda: config_writer.set_learning_enabled(
        choices.memory == "on", path=cfg_path)))
    # Step journal: written BEFORE the first write and after each one, so an interruption (crash,
    # closed window, power loss) leaves a record of exactly which steps landed. Each config write
    # is atomic by itself; the journal is information, the applied mark is the only "done".
    prior = journal(state_path)
    done: list[str] = list(prior["done"]) if prior["digest"] == digest else []
    _write_journal(state_path, digest, done, [n for n, _ in steps], clock)
    for name, write in steps:
        try:
            write()  # idempotent: a journalled step whose write was lost is just rewritten
        except Exception as exc:
            log.warning("first run: writing %s failed; nothing is marked applied", name,
                        exc_info=True)
            raise FirstRunError(name, done) from exc
        if name not in done:
            done.append(name)
        _write_journal(state_path, digest, done, [n for n, _ in steps], clock)
    st._merge_state(  # noqa: SLF001
        {APPLIED_KEY: {"schema_version": SCHEMA_VERSION, "digest": digest, "at": clock()}},
        state_path)
    if applied_digest(state_path) != digest:  # the writer only logs: no mark means not done
        raise FirstRunError("mark", done)
    st.drop_state_keys([JOURNAL_KEY], state_path)
    return ApplyResult(applied=done)


def _write_journal(path: Path | None, digest: str, done: list[str], planned: list[str],
                   clock: Callable[[], str]) -> None:
    st._merge_state(  # noqa: SLF001
        {JOURNAL_KEY: {"schema_version": SCHEMA_VERSION, "digest": digest, "done": list(done),
                       "planned": list(planned), "at": clock()}}, path)


def journal(path: Path | None = None) -> dict[str, Any]:
    """The step journal of an apply that did not finish; empty fields when there is none.

    ``resume`` is the first planned step not done: where a re-run continues. It is information for
    the summary and the logs; the writes are idempotent, so correctness never depends on it.
    """
    raw = st.load_setup_state(path).get(JOURNAL_KEY)
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return {"digest": None, "done": [], "planned": [], "resume": None}
    done = [d for d in raw.get("done") or [] if isinstance(d, str)]
    planned = [d for d in raw.get("planned") or [] if isinstance(d, str)]
    return {"digest": raw.get("digest"), "done": done, "planned": planned,
            "resume": next((p for p in planned if p not in done), None)}


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
                           "verified": False, "reason": "", "detected": None,
                           "levels": _levels(bool(provider))}
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
    if kind == "local":
        # A local engine needs no credential, so «authenticated» says nothing here. What can be
        # read without contact: is its program on this computer? (None = cannot be told.)
        out["detected"] = _local_program_present(provider)
        out["levels"] = _levels(True, detected=out["detected"])
        if out["detected"] is not True:
            out["authenticated"] = False
            out["reason"] = "local_not_found" if out["detected"] is False else "local_unconfirmed"
            return out
        out["ready"] = True
        out["reason"] = "local_server_not_checked"
        return out
    if not out["authenticated"]:
        out["reason"] = "not_connected"
        return out
    out["detected"] = True  # a stored credential / sign-in is on this computer
    out["levels"] = _levels(True, detected=True)
    out["ready"] = True
    out["reason"] = "not_verified_with_a_request"
    return out


def _levels(configured: bool, detected: bool | None = None) -> dict[str, str]:
    """The four honest levels of «engine ready». The last two are never reached here:
    ``reachable`` needs a network/loopback contact and ``verified`` needs a real request, and the
    first run does neither (NOT RUN)."""
    return {
        "configured": "yes" if configured else "no",
        "detected": "unknown" if detected is None else ("yes" if detected else "no"),
        "reachable": "not_checked",
        "verified": "not_run",
    }


#: Program names probed on PATH for the local engines (a read of the environment, no process run).
_LOCAL_PROGRAMS = {"ollama": "ollama"}


def _local_program_present(provider: str) -> bool | None:
    """True/False when the engine's program is (not) on this computer; None when it has none to
    look for (a generic local server such as ``local-openai``)."""
    name = _LOCAL_PROGRAMS.get(provider)
    return None if name is None else shutil.which(name) is not None


def components(choices: FirstRunChoices) -> dict[str, dict[str, str]]:
    """What the choices need on this computer, read locally only: no download, no model pull, no
    network, no microphone. ``present`` means the program/package is installed; the model weights
    and the device are NOT checked (``not_checked``)."""
    voice = choices.interaction in ("voice", "both")
    stack = importlib.util.find_spec("faster_whisper") is not None
    return {"voice": {
        "needed": "yes" if voice else "no",
        "speech_package": ("present" if stack else "missing") if voice else "not_needed",
        "model_weights": "not_checked",
        "microphone": "not_asked",
    }}


def permissions(choices: FirstRunChoices, cfg: Any) -> list[dict[str, str]]:
    """Every permission the first run could touch, each OFF unless the owner explicitly chose it.

    The first run grants NONE of microphone, screen, files or network on its own; choosing voice
    only records a preference (the OS asks at first use). Tools: actions ask first. Memory
    learning: read back from the configuration, off by default.
    """
    ask = str(cfg.safety.approval_mode) == "ask"
    return [
        {"id": "microphone", "state": "off",
         "note": "not_requested" if choices.interaction == "text" else "asked_at_first_use"},
        {"id": "screen", "state": "off", "note": "not_requested"},
        {"id": "files", "state": "off", "note": "not_requested"},
        {"id": "network", "state": "off", "note": "not_requested"},
        {"id": "tools", "state": "ask" if ask else "other", "note": "approval_mode"},
        {"id": "memory_learning", "state": "on" if cfg.memory.learning.enabled else "off",
         "note": "explicit_switch"},
    ]


def summary(
    product: str | None, choices: FirstRunChoices, cfg: Any, registry: Any | None = None,
    state_path: Path | None = None,
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
        "components": components(choices),
        "permissions": permissions(choices, cfg),
        "journal": journal(state_path),
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
    "IsolationError",
    "RESET_KEEPS",
    "RESET_OPTIONS",
    "check_isolation",
    "components",
    "engine_status",
    "journal",
    "permissions",
    "reset_plan",
    "load_choices",
    "personality_block",
    "product_key",
    "reset_choices",
    "save_choices",
    "summary",
]
