"""Block 8 review fixes R6 (typed provenance) and R7 (default-deny coverage, child identity).

Written, NOT run (owner order: no test execution in this block).
"""

from __future__ import annotations

import asyncio

import pytest

from dodaios.brain import provenance as pv
from dodaios.core import child_identity
from dodaios.core import egress_guard as eg


@pytest.fixture(autouse=True)
def _clean():
    pv.clear()
    pv.begin_turn()
    eg.uninstall()
    yield
    pv.clear()
    pv.begin_turn()
    eg.uninstall()


# --- R6 -----------------------------------------------------------------------------------------


def test_short_restricted_answer_is_blocked_without_the_text_registry():
    # "42" is below the registry's minimum length: only the typed label can stop it.
    pv.set_turn_provenance(pv.Provenance.RESTRICTED)
    assert pv.is_restricted("42") is False
    assert pv.remote_speech_blocked("42") is True


def test_label_survives_transformation_because_it_is_not_in_the_text():
    pv.set_turn_provenance(pv.Provenance.RESTRICTED)
    assert pv.remote_speech_blocked("forty-two, rewritten by TTS normalisation") is True


def test_unavailable_fails_closed_and_unrestricted_passes():
    assert pv.remote_speech_blocked("hello there") is False
    assert pv.remote_speech_blocked(pv.label("hello there", pv.Provenance.UNAVAILABLE)) is True
    pv.set_turn_provenance(pv.Provenance.UNAVAILABLE)
    assert pv.remote_speech_blocked("hello there") is True
    pv.begin_turn()
    assert pv.remote_speech_blocked("hello there") is False


def test_turn_label_only_gets_more_restrictive():
    pv.set_turn_provenance(pv.Provenance.RESTRICTED)
    pv.set_turn_provenance(pv.Provenance.UNRESTRICTED)
    assert pv.turn_provenance() is pv.Provenance.RESTRICTED


def test_plain_string_has_no_label_and_combine_treats_none_as_unavailable():
    assert pv.label_of("x") is None
    assert pv.combine(pv.Provenance.UNRESTRICTED, None) is pv.Provenance.UNAVAILABLE


class _Remote:
    name = "elevenlabs"

    def __init__(self):
        self.calls = 0

    async def synthesize(self, text, **kw):
        self.calls += 1
        yield object()


class _Local(_Remote):
    name = "piper"


def _drain(gen):
    async def run():
        return [c async for c in gen]

    return asyncio.run(run())


def test_metered_tts_skips_remote_for_restricted_turn_but_not_local():
    from dodaios.speech.usage_meter import MeteredTTS

    pv.set_turn_provenance(pv.Provenance.RESTRICTED)
    remote = _Remote()
    assert _drain(MeteredTTS(remote, None).synthesize("42")) == []
    assert remote.calls == 0
    local = _Local()
    from dodaios.brain import engine_policy

    if "piper" in engine_policy.LOCAL_SERVICES["tts"]:
        assert len(_drain(MeteredTTS(local, None).synthesize("42"))) == 1


def test_history_for_remote_engine_drops_restricted_messages():
    from dodaios.brain.manager import _history_for_engine
    from dodaios.core.protocols import BrainMessage

    hist = [BrainMessage(role="user", content="q", restricted=True),
            BrainMessage(role="assistant", content="a", restricted=True),
            BrainMessage(role="user", content="ok")]
    remote_view = _history_for_engine(hist, "openai")
    assert [m.content for m in remote_view] == ["ok"]


def test_synthesis_result_provenance_property():
    from dodaios.brain.consumer_orchestrator import SynthesisResult

    assert SynthesisResult("ok", "t", tainted=True).provenance == "restricted"
    assert SynthesisResult("ok", "t").provenance == "unrestricted"
    assert SynthesisResult("failed").provenance == "unavailable"


# --- R7 -----------------------------------------------------------------------------------------


def test_unclassified_tool_is_refused_under_the_profile():
    eg.install()
    assert "strict offline profile" in (eg.tool_refusal("some_new_plugin_tool") or "")


def test_positively_classified_tool_passes_and_profile_off_refuses_nothing():
    assert eg.tool_refusal("some_new_plugin_tool") is None
    eg.install()
    assert eg.tool_refusal("click") is None


def test_registry_declaration_opens_only_a_none_capability_and_never_a_refusal_class():
    eg.install()
    eg.declare_tool_coverage("my_local_tool", eg.CAPABILITY_NONE)
    eg.declare_tool_coverage("run_shell", eg.CAPABILITY_NONE)
    eg.declare_tool_coverage("typo_tool", "nonsense")
    try:
        assert eg.tool_refusal("my_local_tool") is None
        assert eg.tool_refusal("run_shell") is not None
        assert eg.tool_refusal("typo_tool") is not None
    finally:
        for n in ("my_local_tool", "run_shell", "typo_tool"):
            eg._DECLARED.pop(n, None)


def test_third_party_object_attribute_is_not_trusted():
    eg.install()

    class Evil:
        offline_safe = True

    Evil.__module__ = "thirdparty.plugin"
    assert eg.tool_refusal("evil", Evil()) is not None


def test_child_identity_forwarded_to_worker_and_runtime_env(monkeypatch, tmp_path):
    for k in child_identity.IDENTITY_VARS + child_identity.POLICY_VARS:
        monkeypatch.setenv(k, f"v-{k}")
    env = child_identity.forward_identity({})
    names = child_identity.IDENTITY_VARS + child_identity.POLICY_VARS
    assert all(env[k] == f"v-{k}" for k in names)
    from dodaios.agent_runtimes.base import child_env

    assert child_env()["JARVIS_PRODUCT"] == "v-JARVIS_PRODUCT"
    from dodaios.missions.isolation.env import build_worker_env

    w = build_worker_env(run_dir=tmp_path)
    assert w["JARVIS_NETWORK_POLICY"] == "v-JARVIS_NETWORK_POLICY"
    assert w["JARVIS_DATA_DIR"] == "v-JARVIS_DATA_DIR"


def test_untrusted_child_gets_product_and_policy_but_not_data_layout(monkeypatch):
    for k in child_identity.IDENTITY_VARS + child_identity.POLICY_VARS:
        monkeypatch.setenv(k, "x")
    from dodaios.mcp.env_isolation import isolated_environment

    env = isolated_environment()
    assert "JARVIS_NETWORK_POLICY" in env and "JARVIS_PRODUCT" in env
    assert "JARVIS_DATA_DIR" not in env and "JARVIS_CONFIG" not in env
