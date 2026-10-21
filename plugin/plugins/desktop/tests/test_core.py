"""Desktop plugin logic without servers: the approval round trip, waiting and cancelling jobs, local results read once
and deleted, and a diagnosis that is verified only against the file the error names."""
import json
import os
import socket
import subprocess
import sys

import pytest

from plugin.plugins.desktop import core


class FakeConsole:
    def __init__(self, approve_after=2, deny=False, finish_after=2):
        self.calls, self.t, self.pending = [], 0, set()
        self.approve_after, self.deny, self.finish_after = approve_after, deny, finish_after
        self.polls = 0

    def __call__(self, method, name, body=None, timeout=0):
        self.calls.append((method, name, body))
        if name == "pc":
            if body.get("action") == "edit_file" and not body.get("approval_id"):
                self.pending.add("a1")
                return 202, {"status": "needs_approval", "approval_id": "a1", "level": "YELLOW_APPROVAL", "reason": "repairs need the owner"}
            if body.get("approval_id"):
                return (403, {"error": "refused by policy: approval denied"}) if self.deny else (202, {"job": "j2", "status": "queued"})
            return 202, {"job": "j1", "status": "queued"}
        if name == "state":
            self.polls += 1
            if self.polls >= self.approve_after:
                self.pending.clear()
            return 200, {"approvals": [{"id": a} for a in self.pending]}
        if name.startswith("job/") and method == "GET":
            self.t += 1
            done = self.t >= self.finish_after
            return 200, {"id": name[4:], "state": "done" if done else "running", "result": {"ok": True, "backup": "b1"} if done else None}
        if name == "job/cancel":
            return 200, {"id": body["id"], "state": "cancelling"}
        return 404, {}


def pc(console, **kw):
    clock = [0.0]
    return core.Pc(call=console, sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0], **kw)


def test_ordinary_action_waits_for_the_job():
    c = FakeConsole()
    r = pc(c).run({"op": "diag", "check": "disk"}, 30)
    assert r["state"] == "done" and r["result"]["ok"]


def test_approval_is_waited_for_then_the_same_request_continues_with_its_id():
    c, states = FakeConsole(approve_after=3), []
    r = pc(c, on_state=lambda s, d: states.append(s)).run({"op": "repair", "action": "edit_file", "target": "x", "args": {}}, 30, "edit x")
    assert r["state"] == "done" and "waiting_for_permission" in states
    retried = [b for m, n, b in c.calls if n == "pc" and b.get("approval_id") == "a1"]
    assert retried and retried[0]["target"] == "x"


def test_denied_and_never_answered():
    r = pc(FakeConsole(approve_after=1, deny=True)).run({"op": "repair", "action": "edit_file", "target": "x", "args": {}}, 30)
    assert r["state"] == "denied"
    r = pc(FakeConsole(approve_after=10 ** 6), approval_wait_s=10).run({"op": "repair", "action": "edit_file", "target": "x", "args": {}}, 30)
    assert r["state"] == "waiting_for_permission" and r["approval_id"] == "a1" and "PIN" in r["how"]


def test_stop_while_an_approval_is_pending_is_cancelled():
    """Bench B-024 (2026-10-16): the owner's stop during the approval wait came back as 'waiting_for_permission'."""
    c = FakeConsole(approve_after=10 ** 6)
    p = pc(c)
    orig = c.__call__

    def call(method, name, body=None, timeout=0):
        if name == "state" and c.polls >= 1:
            p.stopped = True                               # the owner pressed stop while it waited
        return orig(method, name, body, timeout)
    p.call = call
    r = p.run({"op": "repair", "action": "edit_file", "target": "x", "args": {}}, 30)
    assert r["state"] == "cancelled" and not [b for m, n, b in c.calls if n == "pc" and b.get("approval_id")]


def test_a_dll_that_is_present_is_only_a_hypothesis(tmp_path):
    """Bench E-012 (2026-10-16): with the DLL in the program folder the cause still read 'a required library is missing'."""
    (tmp_path / "NekoRuntime140.dll").write_bytes(b"MZ")
    d = core.diagnose([{"dialog": True, "title": "x", "texts": ["NekoRuntime140.dll was not found."]}], [], [], app_dir=str(tmp_path))
    assert not d["verified"] and "hypothesis" in d["cause"]
    d = core.diagnose([{"dialog": True, "title": "x", "texts": ["NekoRuntime140.dll was not found."]}], [], [], app_dir=str(tmp_path / "none"))
    assert d["verified"] and "hypothesis" not in d["cause"]


