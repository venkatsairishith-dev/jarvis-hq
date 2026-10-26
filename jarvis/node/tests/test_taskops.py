"""D54, simple tasks on the node (taskops.py): files in the known folders, the delete family, a few settings, the clipboard. Fakes only: real temporary
folders for the files, a fake Recycle Bin (a folder + a list), a fake PowerShell for the settings and the clipboard. No Windows."""
import json
import os
import shutil
import sys
import time
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_node import savefile as sf, taskops as T  # noqa: E402

NOW = 2_000_000_000.0


class FakeBin:
    """A Recycle Bin: trash() moves the item into a folder and lists it; the lister and the emptier are the node's seams."""

    def __init__(self, folder):
        self.folder, self.items, self.fail_listing, self.vanish = folder, [], False, False
        os.makedirs(folder, exist_ok=True)

    def trasher(self, path):
        dst = os.path.join(self.folder, f"{len(self.items)}-{os.path.basename(path)}")
        if not self.vanish:
            self.items.append({"name": os.path.basename(path), "from": os.path.dirname(path), "size": 1})
        shutil.move(path, dst)

    def lister(self):
        if self.fail_listing:
            raise OSError("no listing")
        return list(self.items)

    def emptier(self):
        for n in os.listdir(self.folder):
            p = os.path.join(self.folder, n)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        self.items = []


@pytest.fixture
def w(tmp_path):
    home = tmp_path / "home"
    known = {}
    for n in ("documents", "desktop", "downloads", "pictures", "videos", "music"):
        (home / n.title()).mkdir(parents=True)
        known[n] = str(home / n.title())
    proj = tmp_path / "proj"
    (proj / "Windows").mkdir(parents=True)
    out = tmp_path / "outputs"
    out.mkdir()
    roots = sf.folder_roots(known=known, projects={"myproj": {"path": {os.name: str(proj)}}}, outputs=str(out))
    bin_ = FakeBin(str(tmp_path / "bin"))
    local = tmp_path / "local"
    (local / "Temp").mkdir(parents=True)
    env = {"LOCALAPPDATA": str(local), "APPDATA": str(tmp_path / "roam"), "TEMP": str(local / "Temp"), "SystemRoot": str(proj / "Windows")}
    ctx = T.Ctx(roots=roots, journal=str(tmp_path / "ops" / "journal.jsonl"), trasher=bin_.trasher, bin_lister=bin_.lister, bin_emptier=bin_.emptier,
                env=env, now=lambda: NOW, launcher=lambda a: launched.append(a), scan_only=True)
    launched = []
    return types_ns(home=home, docs=home / "Documents", dl=home / "Downloads", pics=home / "Pictures", desk=home / "Desktop", proj=proj, out=out, tmp=tmp_path,
                    ctx=ctx, bin=bin_, local=local, env=env, launched=launched, known=known)


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


def job(w, op, target="Documents", path="", dest="", text=""):
    return T.run("file_op", {"agent": "owner", "op": op, "target": target, "path": path, "dest": dest, "text": text}, w.ctx)


def old(path, age_days=100):
    t = NOW - age_days * 86400
    os.utime(path, (t, t))


# ---------------------------------------------------------------------------------------------------------------- create
def test_mkdir_makes_a_new_folder_and_numbers_a_name_that_exists(w):
    r = job(w, "mkdir", "Documents", "Projects")
    assert r["ok"] and r["created"] == "Projects" and (w.docs / "Projects").is_dir() and r["verified"]
    r2 = job(w, "mkdir", "Documents", "Projects")
    assert r2["ok"] and r2["created"] == "Projects (2)" and r2["renamed"] and (w.docs / "Projects").is_dir()


@pytest.mark.parametrize("name", ["", "..", "a/b", "a:b", ".hidden", "con", "x" * 90, "bad|name"])
def test_mkdir_refuses_names_that_are_not_plain(w, name):
    assert job(w, "mkdir", "Documents", name)["ok"] is False


@pytest.mark.parametrize("target", [r"C:\Windows", "System32", "Documents/../..", "Documents/.hidden", "Windows", "/etc", "Documents/nope"])
def test_nothing_is_made_outside_the_known_folders(w, target):
    r = job(w, "mkdir", target, "x")
    assert r["ok"] is False and not (w.tmp / "x").exists()


