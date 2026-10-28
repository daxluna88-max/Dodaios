"""Block 18 (ADR-0062, GPT round 5, I1): the label of the CURRENT message (text extract, image,
PDF) is enforced at every actual dispatch. Written, NOT run.
"""

from __future__ import annotations

import contextvars
from types import SimpleNamespace
from typing import Any

import pytest

from dodaios.agent_chat import runner_api, runner_cli, send_queue
from dodaios.brain import dispatch_gate, engine_policy
from dodaios.brain import provenance as prov
from dodaios.brain.provenance import InputItem, Provenance


@pytest.fixture(autouse=True)
def locality(monkeypatch: pytest.MonkeyPatch):
    """Only 'ollama' is a verified loopback engine; Work is off unless a test turns it on."""

    def fake(provider: str, brain: Any = None) -> engine_policy.Destination:
        local = provider == "ollama"
        return engine_policy.Destination("127.0.0.1" if local else None, local, "test")

    monkeypatch.setattr(engine_policy, "destination_of", fake)
    monkeypatch.setattr(engine_policy, "work_active", lambda product=None: False)
    monkeypatch.setattr(engine_policy, "require_local_inference", lambda *a, **k: None)


def _declare(kind: str, label: Provenance) -> None:
    prov.declare_input(label, (InputItem(kind, f"f.{kind}", label),))


def _in_fresh_context(fn):
    """Run ``fn`` in a copied context so the ContextVars never leak between tests."""
    return contextvars.copy_context().run(fn)


class _Events:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


def _handle(provider: str, sink: _Events) -> Any:
    return SimpleNamespace(
        session=SimpleNamespace(
            provider=provider, model="m", cwd="", effort=None, vendor_session=None,
            surface="jarvis", permission_mode="default",
        ),
        turn_id="t1", emit=sink.emit, history=[], images=(), tools_disabled=True,
        plain_system="", assistant_name="J", output_language="", request_approval=None,
    )


def _finished_error(sink: _Events) -> str:
    done = [e for e in sink.events if e.get("kind") == "turn_finished"]
    assert done, "a refused turn must be closed visibly"
    return str(done[-1]["payload"].get("error"))


# ----------------------------------------------------- API runner: text and image, remote refused


@pytest.mark.parametrize("kind", ["text", "image"])
@pytest.mark.parametrize("label", [Provenance.UNAVAILABLE, Provenance.RESTRICTED])
async def test_api_runner_refuses_remote_for_a_labelled_attachment(
    monkeypatch: pytest.MonkeyPatch, kind: str, label: Provenance
) -> None:
    sent: list[Any] = []

    class Brain:
        name = "openai"

        def complete(self, req):  # pragma: no cover - must not be reached
            sent.append(req)
            raise AssertionError("a remote engine was called")

    async def boom(*_a, **_k):  # the image must not even be read for a refused engine
        raise AssertionError("images were prepared for a refused destination")

    monkeypatch.setattr(runner_api, "build_brain", lambda provider, model: Brain())
    monkeypatch.setattr(runner_api, "normalize_effort", lambda provider, effort: effort)
    monkeypatch.setattr(runner_api, "_plain_images", boom)
    sink = _Events()

    async def go() -> None:
        _declare(kind, label)
        await runner_api.run_api_turn(_handle("openai", sink), "describe it\n\n[extracted text]")

    await contextvars.copy_context().run(go)
    assert not sent
    assert "stay on this computer" in _finished_error(sink)


async def test_api_runner_local_engine_is_allowed_to_proceed_past_the_gate() -> None:
    def go() -> str | None:
        _declare("text", Provenance.UNAVAILABLE)
        return dispatch_gate.refusal("ollama")

    assert _in_fresh_context(go) is None


# --------------------------------------------------------------------- CLI runner: PDF refused


async def test_cli_runner_refuses_a_pdf_extract_and_spawns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: list[Any] = []
    monkeypatch.setattr(runner_cli.asyncio, "create_subprocess_exec",
                        lambda *a, **k: spawned.append(a))
    sink = _Events()

    async def go() -> str | None:
        _declare("pdf", Provenance.UNAVAILABLE)
        return await runner_cli.run_cli_turn(_handle("claude", sink), "summarise", "claude")

    assert await contextvars.copy_context().run(go) is None
    assert not spawned
    assert "was not sent" in _finished_error(sink)


# ------------------------------------------------------ no label, no claim: nothing is reshaped


def test_a_message_without_attachments_is_not_touched() -> None:
    assert _in_fresh_context(lambda: dispatch_gate.refusal("openai")) is None