def test_a_job_that_never_finishes_is_cancelled():
    c = FakeConsole(finish_after=10 ** 6)
    r = pc(c).run({"op": "diag", "check": "processes"}, 5)
    assert r["state"] == "failed" and ("POST", "job/cancel", {"id": "j1"}) in c.calls


def test_local_results_are_read_once_and_only_from_neko_local(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "LOCAL_RESULTS", tmp_path / "neko-local")
    (tmp_path / "neko-local").mkdir()
    f = tmp_path / "neko-local" / "window_text-1.json"
    f.write_text(json.dumps([{"title": "x"}]), encoding="utf-8")
    assert core.take_local(str(f)) == [{"title": "x"}] and not f.exists()
    other = tmp_path / "elsewhere.json"
    other.write_text("[]", encoding="utf-8")
    with pytest.raises(core.DesktopError):
        core.take_local(str(other))
    assert other.exists()


def test_diagnosis_is_verified_against_the_named_file_and_proposes_a_safe_fix(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{\n  "name": "neko sample",\n  "port": 80x80\n}\n', encoding="utf-8")
    windows = [{"title": "Neko Sample App - configuration error", "dialog": True, "buttons": ["OK"],
                "texts": ["Neko Sample App could not start.", f"File: {cfg}"]}]
    d = core.diagnose(windows, [], [])
    assert d["verified"] and "line 3" in d["cause"]
    assert d["fix"]["args"] == {"find": '"port": 80x80', "replace": '"port": 8080'}
    cfg.write_text('{"port": 8080}', encoding="utf-8")                  # once fixed: no longer a verified cause
    d2 = core.diagnose(windows, [], [])
    assert not d2["verified"] and d2["fix"] is None and "Neko Sample App could not start" not in d2["cause"]


def test_unknown_errors_stay_hypotheses():
    d = core.diagnose([], [], [{"app": "x.exe", "kind": "crash", "module": "y.dll", "code": "c0000005"}])
    assert not d["verified"] and "hypothesis" in d["cause"]


def test_port_in_use_is_verified_against_the_real_listener_and_a_free_port_is_proposed(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"name": "x", "port": port, "listen": True}), encoding="utf-8")
    try:
        d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"Port {port} is already in use by another program.", f"File: {cfg}"]}], [], [])
        assert d["verified"] and f"port {port}" in d["cause"] and d["fix"]["args"]["find"] == f'"port": {port}'
        assert d["fix"]["args"]["replace"] != d["fix"]["args"]["find"]
    finally:
        s.close()
    d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"Port {port} is already in use by another program."]}], [], [])
    assert not d["verified"] and "hypothesis" in d["cause"]          # released since: not claimed


# The same disk checks on the drive this OS really has: C: on Windows, / elsewhere (the Linux test image).
DRIVE = "C:" if os.name == "nt" else "/"


def test_disk_space_and_service_down_are_checked_not_assumed():
    d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"There is not enough space on the disk: it needs 999999999 MB on {DRIVE}, which has 1 MB free."]}], [], [])
    assert d["verified"] and d["fix"] is None and d["needs_mb"] == 999999999 and d["drive"] == DRIVE
    d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"There is not enough space on the disk: it needs 1 MB on {DRIVE}, which has 0 MB free."]}], [], [])
    assert not d["verified"] and "hypothesis" in d["cause"]          # the drive really has space: the claim is not trusted
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"Could not connect to the service at 127.0.0.1:{port} (connection refused)."]}], [], [])
    assert d["verified"] and "nothing is listening" in d["cause"] and d["fix"] is None


def test_named_file_is_found_in_windows_and_posix_paths_but_not_in_urls():
    find = lambda t: (m.group(1) if (m := core.FILE_IN_TEXT.search(t)) else None)
    assert find(r"File: C:\Users\me\app\config.json.") == r"C:\Users\me\app\config.json"
    assert find("File: /home/me/app/config.json.") == "/home/me/app/config.json"
    assert find("see https://example.com/app/config.json for help") is None
    assert find("relative/config.json or ~/x.json") is None


def test_low_space_on_a_drive_that_does_not_exist_is_not_verified():
    drive = "Q:" if os.name != "nt" else "/no/such/mount"      # absent on this OS either way
    d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"There is not enough space on the disk: it needs 5 MB on {drive}"]}], [], [])
    assert not d["verified"] and "hypothesis" in d["cause"] and "None" not in d["cause"] and "could not be checked" in d["cause"]


