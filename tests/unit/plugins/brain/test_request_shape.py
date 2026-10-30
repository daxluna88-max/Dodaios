"""The request-size log line carries sizes and tool names, never content."""
from dodaios.core.protocols import BrainMessage, BrainRequest
from dodaios.plugins.brain._openai_base import request_shape


def test_shape_reports_sizes_and_the_heaviest_tools_without_content() -> None:
    req = BrainRequest(
        messages=(
            BrainMessage(role="user", content="SECRET-USER-TEXT"),
            BrainMessage(role="assistant", content="ok"),
        ),
        tools=(
            {"name": "small", "description": "x"},
            {"name": "big", "description": "y" * 500},
        ),
        system="SECRET-SYSTEM-PROMPT",
    )
    shape = request_shape(req)
    assert "system=20 chars" in shape
    assert "messages=2 (assistant=2, user=16)" in shape
    assert "tools=2" in shape
    assert shape.index("big:") < shape.index("small:")
    assert "SECRET" not in shape


def test_openai_style_tool_names_and_empty_requests() -> None:
    req = BrainRequest(
        messages=(BrainMessage(role="user", content=[{"type": "text", "text": "hi"}]),),
        tools=({"type": "function", "function": {"name": "fn", "parameters": {}}},),
    )
    assert "fn:" in request_shape(req)
    assert "tools=0 (0 chars; heaviest -)" in request_shape(BrainRequest(messages=()))


def test_shape_line_is_a_warning_only_when_asked_for(monkeypatch, caplog) -> None:
    import asyncio
    import logging

    from dodaios.plugins.brain import _openai_base

    class _Boom(Exception):
        pass

    class _Client:
        def __getattr__(self, name):
            raise _Boom

    req = BrainRequest(messages=(BrainMessage(role="user", content="hi"),), system="s")

    async def run() -> None:
        try:
            async for _ in _openai_base.stream_complete(_Client(), "m", req):
                pass
        except Exception:
            pass

    monkeypatch.delenv("JARVIS_LOG_REQUEST_SHAPE", raising=False)
    with caplog.at_level(logging.WARNING, logger=_openai_base.log.name):
        asyncio.run(run())
    assert "Request shape" not in caplog.text

    monkeypatch.setenv("JARVIS_LOG_REQUEST_SHAPE", "1")
    with caplog.at_level(logging.WARNING, logger=_openai_base.log.name):
        asyncio.run(run())
    assert "Request shape for m: system=1 chars" in caplog.text
