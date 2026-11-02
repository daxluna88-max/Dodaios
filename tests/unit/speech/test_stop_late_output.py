"""STOP reaches every voice output path: a cancelled turn never speaks after resume (ADR-0058).

Fakes only: no audio device, no network, no provider. What this pins is the ticket logic and the
probes; it does not prove that a real speaker or a real realtime provider went silent.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from dodaios.control.stop import get_stop_controller, reset_stop_controller
from dodaios.core.bus import EventBus
from dodaios.core.events import SpeechSpoken
from dodaios.core.protocols import AudioChunk
from dodaios.realtime.desktop import DesktopRealtimePlayback
from dodaios.speech import stop_gate
from dodaios.speech.pipeline import SpeechPipeline
from dodaios.speech.stop_parts import voice_subsystems


@pytest.fixture(autouse=True)
def _fresh_controller():
    reset_stop_controller()
    yield
    reset_stop_controller()


@dataclass
class _TTS:
    name: str = "fake-tts"
    supports_streaming: bool = True
    synthesized: list[str] = field(default_factory=list)

    async def synthesize(self, text, voice=None, language_code=None) -> AsyncIterator[AudioChunk]:
        self.synthesized.append(text)
        half = len(text) // 2
        for part in (text[:half], text[half:]):
            yield AudioChunk(pcm=part.encode(), sample_rate=24_000, timestamp_ns=0, channels=1)


@dataclass
class _Player:
    played: list[str] = field(default_factory=list)
    stop_calls: int = 0

    async def play_chunks(self, chunks) -> bool:
        async for chunk in chunks:
            self.played.append(chunk.pcm.decode())
        return True

    def stop(self) -> None:
        self.stop_calls += 1


def _pipeline(tts, player, brain) -> SpeechPipeline:
    pipeline = SpeechPipeline(tts=tts, bus=EventBus(), enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]
    pipeline._brain = brain  # type: ignore[assignment]
    pipeline._latency_tracker = None
    pipeline._tts_lookahead_sentences = 1

    async def _never_barge(**_kw) -> bool:
        await asyncio.sleep(3600)
        return False

    pipeline._barge_monitor = _never_barge  # type: ignore[assignment]
    return pipeline


class _StopsMidStream:
    """Streams sentence 1, waits for the test, then sentence 2 arrives late."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()

    async def generate_stream(self, text: str, **_kw) -> AsyncIterator[str]:
        yield "Erster Satz. "
        await self.gate.wait()
        yield "Spaeter Satz. "


async def _stop_and_resume() -> None:
    ctl = get_stop_controller()
    await ctl.stop()
    ctl.resume()


async def test_late_streamed_sentence_of_a_stopped_turn_is_never_voiced() -> None:
    tts, player, brain = _TTS(), _Player(), _StopsMidStream()
    pipeline = _pipeline(tts, player, brain)
    spoken: list[str] = []

    async def _on_spoken(ev: SpeechSpoken) -> None:
        spoken.append(ev.text)

    pipeline._bus.subscribe(SpeechSpoken, _on_spoken)

    with stop_gate.voice_turn():
        turn = asyncio.create_task(pipeline._brain_streaming("hallo", "de"))
        for _ in range(100):
            if "Erster Satz." in tts.synthesized:
                break
            await asyncio.sleep(0.01)
        await _stop_and_resume()          # STOP, then the owner resumes
        brain.gate.set()                  # the provider keeps going: a late delta
        await asyncio.wait_for(turn, 3.0)
    await asyncio.sleep(0.05)

    assert tts.synthesized == ["Erster Satz."]
    assert all("Spaeter" not in p for p in player.played)
    assert not any("Spaeter" in s for s in spoken)


async def test_speak_of_a_stopped_turn_does_nothing_after_resume() -> None:
    tts, player = _TTS(), _Player()
    pipeline = _pipeline(tts, player, SimpleNamespace())
    with stop_gate.voice_turn():
        await _stop_and_resume()
        assert await pipeline._speak("zu spaet", "de") is False
    assert tts.synthesized == [] and player.played == []


async def test_a_turn_created_after_resume_speaks_normally() -> None:
    tts, player = _TTS(), _Player()
    pipeline = _pipeline(tts, player, SimpleNamespace())
    await _stop_and_resume()
    with stop_gate.voice_turn():
        await pipeline._speak("neu", "de")
    assert tts.synthesized == ["neu"]


