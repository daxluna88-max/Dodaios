"""Audio unit tests drive ``sounddevice`` through monkeypatching; they must not need PortAudio.

When the binding cannot load (a runner image without a matching libportaudio, a headless
container), the modules bind ``sd = None`` and ``monkeypatch.setattr(mod.sd, ...)`` raises
``AttributeError: None has no attribute ...``. This fixture gives those modules an inert
stand-in so the tests exercise the code under test instead of the host's audio stack. A
real ``sounddevice`` is never replaced, and tests that set ``sd`` to ``None`` on purpose still do.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

_MODULES = ("dodaios.audio.devices", "dodaios.audio.capture", "dodaios.audio.player")


class _PortAudioError(OSError):
    """An OSError, like the modules' fallback alias for ``PortAudioError`` when ``sd`` is None."""


def _unavailable(*_args, **_kwargs):
    raise _PortAudioError("PortAudio is not available in this test environment")


def _stub() -> SimpleNamespace:
    return SimpleNamespace(
        PortAudioError=_PortAudioError,
        query_devices=_unavailable,
        query_hostapis=_unavailable,
        default=SimpleNamespace(device=(-1, -1)),
        InputStream=_unavailable,
        OutputStream=_unavailable,
        stop=lambda *_args, **_kwargs: None,
        abort=lambda *_args, **_kwargs: None,
    )


@pytest.fixture(autouse=True)
def _portaudio_stand_in(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    for name in _MODULES:
        module = importlib.import_module(name)
        if getattr(module, "sd", None) is None:
            stub = _stub()
            monkeypatch.setattr(module, "sd", stub)
            # the modules bind ``OSError`` as their PortAudio error type when ``sd`` is None; the
            # stand-in's error class must be the one they test for
            if hasattr(module, "_PortAudioError"):
                monkeypatch.setattr(module, "_PortAudioError", stub.PortAudioError)
