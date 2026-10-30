"""B2 BrowserVoiceSession — the headless/VPS browser-microphone voice path.

Mirrors tests/unit/telephony/test_session.py: drive synthetic PCM16 through the
session with the telephony Fakes (no models, no socket, no sounddevice) and
assert the STT->Brain->TTS turn streams raw PCM back + the JSON control frames.

Seam-level only — the browser AudioWorklet capture + Web Audio playback are
browser-only and NOT unit-testable here; this proves the server session loop.
"""
from __future__ import annotations

import asyncio
import math
import struct

from dodaios.browser_voice.audio import STT_SAMPLE_RATE
from dodaios.browser_voice.session import BrowserVoiceSession
from dodaios.core.protocols import AudioChunk
from tests.fakes.fake_telephony_stack import FakeBrain, FakeSTT, FakeTTS


class _SlowTTS:
    """A TTS that yields 24 kHz chunks with a real delay between them, so
    `_speak` stays mid-stream long enough to test barge-in / end deterministically."""

    name = "slow-tts"
    supports_streaming = True

    def __init__(self, chunks: int = 20, delay: float = 0.02) -> None:
        self._chunks = chunks
        self._delay = delay
        self.calls: list[tuple[str, str]] = []

    async def synthesize(self, text, language_code="de-DE", voice=None):
        self.calls.append((text, language_code))
        for _ in range(self._chunks):
            await asyncio.sleep(self._delay)
            yield AudioChunk(pcm=b"\x01\x00" * 240, sample_rate=24_000, timestamp_ns=0, channels=1)


class _EmptyTTS:
    async def synthesize(self, text, language_code="en-US", voice=None):
        if False:
            yield text, language_code, voice


class _FailingTTS:
    async def synthesize(self, text, language_code="en-US", voice=None):
        if False:
            yield text, language_code, voice
        raise RuntimeError("server TTS is unavailable")


def _pcm16_frame(amp: int, ms: int = 20, rate: int = STT_SAMPLE_RATE, freq: int = 300) -> bytes:
    """One raw int16 mono PCM frame at `rate` (what the browser AudioWorklet sends)."""
    n = rate * ms // 1000
    if amp == 0:
        return b"\x00\x00" * n
    return b"".join(
        struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n)
    )


class _Sink:
    def __init__(self) -> None:
        self.binary: list[bytes] = []
        self.json: list[dict] = []

    async def send_binary(self, data: bytes) -> None:
        self.binary.append(bytes(data))

    async def send_json(self, msg: dict) -> None:
        self.json.append(msg)

    def json_of(self, kind: str) -> list[dict]:
        return [m for m in self.json if m.get("type") == kind]


def _make_session(sink, *, stt=None, brain=None, tts=None, rate=STT_SAMPLE_RATE, **kw):
    params = dict(
        session_id="bv1",
        send_binary=sink.send_binary,
        send_json=sink.send_json,
        stt=stt
        or FakeSTT(
            ["Wie spät ist es?"]  # i18n-allow: simulated German STT transcript
        ),
        brain=brain or FakeBrain("Es ist vierzehn Uhr."),
        tts=tts or FakeTTS(ms_per_char=2),
        browser_sample_rate=rate,
        language_code="de-DE",
    )
    params.update(kw)
    return BrowserVoiceSession(**params)


async def _drive_one_utterance(session, rate=STT_SAMPLE_RATE) -> None:
    """Feed silence -> speech -> silence so the endpointer fires one turn, then
    await the spawned turn task. Waits for the turn count to INCREMENT (relative)
    so it works when called repeatedly."""
    start_turns = session.turns
    session._endpointer.silence_ms = 100
    session._endpointer.min_speech_ms = 60
    for _ in range(2):
        await session.handle_audio_frame(_pcm16_frame(0, rate=rate))
    for _ in range(8):
        await session.handle_audio_frame(_pcm16_frame(15000, rate=rate))
    for _ in range(10):
        await session.handle_audio_frame(_pcm16_frame(0, rate=rate))
    for _ in range(200):
        await asyncio.sleep(0)
        if session.turns > start_turns:
            break
        await asyncio.sleep(0.01)


async def _drive_until_speaking(session) -> None:
    session._endpointer.silence_ms = 100
    session._endpointer.min_speech_ms = 60
    for _ in range(2):
        await session.handle_audio_frame(_pcm16_frame(0))
    for _ in range(8):
        await session.handle_audio_frame(_pcm16_frame(15000))
    for _ in range(10):
        await session.handle_audio_frame(_pcm16_frame(0))
    for _ in range(60):
        await asyncio.sleep(0)
        if session._speaking:
            break
        await asyncio.sleep(0.01)


