"""Every Chrome profile, live, through the real plugin -> console -> kernel -> node -> Playwright extension (2026-10-16).
Windows, agent-venv.  python jarvis\\tests\\live_profiles.py [--only default,venkatamv]

Per profile: open example.com in it, approve the extension's connection for that tab (the owner's manual approval, done
by UI Automation on the owner's instruction), then ask which Google account the visible browser is signed in to and
compare it with the profile's own account (by fingerprint). Prints one row per profile; nothing is changed in any account.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
from live_check import run_entry  # noqa: E402

ALLOW = str(HERE.parent / "scripts" / "allow_playwright_connection.ps1")
PROFILES = [("Default", "default"), ("Profile 1", "venkatamv"), ("Profile 2", "satish"), ("Profile 4", "profile4"),
            ("Profile 5", "profile5"), ("Profile 6", "logicmeshserver")]


def one(folder, alias):
    row = {"profile": folder, "alias": alias}
    o = run_entry("desktop", "open_website", {"url": "https://example.com/", "profile": alias}, wait=120)
    od = o.get("data") or {}
    row["opened_in"] = od.get("profile")
    row["opened_right_profile"] = od.get("profile") == folder
    time.sleep(2)
    helper = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ALLOW, "-TabUrl", "https://example.com/",
                               "-TimeoutS", "90"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    w = run_entry("desktop", "run_browser_action", {"action": "whoami", "profile": alias}, wait=200)
    try:
        row["connection_prompt"] = (helper.communicate(timeout=100)[0] or "").strip().splitlines()[-1:]
    except subprocess.TimeoutExpired:
        helper.kill()
        row["connection_prompt"] = ["helper timed out"]
    wd = w.get("data") or {}
    row.update(connected=bool(wd.get("ok")) or bool(wd.get("seen")), expected=wd.get("expected"), seen=wd.get("seen"),
               account_verified=wd.get("matches"), signed_in_first=wd.get("first_is_expected"),
               error=wd.get("error") or w.get("error"))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    want = set(x.strip() for x in a.only.split(",") if x.strip())
    rows = [one(f, al) for f, al in PROFILES if not want or al in want]
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    ok = all(r["opened_right_profile"] and r["account_verified"] for r in rows)
    print("RESULT", "PASS" if ok else "NOT ALL VERIFIED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
