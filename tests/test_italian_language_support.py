"""Offline language boundary checks; no application server or provider is loaded.

The registry/catalog are imported normally. Heavy application modules are parsed
and their exact methods/annotations compiled in isolation: these tests do not
claim a live HTTP route or a TOML persistence round trip.
"""
from __future__ import annotations

import ast
import json
from string import Formatter
from pathlib import Path
import types
import unittest
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from dodaios.i18n import catalog
from dodaios.i18n.locales import (
    SUPPORTED_LANGUAGE_NAMES,
    SUPPORTED_LOCALES,
    phrase_locale,
)

ROOT = Path(__file__).resolve().parents[1]


def tree(path: str) -> ast.Module:
    return ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))


def load_nodes(nodes: list[ast.stmt], namespace: dict) -> dict:
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<source-boundary>", "exec"), namespace)
    return namespace


def annotated_value(path: str, name: str):
    node = next(n for n in tree(path).body if isinstance(n, ast.AnnAssign) and n.target.id == name)
    return ast.literal_eval(node.value)


def field_type(path: str, class_name: str, field: str):
    cls = next(n for n in tree(path).body if isinstance(n, ast.ClassDef) and n.name == class_name)
    annotation = next(n.annotation for n in cls.body if isinstance(n, ast.AnnAssign) and n.target.id == field)
    return eval(compile(ast.Expression(annotation), "<annotation>", "eval"), {"Literal": Literal})


class ItalianLanguageSupportTests(unittest.TestCase):
    def test_explicit_reply_pin_and_canned_phrase_catalog_are_available(self):
        self.assertIn("it", ("auto", *SUPPORTED_LOCALES))
        self.assertEqual(SUPPORTED_LANGUAGE_NAMES["it"], "Italian")
        self.assertIn("it", SUPPORTED_LOCALES)
        self.assertEqual(phrase_locale("it"), "it")
        self.assertEqual(catalog.t("brain.lang_switch_confirm", "it"), "Fatto: d’ora in poi risponderò in italiano.")

    def test_italian_catalog_matches_every_key_variant_and_placeholder(self):
        root = ROOT / "dodaios/i18n/locales"
        english = json.loads((root / "en.json").read_text())
        italian = json.loads((root / "it.json").read_text())
        formatter = Formatter()

        def walk(master, translated, path=""):
            self.assertIs(type(translated), type(master), path)
            if isinstance(master, dict):
                self.assertEqual(master.keys(), translated.keys(), path)
                for key in master:
                    walk(master[key], translated[key], f"{path}.{key}")
            elif isinstance(master, list):
                self.assertEqual(len(master), len(translated), path)
                for index, (left, right) in enumerate(zip(master, translated)):
                    walk(left, right, f"{path}[{index}]")
            else:
                fields = lambda text: sorted(field for _, field, _, _ in formatter.parse(text) if field is not None)
                self.assertEqual(fields(master), fields(translated), path)
                self.assertTrue(translated.strip(), path)
        walk(english, italian)

    def test_normalize_and_live_switch_use_italian_registry(self):
        source = tree("dodaios/brain/manager.py")
        normalize = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "normalize_reply_language")
        manager = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "BrainManager")
        names = {"set_reply_language", "_mandatory_lang_directive", "_reply_language_directive"}
        methods = [n for n in manager.body if isinstance(n, ast.FunctionDef) and n.name in names]
        ns = load_nodes([normalize, *methods], {"_REPLY_LANGS": frozenset(("auto", *SUPPORTED_LOCALES)), "_REPLY_LANG_NAMES": SUPPORTED_LANGUAGE_NAMES})
        self.assertEqual(ns["normalize_reply_language"](" IT "), "it")
        self.assertEqual(ns["normalize_reply_language"]("unsupported"), "auto")
        owner = types.SimpleNamespace()
        for method in methods:
            setattr(owner, method.name, types.MethodType(ns[method.name], owner))
        owner.set_reply_language(" IT ")
        self.assertEqual(owner._reply_language, "it")
        self.assertIn("Always reply in Italian", owner._reply_language_directive())
        with self.assertRaises(ValueError):
            owner.set_reply_language("unsupported")

    def test_ui_configuration_and_route_allow_italian(self):
        adapter = TypeAdapter(field_type("dodaios/core/config.py", "UIConfig", "language"))
        self.assertEqual(adapter.validate_python("it"), "it")
        with self.assertRaises(ValidationError):
            adapter.validate_python("unsupported")
        self.assertIn("it", annotated_value("dodaios/ui/web/settings_routes.py", "_UI_LANGUAGES"))

    def test_control_boundary_accepts_italian_and_rejects_unknown(self):
        adapter = TypeAdapter(field_type("dodaios/ui/web/control_routes.py", "LanguageBody", "reply_language"))
        self.assertEqual(adapter.validate_python("it"), "it")
        with self.assertRaises(ValidationError):
            adapter.validate_python("unsupported")

    def test_italian_stt_tags_survive_promotion_to_supported_catalog(self):
        from dodaios.core.turn_language import normalize_any_language_tag, normalize_language_tag
        for tag in ("it", "ita", "Italian", "italiano", "it-IT", "it_IT"):
            with self.subTest(tag=tag):
                self.assertEqual(normalize_language_tag(tag), "it")
                self.assertEqual(normalize_any_language_tag(tag), "it")

    def test_valid_italian_output_is_not_rejected_as_spanish(self):
        from dodaios.core.turn_language import validate_output_language
        for text in (
            "La casa è molto bella e la cosa che mi piace di più è la luce.",
            "Questa risposta contiene le informazioni che hai richiesto.",
        ):
            result = validate_output_language(text, resolved_language="it")
            self.assertEqual(result.status, "match")
        result = validate_output_language(
            "The weather today is beautiful and I would like to go outside.",
            resolved_language="it",
        )
        self.assertEqual(result.status, "mismatch")

    def test_tool_confirmation_uses_the_italian_catalog(self):
        from dodaios.voice.tool_confirmation import format_tool_confirmation
        expected = catalog.t("voice.tool_confirmation.generic_question", "it")
        self.assertEqual(format_tool_confirmation("synthetic_unknown_tool", language="it-IT"), expected)
        self.assertNotEqual(expected, catalog.t("voice.tool_confirmation.generic_question", "en"))

    def test_recognition_already_accepts_italian(self):
        self.assertIn("it", annotated_value("dodaios/core/config.py", "RECOGNITION_LANGUAGES"))

    def test_language_writers_preserve_independent_config_paths(self):
        calls = []
        source = tree("dodaios/core/config_writer.py")
        names = {"set_ui_language", "set_reply_language"}
        methods = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
        ns = load_nodes(methods, {"Path": Path, "DEFAULT_CONFIG_FILE": Path("unused"), "_patch_table": lambda *args: calls.append(args)})
        path = Path("synthetic-config.toml")
        ns["set_ui_language"]("it", path=path)
        ns["set_reply_language"]("en", path=path)
        self.assertEqual(calls, [(path, "ui", "language", "it"), (path, "brain", "reply_language", "en")])


if __name__ == "__main__":
    unittest.main()