async def test_full_turn_streams_binary_tts_back():
    sink = _Sink()
    session = _make_session(sink)
    await _drive_one_utterance(session)
    assert session.turns == 1
    assert len(sink.binary) > 0  # raw PCM frames streamed back to the browser
    assert all(isinstance(b, bytes) and b for b in sink.binary)
    assert sink.json_of("tts_start") and sink.json_of("tts_end")


async def test_transcript_control_frame_sent():
    sink = _Sink()
    session = _make_session(sink, stt=FakeSTT(["Hallo Jarvis"]))
    await _drive_one_utterance(session)
    tr = sink.json_of("transcript")
    assert tr and tr[0]["text"] == "Hallo Jarvis" and tr[0]["is_final"] is True


async def test_brain_receives_transcript():
    sink = _Sink()
    brain = FakeBrain("ok")
    session = _make_session(sink, stt=FakeSTT(["Wie geht es dir?"]), brain=brain)
    await _drive_one_utterance(session)
    assert "Wie geht es dir?" in brain.prompts


async def test_empty_transcript_emits_vad_silence_and_skips_turn():
    sink = _Sink()
    session = _make_session(sink, stt=FakeSTT([""]))
    await _drive_one_utterance(session)
    assert session.turns == 0
    assert sink.binary == []
    assert sink.json_of("vad_silence")  # browser can reset its "thinking" UI


async def test_two_consecutive_utterances_both_complete():
    # The _processing re-entrancy gate must reopen after each turn (the core
    # correctness invariant of the session loop).
    sink = _Sink()
    session = _make_session(sink, stt=FakeSTT(["eins", "zwei"]))
    await _drive_one_utterance(session)
    assert session.turns == 1
    await _drive_one_utterance(session)
    assert session.turns == 2


async def test_audio_start_sets_rate_and_language():
    sink = _Sink()
    session = _make_session(sink, rate=16_000)
    await session.handle_control(
        {"type": "audio_start", "sample_rate": 48_000, "language": "en-US"}
    )
    assert session.browser_sample_rate == 48_000
    assert session.language_code == "en-US"
    assert sink.json_of("audio_ready")


async def test_resamples_48k_to_16k_for_stt():
    sink = _Sink()
    stt = FakeSTT(["x"])
    session = _make_session(sink, stt=stt, rate=48_000)
    await _drive_one_utterance(session, rate=48_000)
    assert stt.calls and all(sr == STT_SAMPLE_RATE for (_n, sr) in stt.calls)


async def test_barge_in_cancels_tts():
    sink = _Sink()
    session = _make_session(sink, tts=_SlowTTS())  # yields with a real delay
    await _drive_until_speaking(session)
    assert session._speaking
    await session.handle_control({"type": "barge_in"})
    await asyncio.sleep(0)
    task = session._tts_task
    assert task is None or task.cancelled() or task.done()
    assert session._speaking is False
    # The browser must get a flush signal (it never received tts_end).
    assert sink.json_of("tts_cancel")


async def test_end_cancels_tasks():
    sink = _Sink()
    session = _make_session(sink, tts=_SlowTTS())
    await _drive_until_speaking(session)
    await session.end(reason="test")
    assert session.ended
    await asyncio.sleep(0)
    task = session._tts_task
    assert task is None or task.cancelled() or task.done()


async def _complete_browser_tts_fallback(session, sink, text="Portable answer"):
    task = asyncio.create_task(session._speak(text))
    for _ in range(100):
        await asyncio.sleep(0)
        messages = sink.json_of("tts_browser_fallback")
        if messages:
            break
    assert messages
    await session.handle_control(
        {"type": "tts_browser_done", "id": messages[0]["id"], "outcome": "ended"}
    )
    assert await task == 0
    return messages[0]


async def test_empty_server_tts_uses_acknowledged_browser_voice_fallback():
    sink = _Sink()
    session = _make_session(sink, tts=_EmptyTTS(), language_code="es-ES")

    fallback = await _complete_browser_tts_fallback(session, sink)

    assert fallback["text"] == "Portable answer"
    assert fallback["language"] == "es-ES"
    assert fallback["volume"] == 1.0
    assert sink.binary == []
    assert sink.json_of("tts_start") == []
    assert sink.json_of("tts_end")[-1]["fallback"] == "browser"


