"""Per-connection browser-microphone voice session (B2).

Ports ``dodaios.telephony.session.TelephonyCallSession`` to browser audio. Raw
int16 PCM frames arrive over a WebSocket from an AudioWorklet (at the browser's
sample rate), are resampled to 16 kHz, endpointed by the torch-free
``EnergyEndpointer``, transcribed (STT), routed to the brain, scrubbed for voice,
synthesized (TTS, 24 kHz int16) and the audio bytes are streamed straight back as
binary WS frames for Web Audio playback.

Transport-decoupled like the telephony session: ``send_binary`` / ``send_json``
are injected async callables, so this is fully testable with fakes and a sink —
no socket, no models, no API key. Stdlib-only on the audio path: it NEVER imports
sounddevice, so a headless €5 VPS reaches the full voice experience.

Pipeline per turn (mirrors the mic + phone paths, AD-OE seams):

    inbound int16 PCM @ browser rate
      -> Resampler(browser_rate -> 16 kHz)
      -> EnergyEndpointer (VAD): accumulate until end-of-turn
      -> STT.transcribe_pcm(pcm, 16000) -> text
      -> ... brain turn ...
      -> scrub_for_voice (regex only, AP-11)
      -> TTS.synthesize(scrubbed) -> 24 kHz int16 PCM
      -> send_binary(pcm) per chunk  (+ tts_start / tts_end control frames)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from dodaios.brain.output_filter import FALLBACK_PHRASES, scrub_for_voice
from dodaios.brain.scrub_verdict import is_harmless_scrub_residue

# Same classifier the realtime scrub gate uses, so a hard leak means the same thing on both voice paths.
from dodaios.realtime.scrub_gate import _is_hard_scrub_result
from dodaios.browser_voice.audio import (
    STT_SAMPLE_RATE,
    TTS_SAMPLE_RATE,
    EnergyEndpointer,
    Resampler,
)
from dodaios.core.events import (
    ResponseGenerated,
    SpeechSpoken,
    TranscriptionUpdate,
    VoiceSessionEnded,
    VoiceSessionStarted,
    VoiceTurnCompleted,
    VoiceTurnStarted,
)
from dodaios.sessions.constants import HANGUP_CLIENT_STOP

log = logging.getLogger("dodaios.browser_voice")

SendBinary = Callable[[bytes], Awaitable[None]]
SendJson = Callable[[dict[str, Any]], Awaitable[None]]

# Browser-native speech is the last-resort output path when a downloader has a
# usable STT/Brain credential but no matching server-side TTS family. The
# browser acknowledges completion so the session does not reopen the endpoint
# detector while its own voice is still audible through the microphone.
_BROWSER_TTS_ACK_TIMEOUT_S = 120.0



_CUT = object()  # piece-stream marker: drop the audio still queued in the browser

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_TTS_MIN_CHARS = 80  # first piece: short, so the voice starts early
_TTS_NEXT_MIN_CHARS = 240  # later pieces: longer, so the intonation resets less often
_TTS_MAX_CHARS = 450
_MAX_LEAD_S = 6.0


class _ReplyState:
    """What one streamed reply produced: raw chunks, spoken pieces, leak verdict."""

    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.spoken: list[str] = []
        self.cut = False  # a hard leak was found: nothing more is spoken
        self.leak_actions: list[str] = []
        self.published = False  # ResponseGenerated already sent (text shows while speech plays)
        self.turn_id: str | None = None
        self.user_text = ""
        self.completed = False  # the record turn was closed early, so the chat shows the text during speech


def _browser_engine() -> bool:
    """``JARVIS_BROWSER_VOICE_ENGINE=browser``: speak with the phone's own voice instead of cloud TTS."""
    return os.environ.get("JARVIS_BROWSER_VOICE_ENGINE", "").strip().lower() == "browser"


_WORD = re.compile(r"\w+", re.UNICODE)