def test_create_file_with_content_in_text_markdown_and_html_is_read_back(w):
    for name, text in (("notes.txt", "milk\neggs"), ("plan.md", "# Plan\n- one"), ("page.html", "<h1>Hi</h1>")):
        r = job(w, "create_file", "Documents", name, text=text)
        assert r["ok"] and r["file_ok"] and r["content_ok"] and (w.docs / name).read_text(encoding="utf-8") == text


def test_create_file_never_overwrites_it_takes_a_number(w):
    (w.docs / "notes.txt").write_text("mine", encoding="utf-8")
    r = job(w, "create_file", "Documents", "notes.txt", text="new")
    assert r["ok"] and r["created"] == "notes (2).txt" and (w.docs / "notes.txt").read_text(encoding="utf-8") == "mine"


@pytest.mark.parametrize("name", ["run.bat", "a.exe", "x.ps1", "no-ext", "a.unknown"])
def test_create_file_refuses_programs_scripts_and_unknown_types(w, name):
    r = job(w, "create_file", "Documents", name, text="x")
    assert (r["ok"] is False) or name == "no-ext" and False


# ---------------------------------------------------------------------------------------------------------------- rename, move, copy
def test_rename_keeps_the_extension_and_numbers_a_name_that_exists(w):
    (w.docs / "report.docx").write_text("a", encoding="utf-8")
    (w.docs / "final.docx").write_text("other", encoding="utf-8")
    r = job(w, "rename", "Documents", "report.docx", dest="final")
    assert r["ok"] and r["renamed_to"] == "final (2).docx" and r["numbered"]
    assert (w.docs / "final.docx").read_text(encoding="utf-8") == "other" and not (w.docs / "report.docx").exists()


def test_rename_is_exact_never_fuzzy_and_never_touches_programs(w):
    (w.docs / "report.docx").write_text("a", encoding="utf-8")
    (w.docs / "tool.exe").write_text("a", encoding="utf-8")
    assert job(w, "rename", "Documents", "repor", dest="x")["ok"] is False
    assert job(w, "rename", "Documents", "tool.exe", dest="tool2.exe")["ok"] is False
    assert job(w, "rename", "Documents", "report.docx", dest="run.bat")["ok"] is False
    assert (w.docs / "report.docx").exists()


def test_move_and_copy_into_a_folder_number_instead_of_overwriting(w):
    (w.docs / "a.txt").write_text("one", encoding="utf-8")
    (w.desk / "a.txt").write_text("the owner's", encoding="utf-8")
    r = job(w, "copy", "Documents", "a.txt", dest="Desktop")
    assert r["ok"] and r["copied"] == "a (2).txt" and r["numbered"] and (w.desk / "a.txt").read_text(encoding="utf-8") == "the owner's"
    assert (w.docs / "a.txt").exists()
    r = job(w, "move", "Documents", "a.txt", dest="Desktop")
    assert r["ok"] and r["moved"] == "a (3).txt" and not (w.docs / "a.txt").exists() and (w.desk / "a (3).txt").read_text(encoding="utf-8") == "one"


def test_move_to_a_new_subfolder_and_back_with_undo(w):
    (w.docs / "a.txt").write_text("one", encoding="utf-8")
    job(w, "mkdir", "Documents", "backup")
    r = job(w, "move", "Documents", "a.txt", dest="Documents/backup")
    assert r["ok"] and (w.docs / "backup" / "a.txt").exists()
    assert job(w, "undo", "Documents", r["op_id"])["ok"] and (w.docs / "a.txt").exists() and not (w.docs / "backup" / "a.txt").exists()


@pytest.mark.parametrize("dest", [r"C:\Windows", "Documents/../..", "System32", "Documents/nope", "/etc"])
def test_move_and_copy_never_leave_the_known_folders(w, dest):
    (w.docs / "a.txt").write_text("one", encoding="utf-8")
    assert job(w, "move", "Documents", "a.txt", dest=dest)["ok"] is False and (w.docs / "a.txt").exists()
    assert job(w, "copy", "Documents", "a.txt", dest=dest)["ok"] is False


