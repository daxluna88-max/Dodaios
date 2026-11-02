"""Chat commands about the assistant's style: «more serious», «less irony», «stay like this»...

Deterministic and anchored: a command is a SHORT message that is only the command (with a polite
filler around it). A longer request that merely contains such words ("write a more serious letter")
is a normal turn and never reaches here. No model is asked what the owner meant.

Rules from issue 17:

* a clear command changes THIS SESSION only (a mood event or a session nudge of one dimension);
  nothing here writes the saved profile, and a words like "always" does not change that: the reply
  says the saved personality is changed in Settings;
* an unclear command (``be more X`` with an unknown X, ``change your tone``) gets a clarifying
  question and changes nothing;
* it never touches tools, approvals, budgets or stopping.

Italian and English are recognised; the reply uses the language of the command.
"""

# ruff: noqa: E501 - the command table and the phrases read best one entry per line.
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from dodaios.brain import mood as mood_mod

_FILLER = re.compile(
    r"\b(per favore|per piacere|per cortesia|please|ok|okay|ora|adesso|now|jarvis|grazie|thanks)\b"
)
_PERMANENT = re.compile(r"\b(sempre|per sempre|d ora in poi|always|from now on|forever)\b")
MAX_WORDS = 7

#: (pattern, kind, argument). Patterns match the WHOLE normalised message.
_COMMANDS: list[tuple[str, str, object]] = [
    # moods
    (r"(sii |diventa |fai il |be |become )?(piu|more) seri(?:o|a|ous)?", "mood", "serious"),
    (r"(sii |diventa |be |become )?(piu|more) (allegr[oa]|solare|cheerful|upbeat)", "mood", "cheerful"),
    (r"(sii |diventa |be |become )?(piu|more) (calm[oa]?|tranquill[oa])|calmati|calm down|be calmer", "mood", "calm"),
    (r"(sii |diventa |be |become )?(piu|more) (concentrat[oa]|focused)", "mood", "focused"),
    (r"resta cosi|rimani cosi|mantieni (questo|il) tono|stay like this|keep it like this|stay this way", "hold", None),
    (r"torna neutr[oa]|torna normale|sii neutr[oa]|back to neutral|go neutral|be neutral", "neutral", None),
    (r"(disattiva|spegni) (l )?adattamento|turn off adaptation|disable adaptation|stop adapting", "adaptation", False),
    (r"(attiva|accendi) (l )?adattamento|turn on adaptation|enable adaptation", "adaptation", True),
    # dimensions (session nudges)
    (r"(niente|senza|no) (ironia|battute|umorismo|jokes|humou?r|irony)|no more jokes", "nudge", ("humor", -4)),
    (r"meno (ironia|battute|umorismo)|less (irony|jokes|humou?r|joking)", "nudge", ("humor", -1)),
    (r"(piu|more) (ironia|battute|umorismo)|more (irony|jokes|humou?r|joking)", "nudge", ("humor", 1)),
    (r"(piu|more) formal[ei]?|be more formal", "nudge", ("formality", 1)),
    (r"meno formal[ei]?|less formal|more casual", "nudge", ("formality", -1)),
    (r"(piu|more) (caloros[oa]|warm|warmer)|be warmer", "nudge", ("warmth", 1)),
    (r"(piu|more) (freddo|distaccat[oa])|be (colder|cooler)", "nudge", ("warmth", -1)),
    (r"(piu|more) (brev[ei]|concis[oa]|sintetic[oa]|short|shorter|concise)|be (shorter|briefer)", "nudge", ("concision", 1)),
    (r"(piu|more) (dettagliat[oa]|detailed|thorough)|be more detailed", "nudge", ("concision", -1)),
    (r"stile predefinito|ripristina lo stile|reset (the )?style|default style", "reset_style", None),
]
_COMPILED = [(re.compile(rf"^(?:{pattern})$"), kind, arg) for pattern, kind, arg in _COMMANDS]
# «be more X» / «sii più X» with an X we do not know, or «change your tone».
_UNCLEAR = re.compile(
    r"^(?:(?:sii|diventa|be|become) (?:piu|more) \w+|(?:cambia|change) (?:il |your |the )?(?:tono|stile|"
    r"personalita|tone|style|personality)(?: \w+)?|sii diverso|be different)$"
)
_ITALIAN_MARKERS = re.compile(
    r"\b(sii|piu|meno|resta|torna|attiva|disattiva|niente|senza|cambia|calmati|stile|ironia|battute)\b"
)


def _normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    plain = re.sub(r"[^\w\s]", " ", plain.replace("'", " "))
    return re.sub(r"\s+", " ", plain).strip()


@dataclass(frozen=True, slots=True)
class StyleCommand:
    #: mood | hold | neutral | adaptation | nudge | reset_style | clarify
    kind: str
    argument: object = None
    language: str = "en"
    #: The words said a permanent change («always»): applied to this session only.
    wanted_permanent: bool = False

    def event(self) -> mood_mod.Event | None:
        if self.kind == "mood":
            return mood_mod.OwnerMood(str(self.argument), 2)
        if self.kind == "hold":
            return mood_mod.OwnerHold()
        if self.kind == "neutral":
            return mood_mod.OwnerNeutral()
        if self.kind == "adaptation":
            return mood_mod.OwnerAdaptation(bool(self.argument))
        return None


