"""Fixtures for scripts/timeline-check.mjs: realistic Timeline views built by the REAL backend (jarvis.node.jarvis_node.journal, jarvis.bridge.timeline) from a journal
written the way the desktop plugin writes it, with real JPEG pictures on disk. Written out as the tool results the face receives from jarvis_timeline_wanted and
jarvis_timeline_shot, so the whole chain is exercised: journal file -> view -> panel logic -> page.

    python make_fixtures.py OUT.json
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))

from jarvis.bridge import timeline as T  # noqa: E402
from jarvis.node.jarvis_node import journal as J  # noqa: E402
from jarvis.node.jarvis_node import undo as U  # noqa: E402


def window_picture(title: str, body: list[str], accent: tuple, dark: bool = False, size=(800, 500)) -> bytes:
    bg = (32, 34, 40) if dark else (246, 247, 249)
    fg = (230, 232, 236) if dark else (30, 33, 40)
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, size[0], 36), fill=accent)
    d.text((14, 11), title, fill=(255, 255, 255))
    for i, line in enumerate(body):
        d.text((24, 64 + i * 26), line, fill=fg)
    d.rectangle((24, size[1] - 70, 180, size[1] - 30), outline=accent, width=2)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def main(out: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="timeline-fixtures-"))
    now = time.time()
    fixtures: dict = {"views": {}, "shots": {}, "undo": {}}

    def build(name: str, journal: "J.Journal | None", cap: int | None = None):
        tl = T.Timeline(lambda: journal, clock=lambda: now)
        saved = T.MAX_STEPS
        if cap:
            T.MAX_STEPS = cap
        try:
            got = tl.wanted(0, 0)
        finally:
            T.MAX_STEPS = saved
        got["open_seq"] = 1
        fixtures["views"][name] = {"structuredContent": got}
        fixtures["token"] = tl.panel_token
        if journal is not None:
            for task in got["view"]["tasks"]:
                for s in task["steps"]:
                    for shot in (s.get("shots") or {}).values():
                        r = tl.shot(shot["ref"], shot["token"], tl.panel_token)
                        fixtures["shots"][shot["ref"]] = {"structuredContent": r}
        return tl

    # ---- a day of activity, written like the desktop plugin writes it ----
    j = J.Journal(work / "small", env={"JARVIS_JOURNAL_SCREENSHOTS": "1"}, clock=lambda: now)
    calc_b = j.shots.save("a1-b.jpg", window_picture("Calculator", ["0"], (36, 110, 140)))
    calc_a = j.shots.save("a1-a.jpg", window_picture("Calculator", ["0", "", "(opened by JARVIS)"], (36, 110, 140)))
    pad_b = j.shots.save("b1-b.jpg", window_picture("Untitled - Notepad", [""], (70, 90, 160), dark=True))
    pad_a = j.shots.save("b1-a.jpg", window_picture("Untitled - Notepad", ["hello"], (70, 90, 160), dark=True))
    for r in (calc_b, calc_a, pad_b, pad_a):
        assert r

    def step(task, goal, label, tool, cls, verified, ms, ago, result, undo=None, shots=None, target=""):
        return j.append(J.make_step(task_id=task, goal=goal, step_label=label, tool=tool, cls=cls, target=target, result=result, verified=verified, duration_ms=ms,
                                    ts=now - ago, undo=undo, shots=shots))

    g1 = "Open Calculator and tell me the weather"
    step("t-yday", "Play a Kalyani song", "Play something on YouTube", "play_media", "B", "verified", 12400, 30 * 3600 + 120, "video playing, same id as chosen", target="YouTube")
    step("t-yday", "Play a Kalyani song", "Click in Chrome", "browser", "C", "failed", 2100, 30 * 3600 + 60, "waiting for the owner's approval", target="Chrome")
    s1 = step("t-calc", g1, "Open Calculator", "open_app", "B", "verified", 1800, 5400, "a window for Calculator appeared",
              U.recipe_close_window("calculatorapp", 4242, now - 5400), {"before": calc_b, "after": calc_a}, "Calculator")
    step("t-calc", g1, "Check network", "check_pc", "A", "verified", 640, 5395, "network read")
    step("t-calc", g1, "Open weather.com", "open_website", "B", "unconfirmed", 3100, 5390, "Chrome window appeared in profile Default (the profile is unproven)", target="weather.com")
    g2 = "Type hello in Notepad and save it on my desktop"
    s2 = step("t-pad", g2, "Open Notepad", "open_app", "B", "verified", 1500, 3600, "a window for Notepad appeared", U.recipe_close_window("notepad", 777, now - 3600),
              {"before": pad_b, "after": pad_a}, "Notepad")
    step("t-pad", g2, "Type 5 characters into Notepad", "type_text", "B", "verified", 900, 3590, "the text was typed into notepad and read back from the field",
         U.recipe_cannot("typed text cannot be taken back (if it was saved as a new file, that file can be undone)"), None, "Notepad")
    step("t-pad", g2, "Press ctrl+s in Notepad", "press_key", "B", "unconfirmed", 400, 3585, "ctrl+s was sent to notepad (reported ok, not observed)", None, None, "Notepad")
    desk = work / "Desktop"
    desk.mkdir()
    (desk / "hello.txt").write_text("hello")
    step("t-pad", g2, "Saved a .txt file", "save", "B", "verified", 0, 3580, "a new file appeared in your folders", U.recipe_recycle_new(str(desk / "hello.txt")))
    j.append(J.make_undo_entry(s2, status="undone", detail="undone: the window is closed", ts=now - 3000))
    step("cleanup-1", "Clean up this PC", "Cleaned up the PC", "cleaner", "C", "verified", 0, 600, "12 to the Recycle Bin, 340 deleted for good",
         {"kind": "cleanup", "items": [{"path": "C:\\Users\\me\\Downloads\\a.iso", "size": 9}], "more": 4, "permanent": {"count": 340, "bytes": 2_100_000_000}})
    step("t-last", "What is on my screen", "Look at which windows are open", "whats_on_screen", "A", "verified", 210, 90, "4 windows", None, None)
    assert s1
    build("small", j)

    # ---- the same journal, with the owner's words long and a task that failed through ----
    j2 = J.Journal(work / "long", clock=lambda: now)
    j2.append(J.make_step(task_id="t-long", goal="Open the invoices folder in Downloads, find the March invoice from the electricity board, read it and tell me the amount due and the date it is due by",
                          step_label="Troubleshoot Visual Studio Code with a very long application name", tool="troubleshoot", cls="C", verified="failed", duration_ms=95_000,
                          ts=now - 120, result="could not finish: the cause is a hypothesis and the fix needs the owner's PIN and a restart of the application and more words than fit"))
    build("long", j2)

    # ---- three thousand steps: the list must stay light ----
    j3 = J.Journal(work / "many", clock=lambda: now, rotate_at=10**9, journal_cap=10**12)
    for t in reversed(range(150)):                       # a journal is written oldest first: task 149 is the oldest, task 0 the newest
        for i in reversed(range(20)):
            tool = ("open_app", "check_pc", "window", "type_text")[i % 4]
            j3.append(J.make_step(task_id=f"task-{t:03d}", goal=f"Task number {t}: do twenty small things on the PC", step_label=f"Step {i + 1} of task {t}", tool=tool,
                                  cls="A" if tool == "check_pc" else "B", verified="failed" if (t * 20 + i) % 17 == 0 else ("unconfirmed" if (t + i) % 11 == 0 else "verified"),
                                  duration_ms=200 + 37 * i, ts=now - 20 - (t * 20 + i) * 3, result="done",
                                  undo=U.recipe_close_window("x", 1, 0) if tool == "open_app" else None))
    build("many", j3, cap=3000)

    build("empty", J.Journal(work / "empty", clock=lambda: now))
    build("unavailable", None)

    # what the server answers to the owner's clicks (the real report shape)
    fixtures["undo"]["ok"] = {"structuredContent": T.Timeline._report_view({"ok": True, "undone": 1, "not_undone": 0, "summary": "Undone: Open Notepad.",
                                                                           "results": [{"label": "Open Notepad", "status": "undone", "reason": "the window is closed"}]})}
    fixtures["undo"]["mixed"] = {"structuredContent": T.Timeline._report_view({"ok": False, "undone": 1, "not_undone": 1, "summary": "Undone: Saved a .txt file. Not undone: Open Notepad (the window seems to have unsaved work, so JARVIS did not close it).",
                                                                              "results": [{"label": "Saved a .txt file", "status": "undone", "reason": "it is in the Recycle Bin"},
                                                                                          {"label": "Open Notepad", "status": "refused", "reason": "the window seems to have unsaved work, so JARVIS did not close it"}]})}
    out.write_text(json.dumps(fixtures), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
