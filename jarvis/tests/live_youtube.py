"""YouTube end to end in the owner's default profile (2026-10-16): search, open a real result, check it is PLAYING (the
video's position moves, plus Windows' own media session), then pause it with the media key. Windows, agent-venv.
The extension's connect prompt, if it appears, is approved for the "Example Domain" tab (the owner's instruction)."""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
from live_check import run_entry  # noqa: E402

ALLOW = str(HERE.parent / "scripts" / "allow_playwright_connection.ps1")
q = sys.argv[1] if len(sys.argv) > 1 else "Kalyani raga song"
run_entry("desktop", "open_website", {"url": "https://example.com/", "profile": "default"}, wait=90)
time.sleep(2)
helper = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ALLOW, "-TabUrl", "https://example.com/", "-TimeoutS", "60"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
r = run_entry("desktop", "play_youtube", {"query": q, "profile": "default"}, wait=240)
prompt = (helper.communicate(timeout=90)[0] or "").strip()
d = r.get("data") or {}
print(json.dumps({"run": r.get("status"), "connect_prompt": prompt, **{k: d.get(k) for k in
                  ("ok", "chose", "title", "playing", "same_video", "position_s", "muted", "verified_by", "windows_media", "error")}},
                 ensure_ascii=False))
time.sleep(3)
m = run_entry("desktop", "check_pc", {"check": "media"}, wait=60).get("data") or {}
print("media sessions now:", [f"{s.get('title')} ({s.get('status')})" for s in m.get("sessions") or []])
p = {}
if any(s.get("status") == "Playing" for s in m.get("sessions") or []):        # the media key toggles: press it only to pause
    p = run_entry("desktop", "run_pc_action", {"action": "key", "target": "chrome|", "text": "playpause"}, wait=60).get("data") or {}
time.sleep(2)
m2 = run_entry("desktop", "check_pc", {"check": "media"}, wait=60).get("data") or {}
print("after the pause key:", p.get("state"), [f"{s.get('title')} ({s.get('status')})" for s in m2.get("sessions") or []])
title = str(d.get("title") or "").replace(" - YouTube", "")
ok = (bool(d.get("ok")) and bool(d.get("same_video")) and bool(title) and title.lower()[:40] in str(d.get("chose") or "").lower()
      and any(s.get("status") == "Playing" and title.lower()[:30] in str(s.get("title") or "").lower() for s in m.get("sessions") or []))
print("RESULT", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
