"""Offline checks of real config readback and sensitive-path protection."""
import unittest

# Preserve the application's established import order for self_mod.
import dodaios.core.config  # noqa: F401
from dodaios.voice.config_readback import config_readback


class ItalianConfigReadbackTests(unittest.TestCase):
    def test_values_and_restart(self):
        for value, spoken in ((True, "attivato"), (False, "disattivato"), (None, "vuoto")):
            with self.subTest(value=value):
                output = {"applied": True, "path": "tts.speed", "description": "Audio", "new_value": value}
                self.assertEqual(config_readback(success=True, output=output, language="it"), f"Fatto: Audio ora è {spoken}.")
                output.update(description="", requires_restart=True)
                text = config_readback(success=True, output=output, language="it")
                self.assertIn("l’impostazione", text)
                self.assertIn("Riavvia", text)

    def test_sensitive_value_is_hidden(self):
        output = {"applied": True, "path": "security.admin_password_hash", "description": "Credenziale", "new_value": "SECRET-742"}
        text = config_readback(success=True, output=output, language="it")
        self.assertNotIn("SECRET-742", text)
        self.assertIn("il nuovo valore", text)

    def test_failures_never_claim_success_or_leak_values(self):
        for kind in ("forbidden_path", "path_not_allowed", "validate_failed", "reload_failed_rolled_back", "rollback_failed"):
            with self.subTest(kind=kind):
                output = {"error_kind": kind, "path": "secret.path", "new_value": "SECRET-742"}
                text = config_readback(success=False, output=output, language="it")
                self.assertTrue(text)
                for forbidden in ("Fatto", "SECRET-742", "secret.path"):
                    self.assertNotIn(forbidden, text)

    def test_unrecognized_outcome_keeps_fallthrough(self):
        self.assertIsNone(config_readback(success=False, output={}, language="it"))
        self.assertIsNone(config_readback(success=True, output="unrelated", language="it"))


if __name__ == "__main__":
    unittest.main()
