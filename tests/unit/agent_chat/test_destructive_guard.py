"""Destructive calls always show the card, even in a bypass chat."""
from __future__ import annotations

import pytest

from dodaios.agent_chat.approval_bridge import ChatGrant, ChatApprovalBridge
from dodaios.agent_chat.destructive_guard import needs_confirmation


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf ~/.dodaios",
        "rm chat.db",
        "find . -name '*.md' -delete",
        "git reset --hard HEAD~3",
        "git push --force origin main",
        "sqlite3 data/agent_chat.db 'delete from agent_chat_events'",
        "python3 -c \"import shutil; shutil.rmtree('/home/dax/.dodaios')\"",
        "truncate -s 0 USER.md",
        "echo '' > ~/.dodaios/data/USER.md",
        "mv MEMORY.md /tmp/x",
        "sed -i 's/a/b/' jarvis.toml",
        "pkill -x jarvis",
        "systemctl stop jarvis",
    ],
)
def test_destructive_commands_need_the_card(command):
    assert needs_confirmation("RunCommand", {"command": command}) is True
    assert needs_confirmation("run_shell", {"command": command}) is True


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat USER.md",
        "grep -n tapiro ~/.dodaios/data/USER.md",
        "date",
        "TZ=Europe/Rome date",
        "git status",
        "git log --oneline -5",
        "echo hello",
        "df -h",
        "npm run build",
    ],
)
def test_reads_and_plain_commands_do_not(command):
    assert needs_confirmation("RunCommand", {"command": command}) is False


def test_file_writes_to_the_app_data_need_the_card():
    assert needs_confirmation("Write", {"file_path": "/home/dax/.dodaios/data/USER.md"}) is True
    assert needs_confirmation("Edit", {"file_path": "/home/dax/project/MEMORY.md"}) is True
    assert needs_confirmation("Write", {"file_path": "/home/dax/project/notes.txt"}) is False


def test_other_tools_and_empty_calls_are_untouched():
    assert needs_confirmation("search_web", {"query": "rm -rf"}) is False
    assert needs_confirmation("RunCommand", {}) is False
    assert needs_confirmation("RunCommand", None) is False
    assert needs_confirmation("mcp__jarvis__run_shell", {"command": "rm x"}) is True


def test_bypass_still_asks_for_a_destructive_call_but_not_for_a_safe_one():
    async def _ask(*_a):  # pragma: no cover - never reached
        return "allow"

    grant = ChatGrant(
        session_id="s",
        turn_id="t",
        stance="bypass",
        always_allowed=set(),
        ask=_ask,
        force_ask=needs_confirmation,
    )
    decide = ChatApprovalBridge._decide_inline
    assert decide(grant, "RunCommand", {"command": "rm -rf ~/.dodaios"}) is None
    assert decide(grant, "RunCommand", {"command": "date"}) == "chat-bypass"
