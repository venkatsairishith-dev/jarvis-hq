"""E: error diagnosis and self-repair, against the real code: core.diagnose on real fixture files, real listeners and the
real disk; the node's repair.py on real files (backups in a temp folder; PowerShell/winget faked where they are Windows
programs); the kernel policy for repairs; the desktop troubleshooter end to end through a fake JARVIS console over real
local HTTP (the fake console stands in for the kernel and the laptop node, and runs the node's real repair code)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="bench-e-")
os.environ.setdefault("JARVIS_DATA_DIR", _TMP)
os.environ.setdefault("JARVIS_OUTPUTS", os.path.join(_TMP, "outputs"))

from jarvis.plugins.desktop import coordinator as C  # noqa: E402
from jarvis.plugins.desktop import core  # noqa: E402
from jarvis.plugins.desktop import decide as D  # noqa: E402
from jarvis.plugins.desktop import monitor as MON  # noqa: E402
from jarvis_kernel.policy import NAMES, Action, decide  # noqa: E402
from jarvis_node import repair, runtime  # noqa: E402

DRIVE = "C:" if os.name == "nt" else "/"


def _dir() -> Path:
    return Path(tempfile.mkdtemp(dir=_TMP))


def _home_dir() -> Path:
    """repair.py only writes in the user's own folder (or D:): fixtures live under ~ (HOME=/tmp in the test image)."""
    return Path(tempfile.mkdtemp(prefix="bench-e-", dir=os.path.expanduser("~")))


def _dialog(*texts, title="JARVIS Sample App - error"):
    return [{"title": title, "dialog": True, "buttons": ["OK"], "texts": list(texts)}]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _brief(d):
    return f"verified {d['verified']}, cause '{d['cause'][:140]}', fix {json.dumps(d.get('fix'))[:140]}"


# ---------------------------------------------------------------- diagnosis from real evidence
def diag_json(a):
    d = _dir()
    cfg = d / "config.json"
    cfg.write_text(a["content"], encoding="utf-8")
    path = str(cfg) if not a.get("missing") else str(d / "absent.json")
    if a.get("missing"):
        cfg.unlink()
    r = core.diagnose(_dialog("JARVIS Sample App could not start.", f"File: {path}"), [], [])
    leaked = "could not start" in r["cause"]
    w = a["want"]
    if w == "typo_fix":
        f = r.get("fix") or {}
        raw = a["content"]
        fixed = raw.replace(f.get("args", {}).get("find", "\0"), f.get("args", {}).get("replace", ""), 1)
        try:
            after = json.loads(fixed)
        except ValueError:
            after = None
        ok = r["verified"] and f"line {a['line']}" in r["cause"] and f.get("action") == "edit_file" and raw.count(f["args"]["find"]) == 1 \
            and after == a["after"] and f["target"] == str(cfg)
    elif w == "no_fix":
        ok = r["verified"] and f"line {a['line']}" in r["cause"] and r["fix"] is None
    elif w == "valid_now":
        ok = not r["verified"] and r["fix"] is None and any("valid JSON now" in e for e in r["evidence"]) and not leaked
    else:                                                     # the named file does not exist: nothing is claimed
        ok = not r["verified"] and r["fix"] is None and not leaked
    return ok, _brief(r) + f", dialog text in cause {leaked}"


def diag_port(a):
    d = _dir()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    cfg = d / "config.json"
    cfg.write_text(json.dumps({"name": "x", "port": port, "debug": False}, indent=1), encoding="utf-8")
    try:
        if a["listening"]:
            r = core.diagnose(_dialog(f"Port {port} is already in use by another program.", f"File: {cfg}"), [], [])
            f = r.get("fix") or {}
            new = int(f.get("args", {}).get("replace", '"port": 0').split(":")[1])
            probe = socket.socket()
            try:
                probe.bind(("127.0.0.1", new))
                free = True
            except OSError:
                free = False
            finally:
                probe.close()
            ok = r["verified"] and f"port {port}" in r["cause"] and str(os.getpid()) in r["cause"] and f["args"]["find"] == f'"port": {port}' \
                and new != port and free
            return ok, _brief(r) + f", proposed port {new} free {free}"
    finally:
        s.close()
    r = core.diagnose(_dialog(f"Port {port} is already in use by another program."), [], [])
    return not r["verified"] and "hypothesis" in r["cause"] and r["fix"] is None, _brief(r)


def diag_disk(a):
    need = a["need_mb"]
    r = core.diagnose(_dialog(f"There is not enough space on the disk: it needs {need} MB on {a.get('drive', DRIVE)}"), [], [])
    if a["want"] == "verified":
        ok = r["verified"] and r["fix"] is None and r["needs_mb"] == need and "owner decides" in r.get("advice", "")
    elif a["want"] == "unchecked":
        ok = not r["verified"] and "could not be checked" in r["cause"] and "None" not in r["cause"]
    else:
        ok = not r["verified"] and "hypothesis" in r["cause"] and r["fix"] is None
    return ok, _brief(r) + f", free {r.get('free_mb')} MB"


def diag_service_down(a):
    port = _free_port()
    r = core.diagnose(_dialog(f"Could not connect to the service at 127.0.0.1:{port} (connection refused)."), [], [])
    ok = r["verified"] and "nothing is listening" in r["cause"] and r["fix"] is None and "start" in r.get("advice", "")
    return ok, _brief(r) + f", advice '{r.get('advice')}'"


def diag_dependency(a):
    d = _dir()
    if a["kind"] == "dll_absent":
        r = core.diagnose(_dialog("The code execution cannot proceed because JARVISRuntime140.dll was not found."), [], [], app_dir=str(d))
        ok = r["verified"] and r.get("dependency") == "JARVISRuntime140.dll" and r["fix"] is None
    elif a["kind"] == "dll_present":
        (d / "JARVISRuntime140.dll").write_bytes(b"MZ")
        r = core.diagnose(_dialog("The code execution cannot proceed because JARVISRuntime140.dll was not found."), [], [], app_dir=str(d))
        ok = not r["verified"] and any("present after all" in e for e in r["evidence"]) and "hypothesis" in r["cause"]
    else:
        r = core.diagnose(_dialog("Traceback: ModuleNotFoundError: No module named 'sample_lib'"), [], [])
        ok = not r["verified"] and "hypothesis" in r["cause"] and r.get("dependency") == "sample_lib"
    return ok, _brief(r) + f", evidence {r['evidence'][:2]}"


