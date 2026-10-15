"""Backend of the JARVIS desktop widget. Windows; standard library only; loopback only; no model calls.

    python widget_server.py [--port 48940]

Serves the single-page widget (index.html) and three same-origin endpoints it needs, so the page never talks to the other
servers directly (no CORS, nothing exposed):
    GET  /api/status   one JSON document: the six servers, the four plugins, the JARVIS kernel and approvals count,
                       the newest main-agent task, and the watchdog's verdict
    POST /api/task     {"task": "...", "mode": "work"}  starts a main-agent task (the same call the plugin panel makes)
    GET  /            the page
The widget is read-only for approvals (it shows how many are waiting; approving needs your PIN in the JARVIS console), and it
cannot touch the kill switch.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data")
PORT = 48940
SERVERS = {"N.E.K.O": "http://127.0.0.1:48911/health", "memory": "http://127.0.0.1:48912/health",
           "agent": "http://127.0.0.1:48915/health", "plugins": "http://127.0.0.1:48916/health",
           "speech": "http://127.0.0.1:48930/health", "console": "http://127.0.0.1:8799/api/owner/state"}   # state: fast; /api/health may be slow
PLUGINS = ("jarvis_bridge", "main_agent", "doc_tools", "learn_vault")
MODES = ("chat", "work", "code", "learn")
MAX_TASK = 2000
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def fetch_json(url, timeout=2.5, data=None):
    """GET (or POST a JSON body) and parse JSON; None on any failure."""
    try:
        req = urllib.request.Request(url, method="POST" if data is not None else "GET",
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "null")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def host_ok(host_header):
    host = (host_header or "").lower().rsplit(":", 1)[0].strip("[]")
    return host in LOCAL_HOSTS


def origin_ok(origin, host_header):
    """A browser POST must come from this page itself (same origin), never from another site."""
    if not origin:
        return True                                   # not a browser fetch from another page (e.g. the widget's own XHR has none)
    return origin.rstrip("/").lower() in (f"http://{host_header}".lower(), "http://127.0.0.1:%d" % PORT, "http://localhost:%d" % PORT)


def validate_task(body):
    if not isinstance(body, dict):
        return None, "send a JSON object"
    task = " ".join(str(body.get("task") or "").split())
    mode = str(body.get("mode") or "work").lower()
    if not task:
        return None, "Type the task first."
    if len(task) > MAX_TASK:
        return None, f"The task is longer than {MAX_TASK} characters."
    if mode not in MODES:
        return None, "mode must be one of " + ", ".join(MODES)
    return {"task": task, "mode": mode}, None


def newest_run(runs_dir):
    try:
        files = [os.path.join(runs_dir, f) for f in os.listdir(runs_dir) if f.endswith(".json")]
        newest = max(files, key=os.path.getmtime)
        run = json.load(open(newest, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plan = run.get("plan") or {}
    results = run.get("results") or {}
    steps = []
    for s in plan.get("steps", []):
        r = results.get(s.get("id"), {})
        steps.append({"id": s.get("id"), "agent": s.get("agent") or s.get("plugin") or s.get("ref"),
                      "outcome": r.get("outcome") or ("running" if run.get("state") == "running" else "waiting")})
    return {"id": os.path.splitext(os.path.basename(newest))[0], "state": run.get("state"), "summary": plan.get("summary", ""),
            "mode": run.get("mode"), "steps": steps, "age_s": int(time.time() - os.path.getmtime(newest))}


def build_status(fetch=fetch_json, runs_dir=None, watchdog_file=None):
    runs_dir = runs_dir or os.path.join(DATA, "main_agent_runs")
    watchdog_file = watchdog_file or os.path.join(DATA, "logs", "watchdog_status.json")
    # every probe at once: one slow or closed port (a closed loopback port takes ~2 s to refuse on Windows) must not delay or
    # flip the others
    from concurrent.futures import ThreadPoolExecutor
    jobs = {name: (url, 3.0) for name, url in SERVERS.items()}
    jobs["_plugins"] = ("http://127.0.0.1:48916/plugins", 4.0)
    jobs["_health"] = ("http://127.0.0.1:8799/api/health", 6.0)
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futs = {k: pool.submit(fetch, url, to) for k, (url, to) in jobs.items()}
        got = {k: f.result() for k, f in futs.items()}
    servers = {name: got[name] is not None for name in SERVERS}
    listing = got["_plugins"] or {}
    plugin_status = {p.get("id"): p.get("status") for p in listing.get("plugins", []) if isinstance(p, dict)} if servers["plugins"] else {}
    state = got["console"] or {}
    kernel = (got["_health"] or {}).get("kernel", {}) or {}
    approvals = len(state.get("approvals") or [])
    busy = state.get("busy") if servers["console"] else None
    watchdog = {}
    try:
        watchdog = json.load(open(watchdog_file, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    down = [n for n, ok in servers.items() if not ok] + [p for p in PLUGINS if plugin_status.get(p) != "running" and servers["plugins"]]
    return {
        "time": time.strftime("%H:%M:%S"),
        "servers": servers,
        "plugins": {p: plugin_status.get(p, "unknown") for p in PLUGINS},
        "jarvis": {"reachable": bool(kernel.get("reachable")), "authorized": bool(kernel.get("authorized")),
                   "killed": bool(kernel.get("killed")), "busy": busy, "approvals_waiting": approvals},
        "task": newest_run(runs_dir),
        "watchdog": {"healthy": watchdog.get("healthy"), "gave_up_on": watchdog.get("gave_up_on") or [],
                     "checked": watchdog.get("checked")},
        "healthy": not down and bool(kernel.get("reachable")) and not kernel.get("killed"),
        "problems": down,
    }


def start_task(task, mode, fetch=fetch_json):
    """Start a main-agent task through the plugin runtime; report an immediate refusal (busy, bad input) honestly."""
    res = fetch("http://127.0.0.1:48916/runs", 15.0,
                {"plugin_id": "main_agent", "entry_id": "run_task", "args": {"task": task, "mode": mode}})
    run_id = (res or {}).get("run_id")
    if not run_id:
        return {"ok": False, "error": "The main agent did not accept the task (is the plugin running?)."}
    for _ in range(12):                                # the entry answers within a few seconds, or it is planning
        time.sleep(0.5)
        st = fetch(f"http://127.0.0.1:48916/runs/{run_id}", 3.0) or {}
        if st.get("status") in ("failed", "cancelled", "timeout"):
            return {"ok": False, "error": ((st.get("error") or {}).get("message") or "The task was refused.")[:300]}
        if st.get("status") == "succeeded":
            return {"ok": True, "run_id": run_id}
    return {"ok": True, "run_id": run_id, "note": "planning"}


def make_handler(page_path, fetch=fetch_json):
    class Handler(BaseHTTPRequestHandler):
        server_version = "jarvis-widget"

        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def do_GET(self):
            if not host_ok(self.headers.get("Host")):
                return self._json(403, {"error": "loopback only"})
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                try:
                    return self._send(200, open(page_path, "rb").read(), "text/html; charset=utf-8")
                except OSError:
                    return self._json(500, {"error": "page missing"})
            if path == "/api/status":
                return self._json(200, build_status(fetch))
            if path == "/health":
                return self._json(200, {"status": "ok", "service": "jarvis-widget"})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            host = self.headers.get("Host")
            n = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(min(max(n, 0), 64 * 1024))            # always consume the body before answering
            if not host_ok(host) or not origin_ok(self.headers.get("Origin"), host):
                return self._json(403, {"error": "loopback and same origin only"})
            if self.path.split("?", 1)[0] != "/api/task":
                return self._json(404, {"error": "not found"})
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"ok": False, "error": "send JSON"})
            clean, err = validate_task(body)
            if err:
                return self._json(400, {"ok": False, "error": err})
            return self._json(200, start_task(clean["task"], clean["mode"], fetch))

    return Handler


def main():
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    PORT = a.port
    server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(os.path.join(HERE, "index.html")))
    print(f"jarvis-widget on http://127.0.0.1:{a.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
