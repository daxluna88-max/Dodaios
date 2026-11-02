"""Promoting a draft through the web route must reach the registry the skill runner reads.

The web server and the brain factory each own a ``SkillRegistry`` over the same folder. Before the
fix only the web one was reloaded, so ``run-skill`` kept refusing a skill the user had just enabled.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dodaios.skills.authoring import SkillDraft, write_draft
from dodaios.skills.registry import SkillRegistry
from dodaios.skills.schema import SkillLifecycleState
from dodaios.skills.skill_context import set_skill_context
from dodaios.ui.web import skills_routes


def test_promote_is_seen_by_the_runner_registry(tmp_path: Path) -> None:
    root = tmp_path / "user_skills"
    root.mkdir()
    write_draft(
        SkillDraft(
            slug="offerte",
            name="offerte",
            description="d",
            intent="i",
            triggers_yaml="[{type: voice, pattern: '^offerte'}]",
            body_markdown="## Offerte\n\nStep.",
            state="draft",
        ),
        user_skills_root=root,
    )
    web = SkillRegistry(root, bus=None)
    runner = SkillRegistry(root, bus=None)
    web.reload_sync()
    runner.reload_sync()
    set_skill_context(SimpleNamespace(registry=runner, runner=None))
    try:
        web.promote("offerte")
        assert runner.get("offerte").state == SkillLifecycleState.DRAFT  # the bug
        skills_routes._sync_runtime_registry(web)
        assert runner.get("offerte").state == SkillLifecycleState.ACTIVE
    finally:
        set_skill_context(None)  # type: ignore[arg-type]
