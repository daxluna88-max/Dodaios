"""Italian local voices (ADR-0047): registry pins, Piper voice choice, Kokoro (no model files)."""

from __future__ import annotations

import re
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest

from dodaios.voice_engine import models as store
from dodaios.voice_engine.runtime import SELFTEST_PHRASES
from dodaios.voice_engine.tts import kokoro, load_tts, piper

ITALIAN_MODELS = ("piper-it-paola-medium", "piper-it-riccardo-x-low", "kokoro-multi-lang-v1_0")


@pytest.mark.parametrize("name", ITALIAN_MODELS)
def test_italian_models_are_pinned(name: str) -> None:
    source = store.REGISTRY[name]
    assert source.url.startswith("https://") and source.size > 0
    assert re.fullmatch(r"[0-9a-f]{64}", source.sha256), (
        "a model without a checksum cannot be fetched"
    )
    assert source.licence and source.is_archive


def test_every_voice_has_a_registered_model() -> None:
    for voice in (
        *piper.VOICES.values(),
        *[v for vs in piper.ALTERNATIVE_VOICES.values() for v in vs],
    ):
        assert voice in store.REGISTRY
    assert kokoro.MODEL in store.REGISTRY
    assert piper.VOICES["it"] == "piper-it-paola-medium"
    assert "it" in SELFTEST_PHRASES


def test_piper_refuses_a_voice_outside_the_language_or_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(piper, "is_present", lambda name, root=None: False)
    with pytest.raises(RuntimeError, match="no Piper voice"):
        piper.PiperTts.for_language("it")
    monkeypatch.setattr(piper, "is_present", lambda name, root=None: True)
    with pytest.raises(RuntimeError, match="no Piper voice"):
        piper.PiperTts.for_language(
            "it", voice="piper-de-thorsten-medium"
        )  # another language's voice
    with pytest.raises(RuntimeError, match="no Piper voice"):
        piper.PiperTts.for_language("fr")


def test_piper_picks_the_alternative_italian_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []
    monkeypatch.setattr(piper, "is_present", lambda name, root=None: True)
    monkeypatch.setattr(
        piper.PiperTts, "__init__", lambda self, voice_dir, **kw: seen.append(voice_dir)
    )
    piper.PiperTts.for_language("it", voice="piper-it-riccardo-x-low")
    piper.PiperTts.for_language("it")
    assert [p.name for p in seen] == [
        "vits-piper-it_IT-riccardo-x_low",
        "vits-piper-it_IT-paola-medium",
    ]


def test_kokoro_speaker_ids_are_the_italian_ones() -> None:
    assert kokoro.ITALIAN_SPEAKERS == {"if_sara": 35, "im_nicola": 36}
    assert kokoro.DEFAULTS["it"] == ("if_sara", "it")


def test_kokoro_refuses_when_not_installed_unknown_voice_or_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kokoro, "is_present", lambda name, root=None: False)
    with pytest.raises(RuntimeError, match="not installed"):
        load_tts("kokoro", "it")
    monkeypatch.setattr(kokoro, "is_present", lambda name, root=None: True)
    with pytest.raises(RuntimeError, match="unknown Kokoro voice"):
        load_tts("kokoro", "it", voice="af_bella")
    with pytest.raises(RuntimeError, match="no Kokoro voice configured"):
        load_tts("kokoro", "de")


class _FakeGenerated:
    samples = [0.1, -0.1, 0.2]
    sample_rate = 24000


def _fake_sherpa(calls: dict) -> types.ModuleType:
    mod = types.ModuleType("sherpa_onnx")
    mod.OfflineTtsKokoroModelConfig = lambda **kw: calls.setdefault("kokoro", kw)
    mod.OfflineTtsModelConfig = lambda **kw: calls.setdefault("model", kw)
    mod.OfflineTtsConfig = lambda **kw: calls.setdefault("config", kw)

    class Tts:
        sample_rate = 24000

        def __init__(self, config) -> None:
            calls["built"] = config

        def generate(self, text: str, sid: int = 0, speed: float = 1.0):
            calls.setdefault("generated", []).append((text, sid, speed))
            return _FakeGenerated()

    mod.OfflineTts = Tts
    return mod


def test_kokoro_passes_language_speaker_and_streams(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict = {}
    monkeypatch.setitem(sys.modules, "sherpa_onnx", _fake_sherpa(calls))
    monkeypatch.setattr(kokoro, "is_present", lambda name, root=None: True)
    monkeypatch.setattr(kokoro, "model_path", lambda name, root=None: tmp_path)
    tts = load_tts("kokoro", "it", voice="im_nicola", speed=1.1)
    assert calls["kokoro"]["lang"] == "it" and calls["kokoro"]["lexicon"] == ""
    chunks = list(tts.stream("Fermati."))
    assert len(chunks) == 1 and chunks[0].dtype == np.float32 and tts.sample_rate == 24000
    assert calls["generated"] == [("Fermati.", 36, 1.1)]


def test_kokoro_stream_honours_stop_before_generating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict = {}
    monkeypatch.setitem(sys.modules, "sherpa_onnx", _fake_sherpa(calls))
    monkeypatch.setattr(kokoro, "is_present", lambda name, root=None: True)
    monkeypatch.setattr(kokoro, "model_path", lambda name, root=None: tmp_path)
    tts = load_tts("kokoro", "it")
    stop = threading.Event()
    stop.set()
    assert list(tts.stream("Non deve parlare.", stop=stop)) == []
    assert "generated" not in calls