def _is_echo(heard: str, spoken: str) -> bool:
    """True when most of ``heard`` is words Jarvis itself is saying (speaker bleed)."""
    words = [w.lower() for w in _WORD.findall(heard)]
    if not words:
        return True
    ours = {w.lower() for w in _WORD.findall(spoken)}
    return sum(1 for w in words if w in ours) / len(words) >= 0.6


# Whisper invents these on silence or speaker bleed. A bare one is never taken as an interruption.
_PHANTOM_PHRASES = frozenset(
    {
        "grazie",
        "grazie a tutti",
        "ciao",
        "ok",
        "thank you",
        "thanks",
        "thanks for watching",
        "you",
        "bye",
    }
)


def _is_phantom(heard: str) -> bool:
    return " ".join(w.lower() for w in _WORD.findall(heard)) in _PHANTOM_PHRASES


# What Whisper writes for silence or room noise. Unlike ``_PHANTOM_PHRASES`` this list leaves out "ciao" and "ok",
# which are real one-word turns; a bare "grazie" stays in because it is the most common invention.
_SILENCE_HALLUCINATIONS = _PHANTOM_PHRASES - {"ciao", "ok"}


def _is_silence_hallucination(heard: str) -> bool:
    """True when every sentence is an invented one (so "Grazie. Grazie." counts too)."""
    sentences = [
        " ".join(w.lower() for w in _WORD.findall(part))
        for part in re.split(r"[.!?…]+", heard)
        if part.strip()
    ]
    if not sentences:
        return False
    if sentences[0].startswith(("sottotitoli", "sottotitolato", "subtitles by")):
        return True  # "Sottotitoli creati dalla comunità Amara.org" splits at the dot
    return all(
        sentence in _SILENCE_HALLUCINATIONS
        or sentence.startswith(("sottotitoli", "sottotitolato", "subtitles by"))
        for sentence in sentences
    )


def split_for_tts(text: str) -> list[str]:
    """Split a reply into pieces so the first audio starts after one sentence.

    One long request made the cloud TTS model synthesize the whole reply before
    answering (about 14 s on five sentences, with distorted or skipped speech
    on the phone). Every join restarts the model's intonation, so only the first
    piece is short (``_TTS_MIN_CHARS``); later ones are grouped to at least
    ``_TTS_NEXT_MIN_CHARS`` and never exceed ``_TTS_MAX_CHARS`` unless a single
    word is longer. Later pieces are synthesized while earlier ones play.
    """
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text.strip()):
        for part in _wrap(sentence):
            current = f"{current} {part}".strip()
            if len(current) >= (_TTS_MIN_CHARS if not pieces else _TTS_NEXT_MIN_CHARS):
                pieces.append(current)
                current = ""
    if current:
        if pieces and len(current) < _TTS_NEXT_MIN_CHARS // 2 and len(pieces[-1]) + len(current) < _TTS_MAX_CHARS:
            pieces[-1] = f"{pieces[-1]} {current}"
        else:
            pieces.append(current)
    return pieces or [text]