async def test_server_tts_failure_before_audio_uses_browser_voice_fallback():
    sink = _Sink()
    session = _make_session(sink, tts=_FailingTTS())

    await _complete_browser_tts_fallback(session, sink)

    assert sink.binary == []
    assert sink.json_of("tts_browser_fallback")


async def test_browser_voice_fallback_ack_is_scoped_to_current_id():
    sink = _Sink()
    session = _make_session(sink, tts=_EmptyTTS())
    task = asyncio.create_task(session._speak("Scoped answer"))
    for _ in range(100):
        await asyncio.sleep(0)
        messages = sink.json_of("tts_browser_fallback")
        if messages:
            break
    assert messages

    await session.handle_control(
        {"type": "tts_browser_done", "id": "stale-turn", "outcome": "ended"}
    )
    await asyncio.sleep(0)
    assert not task.done()

    await session.handle_control(
        {"type": "tts_browser_done", "id": messages[0]["id"], "outcome": "ended"}
    )
    assert await task == 0


def test_split_for_tts_keeps_first_piece_short_and_loses_no_text():
    from dodaios.browser_voice.session import split_for_tts

    text = (
        "Un frigorifero sposta il calore dall'interno verso l'esterno. "
        "Un liquido refrigerante evapora dentro lo scomparto freddo e assorbe calore. "
        "Il compressore comprime il gas, che si scalda. "
        "Nel condensatore sul retro il gas cede calore e torna liquido. "
        "Un termostato accende e spegne il compressore."
    )
    pieces = split_for_tts(text)
    assert len(pieces) > 1
    assert " ".join(pieces) == text
    assert all(len(piece) <= 450 for piece in pieces)
    assert len(pieces[0]) < len(pieces[1])  # short first piece, longer next ones
    assert split_for_tts("Ciao.") == ["Ciao."]
    assert " ".join(split_for_tts("parola " * 200)) == ("parola " * 200).strip()


def test_speak_never_runs_far_ahead_of_playback(monkeypatch):
    import asyncio

    from dodaios.browser_voice import session as mod

    class Chunk:
        sample_rate = mod.TTS_SAMPLE_RATE
        pcm = b"\x01\x00" * mod.TTS_SAMPLE_RATE  # 1 s of audio

    class FakeTts:
        async def synthesize(self, text, language_code=None):
            for _ in range(12):  # 12 s of audio, produced instantly
                yield Chunk()

    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    sess = mod.BrowserVoiceSession.__new__(mod.BrowserVoiceSession)
    sess._tts, sess._config, sess._out_limiter = FakeTts(), None, None
    sess.language_code, sess.session_id = "it-IT", "t"

    async def noop(*_a, **_k):
        return None

    sess._send_json = sess._send_binary = noop
    sess._speaking = False
    asyncio.run(sess._speak("Una frase."))
    assert sleeps and max(sleeps) > 0  # the 7th second onward had to wait


class _RecordingTTS:
    name = "recording-tts"
    supports_streaming = True

    def __init__(self, on_call=None) -> None:
        self.texts: list[str] = []
        self._on_call = on_call

    async def synthesize(self, text, language_code="it-IT", voice=None):
        self.texts.append(text)
        if self._on_call:
            self._on_call(text)
        yield AudioChunk(pcm=b"\x01\x00" * 240, sample_rate=24_000, timestamp_ns=0, channels=1)


class _ScriptedBrain:
    """Yields the given text pieces, with a hook that fires right before the LAST one."""

    def __init__(self, parts, before_last=None) -> None:
        self._parts = parts
        self._before_last = before_last

    async def generate_stream(self, user_text, **_kw):
        for i, part in enumerate(self._parts):
            if i == len(self._parts) - 1 and self._before_last:
                self._before_last()
            yield part
            await asyncio.sleep(0)


_LONG_IT = (
    "Un frigorifero sposta il calore dall'interno verso l'esterno. "
    "Un liquido refrigerante evapora dentro lo scomparto freddo e assorbe calore. "
)


async def _run_stream(session, tts_texts_hook=None):
    from dodaios.browser_voice.session import _ReplyState

    state = _ReplyState()
    await session._speak_pieces(session._reply_pieces("domanda", state))
    return state


