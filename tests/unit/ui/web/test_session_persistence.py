from __future__ import annotations

import json

import pytest

from dodaios.ui.web import missions_auth, surface_security


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    missions_auth.reset_tokens()
    yield tmp_path
    missions_auth.reset_tokens()


def test_session_is_not_persisted_by_default(_data_dir):
    tok = missions_auth.issue_token()
    assert not (_data_dir / ".session_tokens").exists()
    missions_auth.reset_tokens()
    assert not missions_auth.validate_token(tok)


def test_session_survives_restart_when_enabled(_data_dir, monkeypatch):
    monkeypatch.setenv("JARVIS_SESSION_PERSIST", "1")
    tok = missions_auth.issue_token()
    path = _data_dir / ".session_tokens"
    assert path.stat().st_mode & 0o777 == 0o600
    assert tok not in path.read_text()  # only a digest reaches disk
    missions_auth.reset_tokens()  # simulates a process restart
    assert missions_auth.validate_token(tok)
    assert not missions_auth.validate_token(tok + "x")


def test_revoke_and_expiry_are_honoured(_data_dir, monkeypatch):
    monkeypatch.setenv("JARVIS_SESSION_PERSIST", "1")
    tok = missions_auth.issue_token()
    missions_auth.revoke_token(tok)
    missions_auth.reset_tokens()
    assert not missions_auth.validate_token(tok)
    old = missions_auth.issue_token()
    path = _data_dir / ".session_tokens"
    path.write_text(json.dumps({k: 0.0 for k in json.loads(path.read_text())}))
    missions_auth.reset_tokens()
    assert not missions_auth.validate_token(old)


def test_cookie_gets_max_age_only_when_enabled(monkeypatch):
    assert "Max-Age" not in surface_security.build_set_cookie_header("abc_DEF-123", True)
    monkeypatch.setenv("JARVIS_SESSION_PERSIST", "1")
    assert "Max-Age=2592000" in surface_security.build_set_cookie_header("abc_DEF-123", True)