def _wrap(sentence: str) -> list[str]:
    words, parts, line = sentence.split(), [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > _TTS_MAX_CHARS:
            parts.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        parts.append(line)
    return parts

class BrowserVoiceSession:
    """One browser microphone/speaker session over a ``/ws/audio`` socket.

    Args:
        session_id: opaque id for logging.
        send_binary: async sink for raw TTS PCM frames -> the browser.
        send_json: async sink for JSON control frames -> the browser.
        stt: object exposing ``transcribe_pcm(pcm, sample_rate=16000)``.
        brain: object exposing ``generate_stream(text) -> AsyncIterator[str]``.
        tts: object exposing ``synthesize(text, language_code=...) ->
            AsyncIterator[AudioChunk]`` (``.pcm`` int16, ``.sample_rate``).
        browser_sample_rate: the AudioWorklet capture rate (usually 48000).
        language_code: BCP-47 language pin for TTS.
        max_session_seconds: hard cap (advisory; the route enforces it).
        bus: optional EventBus (best-effort; unused on the hot path).
    """

    def __init__(
        self,
        *,
        session_id: str,
        send_binary: SendBinary,
        send_json: SendJson,
        stt: Any,
        brain: Any,
        tts: Any,
        browser_sample_rate: int = 48_000,
        language_code: str = "de-DE",
        max_session_seconds: int = 1800,
        bus: Any = None,
        config: Any = None,
    ) -> None:
        self.session_id = session_id
        self._send_binary = send_binary
        self._send_json = send_json
        self._stt = stt
        self._brain = brain
        self._tts = tts
        # Shared config reference so _speak can read [tts].volume per turn (a live
        # change lands on the next utterance). None in test factories → full.
        self._config = config
        self.browser_sample_rate = int(browser_sample_rate or STT_SAMPLE_RATE)
        self.language_code = language_code or "de-DE"
        self.max_session_seconds = max_session_seconds
        self._bus = bus

        self._in_resampler = Resampler(self.browser_sample_rate, STT_SAMPLE_RATE)
        self._endpointer = EnergyEndpointer(sample_rate=STT_SAMPLE_RATE)

        self._started_at = time.time()
        self._turns = 0
        # Bus mirror so a browser-mic turn shows up where every other spoken
        # turn does: the voice transcript (home store), the session record
        # and — via VoiceChatMirror — the Jarvis agent chat. The socket's own
        # control frames stay the transport; these events are the record.
        # Best-effort throughout: a missing bus or a failed publish must never
        # break the audio path.
        self._bus_session_started = False
        self._turn_seq = 0
        # Master-volume limiter for this socket, built on first use so importing
        # browser_voice still costs no numpy. Stateful across chunks, so an
        # utterance streamed as many frames is limited as one continuous signal
        # instead of restarting its gain ramp at every frame boundary.
        self._out_limiter: Any | None = None
        self._speaking = False
        self._processing = False
        self._tts_task: asyncio.Task[None] | None = None
        self._reply_state: _ReplyState | None = None
        self._audio_seconds_total = 0.0
        self._last_stt_confidence: float | None = None
        self._barge_task: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._browser_tts_done: asyncio.Event | None = None
        self._browser_tts_id: str | None = None
        self._ended = False

    # -- public properties -------------------------------------------------

    @property
    def turns(self) -> int:
        return self._turns

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def duration_s(self) -> float:
        return time.time() - self._started_at

    # -- inbound audio -----------------------------------------------------

    async def handle_audio_frame(self, pcm_native: bytes) -> None:
        """Process one inbound binary WS frame (raw int16 at the browser rate)."""
        if self._ended or not pcm_native:
            return
        try:
            if self.browser_sample_rate == STT_SAMPLE_RATE:
                pcm16 = bytes(pcm_native)
            else:
                pcm16 = self._in_resampler.process(bytes(pcm_native))
        except Exception:  # noqa: BLE001 — malformed/odd-length frame, drop it
            return
        utterance = self._endpointer.push(pcm16)
        if utterance is not None and not self._processing:
            # Run the turn off the inbound-frame path so we keep draining audio.
            self._turn_task = asyncio.create_task(
                self._run_turn(utterance), name=f"bv-turn-{self.session_id}"
            )
            self._turn_task.add_done_callback(self._on_turn_done)
        elif utterance is not None and self._speaking and (
            self._barge_task is None or self._barge_task.done()
        ):
            self._barge_task = asyncio.create_task(
                self._maybe_barge_in(utterance), name=f"bv-barge-{self.session_id}"
            )

    async def handle_control(self, msg: dict[str, Any]) -> None:
        """Process a JSON control frame from the browser."""
        kind = str(msg.get("type", ""))
        if kind == "audio_start":
            rate = int(msg.get("sample_rate", self.browser_sample_rate) or self.browser_sample_rate)
            if rate != self.browser_sample_rate:
                self.browser_sample_rate = rate
                self._in_resampler = Resampler(rate, STT_SAMPLE_RATE)
            # Drop any audio buffered at the old rate so STT never sees a
            # rate-mismatched blob across a reconnect / rate change.
            self._endpointer.reset()
            lang = msg.get("language")
            if isinstance(lang, str) and lang:
                self.language_code = lang
            await self._send_json({"type": "audio_ready"})
        elif kind == "barge_in":
            await self._barge_in()
        elif kind == "tts_browser_done":
            fallback_id = str(msg.get("id") or "")
            if fallback_id and fallback_id == self._browser_tts_id:
                outcome = str(msg.get("outcome") or "ended")
                if outcome != "ended":
                    log.warning(
                        "browser_voice[%s] browser TTS ended with outcome=%s",
                        self.session_id,
                        outcome,
                    )
                if self._browser_tts_done is not None:
                    self._browser_tts_done.set()
        elif kind == "audio_stop":
            await self.end(reason=HANGUP_CLIENT_STOP)

    def _on_turn_done(self, task: asyncio.Task[None]) -> None:
        """Surface a turn task that died outside _run_turn's own guard (a bare
        create_task otherwise loses the traceback with no session context)."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.warning("browser_voice[%s] turn task raised", self.session_id, exc_info=exc)

    # -- bus mirror ------------------------------------------------------

    async def _publish(self, event: Any) -> None:
        """Publish one record event, never raising into the audio path."""
        bus = self._bus
        if bus is None:
            return
        try:
            await bus.publish(event)
        except Exception:  # noqa: BLE001 — record-keeping must not break audio
            log.debug("browser_voice[%s] bus publish failed", self.session_id, exc_info=True)

    async def _ensure_bus_session(self) -> None:
        """Open the record session once per connection (idempotent)."""
        if self._bus_session_started or self._bus is None:
            return
        self._bus_session_started = True
        await self._publish(
            VoiceSessionStarted(
                session_id=self.session_id,
                wake_keyword="browser",
                language=self._lang_short(),
                source_layer="browser_voice",
            )
        )

    # -- turn loop ---------------------------------------------------------

    async def _run_turn(self, utterance_pcm16: bytes, transcript: str | None = None) -> None:
        if self._ended:
            return
        self._processing = True
        turn_id: str | None = None
        user_text = ""
        full_reply = ""
        try:
            if transcript is None:
                transcript = await self._transcribe(utterance_pcm16)
            text = (transcript or "").strip()
            if not text:
                # End-of-turn fired but nothing transcribable — let the browser
                # reset any "thinking" affordance (AD-OE6: never a silent drop).
                await self._send_json({"type": "vad_silence"})
                return
            confidence = self._last_stt_confidence
            log.warning(
                "browser_voice[%s] heard %.1f s, confidence %s, speaking=%s: %r",
                self.session_id,
                len(utterance_pcm16) / 2 / STT_SAMPLE_RATE,
                f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "n/a",
                self._speaking,
                text,
            )
            if _is_silence_hallucination(text):
                log.warning(
                    "browser_voice[%s] ignored a transcript that looks invented from silence: %r",
                    self.session_id,
                    text,
                )
                await self._send_json({"type": "vad_silence"})
                return
            log.info("browser_voice[%s] user: %s", self.session_id, text)
            await self._send_json({"type": "transcript", "text": text, "is_final": True})
            user_text = text
            await self._ensure_bus_session()
            self._turn_seq += 1
            turn_id = uuid4().hex
            await self._publish(
                VoiceTurnStarted(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    turn_index=self._turn_seq,
                    source_layer="browser_voice",
                )
            )
            await self._publish(
                TranscriptionUpdate(text=text, is_final=True, source_layer="browser_voice")
            )

            state = _ReplyState()
            self._reply_state = state
            state.turn_id, state.user_text = turn_id, user_text
            pieces = self._reply_pieces(text, state)
            self._tts_task = asyncio.create_task(self._speak_pieces(pieces))
            try:
                await self._tts_task
            except asyncio.CancelledError:
                pass  # barge-in cancelled playback mid-stream — normal
            finally:
                await pieces.aclose()
            full_reply = "".join(state.chunks)
            spoken = " ".join(state.spoken)
            log.warning(
                "browser_voice[%s] turn summary: %d pieces, %d chars spoken, %.1f s of audio sent, reply %d chars, cut=%s",
                self.session_id,
                len(state.spoken),
                len(spoken),
                self._audio_seconds_total,
                len(full_reply),
                state.cut,
            )
            if not spoken.strip():
                # Empty reply, or the whole reply was filler / an honorific: nothing
                # failed, so say nothing rather than claim an error.
                log.info(
                    "browser_voice[%s] reply carried no substance — staying silent: %r",
                    self.session_id,
                    full_reply[:80],
                )
                await self._complete_bus_turn(turn_id, user_text, "")
                return
            if not state.published:  # a barge-in cut the stream before it was published
                await self._publish(
                    ResponseGenerated(
                        text=full_reply,
                        language=self._lang_short(),
                        source_layer="browser_voice",
                    )
                )
            await self._publish(
                SpeechSpoken(
                    text=spoken,
                    language=self._lang_short(),
                    spoken_kind="reply",
                    source_layer="browser_voice",
                )
            )
            # A turn is "complete" once the response was (at least partly) spoken,
            # mirroring the telephony session's count semantics.
            self._turns += 1
            if not state.completed:
                await self._complete_bus_turn(turn_id, user_text, full_reply)
        except Exception:  # noqa: BLE001 — a turn failure must never kill the session
            log.warning("browser_voice[%s] turn failed", self.session_id, exc_info=True)
            if turn_id is not None:
                await self._complete_bus_turn(turn_id, user_text, "")
        finally:
            self._processing = False

    async def _complete_bus_turn(
        self, turn_id: str | None, user_text: str, jarvis_text: str
    ) -> None:
        """Close the record turn so the transcript and the chat see it."""
        if turn_id is None or self._bus is None:
            return
        await self._publish(
            VoiceTurnCompleted(
                session_id=self.session_id,
                turn_id=turn_id,
                user_text=user_text,
                user_lang=self._lang_short(),
                jarvis_text=jarvis_text,
                jarvis_lang=self._lang_short(),
                source_layer="browser_voice",
            )
        )

    async def _transcribe(self, pcm16: bytes) -> str:
        result = await self._stt.transcribe_pcm(pcm16, sample_rate=STT_SAMPLE_RATE)
        self._last_stt_confidence = getattr(result, "confidence", None)
        return getattr(result, "text", "") or ""

    async def _reply_pieces(self, user_text: str, state: _ReplyState) -> AsyncIterator[object]:
        """Stream the brain reply as vetted pieces so speech starts at the first sentence.

        Each piece goes through ``scrub_for_voice`` on its own. A hard leak (tool JSON,
        stacktrace, raw repr, shell command, wrong language) cuts the reply: the audio
        still queued in the browser is dropped and only the standard fallback phrase is
        spoken. Harmless residue (filler, honorific) is skipped in silence. Once the whole
        reply is in, it is scrubbed again as a trailing kill switch, because a leak that
        spans two pieces only shows up in the aggregate.
        """
        lang = self._lang_short()
        pending = ""  # complete sentences waiting to reach the minimum piece length
        tail = ""  # text after the last sentence break, possibly incomplete
        emitted = 0

        def _vet(piece: str) -> list[object]:
            nonlocal emitted
            result = scrub_for_voice(piece, language=lang)
            if _is_hard_scrub_result(result):
                state.cut = True
                state.leak_actions = list(result.actions)
                log.warning(
                    "browser_voice[%s] hard leak in streamed reply (%s) — cutting speech",
                    self.session_id,
                    state.leak_actions,
                )
                fallback = FALLBACK_PHRASES.get(lang, FALLBACK_PHRASES["en"])
                state.spoken.clear()
                state.spoken.append(fallback)
                return [_CUT, fallback]
            cleaned = result.cleaned.strip()
            if not cleaned or is_harmless_scrub_residue(result):
                return []
            state.spoken.append(cleaned)
            emitted += 1
            return [cleaned]

        def _minimum() -> int:
            return _TTS_MIN_CHARS if emitted == 0 else _TTS_NEXT_MIN_CHARS

        async for chunk in self._brain.generate_stream(user_text):
            if not chunk:
                continue
            state.chunks.append(chunk)
            if state.cut:
                continue  # keep draining so the record holds the whole reply
            tail += chunk
            sentences = _SENTENCE_END.split(tail)
            tail = sentences.pop()
            for sentence in sentences:
                pending = f"{pending} {sentence}".strip()
                # An unclosed code fence is held until it closes, so scrub sees it whole.
                if pending.count("```") % 2 or len(pending) < _minimum():
                    continue
                piece, pending = pending, ""
                for item in _vet(piece):
                    yield item
                if state.cut:
                    break
        if state.chunks:
            state.published = True
            await self._publish(
                ResponseGenerated(
                    text="".join(state.chunks),
                    language=lang,
                    source_layer="browser_voice",
                )
            )
        remainder_items: list[object] = []
        if not state.cut:
            rest = f"{pending} {tail}".strip()
            if rest:
                remainder_items = _vet(rest)
        if state.spoken and not state.completed:
            # Close the record turn now, not after the audio: the chat files the text on this event.
            state.completed = True
            await self._complete_bus_turn(state.turn_id, state.user_text, "".join(state.chunks))
        for item in remainder_items:
            yield item
        if not state.cut:
            whole = scrub_for_voice("".join(state.chunks), language=lang)
            if _is_hard_scrub_result(whole):
                state.cut = True
                state.leak_actions = list(whole.actions)
                log.warning(
                    "browser_voice[%s] hard leak across pieces (%s) — cutting speech",
                    self.session_id,
                    state.leak_actions,
                )
                fallback = FALLBACK_PHRASES.get(lang, FALLBACK_PHRASES["en"])
                state.spoken.clear()
                state.spoken.append(fallback)
                yield _CUT
                yield fallback

    async def _speak(self, text: str) -> int:
        """Speak an already vetted ``text`` (split into pieces); see ``_speak_pieces``."""

        async def _pieces() -> AsyncIterator[object]:
            for piece in split_for_tts(text):
                yield piece

        return await self._speak_pieces(_pieces())

    async def _speak_pieces(self, pieces: AsyncIterator[object]) -> int:
        """Synthesize ``pieces`` one by one and stream the raw PCM straight back as binary WS
        frames. The browser plays them via Web Audio at ``TTS_SAMPLE_RATE`` — no
        server resample (off-rate chunks are normalized to that rate). Returns
        the number of binary frames sent. Cancellable: a barge-in cancels the
        underlying task mid-stream.
        """
        from dodaios.audio.gain import (  # noqa: PLC0415 — lazy: keep cold-import light
            PeakLimiter,
            apply_output_gain_pcm16,
        )
        from dodaios.browser_voice.audio import resample_pcm16  # noqa: PLC0415 — off-rate fallback

        # Master output volume (shared with the local speaker + telephony sinks)
        # so the setting works over the browser too — the only voice path on a
        # headless server. Read once per turn; a live change lands next utterance.
        vol = getattr(getattr(self._config, "tts", None), "volume", 1.0)
        if self._out_limiter is None:
            self._out_limiter = PeakLimiter()

        if _browser_engine():
            # Free engine: the phone speaks with its built-in voice (no cloud TTS call).
            self._speaking = True
            try:
                spoken = 0
                async for piece in pieces:
                    if piece is _CUT:
                        if spoken:
                            await self._send_json({"type": "tts_cancel"})
                        continue
                    assert isinstance(piece, str)
                    await self._speak_in_browser(piece, volume=vol)
                    spoken += 1
                return spoken
            finally:
                self._speaking = False

        self._speaking = True
        sent = 0
        spoken_pieces: list[str] = []
        clock_start: float | None = None
        audio_seconds = 0.0
        self._audio_seconds_total = 0.0
        try:
            async for piece in pieces:
                if piece is _CUT:
                    # A hard leak was found after audio started: flush what the browser
                    # still holds and restart the clock; the next piece is the fallback.
                    if sent:
                        await self._send_json({"type": "tts_cancel"})
                        self._out_limiter.reset()
                        sent = 0
                        clock_start, audio_seconds = None, 0.0
                    continue
                assert isinstance(piece, str)
                spoken_pieces.append(piece)
                stream = self._tts.synthesize(piece, language_code=self.language_code)
                iterator = stream.__aiter__()
                while True:
                    try:
                        chunk = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - cross output family once
                        if sent:
                            log.warning(
                                "browser_voice[%s] server TTS failed after audio started",
                                self.session_id,
                                exc_info=True,
                            )
                            await self._send_json({"type": "tts_end", "partial": True})
                            raise
                        log.warning(
                            "browser_voice[%s] server TTS unavailable; using browser speech: %s",
                            self.session_id,
                            exc,
                        )
                        rest = [q async for q in pieces if isinstance(q, str)]
                        await self._speak_in_browser(" ".join([piece, *rest]), volume=vol)
                        return 0

                    pcm = getattr(chunk, "pcm", b"")
                    rate = int(getattr(chunk, "sample_rate", TTS_SAMPLE_RATE) or TTS_SAMPLE_RATE)
                    if not pcm:
                        continue
                    if sent == 0:
                        await self._send_json(
                            {"type": "tts_start", "sample_rate": TTS_SAMPLE_RATE}
                        )
                    if rate != TTS_SAMPLE_RATE:
                        pcm = resample_pcm16(pcm, rate, TTS_SAMPLE_RATE)
                    pcm = apply_output_gain_pcm16(
                        pcm,
                        vol,
                        sample_rate=TTS_SAMPLE_RATE,
                        limiter=self._out_limiter,
                    )
                    # The browser's playback ring holds ~10 s and drops the OLDEST audio
                    # on overrun, so never run more than _MAX_LEAD_S ahead of real time.
                    if clock_start is None:
                        clock_start = time.monotonic()
                    lead = audio_seconds - (time.monotonic() - clock_start)
                    if lead > _MAX_LEAD_S:
                        await asyncio.sleep(lead - _MAX_LEAD_S)
                    audio_seconds += len(pcm) / 2 / TTS_SAMPLE_RATE
                    self._audio_seconds_total += len(pcm) / 2 / TTS_SAMPLE_RATE
                    await self._send_binary(pcm)
                    sent += 1

            if not spoken_pieces:
                return 0  # nothing to say (reply was empty or only filler): stay silent
            if sent == 0:
                log.warning(
                    "browser_voice[%s] server TTS produced no audio; using browser speech",
                    self.session_id,
                )
                await self._speak_in_browser(" ".join(spoken_pieces), volume=vol)
                return 0
            await self._send_json({"type": "tts_end"})
        finally:
            self._speaking = False
        return sent

    async def _speak_in_browser(self, text: str, *, volume: float) -> None:
        """Ask the connected browser to speak and wait for its completion ACK."""
        fallback_id = str(uuid4())
        done = asyncio.Event()
        self._browser_tts_id = fallback_id
        self._browser_tts_done = done
        try:
            await self._send_json(
                {
                    "type": "tts_browser_fallback",
                    "id": fallback_id,
                    "text": text,
                    "language": self.language_code,
                    "volume": max(0.0, min(1.0, float(volume))),
                }
            )
            try:
                await asyncio.wait_for(done.wait(), timeout=_BROWSER_TTS_ACK_TIMEOUT_S)
            except TimeoutError:
                log.warning(
                    "browser_voice[%s] browser TTS acknowledgement timed out",
                    self.session_id,
                )
            await self._send_json({"type": "tts_end", "fallback": "browser"})
        finally:
            if self._browser_tts_id == fallback_id:
                self._browser_tts_id = None
                self._browser_tts_done = None

    # -- barge-in / lifecycle ---------------------------------------------

    async def _maybe_barge_in(self, utterance_pcm16: bytes) -> None:
        """The person spoke while Jarvis was talking: stop and answer them instead.

        The microphone also hears Jarvis through the phone speaker, so the utterance
        is transcribed first and ignored when its words are Jarvis's own (echo).
        """
        try:
            text = ((await self._transcribe(utterance_pcm16)) or "").strip()
        except Exception:  # noqa: BLE001 — a failed check must never break the session
            log.debug("browser_voice[%s] barge-in transcription failed", self.session_id, exc_info=True)
            return
        state = self._reply_state
        spoken = " ".join(state.spoken) if state is not None else ""
        if not text or not self._speaking or self._ended:
            return
        if _is_echo(text, spoken) or _is_phantom(text):
            log.warning("browser_voice[%s] barge-in ignored (echo/phantom): %r", self.session_id, text)
            return
        log.info("browser_voice[%s] barge-in: %s", self.session_id, text)
        turn = self._turn_task
        await self._barge_in()
        if turn is not None and not turn.done():
            await asyncio.wait({turn})
        if self._ended:
            return
        self._turn_task = asyncio.create_task(
            self._run_turn(utterance_pcm16, text), name=f"bv-turn-{self.session_id}"
        )
        self._turn_task.add_done_callback(self._on_turn_done)

    async def _barge_in(self) -> None:
        """Cancel in-flight TTS playback and tell the browser to flush its queue.

        On a mid-stream cancel the ``_speak`` coroutine never reaches its
        ``tts_end``; without a signal the browser's playback queue would hang
        (review BLOCKER). The canceller emits ``tts_cancel`` — best-effort,
        since the socket may already be closing.
        """
        if self._tts_task is not None and not self._tts_task.done():
            self._tts_task.cancel()
        if self._browser_tts_done is not None:
            self._browser_tts_done.set()
        if self._out_limiter is not None:
            # The cancelled audio's gain state must not carry into the reply
            # that follows, or it would open quiet and audibly swell.
            self._out_limiter.reset()
        self._speaking = False
        try:
            await self._send_json({"type": "tts_cancel"})
        except Exception:  # noqa: BLE001, S110 — best-effort on a closing socket
            pass

    async def end(self, *, reason: str = "") -> None:
        """End the session: cancel playback, mark ended."""
        if self._ended:
            return
        self._ended = True
        if self._tts_task is not None and not self._tts_task.done():
            self._tts_task.cancel()
        if self._browser_tts_done is not None:
            self._browser_tts_done.set()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
        log.info(
            "browser_voice[%s] ended: reason=%s turns=%d dur=%.1fs",
            self.session_id,
            reason,
            self._turns,
            self.duration_s,
        )
        if self._bus_session_started:
            await self._publish(
                VoiceSessionEnded(
                    session_id=self.session_id,
                    hangup_reason=reason,
                    turn_count=self._turns,
                    duration_s=self.duration_s,
                    source_layer="browser_voice",
                )
            )

    def _lang_short(self) -> str:
        # scrub_for_voice only knows the runtime locales de/en/es/pt/it (AP-11, regex
        # only); an unknown tag would silently apply the wrong blacklist.
        tag = (self.language_code or "de-DE").split("-", 1)[0].lower()
        return tag if tag in {"de", "en", "es", "pt", "it"} else "en"