def diag_crash_only(a):
    if a["kind"] == "crash":
        r = core.diagnose([], [{"provider": "Application Error"}], [{"app": "JARVISSampleApp.exe", "kind": "crash", "module": "ucrtbase.dll", "code": "c0000409"}])
        ok = not r["verified"] and "hypothesis" in r["cause"] and any("crash record" in e for e in r["evidence"]) \
            and any("1 recent error event" in e for e in r["evidence"])
    else:
        r = core.diagnose([{"title": "JARVIS Sample App", "dialog": False, "texts": ["Ready"]}], [], [])
        ok = not r["verified"] and r["cause"].startswith("no error dialog") and r["fix"] is None
    return ok, _brief(r)


# ---------------------------------------------------------------- repairs (node): backup first, verify after
class _Backups:
    def __enter__(self):
        self.old, self.dir = repair.BACKUPS, _home_dir()
        repair.BACKUPS = str(self.dir / "backups")
        return self

    def __exit__(self, *exc):
        repair.BACKUPS = self.old
        shutil.rmtree(self.dir, ignore_errors=True)

    def count(self):
        return len(os.listdir(repair.BACKUPS)) if os.path.isdir(repair.BACKUPS) else 0


def _cfg(b, text='{\n  "name": "sample",\n  "port": 80x80\n}\n'):
    p = b.dir / "app" / "config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def repair_edit(a):
    with _Backups() as b:
        p = _cfg(b)
        before = p.read_bytes()
        r = repair.edit_file(str(p), '"port": 80x80', '"port": 8080')
        bdir = Path(repair.BACKUPS) / r.get("backup", "?")
        m = json.loads((bdir / "manifest.json").read_text(encoding="utf-8")) if bdir.is_dir() else {}
        ok = r["ok"] and r["verified_on_disk"] and (bdir / "file.bak").read_bytes() == before \
            and m.get("sha256") == hashlib.sha256(before).hexdigest() and json.loads(p.read_text())["port"] == 8080 \
            and r["undo"] == f"repair restore {r['backup']}"
        return ok, f"ok {r['ok']}, backup equals the original {bdir.is_dir() and (bdir / 'file.bak').read_bytes() == before}, undo '{r.get('undo')}'"


def repair_restore(a):
    with _Backups() as b:
        p = _cfg(b)
        r = repair.edit_file(str(p), '"port": 80x80', '"port": 8080')
        back = repair.restore(r["backup"])
        restored = "80x80" in p.read_text()
        again = repair.restore(back["undo"].split()[-1])
        ok = back["ok"] and back["verified_on_disk"] and restored and again["ok"] and json.loads(p.read_text())["port"] == 8080
        return ok, f"restore {back['ok']} (original back {restored}), undo of the restore {again['ok']}"


def repair_refusals(a):
    with _Backups() as b:
        p = _cfg(b, '{"a": "x", "b": "x"}\n')
        before = p.read_bytes()
        amb = repair.edit_file(str(p), '"x"', '"y"')
        empty = repair.edit_file(str(p), "", "z")
        outside = repair.edit_file("/etc/hostname", "a", "b")
        os.makedirs(repair.BACKUPS, exist_ok=True)
        inside_backups = Path(repair.BACKUPS) / "x.json"
        inside_backups.write_text("{}", encoding="utf-8")
        bk = repair.edit_file(str(inside_backups), "{}", "[]")
        bad_id = repair.restore("../../etc")
        ok = not any(x["ok"] for x in (amb, empty, outside, bk, bad_id)) and "2 times" in amb["error"] and p.read_bytes() == before \
            and b.count() == 1                                    # only the file this test put there; no backup was written
        return ok, f"ambiguous: {amb['error'][:60]}; empty find: {empty['error'][:40]}; /etc: {outside['error'][:40]}; backups dir: {bk['error'][:30]}; bad id: {bad_id['error']}; file unchanged {p.read_bytes() == before}"


def repair_tampered(a):
    with _Backups() as b:
        p = _cfg(b)
        r = repair.edit_file(str(p), '"port": 80x80', '"port": 8080')
        (Path(repair.BACKUPS) / r["backup"] / "file.bak").write_text("evil", encoding="utf-8")
        t = repair.restore(r["backup"])
        return not t["ok"] and "does not match" in t["error"] and json.loads(p.read_text())["port"] == 8080, f"restore: {t}"


class _FakeProc:
    """Stands in for PowerShell and process start (Windows programs) around repair.restart_app."""

    def __init__(self, close, check):
        self.close, self.check, self.ps, self.started = close, check, [], []

    def __enter__(self):
        self.o = (runtime.powershell, runtime.popen)
        runtime.powershell = lambda script, env=None, timeout=30: (self.ps.append(script), subprocess.CompletedProcess(
            [], 0, json.dumps(self.close if script == repair.CLOSE else self.check), ""))[1]
        runtime.popen = lambda args, **kw: self.started.append(args)
        return self

    def __exit__(self, *exc):
        runtime.powershell, runtime.popen = self.o


