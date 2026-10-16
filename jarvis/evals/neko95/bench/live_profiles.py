"""Fake Chrome profiles, real Chrome: Neko's profile handling (jarvis_node.browserext registry and resolver, the desktop
decider's account -> profile step) against a REAL Chrome "User Data" folder with four made-up profiles, plus isolation
and persistence of each profile's session on a local fake account site.

    NEKO_DEFAULT_ACCOUNT_PREFIX=rivermoon python jarvis/evals/neko95/bench/live_profiles.py

Headless Google Chrome only (no window, no display, nothing on the desktop); every file lives in a temporary folder
that is deleted afterwards; the "accounts" are made up and the site is local. Windows-only parts (the owner's real
profiles, the Playwright MCP extension, Chrome's toolbar avatar via UI Automation) stay deferred to the laptop.
Results: results/live-profiles-<time>.json (key "cases").
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("NEKO_DEFAULT_ACCOUNT_PREFIX", "rivermoon")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
for p in (ROOT, ROOT / "jarvis" / "node", ROOT / "jarvis" / "kernel"):
    sys.path.insert(0, str(p))
from playwright.sync_api import sync_playwright  # noqa: E402
from jarvis_node import browserext as BX  # noqa: E402
from plugin.plugins.desktop import core, decide  # noqa: E402

FAKE = {  # folder -> (display name, made-up account, site user)
    "Default": ("Robin home", "rivermoon@example.com", "rivermoon"),
    "Profile 1": ("Kiran", "kiran.k@example.org", "kiran"),
    "Profile 2": ("Robin work", "robin.work@example.net", "robinwork"),
    "Profile 3": ("Guest test", "", "guest"),
}


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        c = SimpleCookie(self.headers.get("Cookie", ""))
        user = c["user"].value if "user" in c else ""
        extra = []
        if u.path == "/login":
            user = q.get("user", [""])[0]
            extra.append(f"user={user}; Path=/; Max-Age=86400")
        elif u.path == "/logout":
            user = ""
            extra.append("user=; Path=/; Max-Age=0")
        body = f"<title>Fake account site</title><h1 id=who>{('signed in as ' + user) if user else 'not signed in'}</h1>".encode()
        self.send_response(200)
        for e in extra:
            self.send_header("Set-Cookie", e)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    work = tempfile.mkdtemp(prefix="neko-fake-profiles-")
    ud = os.path.join(work, "User Data")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{srv.server_address[1]}"
    rows = []

    def rec(cid, title, ok, observed, dims, risk="high"):
        rows.append({"id": cid, "area": "C", "title": title, "risk": risk, "dims": dims, "status": "pass" if ok else "fail",
                     "observed": str(observed)[:400]})
        print(("PASS " if ok else "FAIL ") + cid, title, "|", str(observed)[:170], flush=True)

    def who(pw, folder, path="/"):
        ctx = pw.chromium.launch_persistent_context(ud, channel="chrome", headless=True, args=[f"--profile-directory={folder}"])
        try:
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.goto(site + path)
            return pg.inner_text("#who")
        finally:
            ctx.close()

    try:
        with sync_playwright() as pw:
            # 1. Chrome itself creates the four profiles and each signs in to the fake site
            for folder, (_n, _a, user) in FAKE.items():
                got = who(pw, folder, f"/login?user={user}")
            made = sorted(d for d in os.listdir(ud) if d == "Default" or d.startswith("Profile "))
            rec("CP-001", "Chrome creates four separate profile folders", made == sorted(FAKE), f"folders {made}", ["browser"], "medium")
            # name the profiles like a signed-in Chrome would (Local State info_cache), with made-up accounts
            ls_path = os.path.join(ud, "Local State")
            ls = json.load(open(ls_path, encoding="utf-8"))
            cache = ls.setdefault("profile", {}).setdefault("info_cache", {})
            for folder, (name, account, _u) in FAKE.items():
                cache.setdefault(folder, {}).update({"name": name, "user_name": account, "gaia_name": name if account else ""})
            json.dump(ls, open(ls_path, "w", encoding="utf-8"))
            # 2. registry built by Neko's own code
            reg_path = os.path.join(work, "browser_profiles.json")
            reg = BX.build_registry(ls_path, path=reg_path)
            text = open(reg_path, encoding="utf-8").read()
            leaked = [a for _n, a, _u in FAKE.values() if a and a in text]
            rec("CP-002", "Registry lists every profile, addresses masked", len(reg["profiles"]) == 4 and not leaked,
                f"{len(reg['profiles'])} profiles; full addresses in file: {leaked}", ["browser", "security"], "critical")
            ids = {p["dir"]: p["account_id"] for p in reg["profiles"]}
            rec("CP-003", "Accounts get distinct one-way fingerprints", len({v for v in ids.values() if v}) == 3,
                f"{ids}", ["browser", "security"], "medium")
            # 3. resolving what the owner says
            cases = [("kiran", "Profile 1"), ("Robin work", "Profile 2"), ("rivermoon", "Default"), ("default", "Default"),
                     ("guest test", "Profile 3")]
            got = []
            for alias, want in cases:
                try:
                    got.append((alias, BX.resolve(alias, reg)["dir"], want))
                except BX.BrowserError as e:
                    got.append((alias, f"error: {e}"[:60], want))
            rec("CP-004", "Each alias resolves to its own profile", all(g == w for _a, g, w in got), f"{got}", ["browser", "intent"], "critical")
            try:
                BX.resolve("robin", reg)
                amb = "resolved (wrong)"
            except BX.BrowserError as e:
                amb = str(e)
            rec("CP-005", "An ambiguous name asks which profile (never guesses)", "ambiguous" in amb and "Robin home" in amb and "Robin work" in amb,
                amb, ["browser", "intent", "security"], "critical")
            try:
                BX.resolve("satya", reg)
                unk = "resolved (wrong)"
            except BX.BrowserError as e:
                unk = str(e)
            rec("CP-006", "An unknown name is refused with the known profiles", unk.startswith("no Chrome profile matches"), unk[:150],
                ["browser", "intent"], "high")
            leaked_err = [a for _n, a, _u in FAKE.values() if a and (a in amb or a in unk)]
            rec("CP-007", "Errors never show a full account address", not leaked_err, f"leaked {leaked_err}", ["security", "ux"], "critical")
            # 4. the decider's account step (model plan given, as the local model would write it)
            profiles = core.profiles(Path(reg_path))
            plan = decide.validate(json.dumps({"steps": [{"tool": "open_website", "site": "github", "account": "Kiran"}]}), profiles,
                                   "open github in Kiran's profile")
            prof = [s.get("profile") for s in plan.steps if s.get("profile")]
            rec("CP-008", "Plan for 'open github in Kiran's profile' uses Kiran's folder and checks the account first",
                plan.status == "ok" and prof and all(p == "Profile 1" for p in prof) and plan.steps[0].get("tool") == "browser",
                f"status {plan.status}; steps {[(s.get('tool'), s.get('action'), s.get('profile')) for s in plan.steps]}", ["browser", "planning"], "critical")
            plan = decide.validate(json.dumps({"steps": [{"tool": "open_website", "site": "gmail", "account": "robin"}]}), profiles,
                                   "open gmail in robin's profile")
            rec("CP-009", "Plan with an ambiguous account asks instead of acting", plan.status == "clarify",
                f"status {plan.status}; question {plan.question[:100]!r}", ["browser", "planning", "security"], "critical")
            # 5. isolation: each profile sees only its own session
            seen = {folder: who(pw, folder) for folder in FAKE}
            rec("CP-010", "Each profile is signed in as its own account only",
                all(seen[f] == f"signed in as {FAKE[f][2]}" for f in FAKE), f"{seen}", ["browser", "security"], "critical")
            # 6. signing out in one profile does not touch the others; sessions persist across restarts
            who(pw, "Profile 1", "/logout")
            after = {folder: who(pw, folder) for folder in FAKE}
            rec("CP-011", "Signing out in Kiran's profile leaves the other profiles signed in",
                after["Profile 1"] == "not signed in" and all(after[f] == f"signed in as {FAKE[f][2]}" for f in FAKE if f != "Profile 1"),
                f"{after}", ["browser", "security"], "critical")
            # 7. a profile already in use: the second launch must fail cleanly, not hang or open another profile
            ctx = pw.chromium.launch_persistent_context(ud, channel="chrome", headless=True, args=["--profile-directory=Default"])
            t0 = time.time()
            try:
                ctx2 = pw.chromium.launch_persistent_context(ud, channel="chrome", headless=True, args=["--profile-directory=Profile 2"], timeout=20000)
                second = "second launch succeeded"
                ctx2.close()
            except Exception as e:  # noqa: BLE001
                second = f"refused: {type(e).__name__}"
            finally:
                ctx.close()
            rec("CP-012", "A User Data folder already in use is reported, not hung on", second.startswith("refused") and time.time() - t0 < 25,
                f"{second} after {time.time() - t0:.1f} s", ["browser", "reliability"], "medium")
    finally:
        srv.shutdown()
        shutil.rmtree(work, ignore_errors=True)
    counts = {s: sum(r["status"] == s for r in rows) for s in ("pass", "fail")}
    print(json.dumps(counts))
    out = HERE.parent / "results" / f"live-profiles-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "counts": counts, "cases": rows}, indent=1), encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    main()
