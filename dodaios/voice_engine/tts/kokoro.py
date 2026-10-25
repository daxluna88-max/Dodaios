"""Kokoro-82M through sherpa-onnx: a natural CPU voice, optional (ADR-0047).

Same runtime as the Piper floor (Apache-2.0 sherpa-onnx), so no extra package. The
multi-language v1.0 model is about 350 MB; it is fetched explicitly, never at boot. Non-English
text is phonemized with the bundled espeak-ng data (``lang``), and the speaker ids below
were read from the model's own metadata (``speaker_names``), not assumed.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from dodaios.voice_engine.models import is_present, model_path
from dodaios.voice_engine.stt import default_threads

MODEL = "kokoro-multi-lang-v1_0"

#: Speaker ids of the Italian voices in the v1.0 voice table (if = Italian female, im = male).
ITALIAN_SPEAKERS = {"if_sara": 35, "im_nicola": 36}
#: Default speaker and espeak-ng language per app language.
DEFAULTS = {"it": ("if_sara", "it")}


class KokoroTts:
    name = "kokoro"

    def __init__(
        self,
        model_dir: Path,
        *,
        speaker: int,
        lang: str,
        speed: float = 1.0,
        threads: int | None = None,
    ) -> None:
        import sherpa_onnx  # noqa: PLC0415 - heavy runtime, imported on use

        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                    model=str(model_dir / "model.onnx"),
                    voices=str(model_dir / "voices.bin"),
                    tokens=str(model_dir / "tokens.txt"),
                    data_dir=str(model_dir / "espeak-ng-data"),
                    dict_dir=str(model_dir / "dict"),
                    lexicon="",
                    lang=lang,
                ),
                num_threads=threads or default_threads(),
                provider="cpu",
            ),
        )
        self._tts = sherpa_onnx.OfflineTts(config)
        self._speaker, self._speed = int(speaker), float(speed)
        self.sample_rate = int(self._tts.sample_rate)

    @classmethod
    def for_language(
        cls,
        language: str,
        *,
        voice: str | None = None,
        speed: float = 1.0,
        threads: int | None = None,
    ) -> KokoroTts:
        default = DEFAULTS.get(language)
        if default is None:
            raise RuntimeError(f"no Kokoro voice configured for language {language!r}")
        if not is_present(MODEL):
            raise RuntimeError(f"model {MODEL} is not installed (fetch it explicitly)")
        voice = voice or default[0]
        if voice not in ITALIAN_SPEAKERS:
            raise RuntimeError(f"unknown Kokoro voice {voice!r}; known: {sorted(ITALIAN_SPEAKERS)}")
        return cls(
            model_path(MODEL),
            speaker=ITALIAN_SPEAKERS[voice],
            lang=default[1],
            speed=speed,
            threads=threads,
        )

    def stream(self, text: str, *, stop: threading.Event | None = None) -> Iterator[np.ndarray]:
        if stop is not None and stop.is_set():
            return
        audio = self._tts.generate(text, sid=self._speaker, speed=self._speed)
        yield np.asarray(audio.samples, dtype=np.float32)