def test_a_dead_owner_is_not_alive_but_a_running_one_is():
    from plugin.plugins.desktop import coordinator as C
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert C._alive(os.getpid()) and not C._alive(p.pid) and not C._alive(0)


def test_a_crashed_tasks_lock_is_taken_over_but_a_live_ones_is_not(tmp_path):
    from plugin.plugins.desktop import coordinator as C
    lock = tmp_path / "pc_task.lock"
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    lock.write_text(json.dumps({"task": "crashed", "pid": p.pid, "at": __import__("time").time()}), encoding="utf-8")
    C.acquire("next", lock)                                         # owner is gone: taken over
    assert C.current_task(lock) == "next"
    with pytest.raises(C.Busy):
        C.acquire("third", lock)                                    # owner (this process) is alive: refused
    C.release("next", lock)
    assert not lock.exists()


# ---------------------------------------------------------------- bench E-035..E-046 (2026-10-17): malformed input, unusable fixes
def test_a_typo_fix_whose_text_occurs_twice_is_widened_until_it_is_unique(tmp_path):
    """Bench E-035: the fix's text occurred twice, so repair.edit_file (exactly-once rule) could never apply it."""
    cfg = tmp_path / "config.json"
    cfg.write_bytes(b'{\r\n "servers": [\r\n  {"port": 80x80},\r\n  {"port": 80x80}\r\n ]\r\n}\r\n')   # CRLF, as on Windows
    d = core.diagnose([{"dialog": True, "title": "x", "texts": ["Neko Sample App could not start.", f"File: {cfg}"]}], [], [])
    assert d["verified"] and d["line"] == 3
    on_disk = cfg.read_bytes().decode("utf-8")                     # what repair.edit_file counts in (line endings kept)
    f = d["fix"]["args"]
    assert on_disk.count(f["find"]) == 1
    fixed = on_disk.replace(f["find"], f["replace"], 1)
    assert fixed.splitlines()[2] == '  {"port": 8080},' and fixed.splitlines()[3] == '  {"port": 80x80}' and "\r\n" in fixed


def test_a_port_fix_whose_text_occurs_twice_is_widened_until_it_is_unique(tmp_path):
    """Bench E-036: the config held the busy port twice; the proposed '"port": N' could never apply."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    cfg = tmp_path / "config.json"
    cfg.write_text('{\n "web": {"port": %d},\n "admin": {"port": %d}\n}\n' % (port, port), encoding="utf-8")
    try:
        d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"Port {port} is already in use by another program.", f"File: {cfg}"]}], [], [])
    finally:
        s.close()
    assert d["verified"] and f"port {port}" in d["cause"]
    f = d["fix"]["args"]
    assert cfg.read_text(encoding="utf-8").count(f["find"]) == 1 and f'"web": {{"port": {port}}}' in f["find"]
    assert f'"port": {port}' not in f["replace"] and '"port": ' in f["replace"]


def test_wrong_typed_node_output_never_crashes_the_diagnosis_and_claims_nothing():
    """Bench E-041: texts as one string (str + list), a null crash record, events as one object raised."""
    for windows, events, crashes in (
            ([{"title": "x", "dialog": True, "texts": "Port 8080 is already in use"}], [], []),
            ([None, {"title": "x", "dialog": True, "texts": ["could not start", None, 7]}], [], []),
            ([], {"provider": "Application Error"}, []),
            ([], [], [None]),
            ("<html><body>502 Bad Gateway</body></html>", "<html>", "<html>"),
            ([{"title": ["not", "text"], "dialog": True, "texts": [{"x": 1}]}], [None, "e"], [{"app": "a.exe"}, None])):
        d = core.diagnose(windows, events, crashes)
        assert not d["verified"] and d["fix"] is None and d["cause"]


def test_a_console_port_that_answers_html_is_a_desktop_error(monkeypatch):
    """Bench E-046: a 200 reply with an HTML page raised JSONDecodeError out of core.owner / Pc.run."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            data = b"<html><body>Welcome to nginx!</body></html>" if self.path.endswith("/state") else b"[1, 2]"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        do_POST = do_GET

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(core, "CONSOLE", f"http://127.0.0.1:{srv.server_address[1]}")
        for name in ("state", "pc"):                                # HTML, and JSON that is not an object
            with pytest.raises(core.DesktopError, match="not answering properly"):
                core.owner("GET", name)
    finally:
        srv.shutdown()
        srv.server_close()