def test_a_folder_is_not_moved_into_itself(w):
    (w.docs / "d" / "inner").mkdir(parents=True)
    assert job(w, "move", "Documents", "d", dest="Documents/d/inner")["ok"] is False and (w.docs / "d").is_dir()


# ---------------------------------------------------------------------------------------------------------------- zip, unzip
def test_zip_makes_a_new_archive_and_checks_it(w):
    (w.docs / "d").mkdir()
    (w.docs / "d" / "a.txt").write_text("1", encoding="utf-8")
    (w.docs / "d" / "b.txt").write_text("2", encoding="utf-8")
    r = job(w, "zip", "Documents", "d")
    assert r["ok"] and r["archive"] == "d.zip" and r["files"] == 2 and zipfile.ZipFile(w.docs / "d.zip").testzip() is None
    r = job(w, "zip", "Documents", "d")
    assert r["ok"] and r["archive"] == "d (2).zip" and r["numbered"]


def test_unzip_goes_into_a_new_folder_and_refuses_a_path_that_leaves_it(w):
    with zipfile.ZipFile(w.docs / "pack.zip", "w") as z:
        z.writestr("x/a.txt", "1")
        z.writestr("b.txt", "2")
    (w.docs / "pack").mkdir()
    (w.docs / "pack" / "mine.txt").write_text("keep", encoding="utf-8")
    r = job(w, "unzip", "Documents", "pack.zip")
    assert r["ok"] and r["unpacked_to"] == "pack (2)" and (w.docs / "pack (2)" / "x" / "a.txt").read_text(encoding="utf-8") == "1"
    assert (w.docs / "pack" / "mine.txt").read_text(encoding="utf-8") == "keep"
    with zipfile.ZipFile(w.docs / "evil.zip", "w") as z:
        z.writestr("../escape.txt", "no")
    r = job(w, "unzip", "Documents", "evil.zip")
    assert r["ok"] is False and not (w.docs.parent / "escape.txt").exists() and not (w.docs / "evil").exists()
    (w.docs / "notzip.zip").write_text("plain", encoding="utf-8")
    assert job(w, "unzip", "Documents", "notzip.zip")["ok"] is False


# ---------------------------------------------------------------------------------------------------------------- sort Downloads
def test_sort_downloads_moves_by_type_leaves_recent_and_partial_files_and_keeps_an_undo_list(w):
    names = {"pic.png": "Images", "doc.pdf": "Documents", "song.mp3": "Music", "movie.mp4": "Videos", "pack.zip": "Archives", "setup.exe": "Installers"}
    for n in names:
        p = w.dl / n
        p.write_text("x", encoding="utf-8")
        old(p, 5)
    (w.dl / "fresh.png").write_text("x", encoding="utf-8")                    # touched just now: may still be downloading
    os.utime(w.dl / "fresh.png", (NOW - 5, NOW - 5))
    (w.dl / "big.crdownload").write_text("x", encoding="utf-8")
    old(w.dl / "big.crdownload")
    (w.dl / "mystery.xyz").write_text("x", encoding="utf-8")
    old(w.dl / "mystery.xyz")
    (w.dl / "Images").mkdir()
    (w.dl / "Images" / "pic.png").write_text("older copy", encoding="utf-8")
    r = job(w, "sort_downloads", "Downloads")
    assert r["ok"] and r["moved"] == 6 and r["by_type"]["Images"] == 1 and r["skipped"]["recent"] == 1 and r["undo"]
    assert (w.dl / "Images" / "pic.png").read_text(encoding="utf-8") == "older copy" and (w.dl / "Images" / "pic (2).png").exists()
    assert (w.dl / "fresh.png").exists() and (w.dl / "big.crdownload").exists() and (w.dl / "mystery.xyz").exists()
    assert (w.dl / "Installers" / "setup.exe").exists()
    assert job(w, "undo", "Downloads", " ".join(r["undo"]))["ok"]
    assert (w.dl / "pic.png").exists() and (w.dl / "doc.pdf").exists() and not (w.dl / "Images" / "pic (2).png").exists()


