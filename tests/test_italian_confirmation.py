"""Italian go-ahead / veto classification uses its own patterns, not the German fallback."""
import unittest

from dodaios.voice.echo_confirmation import classify_response


class ItalianConfirmationTests(unittest.TestCase):
    def test_confirm(self):
        for text in ("sì", "Sì, procedi", "vai pure", "ok", "fai", "confermo", "certo"):
            with self.subTest(text=text):
                self.assertEqual(classify_response(text, language="it"), "confirm")

    def test_veto_wins_over_confirm(self):
        for text in ("no", "no, anzi sì", "annulla", "ferma tutto", "non farlo", "lascia perdere"):
            with self.subTest(text=text):
                self.assertEqual(classify_response(text, language="it"), "veto")

    def test_ambiguous_and_unknown(self):
        self.assertEqual(classify_response("forse", language="it"), "ambiguous")
        self.assertEqual(classify_response("aspetta un attimo", language="it"), "ambiguous")
        self.assertEqual(classify_response("che ore sono", language="it"), "unknown")

    def test_a_question_is_not_approval(self):
        self.assertNotEqual(classify_response("puoi ripetere?", language="it"), "confirm")

    def test_live_approval_locales_include_italian(self):
        from dodaios.live.tools import _APPROVAL_LOCALES
        self.assertIn("it", _APPROVAL_LOCALES)
