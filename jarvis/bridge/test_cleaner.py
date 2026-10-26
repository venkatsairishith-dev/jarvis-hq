"""Tests for jarvis.bridge.cleaner on a fake PC (a temp folder shaped like Windows) with a fake Recycle Bin: what the scan lists, what a clean
removes (and nothing else), the hard blocks, locked files, review rows never ticked by themselves, and that nothing sent to a chat model
carries a file name."""
from __future__ import annotations

import json
import os
import shutil
import time

import pytest

from jarvis.bridge import cleaner as C

NOW = 1_800_000_000.0
DAY = 86400


class FakeBin:
    """Moves a file into a folder instead of the real Recycle Bin; remembers what it was given."""

    def __init__(self, folder, size=0, items=0):
        self.folder, self.size, self.items, self.trashed, self.emptied = folder, size, items, [], False
        os.makedirs(folder, exist_ok=True)

    def trash(self, path):
        self.trashed.append(path)
        shutil.move(path, os.path.join(self.folder, f"{len(self.trashed)}-{os.path.basename(path)}"))

    def bin_stats(self):
        return self.size, self.items

    def bin_empty(self):
        self.emptied, self.size, self.items = True, 0, 0
        return True


class World:
    def __init__(self, root):
        self.root = root
        r = root
        self.env = C.Env(home=r / "Users" / "me", user_temp=[r / "Users" / "me" / "AppData" / "Local" / "Temp"], windows=r / "Windows",
                         local_appdata=r / "Users" / "me" / "AppData" / "Local", appdata=r / "Users" / "me" / "AppData" / "Roaming",
                         program_files=[r / "Program Files", r / "Program Files (x86)"], program_data=r / "ProgramData", users_root=r / "Users",
                         protected=[r / "jarvis", r / "jarvis-secrets", r / "Users" / "me" / "proj"])
        self.bin = FakeBin(r / "bin", size=5_000, items=3)
        self.cleaner = C.Cleaner(self.env, self.bin, clock=lambda: NOW)

    def mk(self, rel, size=100, age_days=40, content=None):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if content is not None else b"x" * size)
        t = NOW - age_days * DAY
        os.utime(p, (t, t))
        return p

    def row(self, scan, cid):
        return next(r for r in scan["categories"] if r["id"] == cid)


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path / "pc")
    me = "Users/me"
    world.mk(f"{me}/AppData/Local/Temp/a.tmp", 1000, 2)
    world.mk(f"{me}/AppData/Local/Temp/sub/b.tmp", 2000, 3)
    world.mk(f"{me}/AppData/Local/Temp/fresh.tmp", 500, 0)                                       # touched now: may belong to a running installer
    world.mk("Windows/Temp/w.tmp", 4000, 3)
    world.mk("Windows/System32/kernel.dll", 9999, 100)
    world.mk("Windows/SoftwareDistribution/Download/upd.cab", 7000, 30)
    world.mk("Windows/Minidump/old.dmp", 3000, 30)
    world.mk("Windows/Minidump/new.dmp", 3000, 1)
    for part in ("Cache/c1", "Code Cache/c2", "Cookies", "History", "Login Data"):
        world.mk(f"{me}/AppData/Local/Google/Chrome/User Data/Default/{part}", 600, 5)
    world.mk(f"{me}/AppData/Local/Google/Chrome/User Data/Profile 1/Cache/c3", 700, 5)
    world.mk(f"{me}/AppData/Local/pip/Cache/p.whl", 800, 5)
    world.mk(f"{me}/AppData/Local/Microsoft/Windows/Explorer/thumbcache_256.db", 900, 5)
    world.mk(f"{me}/AppData/Local/Microsoft/Windows/Explorer/other.txt", 900, 5)
    world.mk(f"{me}/AppData/Local/CrashDumps/x.dmp", 1100, 30)
    world.mk("jarvis/data/audit.log", 50, 100)
    world.mk("jarvis-secrets/key.txt", 50, 100)
    world.mk(f"{me}/proj/build/cache.bin", 50, 100)
    world.mk(f"{me}/Documents/notes.docx", 300, 5)
    return world


