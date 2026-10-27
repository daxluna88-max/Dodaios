"""ADR-0061 deletion and export. WRITTEN, NOT RUN."""
from __future__ import annotations

import json
import sqlite3
import tarfile
import zipfile
from pathlib import Path

import pytest

from dodaios.memory.control import deletion
from dodaios.memory.control.export import ExportError, build_export
from dodaios.memory.wiki import fts_index, semantic_index

LINE = "Ana lives in Lisbon and works on the harbour project every day."
PAGE = f"---\ntype: entity\nslug: ana\ntitle: Ana\n---\n\n# Ana\n\n{LINE}\n"


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "entities").mkdir(parents=True)
    (root / "entities" / "ana.md").write_text(PAGE, encoding="utf-8")
    (root / "entities" / "bob.md").write_text("# Bob\n\nBob knows [[ana]].\n", encoding="utf-8")
    (root / "concepts").mkdir()
    (root / "concepts" / "copy.md").write_text(
        f"# Copy\n\n{LINE}\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def db(tmp_path, vault):
    path = tmp_path / "jarvis.db"
    conn = sqlite3.connect(path)
    fts_index.ensure_schema(conn)
    semantic_index.ensure_schema(conn)
    fts_index.upsert_page(conn, vault, vault / "entities" / "ana.md")
    conn.execute(
        "INSERT INTO wiki_semantic_pages(path, mtime, model) VALUES ('entities/ana.md','1','m')"
    )
    conn.commit()
    conn.close()
    return path


def _fts_count(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        query = "SELECT COUNT(*) FROM wiki_fts WHERE path='entities/ana.md'"
        return conn.execute(query).fetchone()[0]
    finally:
        conn.close()


def test_logical_delete_hides_and_is_restorable(vault, db):
    report = deletion.logical_delete(vault, "entities/ana.md", db_path=db, now=1_000.0)
    assert not (vault / "entities" / "ana.md").exists()
    assert report.restore_until == 1_000.0 + deletion.RETENTION_DAYS * 86400
    assert _fts_count(db) == 0
    assert (vault / ".trash" / report.trash_id / "entities" / "ana.md").is_file()
    rel = deletion.restore(vault, report.trash_id, now=2_000.0)
    assert rel == "entities/ana.md" and (vault / rel).is_file()


def test_restore_after_window_is_refused_and_purge_removes(vault, db):
    report = deletion.logical_delete(vault, "entities/ana.md", db_path=db, now=1_000.0)
    late = report.restore_until + 1
    with pytest.raises(deletion.DeletionError) as err:
        deletion.restore(vault, report.trash_id, now=late)
    assert err.value.status == 410
    assert deletion.purge_expired(vault, now=late) == 1
    assert deletion.list_trash(vault) == []


def test_permanent_delete_clears_file_indexes_and_archive_copy(vault, db):
    (vault / "_archive" / "entities").mkdir(parents=True)
    (vault / "_archive" / "entities" / "ana.md").write_text(PAGE, encoding="utf-8")
    report = deletion.permanent_delete(vault, rel_path="entities/ana.md", db_path=db)
    assert not (vault / "entities" / "ana.md").exists()
    assert not (vault / "_archive" / "entities" / "ana.md").exists()
    assert _fts_count(db) == 0
    names = {s.store: s.done for s in report.stores}
    assert names["page file"] and names["search index"] and names["embedding index"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM wiki_semantic_pages").fetchone()[0] == 0
    conn.close()


def test_permanent_delete_reports_derived_pages_and_does_not_touch_them(vault, db):
    report = deletion.permanent_delete(vault, rel_path="entities/ana.md", db_path=db)
    found = {d["path"]: d["why"] for d in report.derived}
    assert "links to it" in found["entities/bob.md"]
    assert "repeats a line" in found["concepts/copy.md"]
    assert (vault / "entities" / "bob.md").is_file() and (vault / "concepts" / "copy.md").is_file()


def test_residual_always_names_backups_exports_and_remote(vault, db):
    report = deletion.permanent_delete(vault, rel_path="entities/ana.md", db_path=db)
    text = " ".join(report.residual).lower()
    assert "backup" in text and "export" in text and "remote engine" in text


def test_backup_facts_counts_archives_that_hold_the_page(vault, tmp_path):
    backups = tmp_path / "wiki-backups"
    backups.mkdir()
    for n, member in enumerate(["entities/ana.md", "entities/bob.md"]):
        with tarfile.open(backups / f"wiki-2026010100000{n}.tar.gz", "w:gz") as tar:
            tar.add(vault / member, arcname=member)
    facts = deletion.backup_facts(backups, "entities/ana.md")
    assert facts["archives"] == 2 and facts["archives_with_page"] == 1


def test_missing_database_is_reported_not_claimed(vault, tmp_path):
    report = deletion.permanent_delete(
        vault, rel_path="entities/ana.md", db_path=tmp_path / "none.db"
    )
    idx = [s for s in report.stores if s.store == "search index"][0]
    assert idx.done is False


@pytest.mark.parametrize(
    "bad", ["../x.md", "entities/../../x.md", ".trash/a/b.md", "_archive/a.md", "a.txt", ""]
)
def test_unsafe_paths_are_refused(vault, bad):
    with pytest.raises(deletion.DeletionError):
        deletion.logical_delete(vault, bad)


def test_cannot_delete_a_symlink_page(vault, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = vault / "entities" / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(deletion.DeletionError):
        deletion.permanent_delete(vault, rel_path="entities/link.md")
    assert outside.is_file()


def test_export_contains_pages_manifest_and_skips_archive_and_trash(vault):
    (vault / "_archive").mkdir()
    (vault / "_archive" / "old.md").write_text("old", encoding="utf-8")
    (vault / ".trash").mkdir()
    (vault / ".trash" / "x.md").write_text("trash", encoding="utf-8")
    data, manifest = build_export(vault, product="consumer")
    import io

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert "memory/entities/ana.md" in names and "manifest.json" in names
        assert not any("old.md" in n or "x.md" in n for n in names)
        assert json.loads(z.read("manifest.json"))["product"] == "consumer"
    assert manifest["files"] and manifest["changed_during_export"] == []


def test_export_refuses_the_other_products_vault(tmp_path):
    root = tmp_path / "data-work" / "vault"
    root.mkdir(parents=True)
    with pytest.raises(ExportError) as err:
        build_export(root, product="consumer")
    assert err.value.status == 403
