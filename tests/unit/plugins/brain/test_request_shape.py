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