def repair_restart(a):
    exe = _home_dir() / "JARVISSampleApp.exe"
    exe.write_bytes(b"MZ")
    try:
        m = a["mode"]
        if m == "protected":
            with _FakeProc({}, {}) as f:
                rs = [repair.restart_app(n) for n in ("lsass", "KeePass", "jarvis_node", "a;b")]
            return not any(r["ok"] for r in rs) and not f.ps and not f.started, f"refused {sum(not r['ok'] for r in rs)}/4, scripts run {len(f.ps)}"
        if m == "will_not_close":
            with _FakeProc({"running": True, "path": str(exe), "closed": False, "left": 1}, {}) as f:
                r = repair.restart_app("JARVISSampleApp")
            return not r["ok"] and "NOT forced" in r["error"] and not f.started, f"{r}, started {len(f.started)}"
        if m == "not_responding":
            with _FakeProc({"running": True, "path": str(exe), "closed": True, "left": 0}, {"running": True, "responding": False}) as f:
                r = repair.restart_app("JARVISSampleApp")
            return not r["ok"] and r["running"] and not r["responding"] and "not running and answering" in r["error"] and f.started == [[str(exe)]], f"{r}"
        with _FakeProc({"running": True, "path": str(exe), "closed": True, "left": 0}, {"running": True, "responding": True}) as f:
            r = repair.restart_app("JARVISSampleApp")
        return r["ok"] and f.started == [[str(exe)]] and repair.CHECK in f.ps, f"{r}"
    finally:
        shutil.rmtree(exe.parent, ignore_errors=True)


def repair_install(a):
    o_which, o_run = repair.shutil.which, runtime.run
    calls = []
    repair.shutil.which = lambda n: "/fake/winget" if n == "winget" else o_which(n)
    runtime.run = lambda args, timeout=30, **kw: (calls.append(args[1]), subprocess.CompletedProcess(
        args, 0, "No installed package found matching input criteria." if args[1] == "list" else "Installer cancelled", ""))[1]
    try:
        r = repair.install("JARVIS.SampleApp")
        bad = repair.install("x && calc")
    finally:
        repair.shutil.which, runtime.run = o_which, o_run
    ok = not r["ok"] and not r["listed_after"] and calls == ["install", "list"] and not bad["ok"]
    return ok, f"install ok {r['ok']} (listed after {r['listed_after']}), winget calls {calls}, injected id refused {not bad['ok']}"


def repair_policy(a):
    want = [("edit_file", "/home/owner/app/config.json", "YELLOW_APPROVAL"), ("restart_app", "JARVISSampleApp", "YELLOW_APPROVAL"),
            ("install", "JARVIS.SampleApp", "YELLOW_APPROVAL"), ("restore", "20261016-101500-abcdef", "YELLOW_AUTO"),
            ("edit_file", r"D:\apps\sample\settings.json", "RED"), ("edit_file", r"D:\jarvis-secrets\providers\x.key", "DENY"),
            ("uninstall", "JARVIS.SampleApp", "RED")]
    got = []
    for op, tgt, w in want:
        d = decide(Action("repair", op, tgt, {"find": "a", "replace": "b"}))
        got.append(NAMES[d.level] if isinstance(d.level, int) else str(d.level))
    bad = [f"{op} {tgt}: {g} (want {w})" for (op, tgt, w), g in zip(want, got) if g != w]
    return not bad, "; ".join(bad) or "all 7 repair actions at the expected level: " + ", ".join(got)


# ---------------------------------------------------------------- the troubleshooter, end to end
class _SampleApp:
    """The fake program: its main window shows only when its config parses; otherwise an error dialog names the file."""

    def __init__(self, cfg):
        self.cfg, self.up = cfg, True

    def windows(self):
        if not self.up:
            return []
        try:
            json.loads(self.cfg.read_text(encoding="utf-8"))
            return [{"title": "JARVIS Sample App", "class": "JARVISMain", "dialog": False, "texts": ["Ready"], "buttons": []}]
        except ValueError:
            return [{"title": "JARVIS Sample App - configuration error", "class": "#32770", "dialog": True, "buttons": ["OK"],
                     "texts": ["JARVIS Sample App could not start.", f"File: {self.cfg}"]}]


class _Console:
    def __init__(self, app, local, deny):
        self.app, self.local, self.deny = app, local, deny
        self.jobs, self.approvals, self.repairs, self.n = {}, 0, 0, 0

    def handle(self, method, path, body):
        name = path.split("/api/owner/", 1)[-1]
        if name == "state":
            return 200, {"approvals": []}                     # the owner has answered by the first look
        if name.startswith("job/") and method == "GET":
            return 200, {"id": name[4:], "state": "done", "result": self.jobs.get(name[4:], {"ok": False})}
        if name == "job/cancel":
            return 200, {"state": "cancelling"}
        if name != "pc":
            return 404, {}
        op = body.get("op")
        if op == "repair" and not body.get("approval_id"):
            self.approvals += 1
            return 202, {"status": "needs_approval", "approval_id": "a1", "level": "YELLOW_APPROVAL", "reason": "repairs need the owner"}
        if op == "repair":
            if self.deny:
                return 403, {"error": "refused by policy: approval denied"}
            self.repairs += 1
            res = repair.run({"op": body["action"], "target": body["target"], "args": body["args"]})     # the node's real repair
        elif op == "diag" and body.get("check") in ("window_text", "app_errors"):
            self.n += 1
            f = self.local / f"{body['check']}-{self.n}.json"
            f.write_text(json.dumps(self.app.windows() if body["check"] == "window_text" else []), encoding="utf-8")
            res = {"ok": True, "local_result": str(f)}
        elif op == "diag":
            res = {"ok": True, "rows": []}
        elif op == "ui" and body.get("action") == "click":
            self.app.up = False                               # OK on the error dialog: the program exits
            res = {"ok": True}
        elif op == "open_app":
            self.app.up = True
            res = {"ok": True, "opened": body.get("target"), "verified": "verified"}
        else:
            res = {"ok": False, "error": "not in this fixture"}
        jid = f"j{len(self.jobs) + 1}"
        self.jobs[jid] = res
        return 202, {"job": jid, "status": "queued"}