class _Host:
    """A DesktopPlugin stand-in whose _run answers from a script (no console, no node)."""

    def __init__(self, local, reads, repair_result):
        self.local, self.reads, self.repair_result, self.bodies, self.n = local, list(reads), repair_result, [], 0

    async def _run(self, pc, body, timeout, what=""):
        self.bodies.append(body)
        if body.get("check") == "window_text":
            self.n += 1
            f = self.local / f"window_text-{self.n}.json"
            f.write_text(self.reads.pop(0), encoding="utf-8")
            return {"state": "done", "result": {"ok": True, "local_result": str(f)}}
        if body.get("op") == "repair":
            return {"state": "done", "result": dict(self.repair_result)}
        return {"state": "done", "result": {"ok": True, "rows": []}}


def _troubleshoot(tmp_path, monkeypatch, reads, repair_result=None):
    import asyncio
    from plugin.plugins.desktop import DesktopPlugin
    local = tmp_path / "neko-local"
    local.mkdir()
    monkeypatch.setattr(core, "LOCAL_RESULTS", local)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    h = _Host(local, reads, repair_result or {"ok": True, "backup": "20261017-101500-abcdef", "undo": "repair restore 20261017-101500-abcdef"})
    res = asyncio.run(DesktopPlugin.run_troubleshooter(h, app="NekoSampleApp"))
    return res, h, list(local.iterdir())


def test_a_malformed_screen_read_is_answered_and_changes_nothing(tmp_path, monkeypatch):
    """Bench E-043: take_local returned a string or a dict, and diagnose / the troubleshooter raised AttributeError."""
    for i, bad in enumerate(("<html><body><h1>500 Internal Server Error</h1></body></html>", '[{"title": "Neko Sample App - configuration err',
                             '{"error": "access denied"}', '["Neko Sample App", "could not start"]')):
        sub = tmp_path / f"read{i}"
        sub.mkdir()
        res, h, left = _troubleshoot(sub, monkeypatch, [bad])
        rep = res.value
        assert "fixed" not in rep["outcome"] and not [b for b in h.bodies if b.get("op") == "repair"] and not left


def test_a_malformed_check_after_the_fix_keeps_the_backup_and_undo(tmp_path, monkeypatch):
    """Bench E-045: the read after an applied fix was an HTML page; the report (with its backup and undo) was lost."""
    cfg = tmp_path / "config.json"
    cfg.write_text('{\n  "name": "sample",\n  "port": 80x80\n}\n', encoding="utf-8")
    first = json.dumps([{"title": "Neko Sample App - configuration error", "dialog": True, "texts": ["could not start.", f"File: {cfg}"]}])
    res, h, left = _troubleshoot(tmp_path, monkeypatch, [first, "<html><body><h1>502 Bad Gateway</h1></body></html>"])
    rep = res.value
    assert [b for b in h.bodies if b.get("op") == "repair"] and not left
    assert rep["backup"] == "20261017-101500-abcdef" and rep["undo"] == "repair restore 20261017-101500-abcdef"
    assert rep["outcome"] != "fixed and verified" and "undo" in rep["outcome"]


def test_an_error_after_the_fix_still_reports_the_backup_and_undo(tmp_path, monkeypatch):
    """Bench E-045: once a backup exists, every report names it, even when the console stops answering for the check."""
    cfg = tmp_path / "config.json"
    cfg.write_text('{\n  "name": "sample",\n  "port": 80x80\n}\n', encoding="utf-8")
    first = json.dumps([{"title": "x", "dialog": True, "texts": [f"File: {cfg}"]}])

    class Gone(_Host):
        async def _run(self, pc, body, timeout, what=""):
            if body.get("check") == "window_text" and self.n:
                raise core.DesktopError("the JARVIS console is not answering")
            return await super()._run(pc, body, timeout, what)
    import asyncio
    from plugin.plugins.desktop import DesktopPlugin
    local = tmp_path / "neko-local"
    local.mkdir()
    monkeypatch.setattr(core, "LOCAL_RESULTS", local)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    h = Gone(local, [first], {"ok": True, "backup": "b-1", "undo": "repair restore b-1"})
    rep = asyncio.run(DesktopPlugin.run_troubleshooter(h, app="NekoSampleApp")).value
    assert rep["backup"] == "b-1" and rep["undo"] == "repair restore b-1" and "could not run" in rep["outcome"]


