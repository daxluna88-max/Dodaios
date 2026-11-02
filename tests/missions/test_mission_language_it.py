"""Italian dispatch uses the real event model; no worker/provider is started."""
import re
import unittest
from pathlib import Path
from typing import get_args

from pydantic import ValidationError

from dodaios.missions.events import (
    MISSION_LANGUAGES,
    MissionDispatched,
    MissionLanguage,
    coerce_mission_language,
)


class ItalianMissionLanguageTests(unittest.TestCase):
    def test_italian_dispatch_roundtrip(self):
        language = coerce_mission_language(" IT ")
        event = MissionDispatched(prompt="Offline contract test", language=language)
        self.assertEqual(event.language, "it")
        self.assertEqual(MissionDispatched.model_validate_json(event.model_dump_json()), event)

    def test_all_declared_mission_languages_are_accepted(self):
        self.assertEqual(set(get_args(MissionLanguage)), set(MISSION_LANGUAGES))
        for language in MISSION_LANGUAGES:
            with self.subTest(language=language):
                self.assertEqual(MissionDispatched(prompt="x", language=language).language, language)

    def test_frontend_dispatch_language_parity(self):
        root = Path(__file__).resolve().parents[2]
        text = (root / "dodaios/ui/web/frontend/src/types/missions.ts").read_text()
        match = re.search(r"export interface MissionDispatched extends BasePayload \{.*?language: ([^;]+);", text, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(set(re.findall(r'"([^\"]+)"', match.group(1))), set(MISSION_LANGUAGES))

    def test_unknown_language_remains_rejected(self):
        with self.assertRaises(ValidationError):
            MissionDispatched(prompt="x", language="unsupported")
        self.assertEqual(coerce_mission_language("unsupported"), "en")
