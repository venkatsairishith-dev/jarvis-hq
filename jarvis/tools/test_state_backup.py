"""D71: backup and restore of JARVIS's learned state, on temporary folders only. The rules under test: a closed list of what is copied, secrets and grants never
(even when listed), a manifest of sha256 sums that is checked before anything is restored, the current state saved before it is replaced, and no way to create a
grant or a permission by restoring."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from jarvis.tools import state_backup as SB  # noqa: E402

T0 = 1_800_000_000.0


def playbook(host="decks.test", name="make_deck", label="Generate", **extra):
    return {"name": name, "host": host, "version": 2, "source": "learned", "needs": ["download_to_quarantine"],
            "phases": [{"name": "create", "steps": [{"id": "go", "do": "click", "loc": {"role": "button", "name": label}}]}], **extra}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A data folder with learned state, a config folder, a secrets folder and a backups folder: all temporary."""
    data, cfg, back, secrets = tmp_path / "data", tmp_path / "config", tmp_path / "backups", tmp_path / "jarvis-secrets"
    for d in (data / "plugins" / "site_playbooks", data / "journal" / "shots", cfg, secrets / "sites", data / "kernel"):
        d.mkdir(parents=True)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data))
    monkeypatch.setenv("JARVIS_BACKUP_DIR", str(back))
    monkeypatch.setenv("JARVIS_SECRETS_DIR", str(secrets))
    for k in ("JARVIS_NAMES_FILE", "JARVIS_PLAYBOOKS_DIR", "JARVIS_LANGUAGE_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(SB, "_jarvis_config", lambda: cfg)
    (data / "names.json").write_text(json.dumps({"version": 1, "names": [{"name": "Priya", "kind": "family"}]}), encoding="utf-8")
    (data / "plugins" / "lessons.json").write_text(json.dumps({"v": 1, "entries": {"excel|dialog": {"routes": {"answer": [3, 0, "2026-10-26"]}, "seen": "2026-10-26"}}}), encoding="utf-8")
    (data / "plugins" / "site_playbooks" / "decks.test.make_deck.json").write_text(json.dumps(playbook()), encoding="utf-8")
    (data / "language.json").write_text(json.dumps({"reply_language": "te", "set": 1}), encoding="utf-8")
    (data / "plugins" / "ask_prefs.json").write_text(json.dumps({"folder": "Documents"}), encoding="utf-8")
    for day in ("2026-10-25", "2026-10-26"):
        (data / "journal" / f"journal-{day}.jsonl").write_text(json.dumps({"id": "s1", "ts": 1, "step_label": "Open Notepad"}) + "\n", encoding="utf-8")
    (data / "journal" / "shots" / "j1-b.jpg").write_bytes(b"\xff\xd8 picture")
    (data / "journal" / "shots" / "timing.jsonl").write_text("{}\n", encoding="utf-8")
    (cfg / "decider.json").write_text(json.dumps({"thresholds": {"intent": 0.7}, "models": {}, "use_model": True}), encoding="utf-8")
    (cfg / "specialists.json").write_text(json.dumps({"kinds": {"ui_decide": {"where": "local", "alias": "decider", "fallback": "decider-ling", "max_tokens": 400}}}), encoding="utf-8")
    # things that must never be copied
    (data / "trusted_sites.json").write_text(json.dumps({"sites": [{"host": "gamma.app", "allowed": ["signup"], "seal": "abc"}]}), encoding="utf-8")
    (data / "identity.json").write_text(json.dumps({"email_pattern": "me+{site}@example.com"}), encoding="utf-8")
    (data / "kernel" / "kernel.sqlite").write_bytes(b"SQLite format 3")
    (secrets / "sites" / "gamma.app.cred").write_text("never", encoding="utf-8")
    (secrets / "pin.txt").write_text("1234", encoding="utf-8")
    (data / "Documents.docx").write_bytes(b"the owner's document")
    return {"data": data, "cfg": cfg, "back": back, "secrets": secrets, "tmp": tmp_path}


def clock(start=T0):
    box = {"t": start}

    def now():
        box["t"] += 60
        return box["t"]
    return now


def backup(w, now=None, **kw):
    return SB.make_backup(w["back"], now=now or clock(), windows=False, free_check=False, **kw)


def tree(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


def rehash(folder: Path) -> None:
    """Rewrite the checksum file after a hand edit of manifest.json (what an attacker with write access to the backup folder could do)."""
    raw = (folder / SB.MANIFEST).read_bytes()
    (folder / SB.MANIFEST_SUM).write_text(SB.sha256_bytes(raw) + "  manifest.json\n", encoding="ascii")


# ------------------------------------------------------------------------------------------------------------------------------ backup
def test_a_backup_holds_the_learned_state_with_a_sha256_manifest(world):
    m = backup(world)
    folder = world["back"] / m["id"]
    assert SB.ID_RX.match(m["id"]) and folder.is_dir()
    assert tree(folder / SB.FILES) == {"names/names.json", "lessons/lessons.json", "playbooks/decks.test.make_deck.json", "decider/decider.json",
                                       "specialists/specialists.json", "language/language.json", "ask_prefs/ask_prefs.json",
                                       "journal/journal-2026-10-25.jsonl", "journal/journal-2026-10-26.jsonl"}
    for e in m["files"]:
        raw = (folder / SB.FILES / e["item"] / e["file"]).read_bytes()
        assert e["sha256"] == SB.sha256_bytes(raw) and e["bytes"] == len(raw)
    mm, problems = SB.verify_backup(folder)
    assert problems == [] and mm["reason"] == "backup" and mm["total_bytes"] == sum(e["bytes"] for e in m["files"])
    assert not [p for p in world["back"].iterdir() if p.name.startswith(".tmp")], "no temp folder is left"


def test_secrets_grants_pin_documents_and_pictures_are_never_in_a_backup(world):
    m = backup(world)
    folder = world["back"] / m["id"]
    mf = json.loads((folder / SB.MANIFEST).read_text(encoding="ascii"))
    mf.pop("never_included")                                   # the manifest names what it leaves out; everything else must not mention it
    everything = "\n".join(sorted(tree(folder))) + json.dumps(mf)
    for never in ("trusted_sites", "identity", "kernel", "sqlite", "gamma.app.cred", "pin.txt", "Documents.docx", ".jpg", "shots", "timing", "never", "1234",
                  "jarvis-secrets", "seal"):
        assert never not in everything, never
    blob = b"".join(p.read_bytes() for p in (folder / SB.FILES).rglob("*") if p.is_file())
    assert b"picture" not in blob and b"SQLite" not in blob and b"the owner's document" not in blob and b"abc" not in (folder / SB.FILES / "names" / "names.json").read_bytes()


def test_a_file_that_holds_a_key_shaped_string_is_skipped_and_never_shown(world):
    leaked = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
    (world["data"] / "plugins" / "site_playbooks" / "pitch.com.make_deck.json").write_text(json.dumps(playbook("pitch.com", note=leaked)), encoding="utf-8")
    m = backup(world)
    assert "playbooks/pitch.com.make_deck.json" not in {f"{e['item']}/{e['file']}" for e in m["files"]}
    why = [s for s in m["skipped"] if s["file"] == "pitch.com.make_deck.json"]
    assert why and "key-shaped" in why[0]["reason"] and leaked not in json.dumps(m)
    assert leaked not in (world["back"] / m["id"] / SB.MANIFEST).read_text(encoding="ascii")


def test_secrets_and_grant_paths_are_rejected_even_when_a_table_lists_them(world):
    """Somebody adds the secrets folder, the grants file or a PIN file to what is backed up: every one is refused by name, folder or root."""
    import re
    secrets, data = world["secrets"], world["data"]
    table = [SB.Item("names", "x", lambda: secrets, re.compile(r".*"), lambda raw, rel: ""),                     # the secrets folder itself
             SB.Item("lessons", "x", lambda: secrets / "sites", re.compile(r".*"), lambda raw, rel: ""),
             SB.Item("decider", "x", lambda: data, re.compile(r"(trusted_sites|identity)\.json"), lambda raw, rel: ""),       # grants and identity
             SB.Item("journal", "x", lambda: data / "kernel", re.compile(r".*"), lambda raw, rel: ""),
             SB.Item("language", "x", lambda: data / "journal" / "shots", re.compile(r".*"), lambda raw, rel: "")]           # pictures
    with pytest.raises(SB.BackupError) as e:
        backup(world, table=table)
    assert "nothing to back up" in str(e.value) and "refused or skipped" in str(e.value)
    # and the same table beside one honest item: the honest one is saved, the rest are listed as refused
    table.append(SB.items()[0])
    m = backup(world, table=table)
    assert [f["item"] for f in m["files"]] == ["names"] and (m["files"][0]["file"] == "names.json")
    refused = {s["file"] for s in m["skipped"] if s["reason"].startswith("refused")}
    assert {"gamma.app.cred", "pin.txt", "trusted_sites.json", "identity.json", "kernel.sqlite", "j1-b.jpg"} <= refused


def test_a_symlink_is_not_followed(world):
    link = world["data"] / "plugins" / "site_playbooks" / "evil.test.make_deck.json"
    try:
        os.symlink(world["secrets"] / "pin.txt", link)
    except OSError:
        pytest.skip("no symlinks here")
    m = backup(world)
    assert "evil.test.make_deck.json" not in {e["file"] for e in m["files"]}
    assert [s for s in m["skipped"] if s["file"] == "evil.test.make_deck.json"]


def test_a_damaged_or_foreign_json_file_is_not_saved_as_if_it_were_good(world):
    (world["data"] / "names.json").write_text("{not json", encoding="utf-8")
    (world["data"] / "plugins" / "site_playbooks" / "grant.test.make_deck.json").write_text(json.dumps(playbook("grant.test", allowed=["signup"], seal="x")), encoding="utf-8")
    m = backup(world)
    files = {f"{e['item']}/{e['file']}" for e in m["files"]}
    assert "names/names.json" not in files and "playbooks/grant.test.make_deck.json" not in files
    reasons = " ".join(s["reason"] for s in m["skipped"])
    assert "not valid" in reasons and "a grant, a seal or a permission is not part of a playbook" in reasons


def test_the_journal_index_keeps_the_newest_thirty_and_no_pictures(world):
    for d in range(1, 41):
        (world["data"] / "journal" / f"journal-2026-09-{d:02d}.jsonl" if d < 31 else world["data"] / "journal" / f"journal-2026-10-{d - 30:02d}.jsonl").write_text('{"id": 1}\n', encoding="utf-8")
    m = backup(world)
    j = sorted(e["file"] for e in m["files"] if e["item"] == "journal")
    assert len(j) == SB.JOURNAL_FILES and j[-1] == "journal-2026-10-26.jsonl"


def test_the_size_cap_drops_old_journal_days_first_and_refuses_when_the_rest_is_too_big(world):
    (world["data"] / "journal" / "journal-2026-10-01.jsonl").write_text(json.dumps({"x": "y" * 600_000}) + "\n", encoding="utf-8")
    (world["data"] / "journal" / "journal-2026-10-26.jsonl").write_text(json.dumps({"x": "y" * 600_000}) + "\n", encoding="utf-8")
    m = backup(world, max_mb=1)
    assert [e["file"] for e in m["files"] if e["item"] == "journal" and e["bytes"] > 100_000] == ["journal-2026-10-26.jsonl"], "the newest day stays"
    assert [s for s in m["skipped"] if "size cap" in s["reason"]]
    for i in range(10):
        (world["data"] / "plugins" / "site_playbooks" / f"big{i}.test.make_deck.json").write_text(json.dumps(playbook(f"big{i}.test", notes={"a": "z" * 150_000})), encoding="utf-8")
    with pytest.raises(SB.BackupError, match="over the"):
        backup(world, max_mb=1)


def test_the_backup_folder_is_on_d_only_on_windows():
    with pytest.raises(SB.BackupError, match="D: only"):
        SB.backups_root({"JARVIS_BACKUP_DIR": r"C:\Users\me\backups"}, windows=True)
    assert str(SB.backups_root({"JARVIS_BACKUP_DIR": r"D:\jarvis\backups"}, windows=True)).lower().startswith("d:")
    with pytest.raises(SB.BackupError, match="D: only"):
        SB.make_backup(Path(r"C:\x"), windows=True)


def test_a_backup_that_stops_halfway_leaves_nothing_behind(world, monkeypatch):
    calls = {"n": 0}
    real = SB._atomic_write

    def flaky(path, raw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk went away")
        real(path, raw)
    monkeypatch.setattr(SB, "_atomic_write", flaky)
    with pytest.raises(OSError):
        backup(world)
    assert [p.name for p in world["back"].iterdir()] == [], "neither a half backup nor a temp folder"


def test_only_the_last_n_are_kept_and_foreign_folders_are_left_alone(world):
    (world["back"]).mkdir(exist_ok=True)
    (world["back"] / "my-own-notes").mkdir()
    now = clock()
    ids = [backup(world, now=now, keep=3)["id"] for _ in range(6)]
    kept = [b["id"] for b in SB.list_backups(world["back"])]
    assert kept == sorted(ids, reverse=True)[:3]
    assert (world["back"] / "my-own-notes").is_dir()
    assert len(SB.prune(world["back"], keep=1)) == 0, "never below three"


# ------------------------------------------------------------------------------------------------------------------------------ verify
def test_verify_finds_a_changed_a_missing_and_an_extra_file(world):
    folder = world["back"] / backup(world)["id"]
    assert SB.verify_backup(folder)[1] == []
    (folder / SB.FILES / "lessons" / "lessons.json").write_text("{}", encoding="utf-8")
    (folder / SB.FILES / "names" / "names.json").unlink()
    (folder / SB.FILES / "names" / "extra.json").write_text("{}", encoding="utf-8")
    problems = " | ".join(SB.verify_backup(folder)[1])
    assert "lessons/lessons.json: changed since the backup was made" in problems and "names/names.json: missing" in problems and "names/extra.json: in the folder but not in the manifest" in problems


def test_verify_notices_an_edited_manifest(world):
    folder = world["back"] / backup(world)["id"]
    mf = json.loads((folder / SB.MANIFEST).read_text(encoding="ascii"))
    mf["reason"] = "edited"
    (folder / SB.MANIFEST).write_text(json.dumps(mf), encoding="ascii")
    assert any("checksum file" in p for p in SB.verify_backup(folder)[1])


def test_a_manifest_that_lists_a_secret_or_a_grant_does_not_verify_even_with_the_right_hash(world):
    folder = world["back"] / backup(world)["id"]
    bad = []
    for item, rel, content in (("decider", "trusted_sites.json", b'{"sites": []}'), ("names", "../../escape.json", b"{}"), ("lessons", "pin.txt", b"1234"),
                               ("playbooks", "pitch.com.secret.json", b"{}"), ("playbooks", "pitch.com.make_deck.key", b"x"), ("journal", "identity.json", b"{}"),
                               ("kernel", "kernel.sqlite", b"SQLite"), ("names", "sub/names.json", b"{}")):
        dest = folder / SB.FILES / item / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        except OSError:
            pass
        bad.append({"item": item, "file": rel, "sha256": SB.sha256_bytes(content), "bytes": len(content)})
    mf = json.loads((folder / SB.MANIFEST).read_text(encoding="ascii"))
    mf["files"] += bad
    (folder / SB.MANIFEST).write_text(json.dumps(mf), encoding="ascii")
    rehash(folder)
    problems = SB.verify_backup(folder)[1]
    for entry in bad:
        assert any(f"{entry['item']}/{entry['file']}" in p and ("not an allowed" in p or "not a kind" in p or "refused" in p) for p in problems), entry
    with pytest.raises(SB.BackupError, match="does not verify"):
        SB.restore(world["back"], folder.name, apply=True, windows=False, free_check=False)
    assert not (world["data"].parent / "escape.json").exists()


# ------------------------------------------------------------------------------------------------------------------------------ restore
def snapshot(w):
    return {p.relative_to(w["tmp"]).as_posix(): p.read_bytes() for p in (w["data"], w["cfg"]) for p in p.rglob("*") if p.is_file()}


def test_without_apply_a_restore_only_shows_the_plan(world):
    m = backup(world)
    (world["data"] / "names.json").write_text(json.dumps({"names": [{"name": "Other"}]}), encoding="utf-8")
    before = snapshot(world)
    res = SB.restore(world["back"], m["id"], windows=False, free_check=False)
    assert res["applied"] is False and {s["file"]: s["action"] for s in res["steps"]}["names.json"] == "replace"
    assert {s["action"] for s in res["steps"] if s["file"] != "names.json"} == {"same"}
    assert snapshot(world) == before and [b["id"] for b in SB.list_backups(world["back"])] == [m["id"]]


def test_restore_saves_the_current_state_first_then_replaces_it(world):
    now = clock()
    m = backup(world, now=now)
    mine = json.dumps({"names": [{"name": "Other"}]})
    (world["data"] / "names.json").write_text(mine, encoding="utf-8")
    (world["data"] / "plugins" / "lessons.json").unlink()
    res = SB.restore(world["back"], m["id"], apply=True, now=now, windows=False, free_check=False)
    assert res["applied"] and res["safety_backup"]
    assert json.loads((world["data"] / "names.json").read_text(encoding="utf-8"))["names"][0]["name"] == "Priya", "restored"
    assert (world["data"] / "plugins" / "lessons.json").exists(), "a missing file is created again"
    safety = world["back"] / res["safety_backup"]
    assert (safety / SB.FILES / "names" / "names.json").read_text(encoding="utf-8") == mine, "what was there before is in the safety backup"
    assert SB.verify_backup(safety)[1] == [] and json.loads((safety / SB.MANIFEST).read_text(encoding="ascii"))["reason"].startswith("pre-restore")
    assert {b["id"] for b in SB.list_backups(world["back"])} == {m["id"], res["safety_backup"]}


def test_a_restore_with_a_bad_hash_is_refused_and_changes_nothing(world):
    now = clock()
    m = backup(world, now=now)
    (world["data"] / "names.json").write_text(json.dumps({"names": [{"name": "Other"}]}), encoding="utf-8")
    folder = world["back"] / m["id"]
    (folder / SB.FILES / "lessons" / "lessons.json").write_text(json.dumps({"entries": {}}), encoding="utf-8")        # tampered after the backup
    before = snapshot(world)
    with pytest.raises(SB.BackupError, match="does not verify.*lessons/lessons.json: changed since"):
        SB.restore(world["back"], m["id"], apply=True, now=now, windows=False, free_check=False)
    assert snapshot(world) == before, "not one file was touched, not even the ones whose hash was fine"
    assert [b["id"] for b in SB.list_backups(world["back"])] == [m["id"]], "and no safety backup was made for a restore that never started"


def test_a_restore_cannot_create_a_grant_or_a_permission(world):
    """A backup edited to carry a grant, a seal or a permission in a playbook, or to name the grants file, is refused before the first write."""
    now = clock()
    m = backup(world, now=now)
    folder = world["back"] / m["id"]
    evil = json.dumps(playbook(allowed=["signup", "download_to_quarantine"], seal="forged", granted_by_pin=True)).encode()
    (folder / SB.FILES / "playbooks" / "decks.test.make_deck.json").write_bytes(evil)
    mf = json.loads((folder / SB.MANIFEST).read_text(encoding="ascii"))
    for e in mf["files"]:
        if e["file"] == "decks.test.make_deck.json":
            e["sha256"], e["bytes"] = SB.sha256_bytes(evil), len(evil)
    mf["total_bytes"] = sum(e["bytes"] for e in mf["files"])
    (folder / SB.MANIFEST).write_text(json.dumps(mf), encoding="ascii")
    rehash(folder)
    (world["data"] / "names.json").write_text(json.dumps({"names": [{"name": "Other"}]}), encoding="utf-8")
    before = snapshot(world)
    with pytest.raises(SB.BackupError, match="not part of a playbook"):
        SB.restore(world["back"], m["id"], apply=True, now=now, windows=False, free_check=False)
    after = snapshot(world)
    assert after == {k: v for k, v in before.items()}, "the names file was not restored either: validation happens before the first write"
    assert not any(k.endswith(("safe_mode.json", "auto_click.json")) for k in after), "no permission file appeared"
    assert json.loads((world["data"] / "trusted_sites.json").read_text(encoding="utf-8"))["sites"][0]["host"] == "gamma.app", "the real grants file was never touched"


def test_restore_writes_only_where_this_tool_says_never_where_the_backup_says(world):
    """Even with a hand-made manifest, a restore computes every target itself: the folder of the item, the name of the item."""
    now = clock()
    m = backup(world, now=now)
    steps = SB.restore(world["back"], m["id"], windows=False, free_check=False)["steps"]
    allowed_dirs = {str(world["data"]), str(world["data"] / "plugins"), str(world["data"] / "plugins" / "site_playbooks"), str(world["cfg"]), str(world["data"] / "journal")}
    assert {str(Path(s["target"]).parent) for s in steps} <= allowed_dirs
    assert not any("secret" in s["target"] or "kernel" in s["target"] or "trusted" in s["target"] for s in steps)


def test_only_restores_the_named_parts(world):
    now = clock()
    m = backup(world, now=now)
    (world["data"] / "names.json").write_text(json.dumps({"names": [{"name": "Other"}]}), encoding="utf-8")
    (world["data"] / "language.json").write_text(json.dumps({"reply_language": "hi", "set": 2}), encoding="utf-8")
    res = SB.restore(world["back"], m["id"], only=["language"], apply=True, now=now, windows=False, free_check=False)
    assert [s["item"] for s in res["steps"]] == ["language"]
    assert json.loads((world["data"] / "language.json").read_text(encoding="utf-8"))["reply_language"] == "te"
    assert json.loads((world["data"] / "names.json").read_text(encoding="utf-8"))["names"][0]["name"] == "Other"
    with pytest.raises(SB.BackupError, match="unknown part"):
        SB.restore(world["back"], m["id"], only=["trusted_sites"], windows=False, free_check=False)


def test_a_restore_stops_when_the_current_state_cannot_be_saved_first(world, monkeypatch):
    now = clock()
    m = backup(world, now=now)
    (world["data"] / "names.json").write_text(json.dumps({"names": [{"name": "Other"}]}), encoding="utf-8")
    # the file about to be replaced holds a key-shaped string: it cannot be copied, so it must not be replaced
    (world["data"] / "names.json").write_text(json.dumps({"names": [], "note": "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"}), encoding="utf-8")
    before = snapshot(world)
    with pytest.raises(SB.BackupError, match="could not be backed up first"):
        SB.restore(world["back"], m["id"], apply=True, now=now, windows=False, free_check=False)
    assert snapshot(world) == before
    assert [b["id"] for b in SB.list_backups(world["back"])] == [m["id"]], "no half-made safety backup is left"


def test_the_restore_source_is_never_pruned_by_its_own_safety_backup(world):
    now = clock()
    ids = [backup(world, now=now, keep=3)["id"] for _ in range(3)]
    (world["data"] / "names.json").write_text(json.dumps({"names": [{"name": "Other"}]}), encoding="utf-8")
    res = SB.restore(world["back"], ids[0], apply=True, now=now, keep=3, windows=False, free_check=False)
    assert res["applied"] and ids[0] in {b["id"] for b in SB.list_backups(world["back"])}


def test_no_backups_is_a_clear_refusal_and_a_folder_without_a_manifest_is_not_a_backup(world):
    with pytest.raises(SB.BackupError, match="no backups"):
        SB.restore(world["back"], "latest", windows=False)
    (world["back"] / "20261027-010101").mkdir(parents=True)
    assert SB.list_backups(world["back"]) == []


# ------------------------------------------------------------------------------------------------------------------------------ command line
def test_command_line(world):
    lines = []
    out = lines.append
    assert SB.main(["list"], out=out) == 0 and "no backups" in lines[-1]
    # the command line uses the real clock and the real free-space check, which a temp folder passes
    assert SB.main(["backup", "--keep", "5"], out=out) == 0 and lines[-1].startswith("backup 20") and "9 file(s)" in lines[-1]
    assert SB.main(["list"], out=out) == 0 and "9 file(s)" in lines[-1]
    assert SB.main(["verify"], out=out) == 0 and "every file matches" in lines[-1]
    (world["data"] / "names.json").write_text(json.dumps({"names": [{"name": "Other"}]}), encoding="utf-8")
    assert SB.main(["restore"], out=out) == 0 and "this was the plan" in lines[-1]
    assert json.loads((world["data"] / "names.json").read_text(encoding="utf-8"))["names"][0]["name"] == "Other"
    assert SB.main(["restore", "--apply", "--only", "names"], out=out) == 0 and "restored from" in lines[-1]
    assert json.loads((world["data"] / "names.json").read_text(encoding="utf-8"))["names"][0]["name"] == "Priya"
    assert SB.main(["verify", "20000101-000000"], out=out) == 1 and lines[-1].startswith("refused")
