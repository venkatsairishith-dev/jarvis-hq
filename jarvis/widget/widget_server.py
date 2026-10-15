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
PLUGINS = ("jarvis_bridge", "main_agent", "doc_tools", "learn_vault", "local_team", "web_search", "deliverables", "research")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # neko_state.py lives next to this file
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


# ---------- Neko icon: one state from real signals, pushed live ----------

DELIVERABLES_ROOT = os.environ.get("DELIVERABLES_OUT_DIR", r"D:\jarvis\outputs\neko\deliverables")
_STATUS_CACHE = {"at": 0.0, "value": None}
ACTIONS = ("open", "folder", "regenerate", "stop")


def newest_deliverable(root=None):
    root = root or DELIVERABLES_ROOT
    try:
        dirs = [os.path.join(root, d) for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "qa.json"))]
        d = max(dirs, key=lambda p: os.path.getmtime(os.path.join(p, "qa.json")))
        q = json.load(open(os.path.join(d, "qa.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {"file": q.get("file"), "folder": d, "name": os.path.basename(q.get("file") or d), "kind": q.get("kind"),
            "passed": (q.get("qa") or {}).get("passed"), "age_s": int(time.time() - os.path.getmtime(os.path.join(d, "qa.json")))}


def cached_status(fetch=fetch_json, max_age=5.0):
    """The full status (nine probes) at most every few seconds; the fast signals below are read every second."""
    if _STATUS_CACHE["value"] is None or time.time() - _STATUS_CACHE["at"] > max_age:
        _STATUS_CACHE.update(value=build_status(fetch), at=time.time())
    return _STATUS_CACHE["value"]


def neko_state(fetch=fetch_json):
    import neko_state as ns
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        g = pool.submit(fetch, "http://127.0.0.1:48950/health", 1.5)
        sp = pool.submit(fetch, "http://127.0.0.1:48930/activity", 1.5)
        own = pool.submit(fetch, "http://127.0.0.1:8799/api/owner/state", 2.5)
        gateway, speech, owner = g.result(), sp.result(), own.result()
    status = cached_status(fetch)
    approvals = (owner or {}).get("approvals") or []
    out = ns.decide(status, approvals, gateway, speech, newest_deliverable())
    out["time"] = time.strftime("%H:%M:%S")
    return out


def run_plugin_entry(plugin, entry, args, fetch=fetch_json, wait_s=8.0):
    res = fetch("http://127.0.0.1:48916/runs", 10.0, {"plugin_id": plugin, "entry_id": entry, "args": args})
    run_id = (res or {}).get("run_id")
    if not run_id:
        return {"ok": False, "error": f"the {plugin} plugin did not accept the request"}
    for _ in range(int(wait_s * 2)):
        time.sleep(0.5)
        st = fetch(f"http://127.0.0.1:48916/runs/{run_id}", 3.0) or {}
        if st.get("status") == "succeeded":
            return {"ok": True}
        if st.get("status") in ("failed", "cancelled", "timeout"):
            return {"ok": False, "error": ((st.get("error") or {}).get("message") or "refused")[:200]}
    return {"ok": True, "note": "still running"}


def validate_action(body):
    if not isinstance(body, dict) or body.get("action") not in ACTIONS:
        return None, "action must be one of " + ", ".join(ACTIONS)
    act = body["action"]
    if act == "stop":
        return {"action": act}, None
    path = os.path.realpath(str(body.get("path") or ""))
    root = os.path.realpath(DELIVERABLES_ROOT)
    if not path.startswith(root + os.sep):
        return None, "only files Neko made can be opened here"
    return {"action": act, "path": path}, None


FEEDBACK_FILE = os.path.join(DATA, "neko", "feedback.jsonl")


def validate_feedback(body):
    """{"rating": "up"|"down", "file"?: path of a made file, "run_id"?: str, "note"?: short text}."""
    if not isinstance(body, dict) or body.get("rating") not in ("up", "down"):
        return None, "rating must be up or down"
    note = " ".join(str(body.get("note") or "").split())[:300]
    rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rating": body["rating"], "note": note,
           "run_id": str(body.get("run_id") or "")[:64]}
    if body.get("file"):
        path = os.path.realpath(str(body["file"]))
        if not path.startswith(os.path.realpath(DELIVERABLES_ROOT) + os.sep):
            return None, "feedback can only point at files Neko made"
        rec["file"] = path
    return rec, None


def save_feedback(rec, path=None):
    path = path or FEEDBACK_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "saved": True}


def do_action(clean, fetch=fetch_json):
    act = clean["action"]
    if act == "stop":
        return run_plugin_entry("main_agent", "stop_task", {}, fetch)
    entry = {"open": "open_output", "folder": "show_in_folder", "regenerate": "regenerate"}[act]
    args = {"folder": os.path.dirname(clean["path"])} if act == "regenerate" else {"path": clean["path"]}
    return run_plugin_entry("deliverables", entry, args, fetch, wait_s=60.0 if act == "regenerate" else 8.0)


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

        def _events(self):
            """Server-sent events: the Neko state whenever it changes (checked every second), a comment every 15 s to
            keep the connection, closed after 10 minutes (the page reconnects)."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            last, quiet, t0 = None, 0, time.time()
            try:
                while time.time() - t0 < 600:
                    st = neko_state(fetch)
                    key = json.dumps({k: v for k, v in st.items() if k != "time"}, sort_keys=True)
                    if key != last:
                        self.wfile.write(f"data: {json.dumps(st, ensure_ascii=False)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        last, quiet = key, 0
                    else:
                        quiet += 1
                        if quiet >= 15:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                            quiet = 0
                    time.sleep(1.0)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

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
            if path == "/neko":
                try:
                    return self._send(200, open(os.path.join(HERE, "neko.html"), "rb").read(), "text/html; charset=utf-8")
                except OSError:
                    return self._json(500, {"error": "page missing"})
            if path == "/api/neko":
                return self._json(200, neko_state(fetch))
            if path == "/api/events":
                return self._events()
            if path == "/health":
                return self._json(200, {"status": "ok", "service": "jarvis-widget"})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            host = self.headers.get("Host")
            n = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(min(max(n, 0), 64 * 1024))            # always consume the body before answering
            if not host_ok(host) or not origin_ok(self.headers.get("Origin"), host):
                return self._json(403, {"error": "loopback and same origin only"})
            route = self.path.split("?", 1)[0]
            if route not in ("/api/task", "/api/action", "/api/feedback"):
                return self._json(404, {"error": "not found"})
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"ok": False, "error": "send JSON"})
            if route == "/api/feedback":
                rec, err = validate_feedback(body)
                if err:
                    return self._json(400, {"ok": False, "error": err})
                return self._json(200, save_feedback(rec))
            if route == "/api/action":
                clean, err = validate_action(body)
                if err:
                    return self._json(400, {"ok": False, "error": err})
                return self._json(200, do_action(clean, fetch))
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
