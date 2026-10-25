# ADR-0047 — Italian local voices and an optional Kokoro backend

**Status:** Accepted · **Date:** 2026-10-25

## Context

The local voice engine (ADR-0037) speaks German and English only: `piper.VOICES` has no Italian
entry and the model registry holds no Italian voice. Parakeet TDT 0.6B v3 (the engine's recogniser)
already covers Italian, and `ClauseChunker`, the engine and the selftest are language-parametric.
Kokoro-82M is a natural-sounding CPU voice with Italian speakers, and sherpa-onnx (already the
Piper runtime) runs it, so no new package is needed.

## Decision

1. Registry: `piper-it-paola-medium`, `piper-it-riccardo-x-low` and `kokoro-multi-lang-v1_0`, each with a
   pinned size and SHA-256 measured on the downloaded archive (2026-10-25) and an honest licence note
   (dataset terms that were not checked are stated as unchecked, not assumed).
2. `PiperTts.for_language("it")` uses Paola; `voice="piper-it-riccardo-x-low"` selects the alternative. A voice of
   another language is refused.
3. New backend `kokoro` (`voice_engine/tts/kokoro.py`) through sherpa-onnx, Italian first: speakers
   `if_sara` (35) and `im_nicola` (36), read from the model metadata. The model (~350 MB) is fetched
   explicitly, never at boot, and is never a silent fallback for another voice.
4. Selftest phrase for Italian; `ClauseChunker` learns the unambiguous Italian abbreviations (dott., sig., prof.,
   ing., avv., ecc., ...) so a title does not end a sentence.
5. Nothing changes for German and English, and the default TTS ladder is untouched: Kokoro is opt-in.

## Consequences

- Piper stays the floor. Measured on a 4-core Linux CPU (see the Jarvis integration repository, `docs/VOICE.md`),
  Piper's first clause is far faster than Kokoro's; Kokoro is the more natural-sounding option only after listening.
- Both Piper and Kokoro phonemize Italian with espeak-ng data; the dental diacritic (U+032A) is not in either
  model's vocabulary and is skipped by sherpa-onnx, so pronunciation is close, not exact.
- The Paola voice was fine-tuned from the en_US lessac checkpoint whose own terms are unchecked: review before
  any commercial redistribution.
