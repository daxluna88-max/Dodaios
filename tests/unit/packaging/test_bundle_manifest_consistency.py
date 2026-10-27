"""The freeze recipe names what the last blocks added (ADR-0064). Reads files only.

Written, NOT run (the owner banned every test run for block 16).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SPEC = (REPO / "dodaios.spec").read_text(encoding="utf-8")
PYPROJECT = (REPO / "pyproject.toml").read_text(encoding="utf-8")


def _block_modules() -> list[str]:
    body = SPEC.split("BLOCK_MODULES = (", 1)[1].split(")", 1)[0]
    return re.findall(r'"(dodaios\.[a-z_.]+)"', body)


def test_every_named_block_module_exists_on_disk() -> None:
    for name in _block_modules():
        path = REPO / (name.replace(".", "/") + ".py")
        assert path.is_file() or (REPO / name.replace(".", "/") / "__init__.py").is_file(), name


def test_the_safety_stop_and_provenance_modules_are_named() -> None:
    names = set(_block_modules())
    for required in (
        "dodaios.safety.effects",
        "dodaios.safety.decision",
        "dodaios.safety.preflight",
        "dodaios.brain.provenance",
        "dodaios.core.child_identity",
        "dodaios.speech.stop_gate",
        "dodaios.speech.stop_parts",
        "dodaios.control.stop",
        "dodaios.core.product_profile",
        "dodaios.core.product_backup",
    ):
        assert required in names, required


def test_memory_control_package_is_collected() -> None:
    assert 'collect_submodules("dodaios.memory.control")' in SPEC
    assert (REPO / "dodaios/memory/control/__init__.py").is_file()


def test_the_spec_still_sweeps_package_data_and_locales() -> None:
    assert '_package_root.rglob("*")' in SPEC
    assert '"dodaios/i18n/locales"' in SPEC
    assert 'FRONTEND_DIST = PROJECT_ROOT / "dodaios" / "ui" / "web" / "dist"' in SPEC


def test_the_wheel_manifest_names_the_backend_locales_and_migrations() -> None:
    assert '"i18n/locales/*.json"' in PYPROJECT
    assert '"**/*.sql"' in PYPROJECT
    assert '"ui/web/dist/**/*"' in PYPROJECT


def test_backend_locale_files_are_the_four_the_catalog_loads() -> None:
    found = {p.stem for p in (REPO / "dodaios/i18n/locales").glob("*.json")}
    assert {"de", "en", "es", "pt"} <= found  # there is no it.json: stated in ADR-0064


def test_the_committed_bundle_has_an_entry_document() -> None:
    assert (REPO / "dodaios/ui/web/dist/index.html").is_file()


def test_the_release_gate_keeps_its_dist_freshness_check() -> None:
    text = (REPO / "scripts/ci/check_release_completeness.py").read_text(encoding="utf-8")
    assert "def check_dist_freshness" in text
    assert "check_dist_freshness(root)" in text