def _serve(console):
    # Like the real console (audit SEC-D9, 2026-10-18): its page carries a per-launch secret and a POST without it is
    # refused, so the desktop client must read the page first. The fake console behaves the same way.
    secret = "bench-secret-" + str(time.time_ns())

    class H(BaseHTTPRequestHandler):
        def _go(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if method == "GET" and self.path == "/":
                page = f'<html><meta name="jarvis-console-secret" content="{secret}"></html>'.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            if method == "POST" and self.headers.get("X-Jarvis-Console") != secret:
                code, out = 403, {"error": "refused: missing or stale console secret", "reload": True}
            else:
                code, out = console.handle(method, self.path, body)
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._go("GET")

        def do_POST(self):
            self._go("POST")

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def troubleshooter(a):
    from jarvis.plugins.desktop import DesktopPlugin
    with _Backups() as b:
        cfg = _cfg(b, a["config"])
        before = cfg.read_bytes()
        local = _dir() / "local-only"
        local.mkdir()
        console = _Console(_SampleApp(cfg), local, a.get("deny", False))
        srv = _serve(console)
        saved = core.CONSOLE, core.LOCAL_RESULTS, core.set_activity
        core.CONSOLE, core.LOCAL_RESULTS, core.set_activity = f"http://127.0.0.1:{srv.server_address[1]}", local, (lambda *x, **k: None)

        class Host:
            pass
        h = Host()
        h._run = lambda pc, body, timeout, what="": DesktopPlugin._run(h, pc, body, timeout, what)
        try:
            res = asyncio.run(DesktopPlugin.run_troubleshooter(h, app="JARVISSampleApp", start_name="JARVIS Sample App"))
        finally:
            core.CONSOLE, core.LOCAL_RESULTS, core.set_activity = saved
            srv.shutdown()
            srv.server_close()
        rep = getattr(res, "value", None) or {}
        states = [s["state"] for s in rep.get("steps", [])]
        left = list(local.iterdir())
        w = a["want"]
        if w == "fixed":
            ok = rep.get("outcome") == "fixed and verified" and rep.get("verified") and rep.get("backup") and console.approvals == 1 \
                and console.repairs == 1 and json.loads(cfg.read_text())["port"] == 8080 and rep["verification"]["error_dialogs_now"] == 0 \
                and (Path(repair.BACKUPS) / rep["backup"] / "file.bak").read_bytes() == before
        elif w == "denied":
            ok = rep.get("outcome", "").startswith("not approved") and cfg.read_bytes() == before and console.repairs == 0 and b.count() == 0
        else:
            ok = rep.get("outcome", "").startswith("fix applied, but the check failed") and console.repairs == 1 \
                and rep["verification"]["error_dialogs_now"] == 1 and rep.get("undo")
        ok = ok and "waiting_for_permission" in states and not left            # screen text read once and deleted
        return ok, f"outcome '{rep.get('outcome')}', approvals asked {console.approvals}, repairs run {console.repairs}, " \
                   f"states {states}, local files left {len(left)}, verification {rep.get('verification')}"


def troubleshoot_not_retried(a):
    """A troubleshoot step whose fix did not pass its check (and reports a timeout) is neither retried nor called done."""
    calls = []
    res = {"ok": False, "outcome": "fix applied, but the check failed (undo with the backup)", "verified": True,
           "error": "timed out waiting for the program to answer"}
    d = _dir()
    co = C.Coordinator({"troubleshoot": lambda s: calls.append(1) or dict(res)}, [], tasks_dir=d / "t", lock_path=d / "l")
    rec = co.run("fix the sample app", D.Plan([{"tool": "troubleshoot", "app": "JARVISSampleApp"}], "ok", model="bench"))
    ok = len(calls) == 1 and rec["status"] == "failed" and not C.summary(rec).startswith("Done")
    return ok, f"troubleshooter runs {len(calls)}, status {rec['status']}, summary '{C.summary(rec)[:100]}'"


# ---------------------------------------------------------------- the proactive monitor (detection)
def monitor_case(a):
    t = [1000.0]
    events, up = [], {8080: True}
    path = _dir() / "monitor.json"

    def mon(st):
        return MON.Monitor(st, crashes=lambda after: [e for e in events if e["record"] > after], newest=lambda: 100,
                           disks=lambda: {}, port=lambda p: up[p], tasks=lambda: [], services={"llama-swap": 8080}, clock=lambda: t[0])
    if a["kind"] == "crash_restart":
        st = MON.State()
        m = mon(st)
        m.check()                                              # first look: bookmark at the newest record
        events.append({"record": 101, "id": 1000, "app": "JARVISSampleApp.exe"})
        first = m.check()
        st.save(path)
        m2 = mon(MON.State.load(path))                         # the plugin restarts
        again = m2.check()
        events.append({"record": 102, "id": 1002, "app": "JARVISSampleApp.exe"})
        t[0] += 60
        new = m2.check()
        ok = len(first) == 1 and "crashed" in first[0].text and again == [] and len(new) == 1 and "stopped responding" in new[0].text
        return ok, f"first {[x.text for x in first]}, after restart {len(again)}, new hang {[x.text for x in new]}"
    st = MON.State()
    m = mon(st)
    m.check()
    up[8080] = False
    blip = m.check()
    down = m.check()
    quiet = m.check()
    ok = blip == [] and len(down) == 1 and "port 8080" in down[0].text and quiet == []
    return ok, f"one failed check {len(blip)}, two {len(down)}, three {len(quiet)}"


# ---------------------------------------------------------------- diagnosis: proposed fixes must be applicable (2026-10-17)
def _listening():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def diag_fix_unique(a):
    """A fix the diagnosis proposes must replace text that occurs EXACTLY once in the file as it is now (repair.edit_file
    refuses anything else, so a non-unique fix is a proposal that can never apply); otherwise no fix is proposed."""
    d = _dir()
    s, port = _listening() if a.get("listen") else (None, None)
    try:
        cfg = d / "config.json"
        cfg.write_text(a["content"].replace("{port}", str(port)), encoding="utf-8")
        r = core.diagnose(_dialog(*[t.replace("{port}", str(port)).replace("{file}", str(cfg)) for t in a["dialog"]]), [], [])
    finally:
        if s:
            s.close()
    f = r.get("fix")
    n = cfg.read_text(encoding="utf-8").count(f["args"]["find"]) if f else None
    ok = r["verified"] is True and (f is None or n == 1) and (not a.get("no_fix") or f is None)
    if a.get("cause_contains"):
        ok = ok and a["cause_contains"] in r["cause"].replace(str(port), "{port}")
    return ok, _brief(r) + (f"; the text to replace occurs {n} times in the file" if f else "")


def diag_variants(a):
    """Damaged config files (truncated mid-write, zero bytes, ...): the cause is verified at the right line, no fix is invented."""
    out = []
    for v in a["variants"]:
        d = _dir()
        cfg = d / "config.json"
        cfg.write_bytes(v["content"].encode("utf-8"))
        r = core.diagnose(_dialog("JARVIS Sample App could not start.", f"File: {cfg}"), [], [])
        if not (r["verified"] and r.get("line") == v["line"] and r["fix"] is None and f"line {v['line']}" in r["cause"]):
            return False, f"{v['content'][:30]!r}: " + _brief(r)
        out.append(f"{v['content'][:15]!r}: line {r['line']}, no fix")
    return True, "; ".join(out)


def diag_text(a):
    """Dialog text whose claim cannot be checked here (a remote host, a URL that only looks like a path): no verified cause,
    no fix, and the claim is not repeated as a finding. An optional real file sits at the path the text mentions."""
    d = _dir()
    real = d / "config.json"
    if a.get("file_content") is not None:
        real.write_text(a["file_content"], encoding="utf-8")
    r = core.diagnose(_dialog(*[t.replace("{file}", str(real)) for t in a["texts"]]), [], [])
    bad = []
    if r["verified"] or r["fix"] is not None:
        bad.append("claimed")
    if a.get("no_file") and r.get("file"):
        bad.append(f"took {r['file']} as the named file")
    bad += [f"cause says {w!r}" for w in a.get("cause_lacks", []) if w.lower() in r["cause"].lower()]
    return not bad, "; ".join(bad) + (" | " if bad else "") + _brief(r)


def diag_wrong_types(a):
    """The node's window / event / crash lists arrive with wrong types (a list item that is null, texts given as one string,
    an object instead of a list, an HTML string): diagnose() must still answer (no exception) and claim nothing."""
    out = []
    for v in a["variants"]:
        try:
            r = core.diagnose(v.get("windows", []), v.get("events", []), v.get("crashes", []))
        except Exception as e:  # noqa: BLE001 - the point of the case
            return False, f"{json.dumps(v)[:90]}: diagnose raised {type(e).__name__}: {e}"[:400]
        if r["verified"] or r["fix"] is not None:
            return False, f"{json.dumps(v)[:90]}: claimed " + _brief(r)
        out.append(r["cause"][:40])
    return True, f"{len(out)} malformed inputs answered without a claim: {out}"[:500]


def diag_huge(a):
    """A huge screen read (many windows, megabytes of text) with the real error dialog at the end: the diagnosis still finds
    the named file's fault and its fix, within a time limit."""
    d = _dir()
    cfg = d / "config.json"
    cfg.write_text('{\n  "name": "sample",\n  "port": 80x80\n}\n', encoding="utf-8")
    noise = [{"title": f"Window {i}", "dialog": False, "texts": ["lorem ipsum dolor sit amet " * 20]} for i in range(a["windows"])]
    t0 = time.perf_counter()
    r = core.diagnose(noise + _dialog("JARVIS Sample App could not start.", f"File: {cfg}"), [], [])
    ms = (time.perf_counter() - t0) * 1000
    chars = sum(len(t) for w in noise for t in w["texts"])
    ok = r["verified"] and r.get("line") == 3 and (r.get("fix") or {}).get("args", {}).get("replace") == '"port": 8080' and ms <= a["max_ms"]
    return ok, f"{len(noise)} windows, {chars // 1000} k chars: {ms:.0f} ms (limit {a['max_ms']}); " + _brief(r)


# ---------------------------------------------------------------- the troubleshooter with malformed node output
class _BadReadConsole(_Console):
    """Like _Console, but the node's window_text result file holds `bad` (an HTML page, cut-off JSON, ...): always, or only
    for the read after the fix (`when`). Everything else is the fake console as above, running the node's real repair."""

    def __init__(self, app, local, bad, when):
        super().__init__(app, local, False)
        self.bad, self.when = bad, when

    def handle(self, method, path, body):
        if path.endswith("/pc") and body.get("op") == "diag" and body.get("check") == "window_text" and \
                (self.when == "always" or (self.when == "after_fix" and self.repairs)):
            self.n += 1
            f = self.local / f"window_text-{self.n}.json"
            f.write_text(self.bad, encoding="utf-8")
            jid = f"j{len(self.jobs) + 1}"
            self.jobs[jid] = {"ok": True, "local_result": str(f)}
            return 202, {"job": jid, "status": "queued"}
        return super().handle(method, path, body)


def _troubleshoot_with(console_of, config):
    """Run DesktopPlugin.run_troubleshooter against a fake console; returns (report or None, exception text, console,
    whether the config file changed, local files left, backups on disk, the raw result)."""
    from jarvis.plugins.desktop import DesktopPlugin
    b = _Backups().__enter__()
    try:
        cfg = _cfg(b, config)
        before = cfg.read_bytes()
        local = _dir() / "local-only"
        local.mkdir()
        console = console_of(_SampleApp(cfg), local)
        srv = _serve(console)
        saved = core.CONSOLE, core.LOCAL_RESULTS, core.set_activity
        core.CONSOLE, core.LOCAL_RESULTS, core.set_activity = f"http://127.0.0.1:{srv.server_address[1]}", local, (lambda *x, **k: None)

        class Host:
            pass
        h = Host()
        h._run = lambda pc, body, timeout, what="": DesktopPlugin._run(h, pc, body, timeout, what)
        err, res = "", None
        try:
            res = asyncio.run(DesktopPlugin.run_troubleshooter(h, app="JARVISSampleApp", start_name="JARVIS Sample App"))
        except Exception as e:  # noqa: BLE001 - a raise is what the cases look for
            err = f"{type(e).__name__}: {e}"[:200]
        finally:
            core.CONSOLE, core.LOCAL_RESULTS, core.set_activity = saved
            srv.shutdown()
            srv.server_close()
        rep = getattr(res, "value", None)
        return rep, err, console, cfg.read_bytes() != before, list(local.iterdir()), b.count(), res
    finally:
        b.__exit__(None, None, None)


def troubleshooter_bad_read(a):
    """The node's screen read comes back malformed (fake console, real node repair code). Wanted: the troubleshooter answers
    (no exception), changes nothing it could not diagnose, deletes the screen-text file, and never says 'fixed'."""
    out = []
    for bad in a["reads"]:
        rep, err, console, changed, left, backups, res = _troubleshoot_with(
            lambda app, local, bad=bad: _BadReadConsole(app, local, bad, "always"), '{\n  "name": "sample",\n  "port": 80x80\n}\n')
        if err:
            return False, f"read {bad[:30]!r}: run_troubleshooter raised {err}"
        outcome = (rep or {}).get("outcome", "") if rep is not None else f"Err {getattr(res, 'error', '')}"
        if console.repairs or changed or left or "fixed" in str(outcome):
            return False, f"read {bad[:30]!r}: outcome {outcome!r}, repairs {console.repairs}, file changed {changed}, files left {len(left)}"
        out.append(f"{bad[:20]!r}: {str(outcome)[:40]}")
    return True, "; ".join(out)


def troubleshooter_empty_read(a):
    """The node's screen read is an empty file (nothing captured), twice (the troubleshooter starts the program once and
    looks again). Wanted: 'hypothesis only', no approval asked, no repair, no file left."""
    rep, err, console, changed, left, backups, res = _troubleshoot_with(
        lambda app, local: _BadReadConsole(app, local, "", "always"), '{\n  "name": "sample",\n  "port": 80x80\n}\n')
    rep = rep or {}
    ok = not err and rep.get("outcome") == "hypothesis only" and rep.get("verified") is False and console.approvals == 0 \
        and console.repairs == 0 and not changed and not left
    return ok, f"{err or ''} outcome {rep.get('outcome')!r}, cause {str(rep.get('cause'))[:80]!r}, approvals {console.approvals}, repairs {console.repairs}, files left {len(left)}"


def troubleshooter_bad_verify(a):
    """The fix is approved and applied (a real backup is made), then the read that should verify it comes back as an HTML
    page. Wanted: no exception, the report still names the backup and the undo, and it does not say 'fixed and verified'."""
    rep, err, console, changed, left, backups, res = _troubleshoot_with(
        lambda app, local: _BadReadConsole(app, local, a["read"], "after_fix"), '{\n  "name": "sample",\n  "port": 80x80\n}\n')
    rep = rep or {}
    ok = not err and console.repairs == 1 and bool(rep.get("backup")) and bool(rep.get("undo")) \
        and rep.get("outcome") != "fixed and verified" and not left
    return ok, (f"{err + '; ' if err else ''}repairs {console.repairs}, backups on disk {backups}, report backup {rep.get('backup')!r}, "
                f"undo {rep.get('undo')!r}, outcome {rep.get('outcome')!r}, files left {len(left)}")


class _HtmlServer:
    """Something else answers on the console's port: every request gets 200 with an HTML page (a web server, a captive proxy)."""

    def __enter__(self):
        class H(BaseHTTPRequestHandler):
            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                data = b"<html><body><h1>Welcome to nginx!</h1></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _go

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.saved = core.CONSOLE, core.set_activity
        core.CONSOLE, core.set_activity = f"http://127.0.0.1:{self.srv.server_address[1]}", (lambda *x, **k: None)
        return self

    def __exit__(self, *exc):
        core.CONSOLE, core.set_activity = self.saved
        self.srv.shutdown()
        self.srv.server_close()


def console_not_json(a):
    """The console's port answers 200 with HTML instead of JSON. Wanted: core.Pc reports a DesktopError (the console is not
    answering properly), never a raw decode error, and the troubleshooter returns its error instead of raising."""
    from jarvis.plugins.desktop import DesktopPlugin
    with _HtmlServer():
        try:
            core.Pc().run({"op": "diag", "check": "window_text", "target": "JARVISSampleApp"}, 5)
            pc = "no error at all"
        except core.DesktopError as e:
            pc = f"DesktopError: {e}"
        except Exception as e:  # noqa: BLE001
            pc = f"{type(e).__name__}: {e}"

        class Host:
            pass
        h = Host()
        h._run = lambda p, body, timeout, what="": DesktopPlugin._run(h, p, body, timeout, what)
        try:
            res = asyncio.run(DesktopPlugin.run_troubleshooter(h, app="JARVISSampleApp"))
            ts = type(res).__name__
        except Exception as e:  # noqa: BLE001
            ts = f"raised {type(e).__name__}"
    return pc.startswith("DesktopError") and ts == "Err", f"Pc.run -> {pc[:120]}; troubleshooter -> {ts}"


# ---------------------------------------------------------------- the coordinator with malformed agent results
def coordinator_bad_result(a):
    """A step's agent returns a malformed result (fake agent). Wanted for each: the coordinator records the task (no
    exception), the status is not 'completed', the summary does not start with 'Done', and the PC-task lock is released."""
    out = []
    for res in a["results"]:
        d = _dir()
        co = C.Coordinator({a["tool"]: lambda s, res=res: res}, [], tasks_dir=d / "t", lock_path=d / "l")
        try:
            rec = co.run("check the disk", D.Plan([dict(a["step"])], "ok", model="bench"))
        except Exception as e:  # noqa: BLE001
            return False, f"result {res!r}: the coordinator raised {type(e).__name__}: {e}; lock left behind {(d / 'l').exists()}"[:400]
        saved = list((d / "t").glob("*.json"))
        if rec["status"] == "completed" or C.summary(rec).startswith("Done") or (d / "l").exists() or not saved:
            return False, f"result {res!r}: status {rec['status']}, summary {C.summary(rec)[:80]!r}, lock left {(d / 'l').exists()}, records {len(saved)}"
        out.append(f"{str(res)[:20]!r}: {rec['status']}")
    return True, "; ".join(out)


# ---------------------------------------------------------------- repairs on files as they are now
def repair_changed_since(a):
    """Diagnosis proposes a fix; before the approved repair runs, the owner fixes the file by hand. The repair applies the
    proposal to the file as it is NOW: the text is gone, so it changes nothing and makes no backup."""
    with _Backups() as b:
        p = _cfg(b)
        r = core.diagnose(_dialog("JARVIS Sample App could not start.", f"File: {p}"), [], [])
        fix = r.get("fix") or {}
        p.write_text('{\n  "name": "sample",\n  "port": 9090\n}\n', encoding="utf-8")        # the owner's own edit
        before = p.read_bytes()
        res = repair.run({"op": fix.get("action", "edit_file"), "target": fix.get("target", str(p)), "args": fix.get("args", {})})
        ok = bool(fix) and not res["ok"] and "0 times" in str(res.get("error")) and p.read_bytes() == before and b.count() == 0
        return ok, f"proposed {fix.get('args')}; repair -> {res}; file unchanged {p.read_bytes() == before}; backups {b.count()}"


def repair_minimal(a):
    """A fix on a file with a UTF-8 BOM, CRLF line ends and non-ASCII text changes only the replaced bytes; the backup is
    byte-identical to the original."""
    with _Backups() as b:
        p = _cfg(b)
        orig = '﻿{\r\n  "name": "café ☕ sample",\r\n  "port": 80x80,\r\n  "debug": false\r\n}\r\n'.encode("utf-8")
        p.write_bytes(orig)
        r = repair.edit_file(str(p), '"port": 80x80', '"port": 8080')
        after = p.read_bytes()
        bak = (Path(repair.BACKUPS) / r.get("backup", "?") / "file.bak")
        only = after == orig.replace(b'"port": 80x80', b'"port": 8080')
        crlf = after.count(b"\r\n") == orig.count(b"\r\n")
        same = bak.is_file() and bak.read_bytes() == orig
        ok = r["ok"] and only and same and r["lines_before"] == r["lines_after"]
        return ok, f"ok {r['ok']}; only the value changed {only}; BOM kept {after.startswith(orig[:3])}; CRLF kept {crlf}; backup identical {same}"


def repair_unsafe_files(a):
    """Files a text edit cannot change safely: UTF-16 text, a file over the size limit, a link that leads outside the
    owner's folder, a folder. Each is refused; nothing on disk changes and no backup is made."""
    with _Backups() as b:
        base = b.dir / "app"
        base.mkdir(parents=True, exist_ok=True)
        u16 = base / "u16.json"
        u16.write_bytes('{"port": 80x80}'.encode("utf-16"))
        big = base / "big.json"
        big.write_bytes(b"{" + b" " * (repair.MAX_FILE + 10) + b'"port": 80x80}')
        # a file outside the user's folder (HOME is /tmp in the test image) that a careless edit could really change
        outside = next(x for x in ("/var/tmp", "/dev/shm", "/etc") if os.access(x, os.W_OK) or x == "/etc")
        target = Path(tempfile.mkdtemp(dir=outside)) / "outside.json" if outside != "/etc" else Path("/etc/hostname")
        if outside != "/etc":
            target.write_text('{"port": 80x80}', encoding="utf-8")
        tbefore = target.read_bytes()
        link = base / "link.json"
        os.symlink(target, link)
        cases = {"utf-16": u16, "too large": big, "link outside": link, "a folder": base}
        before = {k: (v.read_bytes() if v.is_file() else None) for k, v in cases.items()}
        res = {k: repair.edit_file(str(v), "80x80", "8080") for k, v in cases.items()}
        changed = [k for k, v in cases.items() if v.is_file() and v.read_bytes() != before[k]]
        ok = not any(r["ok"] for r in res.values()) and not changed and b.count() == 0 and target.read_bytes() == tbefore
        if target.parent != Path("/etc"):
            shutil.rmtree(target.parent, ignore_errors=True)
        return ok, "; ".join(f"{k}: {(r.get('error') or 'ok')[:50]}" for k, r in res.items()) + f"; changed {changed}; backups {b.count()}"


def repair_bad_manifest(a):
    """A backup whose manifest.json is damaged (see variants) is never restored: restore() answers ok=False (no exception)
    and the live file stays as it is."""
    out = []
    for v in a["variants"]:
        with _Backups() as b:
            p = _cfg(b)
            r = repair.edit_file(str(p), '"port": 80x80', '"port": 8080')
            m = Path(repair.BACKUPS) / r["backup"] / "manifest.json"
            good = json.loads(m.read_text(encoding="utf-8"))
            text = {"truncated": json.dumps(good)[:40], "empty": "", "no_sha": json.dumps({k: x for k, x in good.items() if k != "sha256"}),
                    "a list": json.dumps([good]), "path a number": json.dumps(dict(good, path=42)),
                    "sha a number": json.dumps(dict(good, sha256=12345))}[v]
            m.write_text(text, encoding="utf-8")
            live = p.read_bytes()
            try:
                t = repair.restore(r["backup"])
            except Exception as e:  # noqa: BLE001
                return False, f"{v}: restore raised {type(e).__name__}: {e}"
            if t.get("ok") or p.read_bytes() != live:
                return False, f"{v}: restore {t}; live file changed {p.read_bytes() != live}"
            out.append(f"{v}: {t.get('error')}")
    return True, "; ".join(out)


def repair_restart_garbage(a):
    """restart_app when PowerShell's answer is not the expected JSON object (fake PowerShell; see variants). Wanted: never
    an exception, never ok, and the program is started only when the close step was read as a clean close."""
    exe = _home_dir() / "JARVISSampleApp.exe"
    exe.write_bytes(b"MZ")
    clean = json.dumps({"running": True, "path": str(exe), "closed": True, "left": 0})
    out = []
    try:
        for v in a["variants"]:
            close = clean if v["close"] == "CLEAN" else v["close"]

            class P(_FakeProc):
                def __enter__(self):
                    self.o = (runtime.powershell, runtime.popen)
                    runtime.powershell = lambda script, env=None, timeout=30: (self.ps.append(script), subprocess.CompletedProcess(
                        [], 0, close if script == repair.CLOSE else v["check"], ""))[1]
                    runtime.popen = lambda args, **kw: self.started.append(args)
                    return self
            with P({}, {}) as f:
                try:
                    r = repair.restart_app("JARVISSampleApp")
                except Exception as e:  # noqa: BLE001
                    return False, f"close {close[:20]!r} / check {v['check'][:20]!r}: raised {type(e).__name__}: {e}"
            if r["ok"] or bool(f.started) != v["starts"]:
                return False, f"close {close[:20]!r} / check {v['check'][:20]!r}: {r}, started {f.started}"
            out.append(f"{v['close'][:12]!r}/{v['check'][:8]!r}: {str(r.get('error'))[:45]}")
    finally:
        shutil.rmtree(exe.parent, ignore_errors=True)
    return True, "; ".join(out)


def repair_install_listing(a):
    """What counts as 'installed' after winget (fake winget; see variants): only an exact package id in the listing of a
    successful 'winget list'. A longer id that contains it, an error text that echoes it, or a failed list is not success."""
    o_which, o_run = repair.shutil.which, runtime.run
    out = []
    try:
        for v in a["variants"]:
            repair.shutil.which = lambda n: "/fake/winget" if n == "winget" else o_which(n)
            runtime.run = lambda args, timeout=30, v=v, **kw: subprocess.CompletedProcess(
                args, v["exit"] if args[1] == "list" else 0, v["stdout"] if args[1] == "list" else "Successfully installed", "")
            r = repair.install("JARVIS.SampleApp")
            if r["ok"] != v["installed"]:
                return False, f"list said {v['stdout'][:70]!r} (exit {v['exit']}): ok {r['ok']}, want {v['installed']}"
            out.append(f"exit {v['exit']} {v['stdout'][:25]!r}: ok {r['ok']}")
    finally:
        repair.shutil.which, runtime.run = o_which, o_run
    return True, "; ".join(out)


# ---------------------------------------------------------------- the monitor after a damaged state file
def monitor_corrupt_state(a):
    """The monitor's saved state is cut off mid-write and a task record is damaged. Wanted: the monitor starts fresh (no
    flood of old crash alerts), still reports a crash that happens afterwards, and the stuck-task scan skips the damaged
    record while still finding the stuck one."""
    d = _dir()
    path = d / "monitor.json"
    path.write_text('{"bookmark": 95, "seen_up": {"llama-swap": tr', encoding="utf-8")
    events = [{"record": r, "id": 1000, "app": f"OldApp{r}.exe"} for r in range(1, 101)]
    t = [10_000.0]
    st = MON.State.load(path)
    m = MON.Monitor(st, crashes=lambda after: [e for e in events if e["record"] > after], newest=lambda: 100, disks=lambda: {},
                    port=lambda p: True, tasks=lambda: [], services={}, clock=lambda: t[0])
    first = m.check()
    events.append({"record": 101, "id": 1000, "app": "JARVISSampleApp.exe"})
    second = m.check()
    tasks = d / "pc_tasks"
    tasks.mkdir()
    (tasks / "20261017-010000-aaaaaa.json").write_text('{"id": "x", "request": "open', encoding="utf-8")
    (tasks / "20261017-010001-bbbbbb.json").write_text(json.dumps({"id": "stuck1", "request": "type the letter", "started": 1.0,
                                                                   "status": "running"}), encoding="utf-8")
    open_ = MON.open_tasks(tasks)
    m2 = MON.Monitor(MON.State(), crashes=lambda after: [], newest=lambda: 1, disks=lambda: {}, port=lambda p: True,
                     tasks=lambda: open_, services={}, clock=lambda: 5_000.0)
    stuck = m2.check()
    again = m2.check()                                         # the same stuck task is not announced twice
    ok = first == [] and len(second) == 1 and "JARVISSampleApp" in second[0].text and [o["id"] for o in open_] == ["stuck1"] \
        and len(stuck) == 1 and "type the letter" in stuck[0].text and again == []
    return ok, f"alerts after loading the damaged state {len(first)}; new crash {[x.text[:40] for x in second]}; open tasks " \
               f"{[o['id'] for o in open_]}; stuck alerts {[x.text[:40] for x in stuck]}"


def take_local_outside(a):
    """A node result whose local_result path points outside outputs/local-only (an absolute path elsewhere, a ../ escape, a
    link inside local-only that leads out): take_local() refuses each with DesktopError and deletes nothing; a proper file
    inside local-only is read once and deleted."""
    d = _dir()
    local = d / "local-only"
    local.mkdir()
    victim = d / "owner-notes.json"
    victim.write_text('{"keep": true}', encoding="utf-8")
    link = local / "link.json"
    os.symlink(victim, link)
    paths = {"absolute": str(victim), "dot-dot": str(local / ".." / "owner-notes.json"), "link": str(link)}
    saved = core.LOCAL_RESULTS
    core.LOCAL_RESULTS = local
    out = {}
    try:
        for name, p in paths.items():
            try:
                core.take_local(p)
                out[name] = "read"
            except core.DesktopError:
                out[name] = "refused"
            except Exception as e:  # noqa: BLE001
                out[name] = f"raised {type(e).__name__}"
        good = local / "window_text-1.json"
        good.write_text('[{"title": "x"}]', encoding="utf-8")
        got = core.take_local(str(good))
    finally:
        core.LOCAL_RESULTS = saved
    ok = all(v == "refused" for v in out.values()) and victim.read_text(encoding="utf-8") == '{"keep": true}' \
        and got == [{"title": "x"}] and not good.exists()
    return ok, f"{out}; outside file still there {victim.exists()}; proper file read {got} and deleted {not good.exists()}"