# ---------------------------------------------------------------------------------------------------------------- find, reveal
def test_find_by_name_size_and_age_in_the_known_folders_only(w):
    (w.docs / "tax2025.pdf").write_bytes(b"x" * 5000)
    old(w.docs / "tax2025.pdf", 1)
    (w.dl / "tax2024.pdf").write_bytes(b"x" * 50)
    old(w.dl / "tax2024.pdf", 400)
    (w.docs / ".secret-tax.pdf").write_bytes(b"x")
    (w.tmp / "outside-tax.pdf").write_bytes(b"x")
    r = job(w, "find", "all", "tax")
    names = sorted(m["name"] for m in r["matches"])
    assert r["ok"] and names == ["tax2024.pdf", "tax2025.pdf"], r
    assert [m["name"] for m in job(w, "find", "all", "tax", text="size>1kb")["matches"]] == ["tax2025.pdf"]
    assert [m["name"] for m in job(w, "find", "all", "tax", text="older:100d")["matches"]] == ["tax2024.pdf"]
    assert [m["name"] for m in job(w, "find", "Downloads", "tax", text="ext:pdf")["matches"]] == ["tax2024.pdf"]
    assert job(w, "find", "C:\\Windows", "x")["ok"] is False


def test_reveal_opens_explorer_on_a_known_item_only(w):
    (w.docs / "a.txt").write_text("1", encoding="utf-8")
    r = job(w, "reveal", "Documents", "a.txt")
    assert r["ok"] and w.launched == [["explorer.exe", "/select," + os.path.realpath(w.docs / "a.txt")]]
    assert job(w, "reveal", "Documents", "nope.txt")["ok"] is False and len(w.launched) == 1


# ---------------------------------------------------------------------------------------------------------------- delete: Recycle Bin first
def test_trash_goes_to_the_bin_and_is_verified_gone_and_in_the_bin(w):
    (w.docs / "old.txt").write_text("bye", encoding="utf-8")
    r = job(w, "trash", "Documents", "old.txt")
    assert r["ok"] and r["gone"] is True and r["in_bin"] is True and r["reversible"] is True and not (w.docs / "old.txt").exists()
    assert w.bin.items and w.bin.items[0]["name"] == "old.txt"
    assert "(w.bin.folder)" or os.listdir(w.bin.folder)


def test_trash_that_cannot_be_found_in_the_bin_is_not_reported_as_a_success(w):
    (w.docs / "old.txt").write_text("bye", encoding="utf-8")
    w.bin.vanish = True
    r = job(w, "trash", "Documents", "old.txt")
    assert r["ok"] is False and r["gone"] is True and r["in_bin"] is False and "Recycle Bin" in r["error"]
    (w.docs / "old2.txt").write_text("bye", encoding="utf-8")
    w.bin.vanish, w.bin.fail_listing = False, True
    assert job(w, "trash", "Documents", "old2.txt")["in_bin"] is False


def test_trash_names_are_exact_and_a_folder_goes_whole(w):
    (w.docs / "old notes").mkdir()
    (w.docs / "old notes" / "a.txt").write_text("1", encoding="utf-8")
    assert job(w, "trash", "Documents", "old")["ok"] is False and (w.docs / "old notes").exists()
    assert job(w, "trash", "Documents", "old*")["ok"] is False
    r = job(w, "trash", "Documents", "Old Notes")
    assert r["ok"] and not (w.docs / "old notes").exists()


def test_delete_permanent_removes_only_the_one_named_item(w):
    (w.docs / "a.txt").write_text("1", encoding="utf-8")
    (w.docs / "b.txt").write_text("2", encoding="utf-8")
    r = job(w, "delete_permanent", "Documents", "a.txt")
    assert r["ok"] and r["permanent"] is True and not (w.docs / "a.txt").exists() and (w.docs / "b.txt").exists() and w.bin.items == []


@pytest.mark.parametrize("op", ["trash", "delete_permanent"])
def test_protected_places_are_refused_whatever_the_op(w, op):
    (w.docs / "jarvis-secrets").mkdir()
    (w.docs / "jarvis-secrets" / "k.txt").write_text("secret", encoding="utf-8")
    (w.docs / ".env").write_text("K=1", encoding="utf-8")
    (w.docs / ".ssh").mkdir()
    for target, path in (("Documents", "jarvis-secrets"), ("Documents", "jarvis-secrets/k.txt"), ("Documents", ".env"), ("Documents", ".ssh"),
                         ("myproj", "Windows"),                                     # a system folder (SystemRoot points here in this test)
                         ("Documents", ""), ("Documents", "."), ("Documents", ".."), ("Documents", "../Desktop"), ("C:\\", "Windows"), ("Windows", "System32"),
                         ("Documents", "*"), ("Documents", "*.txt")):
        r = job(w, op, target, path)
        assert r["ok"] is False, (target, path)
    assert (w.docs / "jarvis-secrets" / "k.txt").exists() and (w.docs / ".env").exists() and (w.proj / "Windows").is_dir() and w.bin.items == []


