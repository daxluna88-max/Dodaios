"""Block 6 coverage under JARVIS_PRODUCT=work (written, NOT run)."""

from __future__ import annotations

import pytest

from dodaios.brain import engine_policy as ep


@pytest.fixture
def work(monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "work")


def test_require_local_inference_raises_under_work(work):
    with pytest.raises(ep.EnginePolicyError) as exc:
        ep.require_local_inference("X")
    assert exc.value.code == ep.UNAVAILABLE_CODE


def test_require_local_inference_noop_for_consumer(monkeypatch):
    monkeypatch.setenv("JARVIS_PRODUCT", "consumer")
    ep.require_local_inference("X")


def test_provider_blocked_reason(work):
    assert ep.provider_blocked_reason("brain", "openai")
    assert ep.provider_blocked_reason("brain", "ollama") is None
    assert ep.provider_blocked_reason("tts", "elevenlabs")
    assert ep.provider_blocked_reason("tts", "piper-local") is None
    assert ep.provider_blocked_reason("stt", "groq-api")
    assert ep.provider_blocked_reason("realtime", "openai-realtime")


def test_check_endpoint_url(work):
    ep.check_endpoint_url("http://127.0.0.1:11434", "t")
    with pytest.raises(ep.EnginePolicyError):
        ep.check_endpoint_url("https://api.example.com/v1", "t")


def test_gemini_builders_refuse(work):
    from dodaios.core import google_genai

    with pytest.raises(ep.EnginePolicyError):
        google_genai.build_genai_client("AIza-fake")
    with pytest.raises(ep.EnginePolicyError):
        google_genai.build_vertex_client("k")


def test_tts_factory_refuses_cloud_and_unknown(work):
    from dodaios.plugins import tts

    for name in ("gemini-flash-tts", "elevenlabs", "totally-unknown"):
        with pytest.raises(ep.EnginePolicyError):
            tts._build_provider(object(), name)
    assert tts._tts_has_credential("elevenlabs", object()) is False


def test_stt_cloud_has_no_key_under_work(work, monkeypatch):
    from dodaios.plugins import stt

    def boom(*a, **k):
        raise AssertionError("credential read")

    monkeypatch.setattr("dodaios.core.config.get_secret_any", boom)
    assert stt._stt_family_has_key("groq-api") is False


def test_background_providers_drop_remote(work):
    from dodaios.brain import background_policy as bp

    out = bp.background_providers(["openai", "claude-api", "ollama"])
    assert "openai" not in out.allowed and "claude-api" not in out.allowed


def test_resolve_subscription_brain_none_under_work(work):
    from dodaios.brain import resolver

    assert resolver.resolve_subscription_brain(object()) is None


def test_ack_provider_allowed(work):
    from dodaios.brain.factory import _ack_provider_allowed

    assert _ack_provider_allowed("gemini", object()) is False
    assert _ack_provider_allowed("ollama", None) is False


def test_browser_llm_spec_refuses_remote(work):
    from dodaios.society.browser.llm import LLMUnavailable, llm_spec_for

    with pytest.raises(LLMUnavailable):
        llm_spec_for("openai", secret=lambda p: "sk-x")


def test_subscription_voice_never_selected(work):
    from dodaios.voice.subscription_profile import (
        CODEX_SUBSCRIPTION_VOICE_PROFILE,
        subscription_voice_selected,
    )

    class V:
        profile = CODEX_SUBSCRIPTION_VOICE_PROFILE

    class C:
        voice = V()

    assert subscription_voice_selected(C()) is False


def test_cli_runner_refused(work):
    import asyncio

    from dodaios.agent_chat.runner_cli import run_cli_turn

    with pytest.raises(ep.EnginePolicyError):
        asyncio.run(run_cli_turn(object(), "hi", "claude-cli"))
