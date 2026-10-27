"""What each voice output can honestly drive on an avatar (ADR-0058 block 13, ADR-0056).

Read from the code, not from a wish. ``speech_activity`` = "audible speech started / stopped"
(``AudioOutFirst`` and the supervisor leaving SPEAKING). ``lip_sync`` would need a viseme or
phoneme timing stream; NO shipped TTS plugin and NO realtime/live provider adapter produces one,
so it is ``none`` everywhere and nothing may be labelled lip sync.

``TTSProvider.synthesize`` takes text, voice and language only, and ``AudioChunk`` carries PCM
with no timing metadata. Parameters below are fixed when the provider is BUILT (config), never per
turn, so they are not mood modulation (``expression.TTS_TURN_PARAMS`` stays empty).
"""

from __future__ import annotations

#: provider -> (construction-time parameters its code passes to the engine, per-turn modulation).
TTS_CAPABILITIES: dict[str, dict[str, object]] = {
    "elevenlabs": {"build_params": ("stability", "style", "speed"), "per_turn": False},
    "cartesia": {"build_params": ("speed",), "per_turn": False},
    "grok-voice": {"build_params": ("speed",), "per_turn": False},
    "inworld": {"build_params": ("speed",), "per_turn": False},
    "openrouter": {"build_params": ("speed",), "per_turn": False},
    "gemini-flash": {"build_params": ("style_prompt",), "per_turn": False},
    "piper": {"build_params": ("speed",), "per_turn": False},
    "fallback": {"build_params": (), "per_turn": False},
}

#: Output path -> what the avatar can be told. Same vocabulary everywhere.
OUTPUT_SIGNALS: dict[str, dict[str, str]] = {
    "classic_tts": {
        "speech_activity": "audible_start_and_state_end",
        "amplitude": "in_process_only",  # level_tap sink (jarvis bar); not forwarded to the avatar
        "lip_sync": "none",
    },
    "realtime_desktop": {
        "speech_activity": "audible_start_and_state_end",
        "amplitude": "in_process_only",
        "lip_sync": "none",
    },
    "live_browser": {
        "speech_activity": "browser_playback_state",
        "amplitude": "none",
        # speech_timing frames are CHARACTER ranges of captions for a timed_pcm provider,
        # not visemes: they time text, not mouth shapes.
        "lip_sync": "none",
    },
}


def lip_sync_available(output: str) -> bool:
    """Always False today; kept as the single place that would change if a viseme stream existed."""
    return OUTPUT_SIGNALS.get(output, {}).get("lip_sync", "none") != "none"


__all__ = ["OUTPUT_SIGNALS", "TTS_CAPABILITIES", "lip_sync_available"]
