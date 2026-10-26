"""The flight recorder (D61): write, rotate, retention, size caps, D: only, no picture for sensitive targets, the screenshot flag. tmp_path only."""
import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from jarvis_node import journal as J  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 200


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def step(clock, **kw):
    base = dict(task_id="t1", goal="Open Calculator and tell me the weather", step_label="Open Calculator", tool="open_app", cls="B",
                target="Calculator", result="a window for Calculator appeared", verified="verified", duration_ms=420, ts=clock())
    base.update(kw)
    return J.make_step(**base)


def test_a_step_is_one_json_line_with_the_fields_the_owner_asked_for(tmp_path):
    clock = Clock()
    j = J.Journal(tmp_path / "journal", clock)
    e = j.append(step(clock))
    files = [f for f in os.listdir(tmp_path / "journal") if f.endswith(".jsonl")]
    assert len(files) == 1 and J.FILE_RE.match(files[0])
    line = (tmp_path / "journal" / files[0]).read_text(encoding="utf-8").splitlines()
    assert len(line) == 1
    d = json.loads(line[0])
    for key in ("id", "ts", "task_id", "goal_hash", "goal_label", "step_label", "tool", "class", "target", "result", "verified", "duration_ms", "shots", "undo"):
        assert key in d, key
    assert d["class"] == "B" and d["verified"] == "verified" and d["goal_label"].startswith("Open Calculator") and len(d["goal_hash"]) == 16
    assert e["id"] == d["id"]


def test_texts_are_cut_and_cleaned_and_bad_values_are_made_safe():
    c = Clock()
    e = J.make_step(task_id="t", goal="x" * 500 + "\u202e\n\x00evil", step_label="a\nb" * 100, tool="t", cls="Z", verified="great", result="r" * 999)
    assert len(e["goal_label"]) <= 90 and "\n" not in e["step_label"] and "\u202e" not in e["goal_label"]
    assert e["class"] == "B" and e["verified"] == "failed" and len(e["result"]) <= 200
    assert J.goal_hash("Open  CALCULATOR") == J.goal_hash("open calculator")


def test_append_refuses_what_is_not_a_step(tmp_path):
    j = J.Journal(tmp_path)
    for bad in (None, {}, {"k": "step"}, {"k": "other", "id": "x"}, "line"):
        with pytest.raises(ValueError):
            j.append(bad)


def test_newest_first_grouped_by_task_and_an_undo_marks_its_step(tmp_path):
    clock = Clock()
    j = J.Journal(tmp_path, clock)
    a = j.append(step(clock, task_id="t1"))
    clock.t += 5
    b = j.append(step(clock, task_id="t1", step_label="Open Notepad", target="Notepad"))
    clock.t += 5
    c = j.append(step(clock, task_id="t2", goal="Open the invoices folder", step_label="Open Downloads folder"))
    clock.t += 5
    j.append(J.make_undo_entry(b, status="undone", detail="Notepad closed", ts=clock()))
    steps = j.steps()
    assert [s["step_label"] for s in steps][:2] == ["Undo: Open Notepad", "Open Downloads folder"]
    by_id = {s["id"]: s for s in steps}
    assert by_id[b["id"]]["undone"]["status"] == "undone" and "undone" not in by_id[a["id"]]
    tasks = j.tasks()
    assert [t["task_id"] for t in tasks] == ["t1", "t2"] or [t["task_id"] for t in tasks] == ["t2", "t1"]
    assert {t["task_id"]: len(t["steps"]) for t in tasks} == {"t1": 3, "t2": 1}
    assert tasks[0]["steps"][0]["ts"] >= tasks[0]["steps"][-1]["ts"]


