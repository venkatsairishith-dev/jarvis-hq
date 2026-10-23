from __future__ import annotations

import json

import pytest

from jarvis.plugins.learn_vault import core
from jarvis.plugins.learn_vault.core import VaultError


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    root = tmp_path / "learn-vault"
    monkeypatch.setenv("LEARN_VAULT_DIR", str(root))
    return root


def test_a_missing_vault_is_reported_not_created_by_reading(vault):
    s = core.status()
    assert s["exists"] is False and not vault.exists()
    assert core.list_notes() == [] and core.search_notes("anything") == []


def test_save_creates_adds_md_and_never_overwrites(vault):
    r = core.save_note("notes/Cell biology", "# Cell biology\n\nThe cell is the unit of life.")
    assert r["path"] == "notes/Cell biology.md" and (vault / "notes" / "Cell biology.md").is_file()
    with pytest.raises(VaultError):
        core.save_note("notes/Cell biology", "replacement")
    core.save_note("notes/Cell biology", "More: mitochondria make ATP.", mode="append")
    text = core.read_note("notes/Cell biology.md")["text"]
    assert "unit of life" in text and "mitochondria" in text
    with pytest.raises(VaultError):
        core.save_note("notes/never-created", "x", mode="append")


@pytest.mark.parametrize("bad", ["../escape.md", "a/../../b.md", "/abs.md", "C:/x.md", ".obsidian/app.md", "notes/.hidden/x.md",
                                 "", "   ", "_progress/progress.jsonl", "notes/bad|name.md"])
def test_unsafe_paths_are_refused_for_writing(vault, bad):
    with pytest.raises(VaultError):
        core.save_note(bad, "content")
    assert not (vault.parent / "escape.md").exists()


def test_reading_refuses_traversal_and_hidden_and_missing(vault):
    core.save_note("notes/a.md", "hello")
    for bad in ("../a.md", ".obsidian/workspace.md", "notes/none.md"):
        with pytest.raises(VaultError):
            core.read_note(bad)


def test_search_ranks_title_and_counts_and_gives_a_snippet(vault):
    core.save_note("notes/photosynthesis.md", "Plants use light.\nPhotosynthesis happens in chloroplasts.")
    core.save_note("notes/respiration.md", "Cells release energy. Photosynthesis is the opposite process.")
    core.save_note("notes/history.md", "The Roman empire fell.")
    hits = core.search_notes("photosynthesis chloroplasts")
    assert [h["path"] for h in hits][0] == "notes/photosynthesis.md"
    assert "history" not in " ".join(h["path"] for h in hits)
    assert "Photosynthesis" in hits[0]["snippet"]
    with pytest.raises(VaultError):
        core.search_notes("   ")


def test_hidden_folders_are_not_listed_or_searched(vault):
    core.save_note("notes/visible.md", "findme")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "private.md").write_text("findme", encoding="utf-8")
    assert [n["path"] for n in core.list_notes()] == ["notes/visible.md"]
    assert [h["path"] for h in core.search_notes("findme")] == ["notes/visible.md"]


def test_progress_keeps_the_latest_status_per_topic_and_writes_a_table(vault):
    core.record_progress("Cell biology", "learning", "started chapter 1")
    core.record_progress("Algebra", "review")
    core.record_progress("cell biology", "mastered", "quiz 9/10")
    latest = {e["topic"].lower(): e["status"] for e in core.progress()}
    assert latest == {"cell biology": "mastered", "algebra": "review"}
    table = (vault / "_progress" / "progress.md").read_text(encoding="utf-8")
    assert "| cell biology | mastered" in table.replace("Cell biology", "cell biology") or "mastered" in table
    assert core.status()["topics"] == 2
    with pytest.raises(VaultError):
        core.record_progress("x", "done-ish")
    with pytest.raises(VaultError):
        core.record_progress("", "learning")


def test_size_limit(vault):
    with pytest.raises(VaultError):
        core.save_note("notes/big.md", "x" * (core.MAX_NOTE_BYTES + 1))