def test_unrestricted_attachments_may_go_remote() -> None:
    def go() -> str | None:
        _declare("image", Provenance.UNRESTRICTED)
        return dispatch_gate.refusal("openai")

    assert _in_fresh_context(go) is None


# ---------------------------------------------------- Work: verified-local is required anyway


def test_work_requires_loopback_even_for_an_allowlisted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_policy, "work_active", lambda product=None: True)
    monkeypatch.setattr(
        engine_policy, "destination_of",
        lambda provider, brain=None: engine_policy.Destination("10.0.0.5", False, "lan"),
    )

    def go() -> str | None:
        _declare("text", Provenance.RESTRICTED)
        return dispatch_gate.refusal("ollama")

    assert _in_fresh_context(go) is not None


# ------------------------------------------------------------------- fallback chain re-check


def test_every_fallback_attempt_rechecks_the_same_label() -> None:
    def go() -> list[str | None]:
        _declare("image", Provenance.RESTRICTED)
        # primary remote -> refused; second remote (fallback) -> refused; local -> allowed
        return [dispatch_gate.refusal(p) for p in ("anthropic", "openai", "ollama")]

    first, second, third = _in_fresh_context(go)
    assert first and second and third is None


async def test_dispatcher_refuses_a_remote_brain_before_any_request() -> None:
    from dodaios.brain.dispatcher import BrainDispatcher

    class Remote:
        name = "openai"

        def complete(self, req):  # pragma: no cover - must not be reached
            raise AssertionError("remote engine called")

    async def go() -> None:
        _declare("text", Provenance.UNAVAILABLE)
        with pytest.raises(dispatch_gate.InputLabelRefused):
            await BrainDispatcher(Remote()).dispatch("hi")

    await contextvars.copy_context().run(go)


def test_an_unnamed_brain_is_unprovable_hence_remote() -> None:
    def go() -> str | None:
        _declare("text", Provenance.UNAVAILABLE)
        return dispatch_gate.refusal(dispatch_gate.provider_of(object()))

    assert _in_fresh_context(go) is not None


# --------------------------------------------------------- begin_turn merges, never loosens


@pytest.mark.parametrize("label", [Provenance.UNAVAILABLE, Provenance.RESTRICTED])
def test_begin_turn_keeps_the_inherited_input_label(label: Provenance) -> None:
    def go() -> Provenance:
        _declare("text", label)
        prov.begin_turn("turn-1")  # the manager's own turn start
        return prov.turn_provenance()

    assert _in_fresh_context(go) is label


def test_begin_turn_does_not_forget_restricted_even_with_a_declared_clean_item() -> None:
    def go() -> tuple[Provenance, bool]:
        _declare("text", Provenance.RESTRICTED)
        _declare("image", Provenance.UNRESTRICTED)  # a later, looser item cannot lift it
        prov.begin_turn("turn-2")
        return prov.turn_provenance(), prov.input_requires_local()

    assert _in_fresh_context(go) == (Provenance.RESTRICTED, True)


def test_the_turn_label_is_the_most_restrictive_item_and_items_are_kept() -> None:
    def go() -> prov.InputLabel:
        prov.declare_input(None, (
            InputItem("text", "a.txt", Provenance.UNRESTRICTED),
            InputItem("pdf", "b.pdf", Provenance.UNAVAILABLE),
        ))
        label = prov.input_label()
        assert label is not None
        return label

    label = _in_fresh_context(go)
    assert label.turn is Provenance.UNAVAILABLE  # a missing declared label reads UNAVAILABLE
    assert [i.kind for i in label.items] == ["text", "pdf"]
    with pytest.raises(AttributeError):
        label.turn = Provenance.UNRESTRICTED  # type: ignore[misc]  # frozen


# --------------------------------------------------------------- queued turn keeps its label


def test_a_queued_record_keeps_its_label_and_the_gate_reads_it_back() -> None:
    from dodaios.agent_chat import attachments as chat_attachments

    record = send_queue._Queued(  # noqa: SLF001
        "q1", "hi", [{"name": "a.pdf", "kind": "pdf", "detail": "x"}], None, None, None,
        "unavailable",
    )
    again = send_queue._Queued.from_dict(record.to_dict())  # noqa: SLF001
    analyses = chat_attachments.to_analysis(again.attachments)
    carried = chat_attachments.turn_label(analyses, again.provenance)

    def go() -> str | None:
        prov.declare_input(carried, tuple(
            InputItem(a.kind, a.name, prov.parse(a.provenance)) for a in analyses))
        return dispatch_gate.refusal("openai")

    assert carried is Provenance.UNAVAILABLE
    assert _in_fresh_context(go) is not None