async def test_a_turn_created_while_the_door_is_closed_can_still_say_the_refusal() -> None:
    tts, player = _TTS(), _Player()
    pipeline = _pipeline(tts, player, SimpleNamespace())
    await get_stop_controller().stop()
    with stop_gate.voice_turn():
        await pipeline._speak("gestoppt", "de")
    assert tts.synthesized == ["gestoppt"]


async def test_audio_chunks_stop_flowing_after_stop() -> None:
    tts, player = _TTS(), _Player()
    pipeline = _pipeline(tts, player, SimpleNamespace())
    original = tts.synthesize

    async def stop_after_first(text, voice=None, language_code=None):
        first = True
        async for chunk in original(text, voice, language_code):
            yield chunk
            if first:
                first = False
                await get_stop_controller().stop()   # STOP lands between two chunks

    tts.synthesize = stop_after_first  # type: ignore[method-assign]
    with stop_gate.voice_turn():
        await pipeline._speak("abcdef", "de")
    assert "def" not in player.played


# -- stop phrase ----------------------------------------------------------------------------


async def test_a_spoken_stop_phrase_closes_the_door_like_the_button() -> None:
    pipeline = _pipeline(_TTS(), _Player(), SimpleNamespace())
    assert get_stop_controller().door_open
    assert await pipeline._close_door_on_stop_phrase("Stopp") is True
    assert not get_stop_controller().door_open
    report = await pipeline._stop_phrase_task
    assert {r.name for r in report.results} >= {"voice_playback", "realtime_session"}


async def test_an_ordinary_sentence_does_not_close_the_door() -> None:
    pipeline = _pipeline(_TTS(), _Player(), SimpleNamespace())
    assert await pipeline._close_door_on_stop_phrase("wie wird das Wetter") is False
    assert get_stop_controller().door_open


# -- probes ---------------------------------------------------------------------------------


async def test_voice_parts_are_unknown_without_a_pipeline() -> None:
    playback, realtime = voice_subsystems(lambda: None)
    await playback.request()
    await realtime.request()
    assert await playback.probe() == "unknown"
    assert await realtime.probe() == "unknown"


async def test_playback_probe_follows_the_player_state() -> None:
    player = SimpleNamespace(stop=lambda: None, playback_in_flight=True)
    playback, _ = voice_subsystems(lambda: SimpleNamespace(_player=player))
    assert await playback.probe() == "still_active"
    await playback.request()
    player.playback_in_flight = False
    assert await playback.probe() == "confirmed"


async def test_playback_probe_is_unknown_for_a_player_that_cannot_say() -> None:
    playback, _ = voice_subsystems(lambda: SimpleNamespace(_player=_Player()))
    assert await playback.probe() == "unknown"


async def test_realtime_part_hangs_up_only_a_live_session_and_probes_the_handle() -> None:
    state = {"handle": object(), "hangups": 0}

    def hangup() -> bool:
        state["hangups"] += 1
        state["handle"] = None
        return True

    pipeline = SimpleNamespace(
        _realtime_voice_handle=lambda: state["handle"], request_voice_hangup=hangup)
    _, realtime = voice_subsystems(lambda: pipeline)
    assert await realtime.probe() == "still_active"
    await realtime.request()
    assert state["hangups"] == 1
    assert await realtime.probe() == "confirmed"
    await realtime.request()                      # no session left: nothing to hang up
    assert state["hangups"] == 1


# -- realtime playback ----------------------------------------------------------------------


async def test_realtime_audio_of_a_stopped_session_is_dropped() -> None:
    sent: list[bytes] = []

    class _P:
        output_muted = False
        output_generation = 0

        async def play_chunks(self, chunks) -> bool:
            async for c in chunks:
                sent.append(c.pcm)
            return True

        def stop(self) -> None: ...

    with stop_gate.voice_turn() as turn:
        playback = DesktopRealtimePlayback(_P(), prebuffer_ms=0, stale=turn.stale)
        await _stop_and_resume()
        await playback.send_binary(b"\x00\x01" * 100)
        await playback.cancel()
    assert sent == []


async def test_a_queued_fragment_dispatched_after_stop_and_resume_stays_silent() -> None:
    class _Brain:
        calls = 0

        async def generate(self, text, **_kw) -> str:
            _Brain.calls += 1
            return "Antwort auf das alte Fragment."

    tts, player = _TTS(), _Player()
    pipeline = _pipeline(tts, player, _Brain())
    # The fragment was queued (ticket taken) before STOP; the timer fires after resume.
    with stop_gate.voice_turn():
        await _stop_and_resume()
        await pipeline._handle_flushed_pending_text("altes fragment", "de")
    assert tts.synthesized == [] and player.played == []