def test_owner_posts_carry_this_launchs_console_secret_and_follow_a_console_restart(monkeypatch, tmp_path):
    """Audit SEC-D9: the console accepts a POST only with its per-launch secret (read from its page). Without this the desktop
    plugin could not reach the owner plane at all; it must also survive the console restarting (new secret)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path / "no-token-here"))      # a console that still serves its secret in its page (legacy path)
    state = {"secret": "LAUNCH-A", "posts": [], "gets": 0}

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                state["gets"] += 1
                return self._send(200, f'<html><meta name="jarvis-console-secret" content="{state["secret"]}"></html>'.encode(), "text/html")
            self._send(200, {"ok": True})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            given = self.headers.get("X-Jarvis-Console")
            state["posts"].append(given)
            if given != state["secret"]:
                return self._send(403, {"error": "refused", "reload": True})
            self._send(200, {"state": "done"})

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(core, "CONSOLE", f"http://127.0.0.1:{srv.server_address[1]}")
        core._SECRETS.clear()
        assert core.owner("POST", "pc", {"op": "diag"}) == (200, {"state": "done"})
        assert state["posts"] == ["LAUNCH-A"]                      # never the old constant "1"
        state["secret"] = "LAUNCH-B"                               # the console restarted: the cached secret is stale
        assert core.owner("POST", "pc", {"op": "diag"})[0] == 200
        assert state["posts"][-2:] == ["LAUNCH-A", "LAUNCH-B"]     # one refused try, then the fresh secret
        gets_before = state["gets"]
        assert core.owner("GET", "state")[0] == 200 and state["gets"] == gets_before   # a read needs no secret
    finally:
        srv.shutdown()
        srv.server_close()


def test_owner_refuses_an_address_whose_page_carries_no_console_secret(monkeypatch, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path / "no-token-here"))

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            data = b"<html>some other web server</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(core, "CONSOLE", f"http://127.0.0.1:{srv.server_address[1]}")
        core._SECRETS.clear()
        with pytest.raises(core.DesktopError, match="console token is not available"):
            core.owner("POST", "pc", {"op": "diag"})
    finally:
        srv.shutdown()
        srv.server_close()


def test_owner_reads_and_writes_send_the_master_token_from_its_0600_file_and_follow_a_restart(monkeypatch, tmp_path):
    """Re-review M3 (2026-10-19): the console puts no credential in `GET /` any more. The plugin reads console.token from the run
    folder (refusing a file other users can read) and sends it on EVERY request; a 401 "login" or 403 "reload" re-reads it once."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    state = {"token": "MASTER-A", "seen": [], "page_gets": 0}

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                state["page_gets"] += 1
                return self._send(401, {"error": "login page"})                    # the real console: no secret in the page
            given = self.headers.get("X-Jarvis-Console")
            state["seen"].append(("GET", given))
            return self._send(200, {"ok": True}) if given == state["token"] else self._send(401, {"error": "not signed in", "login": True})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            given = self.headers.get("X-Jarvis-Console")
            state["seen"].append(("POST", given))
            return self._send(200, {"state": "done"}) if given == state["token"] else self._send(403, {"error": "refused", "reload": True})

        def log_message(self, *a):
            pass
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    monkeypatch.setenv("JARVIS_RUN_DIR", str(run))

    def publish(token, mode=0o600):
        f = run / "console.token"
        f.write_text(token)
        f.chmod(mode)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(core, "CONSOLE", f"http://127.0.0.1:{srv.server_address[1]}")
        core._SECRETS.clear()
        publish("MASTER-A")
        assert core.owner("GET", "state") == (200, {"ok": True})
        assert core.owner("POST", "pc", {"op": "diag"}) == (200, {"state": "done"})
        assert state["seen"] == [("GET", "MASTER-A"), ("POST", "MASTER-A")] and state["page_gets"] == 0
        state["token"] = "MASTER-B"                                                # the console restarted: new token in the file
        publish("MASTER-B")
        assert core.owner("GET", "state") == (200, {"ok": True})                   # a 401 login re-reads the file once
        assert core.owner("POST", "pc", {"op": "diag"})[0] == 200
        # a token file that other users can read is not trusted (and the page has no secret to fall back on)
        core._SECRETS.clear()
        publish("MASTER-B", mode=0o644)
        with pytest.raises(core.DesktopError, match="console token is not available"):
            core.owner("POST", "pc", {"op": "diag"})                               # a write never goes out without a token
        code, reply = core.owner("GET", "state")                                    # a read goes out bare; the console answers 401
        assert code == 401 and reply.get("login") is True
    finally:
        srv.shutdown()
        srv.server_close()
