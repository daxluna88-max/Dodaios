"""The uninstall bootstraps never remove the shared install for a product (ADR-0054, block 5).

Text-level checks only: nothing here runs either script. Written, not run.
"""

from __future__ import annotations

from pathlib import Path

_INSTALL = Path(__file__).resolve().parents[3] / "install"
SH = (_INSTALL / "uninstall.sh").read_text(encoding="utf-8")
PS1 = (_INSTALL / "uninstall.ps1").read_text(encoding="utf-8")


def test_sh_product_mode_exits_before_the_folder_is_removed() -> None:
    assert 'PRODUCT_MODE=1' in SH
    assert SH.index('if [ "$PRODUCT_MODE" -eq 1 ]; then\n    step') < SH.index(
        '    remove_install_dir "$INSTALL_DIR"\n    ok "Removed'
    )


def test_sh_refusal_code_has_no_rm_advice() -> None:
    branch = SH.split('elif [ "$RC" -eq 3 ]; then', 1)[1].split("elif", 1)[0]
    assert "exit 3" in branch and "rm -rf" not in branch


def test_sh_no_venv_fallback_refuses_with_product_data_or_product_flag() -> None:
    fallback = SH.split("Python environment missing", 1)[1].split("case \"$(uname -s)\"", 1)[0]
    assert "exit 3" in fallback
    assert ".dodaios-data-work" in fallback and ".dodaios-data-consumer" in fallback
    assert 'PRODUCT_MODE" -eq 1' in fallback


def test_ps1_product_mode_exits_before_the_folder_is_removed() -> None:
    assert "$ProductMode" in PS1
    assert PS1.index("if ($ProductMode) {\n    Write-Step") < PS1.index("# 1: delete the folder")


def test_ps1_refusal_code_and_no_venv_guard() -> None:
    assert "if ($Rc -eq 3)" in PS1
    fallback = PS1.split("Python environment missing", 1)[1].split("Remove-Item", 1)[0]
    assert "exit 3" in fallback and "Dodaios-consumer" in fallback and "Dodaios-work" in fallback
