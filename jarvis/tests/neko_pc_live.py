"""Live checks of Neko's PC control on THIS laptop, through the real plugin, console, kernel and node (2026-10-16). Windows.

    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe jarvis\\tests\\neko_pc_live.py [--only 1,4,8]

Visible on screen: a Chrome tab with example.com in the default profile and in the VenkatAMV profile, a YouTube search
tab, and the Neko sample app's error dialog. Nothing is bought, sent, deleted or changed in an account: the one risky
action (a "Buy now" click) and the one repair are refused at the approval step by this script itself (deny is the safe
direction and needs no PIN). Prints PASS / FAIL / NOT TESTABLE per check with what was observed; exit 1 on a FAIL.
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
from live_check import jget, run_entry  # noqa: E402

CONSOLE = "http://127.0.0.1:8799/api/owner"
DATA = Path(r"D:\jarvis\N.E.K.O-data")
OUT = Path(r"D:\jarvis\outputs")
results = []


def report(n, name, verdict, detail):
    results.append((n, name, verdict, detail))
    print(f"[{verdict}] {n}. {name}: {detail}", flush=True)


def console(method, name, body=None, timeout=30):
    return jget(f"{CONSOLE}/{name}", method=method, body=body, timeout=timeout, headers={"X-Jarvis-Console": "1"})


def deny_next_approval(match, seen=None, wait=120):
    """Deny the first pending approval whose action mentions `match` (the safe direction; no PIN)."""
    end = time.time() + wait
    while time.time() < end:
        _, st = console("GET", "state")
        for a in (st or {}).get("approvals") or []:
            if match.lower() in json.dumps(a).lower():
                code, r = console("POST", "deny", {"id": a["id"]})
                if seen is not None:
                    seen.append({"id": a["id"], "level": a.get("level"), "denied": code == 200})
                return
        time.sleep(1.5)


def t1():
    r = run_entry("desktop", "open_website", {"url": "https://example.com/", "profile": "default"}, wait=120)
    d = r.get("data") or {}
    ok = r["status"] == "succeeded" and d.get("ok") and d.get("profile") == "Default"
    report(1, "open a site in the default profile", "PASS" if ok else "FAIL",
           f"run {r['status']}; profile {d.get('profile')} {d.get('account')}; window '{str(d.get('window') or d.get('title') or '')[:60]}'; "
           f"verified {d.get('verified')}; {d.get('error') or ''}")


def t2():
    r = run_entry("desktop", "open_website", {"url": "https://example.com/", "profile": "VenkatAMV"}, wait=120)
    d = r.get("data") or {}
    opened = r["status"] == "succeeded" and d.get("ok") and d.get("profile") == "Profile 1"
    amb = run_entry("desktop", "open_website", {"url": "https://example.com/", "profile": "Venkat"}, wait=60)
    ad = amb.get("data") or {}
    asked = "ambiguous" in str(ad.get("error") or amb.get("error") or "")
    who = run_entry("desktop", "run_browser_action", {"action": "whoami", "profile": "VenkatAMV"}, wait=120)
    wd = who.get("data") or {}
    verdict = "PASS" if opened and asked and wd.get("matches") else ("PARTIAL" if opened and asked else "FAIL")
    report(2, "switch profile and verify its account", verdict,
           f"opened in {d.get('profile')} ({d.get('account')}); Chrome's profile button shows '{d.get('profile_name_shown')}' "
           f"(matches {d.get('profile_name_matches')}; a display name only); 'Venkat' alone -> asked which one: {asked}; "
           f"account check: {wd.get('matches') if wd.get('ok') else (wd.get('error') or who.get('error'))}"[:400])


def t3():
    r = run_entry("desktop", "play_youtube", {"query": "Kalyani raga song", "profile": "default"}, wait=200)
    d = r.get("data") or {}
    m = run_entry("desktop", "check_pc", {"check": "media"}, wait=60).get("data") or {}
    playing = [f"{s.get('title')} ({s.get('status')})" for s in m.get("sessions") or []]
    if d.get("playing"):
        verdict = "PASS"
    elif d.get("opened_search"):
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"
    report(3, "YouTube: a result and an observable outcome", verdict,
           f"chose '{str(d.get('chose') or '')[:60]}', playing {d.get('playing')}; opened search {d.get('opened_search')}; "
           f"Windows media now: {playing or 'nothing'}; {str(d.get('error') or '')[:160]}")


def t4():
    os.system('taskkill /IM NekoSampleApp.exe /F >nul 2>&1')
    r = run_entry("desktop", "run_pc_action", {"action": "open_app", "target": "Neko Sample App"}, wait=60)
    time.sleep(3)
    code, j = console("POST", "pc", {"op": "diag", "check": "window_text", "target": "NekoSampleApp"})
    jid = (j or {}).get("job")
    res = {}
    for _ in range(60):
        time.sleep(1)
        _, res = console("GET", f"job/{jid}")
        if (res or {}).get("state") in ("done", "failed", "cancelled", "expired"):
            break
    rr = (res or {}).get("result") or {}
    local = rr.get("local_result")
    texts = []
    if local and os.path.isfile(local):
        win = json.load(open(local, encoding="utf-8"))
        os.remove(local)
        texts = [t for w in win if w.get("dialog") for t in w.get("texts") or []]
    server_has_text = "configuration" in json.dumps(rr).lower() or "80x80" in json.dumps(rr)
    ok = (r.get("data") or {}).get("ok") and (rr.get("summary") or {}).get("dialogs", 0) >= 1 and texts and not server_has_text
    report(4, "inspect a harmless sample error dialog", "PASS" if ok else "FAIL",
           f"app opened {(r.get('data') or {}).get('ok')}; dialogs {(rr.get('summary') or {}).get('dialogs')}; "
           f"text read locally: {' | '.join(texts)[:140]}; text sent to the server: {server_has_text}")


def t5():
    seen = []
    th = threading.Thread(target=deny_next_approval, args=("edit_file", seen), daemon=True)
    th.start()
    r = run_entry("desktop", "run_troubleshooter", {"app": "NekoSampleApp", "start_name": "Neko Sample App"}, wait=400)
    th.join(5)
    d = r.get("data") or {}
    steps = [s["state"] for s in d.get("steps") or []]
    cfg = open(r"D:\jarvis\tests\neko-sample-app\config.json", encoding="utf-8").read()
    ok = d.get("verified") and "line 3" in str(d.get("cause")) and seen and "80x80" in cfg and "not approved" in str(d.get("outcome"))
    report(5, "diagnose a reproducible app error; the fix waits for approval", "PASS" if ok else "FAIL",
           f"cause: {d.get('cause')} (verified {d.get('verified')}); proposed {d.get('fix')}; approval {seen}; outcome: {d.get('outcome')}; "
           f"steps {steps}; config unchanged: {'80x80' in cfg}")


def t6():
    seen = []
    th = threading.Thread(target=deny_next_approval, args=("buy now", seen), daemon=True)
    th.start()
    r = run_entry("desktop", "run_browser_action", {"action": "click", "profile": "default", "target": "Buy now"}, wait=200)
    th.join(5)
    d = r.get("data") or {}
    ok = bool(seen) and seen[0]["level"] == "YELLOW_APPROVAL" and d.get("state") == "denied"
    seen2 = []
    th = threading.Thread(target=deny_next_approval, args=("alt+f4", seen2), daemon=True)
    th.start()
    plain = run_entry("desktop", "run_pc_action", {"action": "key", "target": "notepad|", "text": "alt+f4"}, wait=200)
    th.join(5)
    ok = ok and bool(seen2) and (plain.get("data") or {}).get("state") == "denied"
    report(6, "risky actions wait for approval", "PASS" if ok else "FAIL",
           f"'Buy now' click -> {seen[0]['level'] if seen else 'no approval asked'}, denied -> state {d.get('state')}; "
           f"alt+f4 -> {seen2[0]['level'] if seen2 else 'no approval asked'}, denied -> {(plain.get('data') or {}).get('state') or plain.get('error')}")


def t7(since):
    pats = {"default account": re.compile(r"venkatgaminggem17@gmail\.com", re.I), "extension token": re.compile(r"PLAYWRIGHT_MCP_EXTENSION_TOKEN=\S"),
            "bearer": re.compile(r"Bearer [A-Za-z0-9._-]{16,}"), "groq key": re.compile(r"gsk_[A-Za-z0-9]{20,}"),
            "tavily key": re.compile(r"tvly-[A-Za-z0-9]{10,}"), "openai-style key": re.compile(r"\bsk-[A-Za-z0-9]{20,}")}
    hits = []
    files = [p for p in (DATA / "logs").glob("*.log")] + [DATA / "neko" / "browser_profiles.json", DATA / "neko" / "activity.json"]
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits += [f"{p.name}: {k}" for k, rx in pats.items() if rx.search(text)]
    _, ev = console("GET", "events?limit=400")
    audit = json.dumps(ev or {})
    hits += [f"kernel audit: {k}" for k, rx in pats.items() if rx.search(audit)]
    shots = [p.name for p in (OUT / "screens").glob("*.png") if p.stat().st_mtime >= since] if (OUT / "screens").is_dir() else []
    left = [p.name for p in (OUT / "neko-local").glob("*")] if (OUT / "neko-local").is_dir() else []
    ok = not hits and not shots and not left
    report(7, "no credential or screenshot leaks", "PASS" if ok else "FAIL",
           f"secret patterns found: {hits or 'none'}; new screenshots: {shots or 'none'}; local screen text left behind: {left or 'none'}")


def t8():
    _, j = console("POST", "pc", {"op": "diag", "check": "crashes"})
    jid = (j or {}).get("job")
    _, c = console("POST", "job/cancel", {"id": jid})
    final = {}
    for _ in range(30):
        _, final = console("GET", f"job/{jid}")
        if (final or {}).get("state") in ("done", "failed", "cancelled", "expired"):
            break
        time.sleep(1)
    ok = (final or {}).get("state") == "cancelled"
    report(8, "cancellation", "PASS" if ok else "FAIL",
           f"cancel answered '{(c or {}).get('state')}', job ended '{(final or {}).get('state')}' "
           "(a queued job never starts; a running one is killed by the node: unit-tested with a real process)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="1,2,3,4,5,6,7,8")
    a = ap.parse_args()
    since = time.time()
    want = {int(x) for x in a.only.split(",")}
    for n, fn in ((1, t1), (2, t2), (3, t3), (4, t4), (5, t5), (6, t6), (8, t8)):
        if n in want:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                report(n, fn.__name__, "FAIL", f"{type(e).__name__}: {e}"[:200])
    if 7 in want:
        t7(since)
    os.system('taskkill /IM NekoSampleApp.exe /F >nul 2>&1')
    print("SUMMARY", json.dumps({v: sum(1 for r in results if r[2] == v) for v in ("PASS", "PARTIAL", "FAIL", "NOT TESTABLE")}))
    return 1 if any(r[2] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