def test_undoable_picks_the_newest_changing_steps_not_reads_and_not_undone(tmp_path):
    clock = Clock()
    j = J.Journal(tmp_path, clock)
    ids = {}
    for i, (label, undo) in enumerate([("Open Calculator", {"kind": "close_window"}), ("Check disk", None), ("Type 5 characters into Notepad", {"kind": "cannot_undo", "why": "typed"}),
                                       ("Open Notepad", {"kind": "close_window"})]):
        clock.t += 1
        ids[label] = j.append(step(clock, step_label=label, undo=undo))
    clock.t += 1
    j.append(J.make_undo_entry(ids["Open Notepad"], status="undone", detail="closed", ts=clock()))
    got = [s["step_label"] for s in j.undoable(2)]
    assert got == ["Type 5 characters into Notepad", "Open Calculator"], "the read is skipped, the undone one is skipped, n counts changing steps"
    assert [s["step_label"] for s in j.undoable(1)] == ["Type 5 characters into Notepad"]
    assert len(j.undoable(task_id="t1")) == 2
    summ = j.summary()
    assert summ["steps"] == 4 and summ["undone"] == 1 and summ["undoable"] == 1 and summ["failed"] == 0


def test_a_day_file_rotates_and_the_oldest_closed_files_go_first_under_the_cap(tmp_path):
    clock = Clock()
    j = J.Journal(tmp_path, clock, journal_cap=6000, rotate_at=1500)
    for i in range(60):
        clock.t += 1
        j.append(step(clock, step_label=f"Step number {i}"))
    names = sorted(os.listdir(tmp_path))
    assert any(".1.jsonl" in n for n in names), names
    j.enforce()
    total = sum(f.stat().st_size for f in tmp_path.glob("journal-*.jsonl"))
    assert total <= 6000 + 1500, total
    newest = j.steps(1)[0]
    assert newest["step_label"] == "Step number 59", "the newest entry survives the cap"
    assert len(j.steps(1000)) < 60, "the oldest went"


def test_the_journal_cap_never_removes_the_only_file(tmp_path):
    clock = Clock()
    j = J.Journal(tmp_path, clock, journal_cap=10)
    j.append(step(clock))
    j.enforce()
    assert len(j.steps()) == 1


def test_pictures_are_kept_seven_days_and_capped_oldest_first(tmp_path):
    clock = Clock()
    j = J.Journal(tmp_path, clock, shots_cap=1000)
    old = j.shots.save("old-b.jpg", JPEG)
    assert old
    os.utime(j.shots.path(old), (clock() - 8 * 86400, clock() - 8 * 86400))
    for i in range(8):
        ref = j.shots.save(f"job{i}-a.jpg", JPEG)
        os.utime(j.shots.path(ref), (clock() - 100 + i, clock() - 100 + i))
    removed = j.shots.purge()
    assert not j.shots.exists("old-b.jpg") and removed >= 1
    assert j.shots.total() <= 1000
    assert j.shots.exists("job7-a.jpg"), "the newest picture stays"
    assert not j.shots.exists("job0-a.jpg"), "the oldest of the rest went first"


def test_a_picture_ref_cannot_leave_the_folder_and_only_real_jpegs_are_stored(tmp_path):
    s = J.Shots(tmp_path / "shots")
    for bad in ("../x.jpg", "a/b.jpg", "a\\b.jpg", "x.png", "x.jpg.part", "", ".jpg", "/etc/passwd"):
        assert s.path(bad) is None, bad
        assert s.read(bad) is None
    assert s.save("ok-b.jpg", b"not a jpeg") is None
    assert s.save("ok-b.jpg", b"") is None
    assert s.save("ok-b.jpg", b"\xff\xd8\xff" + b"x" * (J.SHOT_MAX_BYTES + 1)) is None
    assert s.save("ok-b.jpg", JPEG) == "ok-b.jpg" and s.read("ok-b.jpg") == JPEG


