"""Offline slash-command locale checks using real control/store modules and SQLite."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from dodaios.agent_chat import control
from dodaios.agent_chat.control import ChatControls
from dodaios.agent_chat.control_locale import ControlError, control_message, _MESSAGES_IT
from dodaios.agent_chat.control_types import CommandRequest, GoalState
from dodaios.agent_chat.store import AgentChatStore


class ItalianCommandLocaleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.chat_store = AgentChatStore()
        self.session = self.chat_store.create_session(
            provider="openrouter", model="synthetic", effort="", cwd=".", surface="jarvis",
        )
        self.service = SimpleNamespace(
            store=self.chat_store, is_running=lambda sid: False, cancel=AsyncMock(),
            post_notice=AsyncMock(),
        )
        self.controls = ChatControls(self.service)
        self.language = patch.object(control, "_chat_language", lambda current, text: current)
        self.language.start()
        # Load the real regex-only guard without importing the optional wiki DB
        # package (aiosqlite is not needed for command localization).
        guard_path = Path(control.__file__).parents[1] / "memory/wiki/secret_guard.py"
        spec = importlib.util.spec_from_file_location("isolated_secret_guard", guard_path)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        self.guard = patch.dict(sys.modules, {"dodaios.memory.wiki.secret_guard": guard})
        self.guard.start()

    def tearDown(self):
        self.guard.stop()
        self.language.stop()
        self.chat_store.close()

    def request(self, command, *, locale="it", arguments="", request_id="test-1"):
        return CommandRequest(command=command, locale=locale, arguments=arguments, request_id=request_id)

    def test_request_accepts_italian_but_rejects_unknown_locale(self):
        self.assertEqual(self.request("help").locale, "it")
        with self.assertRaises(ValidationError):
            self.request("help", locale="unsupported")

    async def test_missing_find_argument_is_localized_and_receipt_stays_failed(self):
        request = self.request("find")
        with self.assertLogs(control.log, level="WARNING") as captured:
            result = await self.controls.execute(self.session.session_id, request)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "Usa /find seguito dal testo da cercare")
        self.assertIn("Use /find followed by search text", captured.output[0])
        replay = await self.controls.execute(self.session.session_id, request)
        self.assertEqual(replay.error, result.error)
        self.assertIsNone(control._command_origin.get())

    async def test_other_locale_keeps_existing_english_behavior(self):
        result = await self.controls.execute(self.session.session_id, self.request("find", locale="de"))
        self.assertEqual(result.error, "Use /find followed by search text")

    async def test_provider_diagnostic_is_not_translated_even_if_text_matches_local_error(self):
        diagnostic = "The chat is working; use /stop first"
        with patch.object(self.controls, "_dispatch", AsyncMock(side_effect=ValueError(diagnostic))):
            result = await self.controls.execute(self.session.session_id, self.request("find", arguments="word"))
        self.assertEqual(result.error, diagnostic)

    async def test_stop_reason_uses_request_locale_without_changing_language_or_status_ids(self):
        state = self.controls.state(self.session.session_id)
        state.output_language = "fr"
        state.goal = GoalState(id="synthetic-goal", objective="synthetic", started_ms=1, updated_ms=1)
        self.controls.store.save(state)
        result = await self.controls.execute(self.session.session_id, self.request("stop"))
        self.assertEqual(result.status, "done")
        self.assertEqual(result.state.goal.status, "paused")
        self.assertEqual(result.state.goal.reason, "Fermato dall’utente")
        self.assertEqual(result.state.output_language, "fr")
        self.service.cancel.assert_awaited_once_with(self.session.session_id)

    async def test_surface_admission_error_is_localized(self):
        with self.assertRaisesRegex(ValueError, "soltanto nelle chat"):
            await self.controls.execute("missing-session", self.request("help"))

    def test_dynamic_command_and_permission_identifiers_are_preserved(self):
        self.assertEqual(ControlError("/{command} needs an argument", command="remember").localized("it"), "/remember richiede un argomento")
        message = ControlError("{runner} cannot provide an actionable approval for {permission}", runner="codex-cli", permission="ask")
        self.assertIn("codex-cli", message.localized("it"))
        self.assertIn("ask", message.localized("it"))
        self.assertIn("cannot provide", str(message))
        self.assertEqual(control_message("raw error {identifier}", "it"), "raw error {identifier}")

    def test_every_finite_local_error_template_is_covered(self):
        source = ast.parse(Path(control.__file__).read_text())
        templates = [
            node.args[0].value for node in ast.walk(source)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("ControlError", "_control_text")
            and node.args and isinstance(node.args[0], ast.Constant)
        ]
        self.assertTrue(templates)
        for message in templates:
            self.assertIn(message, _MESSAGES_IT)


if __name__ == "__main__":
    unittest.main()