def parse(text: str) -> StyleCommand | None:
    """The style command a message is, or None when it is an ordinary message."""
    norm = _normalise(text)
    permanent = bool(_PERMANENT.search(norm))
    # The permanent wish goes first: "from now on" holds a filler word ("now") of its own.
    norm = _normalise(_FILLER.sub(" ", _PERMANENT.sub(" ", norm)))
    if not norm or len(norm.split()) > MAX_WORDS:
        return None
    language = "it" if _ITALIAN_MARKERS.search(norm) else "en"
    for pattern, kind, arg in _COMPILED:
        if pattern.match(norm):
            return StyleCommand(kind, arg, language, permanent)
    if _UNCLEAR.match(norm):
        return StyleCommand("clarify", None, language, permanent)
    return None


_REFUSALS = {
    "it": {"mood_not_allowed": "quell'umore non è consentito dalle tue impostazioni",
           "nothing_to_hold": "non c'è nessun umore da mantenere",
           "bad_value": "valore non valido"},
    "en": {"mood_not_allowed": "that mood is not allowed in your settings",
           "nothing_to_hold": "there is no mood to hold",
           "bad_value": "not a valid value"},
}


def refusal_text(code: str, language: str) -> str:
    return _REFUSALS.get(language, _REFUSALS["en"]).get(code, code)


_PHRASES = {
    "it": {
        "mood": "Va bene, per questa conversazione sarò {mood}.",
        "hold": "Va bene, resto così per questa conversazione.",
        "neutral": "Va bene, torno neutro.",
        "adaptation_off": "Va bene, per questa conversazione non adatto più il mio modo di parlare.",
        "adaptation_on": "Va bene, adatto di nuovo il mio modo di parlare entro i limiti che hai scelto.",
        "nudge": "Va bene, per questa conversazione cambio così: {what}.",
        "reset_style": "Va bene, torno allo stile salvato.",
        "clarify": "Che cosa intendi? Posso essere più serio, più allegro, più calmo o più "
                   "concentrato, e cambiare ironia, formalità, calore e lunghezza.",
        "refused": "Questo non è possibile con le impostazioni attuali: {why}.",
        "permanent": " Per renderlo permanente usa le impostazioni della personalità.",
        "moods": {"serious": "più serio", "cheerful": "più allegro", "calm": "più calmo",
                  "focused": "più concentrato"},
        "dims": {("humor", -1): "meno ironia", ("humor", -4): "senza ironia",
                 ("humor", 1): "più ironia", ("formality", 1): "più formale",
                 ("formality", -1): "meno formale", ("warmth", 1): "più caloroso",
                 ("warmth", -1): "più distaccato", ("concision", 1): "più breve",
                 ("concision", -1): "più dettagliato"},
    },
    "en": {
        "mood": "Okay, I'll be {mood} for this conversation.",
        "hold": "Okay, I'll stay like this for this conversation.",
        "neutral": "Okay, back to neutral.",
        "adaptation_off": "Okay, I'll stop adapting how I speak for this conversation.",
        "adaptation_on": "Okay, I'll adapt how I speak again, within the limits you chose.",
        "nudge": "Okay, for this conversation: {what}.",
        "reset_style": "Okay, back to your saved style.",
        "clarify": "What do you mean? I can be more serious, more cheerful, calmer or more "
                   "focused, and change humor, formality, warmth and length.",
        "refused": "I can't do that with the current settings: {why}.",
        "permanent": " To make it permanent, use the personality settings.",
        "moods": {"serious": "more serious", "cheerful": "more cheerful", "calm": "calmer",
                  "focused": "more focused"},
        "dims": {("humor", -1): "less humor", ("humor", -4): "no humor",
                 ("humor", 1): "more humor", ("formality", 1): "more formal",
                 ("formality", -1): "less formal", ("warmth", 1): "warmer",
                 ("warmth", -1): "cooler", ("concision", 1): "shorter",
                 ("concision", -1): "more detailed"},
    },
}


def reply(cmd: StyleCommand, *, refused: str = "") -> str:
    """The short confirmation (or question) for a command, in the command's language."""
    ph = _PHRASES[cmd.language if cmd.language in _PHRASES else "en"]
    if refused:
        text = ph["refused"].format(why=refused)
    elif cmd.kind == "mood":
        text = ph["mood"].format(mood=ph["moods"][str(cmd.argument)])
    elif cmd.kind == "adaptation":
        text = ph["adaptation_on" if cmd.argument else "adaptation_off"]
    elif cmd.kind == "nudge":
        text = ph["nudge"].format(what=ph["dims"][cmd.argument])  # type: ignore[index]
    else:
        text = ph[cmd.kind]
    if cmd.wanted_permanent and not refused and cmd.kind != "clarify":
        text += ph["permanent"]
    return text


__all__ = ["MAX_WORDS", "StyleCommand", "parse", "refusal_text", "reply"]