def test_storage_is_d_only_on_windows_and_off_windows_needs_an_explicit_folder():
    root, why = J.default_root({"JARVIS_DATA_DIR": "C:\\Users\\me\\data"}, nt=True)
    assert root is None and "D:" in why
    root, why = J.default_root({"JARVIS_DATA_DIR": "D:\\jarvis\\data"}, nt=True)
    assert why == "" and str(root).replace("/", "\\") == "D:\\jarvis\\data\\journal"
    root, why = J.default_root({}, nt=True)
    assert why == "" and str(root).replace("/", "\\").startswith("D:\\jarvis")
    root, why = J.default_root({"JARVIS_JOURNAL_DIR": "E:\\x"}, nt=True)
    assert root is None, "an explicit folder on another drive is still not allowed on Windows"
    assert J.default_root({}, nt=False)[0] is None
    assert J.default_root({"JARVIS_JOURNAL_DIR": "/tmp/j"}, nt=False)[0] == J.Path("/tmp/j")
    assert J.default_root({"JARVIS_DATA_DIR": "D:\\jarvis\\data"}, nt=False)[0] is None, "off Windows nothing is written to a D:\\ look-alike folder"


def test_the_screenshot_flag_env_then_settings_then_toml(tmp_path):
    toml = tmp_path / "p.toml"
    toml.write_text("[config]\njournal_screenshots = false\n")
    assert J.screenshots_on({}, None, toml) is False
    assert J.screenshots_on({"JARVIS_JOURNAL_SCREENSHOTS": "1"}, None, toml) is True
    toml.write_text("[config]\njournal_screenshots = true\n")
    assert J.screenshots_on({}, None, toml) is True
    assert J.screenshots_on({"JARVIS_JOURNAL_SCREENSHOTS": "0"}, None, toml) is False
    (tmp_path / "settings.json").write_text(json.dumps({"journal_screenshots": False}))
    assert J.screenshots_on({}, tmp_path, toml) is False
    assert J.screenshots_on({}, tmp_path / "nowhere", tmp_path / "missing.toml") is True, "default on"
    assert "journal_screenshots" in open(J.PLUGIN_TOML, encoding="utf-8").read(), "the real plugin.toml names the flag"


# ----------------------------------------------------------------------------------------------------- the node's pictures
class FakeCapture:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, proc, path):
        self.calls.append(proc)
        if self.ok:
            with open(path, "wb") as fh:
                fh.write(JPEG)
        return self.ok, ""


def rec(tmp_path, cap, skip=None, env=None):
    j = J.Journal(tmp_path / "j", env=env or {"JARVIS_JOURNAL_SCREENSHOTS": "1"})
    return J.NodeRecorder(j, capture=cap, skip=skip or (lambda texts, proc: J.skip_reason(texts, lambda p: "", proc))), j


def ui_job(op="click", target="notepad|Save", jid="aaaa1111"):
    return {"id": jid, "kind": "ui_action", "params": {"agent": "owner", "op": op, "target": target, "text": ""}}


def test_a_changing_job_gets_a_before_and_an_after_picture_named_after_the_job(tmp_path):
    cap = FakeCapture()
    r, j = rec(tmp_path, cap)
    seen = []
    out = r.around(ui_job(), lambda: seen.append(j.shots.exists("aaaa1111-b.jpg")) or {"ok": True})
    assert out == {"ok": True} and seen == [True], "the before picture exists before the job runs"
    assert j.shots.exists("aaaa1111-a.jpg") and cap.calls == ["notepad", "notepad"]
    assert not list((tmp_path / "j" / "shots").glob("*.tmp")), "no temporary file is left"


def test_each_picture_logs_what_it_cost(tmp_path):
    r, j = rec(tmp_path, FakeCapture())
    r.around(ui_job(jid="cost0001"), lambda: {"ok": True})
    lines = [json.loads(x) for x in (tmp_path / "j" / "shots" / "timing.jsonl").read_text().splitlines()]
    assert [(x["job"], x["which"]) for x in lines] == [("cost0001", "before"), ("cost0001", "after")]
    assert all(x["bytes"] == len(JPEG) and x["ms"] >= 0 for x in lines)