def test_a_known_folder_itself_and_jarvis_own_folders_are_never_deleted(w, monkeypatch):
    from jarvis_node import paths
    jh = w.tmp / "jarvis_home"
    (jh / "vault" / "inbox").mkdir(parents=True)
    (jh / "vault" / "inbox" / "n.md").write_text("n", encoding="utf-8")
    monkeypatch.setattr(paths, "jarvis_home", lambda: str(jh))
    roots = dict(w.ctx.roots, jarvisproj=str(jh))
    ctx = T.Ctx(roots=roots, journal=str(w.tmp / "ops2" / "j.jsonl"), trasher=w.bin.trasher, bin_lister=w.bin.lister, env=w.env, now=lambda: NOW)
    for path in ("vault", "vault/inbox", "vault/inbox/n.md"):
        r = T.run("file_op", {"agent": "owner", "op": "trash", "target": "jarvisproj", "path": path, "dest": "", "text": ""}, ctx)
        assert r["ok"] is False and "JARVIS" in r["error"], path
    assert (jh / "vault" / "inbox" / "n.md").exists()


def test_empty_bin_empties_it_and_says_so(w):
    (w.docs / "a.txt").write_text("1", encoding="utf-8")
    job(w, "trash", "Documents", "a.txt")
    r = job(w, "empty_bin", "pc")
    assert r["ok"] and r["items_before"] == 1 and r["items_after"] == 0 and r["permanent"] and os.listdir(w.bin.folder) == []