async def test_speech_starts_before_the_reply_is_complete():
    sink = _Sink()
    finished: list[str] = []
    tts = _RecordingTTS(on_call=lambda text: finished.append("tts"))
    brain = _ScriptedBrain(
        [_LONG_IT, "Il compressore comprime il gas, che si scalda e lo spinge verso il retro."],
        before_last=lambda: finished.append("last-brain-chunk"),
    )
    session = _make_session(sink, brain=brain, tts=tts, language_code="it-IT")
    state = await _run_stream(session)
    assert finished[0] == "tts"  # the first piece was synthesized before the brain ended
    assert "".join(state.chunks).startswith("Un frigorifero")
    assert sink.json_of("tts_start") and sink.json_of("tts_end")


async def test_hard_leak_in_a_later_piece_cuts_speech_and_says_the_fallback():
    from dodaios.brain.output_filter import FALLBACK_PHRASES

    sink = _Sink()
    tts = _RecordingTTS()
    leak = "Traceback (most recent call last):\n  File \"x.py\", line 1, in <module>\nValueError: boom"
    brain = _ScriptedBrain([_LONG_IT, leak])
    session = _make_session(sink, brain=brain, tts=tts, language_code="it-IT")
    state = await _run_stream(session)
    assert state.cut
    assert tts.texts[-1] == FALLBACK_PHRASES["en"] or tts.texts[-1] in FALLBACK_PHRASES.values()
    assert not any("Traceback" in text or "ValueError" in text for text in tts.texts)
    assert sink.json_of("tts_cancel")  # audio already queued in the browser was dropped


async def test_whole_reply_scrub_catches_a_leak_that_spans_pieces():
    sink = _Sink()
    tts = _RecordingTTS()
    # "Traceback" alone is harmless; the aggregate with the next lines is the leak.
    brain = _ScriptedBrain([_LONG_IT + "Traceback (most recent call last):", "\n  File \"x.py\", line 1\nValueError: boom"])
    session = _make_session(sink, brain=brain, tts=tts, language_code="it-IT")
    state = await _run_stream(session)
    assert state.cut
    assert not any("ValueError" in text for text in tts.texts)


async def test_empty_reply_is_silent_not_an_error():
    sink = _Sink()
    tts = _RecordingTTS()
    session = _make_session(sink, brain=_ScriptedBrain([""]), tts=tts, language_code="it-IT")
    state = await _run_stream(session)
    assert not state.cut and not state.spoken and tts.texts == []


class _Bus:
    def __init__(self, session_ref) -> None:
        self.events: list[tuple[str, bool]] = []
        self._session_ref = session_ref

    async def publish(self, event) -> None:
        self.events.append((type(event).__name__, self._session_ref[0]._speaking))


async def test_reply_text_is_published_while_the_voice_is_still_speaking():
    sink = _Sink()
    ref: list = []
    bus = _Bus(ref)
    session = _make_session(sink, tts=_SlowTTS(chunks=15, delay=0.02), bus=bus)
    ref.append(session)
    await _drive_one_utterance(session)
    published = [speaking for name, speaking in bus.events if name == "ResponseGenerated"]
    assert published == [True]  # text appeared during speech, not after it


async def test_speaking_over_jarvis_interrupts_and_answers_the_new_request():
    sink = _Sink()
    brain = FakeBrain("Una risposta lunga che continua a parlare per un bel po' di tempo.")
    session = _make_session(
        sink,
        stt=FakeSTT(["Raccontami cosa puoi fare", "grazie perfetto"]),
        brain=brain,
        tts=_SlowTTS(chunks=40, delay=0.02),
    )
    await _drive_until_speaking(session)
    assert session._speaking
    # The person talks over the reply.
    for _ in range(8):
        await session.handle_audio_frame(_pcm16_frame(15000))
    for _ in range(10):
        await session.handle_audio_frame(_pcm16_frame(0))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if "grazie perfetto" in brain.prompts:
            break
    assert sink.json_of("tts_cancel")
    assert "grazie perfetto" in brain.prompts


async def test_jarvis_own_voice_picked_up_by_the_mic_does_not_interrupt_it():
    sink = _Sink()
    brain = FakeBrain("Una risposta lunga che continua a parlare per un bel po' di tempo.")
    session = _make_session(
        sink,
        stt=FakeSTT(["Raccontami cosa puoi fare", "continua a parlare per un bel po"]),
        brain=brain,
        tts=_SlowTTS(chunks=40, delay=0.02),
    )
    await _drive_until_speaking(session)
    for _ in range(8):
        await session.handle_audio_frame(_pcm16_frame(15000))
    for _ in range(10):
        await session.handle_audio_frame(_pcm16_frame(0))
    await asyncio.sleep(0.2)
    assert not sink.json_of("tts_cancel")
    assert brain.prompts == ["Raccontami cosa puoi fare"]
