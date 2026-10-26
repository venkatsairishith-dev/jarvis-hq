"""Fixtures for scripts/ask-check.mjs: realistic forms built by the real backend (jarvis.bridge.ask*, ui_patterns, cleaner_ui) on a fake PC, written
out as the tool results the face receives from jarvis_ask_wanted. So the whole chain is exercised: Python spec -> cleaned -> panel logic -> page.

    python make_fixtures.py OUT.json
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))

from jarvis.bridge import ask as A  # noqa: E402
from jarvis.bridge import ui_patterns as U  # noqa: E402
from jarvis.bridge.cleaner import BIG_FILE_BYTES, Cleaner, Env  # noqa: E402
from jarvis.bridge.cleaner_ui import CleanerService  # noqa: E402

DAY = 86400


class Bin:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def trash(self, path):
        self.n += 1
        os.replace(path, self.folder / f"{self.n}-{os.path.basename(path)}")

    def bin_stats(self):
        return 412 * 1024 * 1024, 38

    def bin_empty(self):
        return True


def sparse(path: Path, size: int, age_days: float, content: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
        if size > len(content):
            f.truncate(size)
    t = time.time() - age_days * DAY
    os.utime(path, (t, t))


def pc(root: Path) -> tuple[Env, Bin]:
    me = root / "Users" / "me"
    env = Env(home=me, user_temp=[me / "AppData" / "Local" / "Temp"], windows=root / "Windows", local_appdata=me / "AppData" / "Local", appdata=me / "AppData" / "Roaming",
              program_files=[root / "Program Files"], program_data=root / "ProgramData", users_root=root / "Users", protected=[root / "jarvis", root / "jarvis-secrets"])
    mb = 1024 * 1024
    for rel, size, age in [("AppData/Local/Temp/setup-cache.tmp", 842 * mb, 3), ("AppData/Local/Temp/render.tmp", 120 * mb, 2),
                           ("AppData/Local/Google/Chrome/User Data/Default/Cache/data_1", 1480 * mb, 5), ("AppData/Local/Microsoft/Edge/User Data/Default/Cache/data_2", 310 * mb, 5),
                           ("AppData/Roaming/discord/Cache/f_0001", 205 * mb, 5), ("AppData/Roaming/Code/CachedData/a", 96 * mb, 5),
                           ("AppData/Local/Microsoft/Windows/Explorer/thumbcache_256.db", 64 * mb, 5), ("AppData/Local/CrashDumps/app.dmp", 380 * mb, 30),
                           ("AppData/Local/pip/Cache/wheel", 540 * mb, 5), ("AppData/Local/npm-cache/_cacache/x", 770 * mb, 5)]:
        sparse(me / rel, size, age)
    for rel, size, age in [("Windows/Temp/log.tmp", 40 * mb, 4), ("Windows/SoftwareDistribution/Download/upd.cab", 1900 * mb, 20)]:
        sparse(root / rel, size, age)
    for i in range(160):                                               # 160 duplicates of one report: the review list is long
        sparse(me / "Downloads" / f"quarterly-report-final-copy ({i}).pdf", 3 * mb, 60 - i % 40 if i else 1, b"r" * 4096)
    for i in range(160):
        sparse(me / "Downloads" / f"installer-v{i}.exe", (20 + i) * mb, 45 + i)
    for i in range(160):
        (me / "Documents" / f"empty-{i:03d}").mkdir(parents=True, exist_ok=True)
    sparse(me / "Videos" / "recording-2025.mkv", BIG_FILE_BYTES + 700 * mb, 400)
    return env, Bin(root / "bin")


def as_result(broker: A.AskBroker, seen: int = 0) -> dict:
    got = broker.wanted(seen)
    return {"structuredContent": got}


def steps_for(broker: A.AskBroker, spec: dict) -> dict:
    broker.show(spec)
    return as_result(broker)


def main(out: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="ask-fixtures-"))
    env, trash = pc(work / "pc")
    home = env.home
    for name in ("Downloads", "Documents", "Desktop", "Pictures"):
        (home / name).mkdir(parents=True, exist_ok=True)
    palette = [(200, 90, 70), (70, 130, 200), (90, 170, 110), (210, 170, 70), (150, 100, 190), (60, 160, 170)]
    for i, color in enumerate(palette):
        im = Image.new("RGB", (640, 420), color)
        d = ImageDraw.Draw(im)
        d.ellipse((200, 80, 440, 320), fill=tuple(min(255, c + 50) for c in color))
        d.rectangle((0, 330, 640, 420), fill=tuple(max(0, c - 60) for c in color))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        (home / "Pictures" / f"holiday-{i + 1}.jpg").write_bytes(buf.getvalue())
    for name, size in [("invoice-march-2026.pdf", 182_000), ("Q3 board deck.pptx", 4_200_000), ("flights-hyderabad.pdf", 96_000), ("tax-notes.docx", 31_000), ("scan-0042.png", 1_300_000)]:
        folder = {"invoice-march-2026.pdf": "Downloads", "Q3 board deck.pptx": "Documents", "flights-hyderabad.pdf": "Downloads", "tax-notes.docx": "Documents", "scan-0042.png": "Desktop"}[name]
        sparse(home / folder / name, size, 1 + len(name) % 5)
    fixtures: dict[str, dict] = {}

    def solo(name: str, spec: dict) -> None:
        broker = A.AskBroker(home=home)
        fixtures[name] = steps_for(broker, spec)

    # the cleaner in each of its states
    broker = A.AskBroker(home=home)
    cleaner = Cleaner(env, trash)
    svc = CleanerService(cleaner, broker, spawn=lambda fn: None)
    svc._open_form()
    svc.phase, svc.rows = "scanning", []
    broker.update(svc.form_id, svc.spec(), busy=True, status="Looking for things to clean...", progress={"done": 3, "total": 13, "label": "Browser cache"})
    fixtures["cleanup-scanning"] = as_result(broker)
    svc.spawn = lambda fn: fn()
    svc.phase = "idle"
    svc.idle.set()
    svc.open("open the cleaner", wait_s=0)
    fixtures["cleanup-ready"] = as_result(broker)
    svc.safe = {r["id"] for r in svc.rows if r["kind"] == "safe" and r["id"] != "recycle_bin"}
    svc.items = {f"old_installers:{i['id']}" for r in svc.rows if r["id"] == "old_installers" for i in r["items"][:6]}
    svc._push()
    fixtures["cleanup-ticked"] = as_result(broker)
    svc.phase = "cleaning"
    broker.update(svc.form_id, svc.spec(), busy=True, status="Cleaning Browser cache",
                  progress={"done": 5, "total": 13, "bytes_done": 3_100_000_000, "bytes_total": 6_900_000_000, "label": "Browser cache"})
    fixtures["cleanup-cleaning"] = as_result(broker)
    svc.phase = "ready"
    svc.idle.set()
    svc._start_clean()
    fixtures["cleanup-done"] = as_result(broker)

    solo("radio", U.build("clarify", {"question": "Which one: notepad or wordpad?"}))
    solo("file", U.build("clarify", {"question": "Which file?"}))
    solo("text", U.build("text", {"title": "Name the folder", "question": "What should the new folder be called? " + "It will be created inside Documents and shared with the project. " * 4,
                                  "multiline": True, "placeholder": "For example: Q4 planning"}))
    solo("gallery", U.build("pick_file", {"source": "pictures", "question": "Which picture should I set as the wallpaper?"}))
    solo("cards", U.build("choose", {"title": "Pick a plan", "question": "How should I back up the laptop?", "options": [
        {"id": "cloud", "label": "Cloud backup", "detail": "Encrypted, off-site", "pros": ["Safe if the laptop is lost", "Automatic"], "cons": ["Needs internet", "Uses free-tier space"], "badge": "Suggested"},
        {"id": "disk", "label": "External disk", "detail": "Plug in once a week", "pros": ["Fast", "No account"], "cons": ["You must remember it"]},
        {"id": "both", "label": "Both", "detail": "Belt and braces", "pros": ["Two copies"], "cons": ["Takes the longest"]}]}))
    solo("code", U.build("code", {"title": "Here is the fix", "language": "python", "can_type": True, "explanation": "The loop ran one step too far because range(len(x)) already stops at the last index. "
                                  "I changed the bound and added a guard for an empty list.",
                                  "code": "def last_items(x, n):\n    if not x:\n        return []\n    out = []\n    for i in range(len(x) - n, len(x)):\n        out.append(x[i])\n    return out\n\n\nprint(last_items([1, 2, 3, 4], 2))\n"}))
    solo("steps", U.build("job", {"title": "Building the Q3 deck", "intro": "This takes about a minute.", "value": 55, "max": 100, "label": "Step 3 of 5", "steps": [
        {"label": "Search the web for sources", "status": "done", "detail": "6 sources"}, {"label": "Write the outline", "status": "done"},
        {"label": "Make the slides", "status": "running", "detail": "slide 7 of 12"}, {"label": "Check the layout", "status": "pending"}, {"label": "Save to Documents", "status": "pending"}]}))
    solo("settings", U.build("settings", {"title": "Sound and focus", "intro": "Changes apply at once. Undo puts them back.", "items": [
        {"id": "vol", "label": "Volume", "kind": "slider", "value": 40, "unit": "%"}, {"id": "bright", "label": "Brightness", "kind": "slider", "value": 70, "unit": "%"},
        {"id": "dnd", "label": "Do not disturb", "kind": "toggle", "value": True}, {"id": "night", "label": "Night light", "kind": "toggle", "value": False},
        {"id": "out", "label": "Output", "kind": "choice", "value": "spk", "options": [{"id": "spk", "label": "Speakers"}, {"id": "hp", "label": "Headphones"}, {"id": "bt", "label": "Bluetooth speaker"}]},
        {"id": "name", "label": "Device name", "kind": "text", "value": "Venkat's laptop"}]}))
    solo("compare", U.build("compare", {"title": "Laptops under $1000", "question": "Sort by a column, then choose one.", "columns": [{"id": "name", "label": "Model"}, {"id": "price", "label": "Price", "align": "right"},
                                        {"id": "ram", "label": "RAM", "align": "right"}, {"id": "bat", "label": "Battery", "align": "right"}],
                                        "rows": [{"id": f"m{i}", "cells": {"name": n, "price": f"${p}", "ram": f"{r} GB", "bat": f"{b} h"}} for i, (n, p, r, b) in enumerate(
                                            [("Aero 14", 899, 16, 12), ("Slate Pro", 949, 16, 15), ("Studio Lite", 799, 8, 10), ("Forge 15", 989, 32, 8)])]}))
    solo("data", U.build("data_view", {"title": "Disk use by month", "kind": "bar", "unit": "GB", "labels": ["May", "Jun", "Jul", "Aug", "Sep", "Oct"],
                                       "series": [{"name": "Used", "values": [210, 232, 251, 262, 301, 338]}, {"name": "Cache", "values": [40, 52, 61, 70, 96, 128]}]}))
    solo("confirm", U.build("confirm", {"title": "Send the report?", "rows": [["To", "Asha Rao"], ["File", "Q3 board deck.pptx"], ["Size", "4.0 MB"]], "warn": "This sends the file outside your PC.", "approve_label": "Send it"}))
    solo("schedule", U.build("schedule", {"title": "Remind me", "note": "When should I remind you about the dentist?"}))
    solo("save", U.build("save", {"what": "the Q3 report"}))
    # D63: forms used by voice. An editable heading (the reminder text, the file name), a date, a time, a choice, a note; and the settings page.
    solo("voice-schedule", U.build("schedule", {"title": "Remind me", "what": "Call the dentist", "note": "Say or type the reminder, the day and the time."}))
    solo("voice-save", U.build("save", {"what": "the Q3 deck", "file_name": "Q3 board deck"}))
    solo("fallback", {"title": "Odd form", "fields": [{"id": "x", "type": "hologram", "label": "Spin it", "options": [{"id": "a", "label": "Left"}, {"id": "b", "label": "Right"}]},
                                                       {"type": "callout", "text": "The rest of the form still works."}]})
    out.write_text(json.dumps(fixtures), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