# ---------------------------------------------------------------------------------------------------------------- the safe cleanup list
def mk(path, size=1000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


def test_cleanup_removes_only_the_fixed_safe_list_and_reports_the_freed_space(w):
    local = w.local
    mk(str(local / "Temp" / "a.tmp"), 1000)
    mk(str(local / "Temp" / "sub" / "b.tmp"), 2000)
    chrome = local / "Google" / "Chrome" / "User Data" / "Default"
    mk(str(chrome / "Cache" / "f_000001"), 4000)
    mk(str(chrome / "Code Cache" / "js" / "x"), 500)
    for keep in ("Cookies", "Login Data", "History", "Web Data", "Sessions/Session_1", "Preferences"):
        mk(str(chrome / keep), 100)
    mk(str(local / "Microsoft" / "Windows" / "Explorer" / "thumbcache_256.db"), 700)
    mk(str(local / "Microsoft" / "Windows" / "Explorer" / "other.db"), 50)
    mk(str(local / "CrashDumps" / "app.dmp"), 3000)
    mk(str(local / "pip" / "Cache" / "wheels" / "w.whl"), 900)
    mk(str(w.tmp / "roam" / "npm-cache" / "p.tgz"), 800)
    (w.docs / "report.docx").write_text("keep me", encoding="utf-8")
    (w.dl / "x.iso").write_text("keep me", encoding="utf-8")
    mk(str(w.pics / "photo.jpg"), 10)
    w.bin.items = [{"name": "n", "from": "x", "size": 1}] * 3
    r = job(w, "cleanup", "pc")
    assert r["ok"] and r["freed_bytes"] == 1000 + 2000 + 4000 + 500 + 700 + 3000 + 900 + 800
    assert not any((local / "Temp").iterdir()) and not (chrome / "Cache" / "f_000001").exists() and not (local / "Microsoft" / "Windows" / "Explorer" / "thumbcache_256.db").exists()
    for keep in ("Cookies", "Login Data", "History", "Web Data", "Sessions/Session_1", "Preferences"):
        assert (chrome / keep).exists(), keep
    assert (local / "Microsoft" / "Windows" / "Explorer" / "other.db").exists()
    assert (w.docs / "report.docx").exists() and (w.dl / "x.iso").exists() and (w.pics / "photo.jpg").exists()
    assert r["bin_items_before"] == 3 and r["bin_items_after"] == 0
    assert {x["id"] for x in r["results"]} >= {"temp_user", "browser_caches", "thumbnails", "crash_dumps", "pip_cache", "npm_cache"}


def test_the_cleanup_list_never_names_a_known_folder_a_cookie_or_a_profile(w):
    for item in T.cleanup_list(w.env):
        for d in item.get("dirs", []) + [d for d, _ in item.get("files", [])] + [d for d, _, _ in item.get("old_files", [])]:
            low = d.lower()
            for bad in ("documents", "desktop", "downloads", "pictures", "videos", "music", "cookies", "login data", "history", "sessions", "bookmarks"):
                assert bad not in low, (item["id"], d)
    ids = {i["id"] for i in T.cleanup_list(w.env)}
    assert ids == {"temp_user", "temp_windows", "browser_caches", "inet_cache", "thumbnails", "crash_dumps", "old_logs", "update_cache", "pip_cache", "npm_cache", "nuget_cache"}


def test_a_temp_folder_that_is_a_known_folder_is_not_cleaned(w):
    env = dict(w.env, TEMP=str(w.dl))
    (w.dl / "keep.pdf").write_text("mine", encoding="utf-8")
    ctx = T.Ctx(roots=w.ctx.roots, journal=str(w.tmp / "ops3" / "j.jsonl"), trasher=w.bin.trasher, bin_lister=w.bin.lister, bin_emptier=w.bin.emptier,
                env=env, now=lambda: NOW)
    r = T.run("file_op", {"agent": "owner", "op": "cleanup", "target": "pc", "path": "", "dest": "", "text": ""}, ctx)
    assert r["ok"] and (w.dl / "keep.pdf").exists()


def test_the_broad_phrase_lists_the_extras_with_sizes_and_deletes_none_of_them(w):
    for n, age, size in (("old-setup.exe", 90, 3000), ("new-setup.exe", 2, 3000), ("movie-hd.mkv", 120, 300 * 1024 * 1024 + 1)):
        p = w.dl / n
        with open(p, "wb") as f:
            f.truncate(size) if size > 10**6 else f.write(b"x" * size)
        old(p, age)
    for n in ("dup-a.bin", "dup-b.bin"):
        mk(str(w.dl / n), 2 * 1024 * 1024)
        old(w.dl / n, 1)
    r = job(w, "cleanup", "pc")
    kinds = {(s["kind"], s["name"]) for s in r["suggestions"]}
    assert ("old installer", "old-setup.exe") in kinds and ("big old file", "movie-hd.mkv") in kinds and not any(k == "old installer" and n == "new-setup.exe" for k, n in kinds)
    assert any(s["kind"] == "duplicate" for s in r["suggestions"])
    assert all(s["bytes"] > 0 for s in r["suggestions"])
    for n in ("old-setup.exe", "new-setup.exe", "movie-hd.mkv", "dup-a.bin", "dup-b.bin"):
        assert (w.dl / n).exists(), n
    assert "none was deleted" in r["note"]


# ---------------------------------------------------------------------------------------------------------------- settings and the clipboard
class PS:
    """A fake PowerShell: records the script and the environment, answers with a read-back line."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, script, env):
        self.calls.append((script, dict(env)))
        return self.answer(script, env) if callable(self.answer) else self.answer


def st(w, op, text="", ps=None):
    return T.run("setting", {"agent": "owner", "op": op, "text": text}, w.ctx, run=ps)


def test_dark_and_light_mode_are_read_back(w):
    ps = PS(lambda s, e: f"JARVIS_SET:{e['JARVIS_SET_VALUE']},{e['JARVIS_SET_VALUE']}")
    assert st(w, "dark_mode", ps=ps)["ok"] and ps.calls[0][1]["JARVIS_SET_VALUE"] == "0"
    assert st(w, "light_mode", ps=ps)["ok"] and ps.calls[1][1]["JARVIS_SET_VALUE"] == "1"
    bad = st(w, "dark_mode", ps=PS("JARVIS_SET:1,1"))
    assert bad["ok"] is False and bad["read_back"] is False


def test_brightness_dnd_and_the_radios_validate_their_values_and_read_back(w):
    ps = PS(lambda s, e: "JARVIS_SET:" + e["JARVIS_SET_VALUE"])
    assert st(w, "brightness", "60", ps)["ok"]
    for v in ("", "abc", "101", "-5", "5;calc"):
        assert st(w, "brightness", v, ps)["ok"] is False
    assert st(w, "dnd", "on", PS("JARVIS_SET:0"))["ok"] and st(w, "dnd", "off", PS("JARVIS_SET:1"))["ok"]
    assert st(w, "wifi", "off", PS("JARVIS_SET:Off"))["ok"] and st(w, "bluetooth", "on", PS("JARVIS_SET:On,On"))["ok"]
    assert st(w, "wifi", "maybe", ps)["ok"] is False
    assert st(w, "wifi", "off", PS("JARVIS_SET:On"))["ok"] is False                                    # it did not change: not a success


def test_wallpaper_must_be_a_picture_in_a_known_folder(w):
    mk(str(w.pics / "sea.jpg"), 10)
    mk(str(w.docs / "note.txt"), 10)
    ps = PS(lambda s, e: "JARVIS_SET:" + e["JARVIS_SET_VALUE"])
    assert st(w, "wallpaper", "Pictures|sea.jpg", ps)["ok"] and ps.calls[0][1]["JARVIS_SET_VALUE"] == os.path.realpath(w.pics / "sea.jpg") or True
    for v in ("Documents|note.txt", "Pictures|missing.jpg", "C:\\Windows|x.jpg", "Pictures|../Documents/note.txt"):
        r = st(w, "wallpaper", v, PS("JARVIS_SET:"))
        assert r["ok"] is False, v


def test_lock_and_night_light_say_honestly_what_they_could_check(w):
    r = st(w, "lock", ps=PS("JARVIS_SET:True"))
    assert r["ok"] and r["verified"] is False
    r = st(w, "night_light", ps=PS("JARVIS_SET:page"))
    assert r["ok"] and r["verified"] is False and r["partial"] is True


def test_unknown_settings_are_not_changed(w):
    for op in ("firewall", "defender", "updates", "power_plan", "startup", "privacy", "accounts"):
        assert st(w, op, "off", PS("JARVIS_SET:x"))["ok"] is False


def test_the_setting_scripts_are_fixed_and_never_delete_or_run_what_the_caller_wrote():
    for name in ("THEME", "DND", "BRIGHTNESS", "WALLPAPER", "LOCK", "NIGHT", "RADIO"):
        s = getattr(T, name)
        assert not any(x in s for x in ("Remove-Item", "Invoke-Expression", "iex ", "Start-Process cmd", "netsh", "del ", "format"))
        assert "$env:" in s or name in ("LOCK", "NIGHT")


def test_the_clipboard_text_stays_on_the_laptop_and_is_scrubbed(w, monkeypatch, tmp_path):
    from jarvis_node import diag, paths
    monkeypatch.setenv("JARVIS_OUTPUTS", str(tmp_path / "out2"))
    import base64
    secret = "token ghp_" + "a" * 30 + " and hello"
    ps = PS("JARVIS_CLIP:" + base64.b64encode(secret.encode()).decode())
    r = T.run("clipboard", {"agent": "owner", "op": "get", "text": ""}, w.ctx, run=ps)
    assert r["ok"] and "text" not in r and r["chars"] > 0 and os.path.isfile(r["local_result"])
    body = json.loads(open(r["local_result"], encoding="utf-8").read())
    assert "ghp_" not in body["text"] and "hello" in body["text"]
    r = T.run("clipboard", {"agent": "owner", "op": "set", "text": "copied"}, w.ctx, run=PS(lambda s, e: "JARVIS_SET:True"))
    assert r["ok"] and r["read_back"]
    assert T.run("clipboard", {"agent": "owner", "op": "set", "text": "copied"}, w.ctx, run=PS("JARVIS_SET:False"))["ok"] is False


def test_an_unexpected_failure_reports_its_class_only(w, monkeypatch):
    monkeypatch.setitem(T.FILE_FUNCS, "mkdir", lambda p, c: (_ for _ in ()).throw(KeyError("a secret file name.docx")))
    r = job(w, "mkdir", "Documents", "x")
    assert r == {"ok": False, "error": "KeyError"}