def test_scan_lists_the_fixed_rows_with_sizes_and_defaults(w):
    scan = w.cleaner.scan()
    ids = [r["id"] for r in scan["categories"]]
    assert ids == ["user_temp", "windows_temp", "browser_cache", "app_cache", "thumbnail_cache", "crash_logs", "update_cache", "recycle_bin", "dev_caches",
                   "duplicates", "old_installers", "big_unused", "empty_folders"]
    for r in scan["categories"]:
        assert set(r) >= {"id", "label", "what_it_is", "bytes", "files", "kind", "default_checked"}
        assert r["default_checked"] is (r["kind"] == "safe"), "safe rows are ticked, review rows never"
    assert w.row(scan, "user_temp")["bytes"] == 3000 and w.row(scan, "user_temp")["files"] == 2, "the fresh temp file is left alone"
    assert w.row(scan, "windows_temp")["bytes"] == 4000
    assert w.row(scan, "browser_cache")["bytes"] == 600 + 600 + 700 and w.row(scan, "browser_cache")["files"] == 3, "Cache folders only, in every profile"
    assert w.row(scan, "dev_caches")["bytes"] == 800 and w.row(scan, "update_cache")["bytes"] == 7000
    assert w.row(scan, "thumbnail_cache")["bytes"] == 900, "thumbcache files only"
    assert w.row(scan, "crash_logs")["bytes"] == 3000 + 1100, "dumps older than 7 days only"
    assert w.row(scan, "recycle_bin")["bytes"] == 5000 and w.row(scan, "recycle_bin")["files"] == 3


def test_cookies_history_passwords_and_profiles_are_never_listed(w):
    listed = {e.path for cid in ("browser_cache", "app_cache") for e in w.cleaner.entries(cid)}
    assert listed and not any(os.path.basename(p) in ("Cookies", "History", "Login Data") for p in listed)
    clean = w.cleaner.clean({"categories": ["browser_cache"]})
    assert clean["deleted"] == 3
    chrome = w.root / "Users/me/AppData/Local/Google/Chrome/User Data/Default"
    assert (chrome / "Cookies").exists() and (chrome / "History").exists() and (chrome / "Login Data").exists()


def test_clean_removes_exactly_the_selected_rows_for_real_and_rescans(w):
    res = w.cleaner.clean({"categories": ["user_temp", "update_cache"]})
    assert res["deleted"] == 3 and res["freed_bytes"] == 3000 + 7000 and res["skipped"] == 0 and res["binned"] == 0
    assert not (w.root / "Users/me/AppData/Local/Temp/a.tmp").exists() and (w.root / "Users/me/AppData/Local/Temp/fresh.tmp").exists()
    assert (w.root / "Windows/Temp/w.tmp").exists(), "a row that was not selected stays"
    assert (w.root / "Windows/System32/kernel.dll").exists()
    assert not (w.root / "Users/me/AppData/Local/Temp/sub").exists(), "the emptied sub-folder goes, the root stays"
    assert (w.root / "Users/me/AppData/Local/Temp").is_dir() and (w.root / "Windows/SoftwareDistribution/Download").is_dir()
    assert w.row(res["scan"], "user_temp")["bytes"] == 0 and w.row(res["scan"], "windows_temp")["bytes"] == 4000, "re-scanned after the clean"
    assert w.bin.trashed == []


def test_the_recycle_bin_row_empties_the_bin(w):
    res = w.cleaner.clean({"categories": ["recycle_bin"]})
    assert w.bin.emptied and res["freed_bytes"] == 5000
    assert w.row(res["scan"], "recycle_bin")["bytes"] == 0


def test_dry_run_changes_nothing_and_says_what_would_happen(w):
    before = sorted(str(p) for p in w.root.rglob("*"))
    res = w.cleaner.clean({"categories": ["user_temp", "windows_temp", "recycle_bin"]}, dry_run=True)
    assert res["dry_run"] is True and res["freed_bytes"] == 3000 + 4000 + 5000 and res["deleted"] == 2 + 1 + 3 and "scan" not in res
    assert sorted(str(p) for p in w.root.rglob("*")) == before and not w.bin.emptied


def test_locked_files_are_skipped_and_counted(w):
    locked = str(w.root / "Users/me/AppData/Local/Temp/a.tmp")

    def remove(path):
        if path == locked:
            raise PermissionError("in use")
        os.remove(path)
    w.cleaner.remove = remove
    res = w.cleaner.clean({"categories": ["user_temp"]})
    assert res["skipped"] == 1 and res["deleted"] == 1 and res["freed_bytes"] == 2000
    assert os.path.exists(locked)


# ---- review rows ----------------------------------------------------------------------------------------------------------------------

