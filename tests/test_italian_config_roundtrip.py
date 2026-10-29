"""Exercise real TOML writers and config parsing against a disposable file."""
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest

from dodaios.core.config import load_config
from dodaios.core import config_writer


class ItalianConfigRoundtripTests(unittest.TestCase):
    def test_ui_and_reply_language_survive_reload_independently(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "jarvis.toml"
            path.write_text('[ui]\nlanguage = "en"\n[brain]\nreply_language = "auto"\n[stt]\nlanguage = "de"\n')
            config_writer.set_ui_language("it", path=path)
            cfg = load_config(config_file=path)
            self.assertEqual(cfg.ui.language, "it")
            self.assertEqual(cfg.brain.reply_language, "auto")
            self.assertEqual(cfg.stt.language, "de")
            config_writer.set_reply_language("it", path=path)
            cfg = load_config(config_file=path)
            self.assertEqual(cfg.brain.reply_language, "it")
            self.assertEqual(cfg.ui.language, "it")
            self.assertEqual(cfg.stt.language, "de")
            saved = tomllib.loads(path.read_text())
            self.assertEqual(saved["ui"]["language"], "it")
            self.assertEqual(saved["brain"]["reply_language"], "it")
            self.assertEqual(saved["stt"]["language"], "de")


if __name__ == "__main__":
    unittest.main()