def test_a_failed_job_has_a_before_picture_only_and_a_failed_capture_never_hurts_the_job(tmp_path):
    cap = FakeCapture()
    r, j = rec(tmp_path, cap)
    r.around(ui_job(jid="bbbb2222"), lambda: {"ok": False, "error": "no element"})
    assert j.shots.exists("bbbb2222-b.jpg") and not j.shots.exists("bbbb2222-a.jpg")
    bad = FakeCapture(ok=False)
    r2, j2 = rec(tmp_path, bad)
    assert r2.around(ui_job(), lambda: {"ok": True, "x": 1}) == {"ok": True, "x": 1}
    boom = lambda proc, path: (_ for _ in ()).throw(RuntimeError("powershell exploded"))        # noqa: E731
    r3, _ = rec(tmp_path, boom)
    assert r3.around(ui_job(), lambda: {"ok": True}) == {"ok": True}


def test_no_picture_for_sensitive_targets_secret_paths_or_ai_chat_sites(tmp_path):
    cap = FakeCapture()
    r, j = rec(tmp_path, cap)
    for target in ("1Password|Vault", "chrome|ICICI Bank", "Zerodha Kite|Buy", "KeePass|Entry", "metamask|Send"):
        r.around(ui_job(target=target, jid="s" + str(abs(hash(target)))[:8]), lambda: {"ok": True})
    assert cap.calls == [], "nothing was photographed"
    r.around({"id": "cccc3333", "kind": "browser_ext", "params": {"profile": "default", "op": "open", "url": "https://www.icicibank.com/", "target": "", "text": ""}},
             lambda: {"ok": True})
    r.around({"id": "dddd4444", "kind": "browser_ext", "params": {"profile": "default", "op": "click", "url": "https://chatgpt.com/", "target": "Send", "text": ""}},
             lambda: {"ok": True})
    r.around({"id": "eeee5555", "kind": "pc_open", "params": {"agent": "owner", "op": "open_file", "target": "Documents", "path": "jarvis-secrets\\keys.json"}},
             lambda: {"ok": True, "window": {"process": "notepad", "title": "keys"}})
    assert cap.calls == []
    assert list((tmp_path / "j" / "shots").glob("*.jpg")) == [] if (tmp_path / "j" / "shots").exists() else True


def test_a_window_title_that_shows_a_bank_stops_the_picture_even_for_a_browser(tmp_path):
    cap = FakeCapture()
    r, j = rec(tmp_path, cap, skip=lambda texts, proc: J.skip_reason(texts, lambda p: "one of its windows is a bank", proc))
    r.around({"id": "ffff6666", "kind": "browser_ext", "params": {"profile": "default", "op": "click", "url": "", "target": "Next", "text": ""}}, lambda: {"ok": True})
    assert cap.calls == []


def test_the_flag_switches_every_picture_off(tmp_path):
    cap = FakeCapture()
    r, j = rec(tmp_path, cap, env={"JARVIS_JOURNAL_SCREENSHOTS": "0"})
    r.around(ui_job(), lambda: {"ok": True})
    assert cap.calls == [] and not (tmp_path / "j" / "shots").exists()


def test_reads_and_file_jobs_get_no_picture_and_a_media_key_none_either(tmp_path):
    cap = FakeCapture()
    r, j = rec(tmp_path, cap)
    for job in ({"id": "r1", "kind": "ui_snapshot", "params": {"target": "notepad"}}, {"id": "r2", "kind": "diag", "params": {"op": "disk", "target": ""}},
                {"id": "r3", "kind": "repair", "params": {"op": "restore", "target": "20261026-101010-abcdef", "args": {}}},
                {"id": "r4", "kind": "screenshot", "params": {}}, ui_job(target="media|", jid="r5"), {"id": "r6", "kind": "pc_open", "params": {"op": "list_folder", "target": "Downloads", "path": ""}}):
        r.around(job, lambda: {"ok": True})
    assert cap.calls == []