def make_review(w):
    me = "Users/me"
    same = b"same content" * 100
    w.mk(f"{me}/Downloads/report (1).pdf", content=same, age_days=10)
    w.mk(f"{me}/Downloads/report.pdf", content=same, age_days=3)                  # the newest copy: stays
    w.mk(f"{me}/Documents/copy-of-report.pdf", content=same, age_days=20)
    w.mk(f"{me}/Downloads/unique.pdf", content=b"unique" * 100, age_days=20)
    w.mk(f"{me}/Downloads/setup.exe", 5000, 60)                                    # old installer
    w.mk(f"{me}/Downloads/new-setup.msi", 5001, 5)                                 # too new
    w.mk(f"{me}/Downloads/movie.mkv", 10, 400)
    big = w.root / me / "Documents" / "disk.img"
    with open(big, "wb") as f:
        f.truncate(C.BIG_FILE_BYTES + 1)
    os.utime(big, (NOW - 400 * DAY, NOW - 400 * DAY))
    (w.root / me / "Downloads" / "empty" / "inner").mkdir(parents=True)
    (w.root / me / "Documents" / "keep").mkdir()
    w.mk(f"{me}/Documents/keep/f.txt", 11, 1)


def test_review_rows_are_listed_with_items_and_never_ticked(w):
    make_review(w)
    scan = w.cleaner.scan()
    dup, inst, big, empty = (w.row(scan, c) for c in ("duplicates", "old_installers", "big_unused", "empty_folders"))
    assert dup["kind"] == inst["kind"] == big["kind"] == empty["kind"] == "review"
    assert not any(r["default_checked"] for r in (dup, inst, big, empty))
    assert dup["files"] == 2 and dup["bytes"] == 2 * 1200, "two copies go, the newest stays"
    assert not any("report.pdf" == i["label"].split(os.sep)[-1] for i in dup["items"]), "the newest copy is never offered"
    assert inst["files"] == 1 and inst["items"][0]["label"].endswith("setup.exe")
    assert big["files"] == 1 and big["bytes"] == C.BIG_FILE_BYTES + 1
    assert empty["files"] == 1 and empty["items"][0]["label"].endswith("empty"), "only the top-most empty folder, not its child"
    assert len(dup["top"]) <= C.TOP_ITEMS


def test_review_items_go_to_the_recycle_bin_with_an_undo_list(w):
    make_review(w)
    scan = w.cleaner.scan()
    inst = w.row(scan, "old_installers")["items"][0]
    res = w.cleaner.clean({"items": {"old_installers": [inst["id"]]}})
    assert res["binned"] == 1 and res["freed_bytes"] == 0 and res["to_bin_bytes"] == 5000
    assert len(w.bin.trashed) == 1 and w.bin.trashed[0].endswith("setup.exe") and not (w.root / "Users/me/Downloads/setup.exe").exists()
    assert res["undo"][0]["label"].endswith("setup.exe") and res["undo"][0]["bytes"] == 5000
    assert (w.root / "Users/me/Downloads/new-setup.msi").exists(), "only the chosen item"
    assert w.row(res["scan"], "old_installers")["files"] == 0


def test_a_review_item_that_changed_since_the_scan_is_not_removed(w):
    make_review(w)
    inst = w.row(w.cleaner.scan(), "old_installers")["items"][0]
    os.remove(w.root / "Users/me/Downloads/setup.exe")
    res = w.cleaner.clean({"items": {"old_installers": [inst["id"]]}})
    assert res["binned"] == 0 and w.bin.trashed == []


def test_an_unknown_category_or_item_id_removes_nothing(w):
    make_review(w)
    res = w.cleaner.clean({"categories": ["system32", "../../Windows"], "items": {"duplicates": ["deadbeef"], "user_temp": ["x"]}})
    assert res["deleted"] == res["binned"] == 0 and (w.root / "Windows/System32/kernel.dll").exists() and len(w.bin.trashed) == 0
    assert (w.root / "Users/me/AppData/Local/Temp/a.tmp").exists(), "an item list for a safe row is not a request to clean that row"


# ---- hard blocks ----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rel", ["Windows/System32/kernel.dll", "Windows", "Program Files/app.exe", "ProgramData/x", "Users/me", "Users/other/Documents/a.txt",
                                 "Users", "jarvis/data/audit.log", "jarvis-secrets/key.txt", "Users/me/proj/build/cache.bin", "Users/me/Documents",
                                 "Users/me/Downloads", "Users/me/AppData/Local"])
