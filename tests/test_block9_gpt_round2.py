"""Block 9 GPT round 2 (F1 provenance, F2 allowlist identity, F4 child identity).

Written, NOT run (owner order: no test execution in this block).
"""

from __future__ import annotations

import asyncio

import pytest

from dodaios.brain import provenance as pv
from dodaios.core import child_identity as ci
from dodaios.core import egress_guard as eg


@pytest.fixture(autouse=True)
def _clean():
    pv.clear()
    pv.begin_turn()
    eg.uninstall()
    yield
    pv.clear()
    eg.uninstall()


def test_plain_and_short_text_is_refused_for_remote_speech():
    for text in ("42", "ok", "a longer unlabelled sentence"):
        assert pv.remote_speech_blocked(text) is True


def test_only_a_labelled_unrestricted_chunk_passes_and_a_transform_loses_it():
    chunk = pv.label("hello there", pv.Provenance.UNRESTRICTED)
    pv.attach_unrestricted()
    assert pv.remote_speech_blocked(chunk) is False
    assert pv.remote_speech_blocked(str(chunk).upper()) is True  # plain str after transform
    assert pv.remote_speech_blocked(pv.label_like(str(chunk).upper(), chunk)) is False


def test_unrestricted_cannot_lift_unavailable_or_restricted():
    chunk = pv.label("hello there", pv.Provenance.UNRESTRICTED)
    pv.set_turn_provenance(pv.Provenance.UNAVAILABLE)
    pv.attach_unrestricted()
    assert pv.remote_speech_blocked(chunk) is True


def test_concurrent_turns_do_not_reset_each_other():
    chunk = pv.label("hello there", pv.Provenance.UNRESTRICTED)

    async def restricted_turn():
        pv.begin_turn("a")
        pv.set_turn_provenance(pv.Provenance.RESTRICTED)
        await asyncio.sleep(0.01)
        return pv.remote_speech_blocked(chunk)

    async def clean_turn():
        await asyncio.sleep(0.005)
        pv.begin_turn("b")  # must not clear turn a's state
        pv.attach_unrestricted()
        return pv.remote_speech_blocked(chunk)

    async def run():
        return await asyncio.gather(restricted_turn(), clean_turn())

    a, b = asyncio.run(run())
    assert a is True and b is False


def test_homonymous_third_party_tool_gets_no_allowlist_credit():
    eg.install()
    for name in ("whoami", "wiki-list", "click"):
        fake = type("Fake", (), {"name": name})()
        assert eg.tool_refusal(name, fake) is not None
        assert eg.tool_refusal(name) is not None  # name only


def test_spoofed_module_is_refused_and_real_first_party_class_passes():
    eg.install()

    class Spoof:
        name = "whoami"

    Spoof.__module__ = "dodaios.plugins.tool.whoami"
    assert eg.tool_refusal("whoami", Spoof()) is not None
    from dodaios.plugins.tool.whoami import WhoAmITool

    assert eg.tool_refusal("whoami", WhoAmITool()) is None


def test_child_identity_conflicts_fail_closed():
    parent = {"JARVIS_PRODUCT": "alpha", "JARVIS_DATA_DIR": "/data/a",
              "JARVIS_NETWORK_POLICY": "local-only"}
    for name, bad in (("JARVIS_PRODUCT", "beta"), ("JARVIS_DATA_DIR", "/data/b"),
                      ("JARVIS_NETWORK_POLICY", "open")):
        with pytest.raises(ci.ChildIdentityConflict):
            ci.forward_identity({name: bad}, source=parent)
    env = ci.forward_identity({"JARVIS_PRODUCT": "ALPHA", "JARVIS_DATA_DIR": "/data/a/"},
                              source=parent)
    assert env["JARVIS_NETWORK_POLICY"] == "local-only"
    with pytest.raises(ci.ChildIdentityConflict):
        ci.forward_identity({"JARVIS_KEYRING_SERVICE": "x"}, source=parent)  # parent has none
