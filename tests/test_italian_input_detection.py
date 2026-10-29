"""Reply language 'auto' mirrors the input: Italian text must not read as Spanish or English."""
import unittest
from itertools import combinations

from dodaios.core import turn_language as tl


class ItalianInputDetectionTests(unittest.TestCase):
    def test_italian_sentences_resolve_to_italian(self):
        for text in (
            "A breve inizierò a lavorare con te",
            "Ciao, come stai?",
            "Mi puoi aiutare con una cosa?",
            "Voglio scrivere una email al mio capo",
            "Cosa fai oggi?",
            "Perché non funziona?",
        ):
            with self.subTest(text=text):
                self.assertEqual(tl.resolve_output_language("auto", "unknown", text, default="en"), "it")

    def test_other_supported_languages_are_unchanged(self):
        for text, code in (("Hola, ¿cómo estás?", "es"), ("Olá, tudo bem?", "pt"),
                           ("Wie geht es dir heute?", "de"), ("What is the weather today?", "en")):
            with self.subTest(text=text):
                self.assertEqual(tl.resolve_output_language("auto", "unknown", text, default="en"), code)

    def test_word_sets_stay_disjoint(self):
        for (a, x), (b, y) in combinations(tl._SETS, 2):
            self.assertFalse(x & y, (a, b, sorted(x & y)))

    def test_italian_reply_to_italian_target_is_a_match(self):
        result = tl.validate_output_language("Va bene, sono qui quando hai bisogno di me.", resolved_language="it")
        self.assertNotEqual(result.status, "mismatch")
        self.assertEqual(
            tl.validate_output_language("Perfecto. Aquí estoy cuando lo necesites ahora mismo.", resolved_language="it").status,
            "mismatch",
        )