def test_hard_blocks(w, rel):
    for p in ("Program Files/app.exe", "ProgramData/x", "Users/other/Documents/a.txt"):
        w.mk(p)
    assert w.cleaner.guard.reason(w.root / rel) is not None, rel


def test_drive_roots_are_blocked_and_windows_temp_is_the_only_hole(w):
    assert w.cleaner.guard.reason("/") is not None
    win_temp = w.root / "Windows/Temp"
    assert w.cleaner.guard.reason(win_temp / "w.tmp") is not None, "inside Windows without an exemption"
    assert w.cleaner.guard.reason(win_temp / "w.tmp", [win_temp]) is None
    assert w.cleaner.guard.reason(w.root / "Windows/System32/kernel.dll", [win_temp]) is not None


def test_a_temp_folder_inside_a_protected_folder_is_never_touched(w):
    w.env.user_temp = [w.root / "jarvis" / "tmp"]
    w.mk("jarvis/tmp/job.tmp", 100, 5)
    cl = C.Cleaner(w.env, w.bin, clock=lambda: NOW)
    assert cl.entries("user_temp") == []
    cl.clean({"categories": ["user_temp"]})
    assert (w.root / "jarvis/tmp/job.tmp").exists()


def test_a_link_is_never_followed_or_removed(w):
    if not hasattr(os, "symlink"):
        pytest.skip("no symlinks")
    target = w.root / "Windows" / "System32"
    link = w.root / "Users/me/AppData/Local/Temp/link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("cannot make a symlink")
    res = w.cleaner.clean({"categories": ["user_temp"]})
    assert (target / "kernel.dll").exists() and os.path.islink(link), "a link to a protected folder is left alone"
    assert res["deleted"] == 2


def test_the_race_between_listing_and_removing_is_closed(w):
    """A file swapped for a link after the listing is skipped, not followed."""
    if not hasattr(os, "symlink"):
        pytest.skip("no symlinks")
    victim = w.root / "Windows/System32/kernel.dll"
    target = w.root / "Users/me/AppData/Local/Temp/a.tmp"
    real_entries = w.cleaner.entries

    def entries(cid, now=None):
        found = real_entries(cid, now)
        os.remove(target)
        try:
            os.symlink(victim, target)
        except OSError:
            pytest.skip("cannot make a symlink")
        return found
    w.cleaner.entries = entries
    w.cleaner.clean({"categories": ["user_temp"]})
    assert victim.exists()


def test_project_folders_in_config_are_blocked(tmp_path):
    cfg = tmp_path / "projects.json"
    cfg.write_text(json.dumps({"projects": {"x": {"path": {"nt": str(tmp_path / "code" / "x")}}, "y": {"path": str(tmp_path / "code" / "y")}}}))
    got = {str(p) for p in C.project_folders(cfg)}
    assert got == {str(tmp_path / "code" / "x"), str(tmp_path / "code" / "y")}
    assert C.project_folders(tmp_path / "missing.json") == []
    real = C.project_folders()
    assert real, "the repo's own config/projects.json lists the project folder"


# ---- nothing with a file name goes to the chat model -------------------------------------------------------------------------------------

def test_the_model_view_has_ids_sizes_and_counts_but_no_file_names(w):
    make_review(w)
    res = w.cleaner.clean({"categories": ["user_temp"], "items": {"old_installers": [w.row(w.cleaner.scan(), "old_installers")["items"][0]["id"]]}})
    for value in (w.cleaner.scan(), res):
        text = json.dumps(C.model_view(value))
        for name in ("setup.exe", "a.tmp", "report", "Downloads", "AppData", "disk.img", "notes.docx", "Users"):
            assert name not in text, name
    view = C.model_view(w.cleaner.scan())
    assert view["categories"][0].keys() >= {"id", "label", "bytes", "files", "kind", "default_checked"}


def test_progress_is_reported_while_cleaning(w):
    seen = []
    w.cleaner.clean({"categories": ["user_temp", "windows_temp"]}, on_progress=seen.append)
    assert seen and seen[-1]["done_items"] == seen[-1]["total_items"] == 3 and seen[-1]["done_bytes"] == 7000


def test_a_stop_request_ends_the_clean_early(w):
    res = w.cleaner.clean({"categories": ["user_temp", "windows_temp"]}, should_stop=lambda: True)
    assert res.get("stopped") and res["deleted"] == 0