def test_an_opened_app_is_photographed_after_the_open_by_the_process_the_node_saw(tmp_path):
    cap = FakeCapture()
    r, j = rec(tmp_path, cap)
    job = {"id": "oooo7777", "kind": "pc_open", "params": {"agent": "owner", "op": "open_app", "target": "Calculator", "path": ""}}
    r.around(job, lambda: {"ok": True, "opened": "Calculator", "verified": "verified", "window": {"title": "Calculator", "process": "CalculatorApp"}})
    assert cap.calls == ["CalculatorApp"] and j.shots.exists("oooo7777-a.jpg") and not j.shots.exists("oooo7777-b.jpg")


def test_the_capture_script_is_one_window_only_scaled_and_jpeg():
    s = J.SHOT_SCRIPT
    assert "PrintWindow" in s and "image/jpeg" in s and "JARVIS_SHOT_W" in s and "__PICK__" in s
    assert "CopyFromScreen" not in s and "GetDC(0)" not in s and "BitBlt" not in s, "never the whole desktop"
    assert J.SHOT_WIDTH == 800


def test_capture_window_jpeg_uses_the_pick_env_and_reports_why_it_failed(tmp_path, monkeypatch):
    from jarvis_node import windowpick
    monkeypatch.setattr(windowpick, "pick_env", lambda proc, mutating: {"JARVIS_UI_HWND": "5"})
    seen = {}

    class R:
        returncode, stderr = 0, ""

    def runner(script, env, timeout):
        seen.update(env=env, timeout=timeout)
        open(env["JARVIS_SHOT"], "wb").write(JPEG)
        return R()
    path = str(tmp_path / "x.jpg")
    assert J.capture_window_jpeg("notepad", path, runner) == (True, "")
    assert seen["env"]["JARVIS_UI_HWND"] == "5" and seen["env"]["JARVIS_SHOT_W"] == "800" and seen["timeout"] <= 15
    assert J.capture_window_jpeg("bad|name", path, runner) == (False, "bad target")

    class Bad:
        returncode, stderr = 1, "no window"
    assert J.capture_window_jpeg("notepad", str(tmp_path / "y.jpg"), lambda s, e, t: Bad()) == (False, "no window")


def test_the_listener_hears_every_append_and_a_broken_one_does_not_stop_the_recorder(tmp_path):
    clock = Clock()
    j = J.Journal(tmp_path, clock)
    heard = []
    j.listeners += [lambda e: (_ for _ in ()).throw(RuntimeError("x")), heard.append]
    j.append(step(clock))
    assert len(heard) == 1 and len(j.steps()) == 1


def test_a_damaged_line_is_skipped(tmp_path):
    clock = Clock()
    j = J.Journal(tmp_path, clock)
    j.append(step(clock))
    f = next(tmp_path.glob("journal-*.jsonl"))
    f.write_text("{not json\n" + f.read_text(encoding="utf-8") + "\n[1,2]\n", encoding="utf-8")
    assert len(j.steps()) == 1


def test_stamp_changes_with_size_or_mtime_and_a_folder_is_stamped_by_its_content(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    s1 = J.stamp(str(f))
    f.write_text("hello!")
    assert J.stamp(str(f))["hash"] != s1["hash"]
    h = J.stamp(str(f))["hash"]
    os.utime(f, (1, 1))
    assert J.stamp(str(f))["hash"] != h, "a different mtime is a different stamp"
    assert J.stamp(str(tmp_path / "nope")) is None
    d = tmp_path / "d"
    d.mkdir()
    (d / "x").write_text("1")
    h = J.stamp(str(d))["hash"]
    (d / "y").write_text("22")
    assert J.stamp(str(d))["hash"] != h


def test_a_new_empty_folder_is_still_the_recorded_one_after_something_came_and_went(tmp_path):
    d = tmp_path / "new"
    d.mkdir()
    h = J.stamp(str(d))["hash"]
    (d / "t.txt").write_text("x")
    assert J.stamp(str(d))["hash"] != h
    (d / "t.txt").unlink()
    os.utime(d, (1, 1))
    assert J.stamp(str(d))["hash"] == h, "the folder's own mtime does not count: it is empty, as recorded"
