"""Backend of the JARVIS desktop widget. Windows; standard library only; loopback only; no model calls.

    python widget_server.py [--port 48940]

Serves the single-page widget (index.html) and three same-origin endpoints it needs, so the page never talks to the other
servers directly (no CORS, nothing exposed):
    GET  /api/status   one JSON document: the six servers, the four plugins, the JARVIS kernel and approvals count,
                       the newest main-agent task, and the watchdog's verdict
    POST /api/task     {"task": "...", "mode": "work"}  starts a main-agent task (the same call the plugin panel makes); the
                       answer says whether it is running, still being planned, was refused or was stopped
    POST /api/action   {"action": "stop"} stops the main agent's task; the answer says whether anything was stopped
    GET  /            the page
The widget is read-only for approvals (it shows how many are waiting; approving needs your PIN in the JARVIS console), and it
cannot touch the kill switch.

Authentication (audit M15 2026-10-18; re-review M3 2026-10-19; design and limits in scripts/control_auth.py).
  public-harmless   GET /health, GET /auth/login.js
  sensitive-read    GET /api/status, /api/neko, /api/events (tasks, approvals, deliverables, PC activity), GET /open-console
  state-changing    POST /api/task, /api/action, /api/feedback
A bare `GET /` or `GET /neko` gets a login page with NO secret. The browser signs in with a one-time launch code (open-widget.ps1
fetches it with the master token from `widget.token`, a 0600 file in the per-user run folder, and opens the page at `/#code=...`),
receives an HttpOnly SameSite=Strict cookie bound to this Host:port, and its page carries only the session's double-submit token.
First-party programs (the wake listener) send the master token in `X-Jarvis-Control`. Every request needs an exact loopback
Host (name and port) and, if a browser sends an Origin, this server's own; a POST also needs the credential (master token, or
session cookie plus its double-submit token). A process of the same OS user that can read the token file or the browser profile
can still do what the first-party client does; kernel approvals (PIN) still gate every side effect a started task can cause.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data")
PORT = 48940
SERVERS = {"N.E.K.O": "http://127.0.0.1:48911/health", "memory": "http://127.0.0.1:48912/health",
           "agent": "http://127.0.0.1:48915/health", "plugins": "http://127.0.0.1:48916/health",
           "speech": "http://127.0.0.1:48930/health", "console": "http://127.0.0.1:8799/api/health?minimal=1",
           "privacy": "http://127.0.0.1:48950/health"}   # liveness only: fast and open (the full /api/health may be slow)
PLUGINS = ("jarvis_bridge", "main_agent", "doc_tools", "learn_vault", "local_team", "web_search", "deliverables", "research", "desktop")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # neko_state.py lives next to this file
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))    # control_auth.py
import control_auth   # noqa: E402
MODES = ("chat", "work", "code", "learn")
MAX_TASK = 2000
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
RUNS = "http://127.0.0.1:48916/runs"
ACTIVE_MARKER = "active_run.state"      # plugin/plugins/main_agent/store.py: a task being planned (no run file yet)
FINAL = ("succeeded", "failed", "canceled", "cancelled", "timeout")   # the plugin runtime spells it "canceled"


AUTH_PORTS = {8799: "console", 48945: "wake"}      # servers whose reads need the master token (their 0600 token files)


def _auth_headers(url):
    """The master token of the console / wake listener for a call to them (re-review M3: their private reads need a credential)."""
    u = urllib.parse.urlsplit(url)
    name = AUTH_PORTS.get(u.port) if u.hostname == "127.0.0.1" else None
    return control_auth.headers_for(name) if name else {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                                   # a 3xx is an error, never followed


# Calls to local services (the speech server, the widget, the privacy gateway ...): an explicit empty proxy map, so http_proxy /
# https_proxy / ALL_PROXY in the environment can never send the owner's voice or text to a proxy instead of 127.0.0.1 (re-review M13).
# The same rule as plugin/plugins/_shared/loopback_http.py, repeated because this program does not import the plugin package.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
_LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _open_local(req, timeout=None):
    host = (urllib.parse.urlsplit(req.full_url if isinstance(req, urllib.request.Request) else str(req)).hostname or "").lower()
    if host not in _LOOPBACK:
        raise ValueError("this call may only go to this machine, so nothing was sent")
    return _LOCAL_OPENER.open(req, timeout=timeout)


def fetch_json(url, timeout=2.5, data=None):
    """GET (or POST a JSON body) and parse JSON; None on any failure."""
    try:
        headers = dict(_auth_headers(url))
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, method="POST" if data is not None else "GET",
                                     data=json.dumps(data).encode() if data is not None else None, headers=headers)
        with _open_local(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "null")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def host_ok(host_header):
    host = (host_header or "").lower().rsplit(":", 1)[0].strip("[]")
    return host in LOCAL_HOSTS


def origin_ok(origin, host_header):
    """Origin shape check only (kept for its tests). It is NOT authentication: a local program can write any Origin. POST routes
    are decided by `_post_allowed` in make_handler (secret + exact Host + allow-listed Origin)."""
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


def _planning_marker(runs_dir):
    """main_agent's marker for a task that is being planned, or was and never started (stopped, or the plan failed)."""
    path = os.path.join(runs_dir, ACTIVE_MARKER)
    try:
        mark = json.load(open(path, encoding="utf-8"))
        at = os.path.getmtime(path)
    except (OSError, ValueError):
        return None
    if not isinstance(mark, dict):
        return None
    state = str(mark.get("state") or "")
    summary = "Planning your task" if state == "planning" else ""
    return at, {"id": str(mark.get("run_id") or ""), "state": state, "summary": summary, "mode": mark.get("mode"),
                "steps": [], "error": str(mark.get("error") or "")[:300], "age_s": int(time.time() - at)}


def newest_run(runs_dir):
    """The main agent's newest task: its run file, or the planning marker when that is newer (a task still being planned
    has no run file yet, and a stop during planning or a failed plan never gets one)."""
    marker = _planning_marker(runs_dir)
    try:
        files = [os.path.join(runs_dir, f) for f in os.listdir(runs_dir) if f.endswith(".json")]
        newest = max(files, key=os.path.getmtime)
        if marker and marker[0] >= os.path.getmtime(newest):
            return marker[1]
        run = json.load(open(newest, encoding="utf-8"))
    except (OSError, ValueError):
        return marker[1] if marker else None
    plan = run.get("plan") or {}
    results = run.get("results") or {}
    steps = []
    for s in plan.get("steps", []):
        r = results.get(s.get("id"), {})
        steps.append({"id": s.get("id"), "agent": s.get("agent") or s.get("plugin") or s.get("ref"),
                      "outcome": r.get("outcome") or ("running" if run.get("state") == "running" else "waiting")})
    out = {"id": os.path.splitext(os.path.basename(newest))[0], "state": run.get("state"), "summary": plan.get("summary", ""),
           "mode": run.get("mode"), "steps": steps, "age_s": int(time.time() - os.path.getmtime(newest))}
    req = run.get("pending_input")
    # only THAT a question waits (its text and fields stay with main_agent / the chat card): the icon can say so
    if isinstance(req, dict) and req.get("status") == "open" and float(req.get("expires") or 0) > time.time():
        out["waiting_for_input"] = True
    if run.get("error"):
        out["error"] = str(run["error"])[:300]
    return out


OPS_FRESH_S = 20.0      # the desktop plugin rewrites its operations snapshot every 5 s while any PC operation lives


def pc_operations(path=None):
    """The desktop plugin's registry snapshot (plugin/plugins/desktop/coordinator.publish): which PC operations (a task, a mission,
    a single action, a troubleshooter ...) are registered right now. It is rewritten every few seconds for as long as any
    operation lives, whatever the operation is doing, so a long step (research up to 16 minutes) never looks idle; it counts as
    stale only when the snapshot itself stops being refreshed (the plugin process is gone). None when it cannot be read."""
    try:
        a = json.load(open(path or os.path.join(DATA, "neko", "pc_operations.json"), encoding="utf-8"))
        ops = [o for o in (a.get("operations") or []) if isinstance(o, dict)]
        return {"busy": bool(a.get("busy")) and bool(ops), "age_s": time.time() - float(a.get("at") or 0), "operations": ops,
                "approvals_pending": int(a.get("approvals_pending") or 0)}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def pc_busy(path=None, ops_path=None):
    """Is the desktop plugin doing something on the PC right now? The authoritative answer is the plugin's operation registry
    (pc_operations): busy exactly while an operation is registered, however long its current step takes. The older activity file
    (entries older than neko_state.ACTIVITY_FOR_S are ignored) is the fallback for a plugin that does not publish the registry.
    {"busy": bool, "state", "detail"}, and for a registry answer also the operations and source "registry"."""
    import neko_state as ns
    reg = pc_operations(ops_path)
    if reg and reg["busy"] and reg["age_s"] <= OPS_FRESH_S:
        ops = reg["operations"]
        first = ops[0]
        stopping = [o for o in ops if o.get("stop_requested_s_ago") is not None]
        detail = f"{len(ops)} PC operation(s): " + ", ".join(f"{o.get('kind')} {o.get('state')}" for o in ops[:3])
        return {"busy": True, "state": str(first.get("state") or "running"), "detail": detail[:200], "source": "registry",
                "operations": [{k: o.get(k) for k in ("id", "kind", "state", "age_s", "idle_s")} for o in ops[:5]],
                "stopping": len(stopping)}
    act = pc_activity(path)
    if act and act["age_s"] <= ns.ACTIVITY_FOR_S and act["state"] in ns.PC_STEPS:
        return {"busy": True, "state": act["state"], "detail": act["detail"]}
    return {"busy": False, "state": "", "detail": ""}


def build_status(fetch=fetch_json, runs_dir=None, watchdog_file=None, activity_file=None, ops_file=None):
    runs_dir = runs_dir or os.path.join(DATA, "main_agent_runs")
    watchdog_file = watchdog_file or os.path.join(DATA, "logs", "watchdog_status.json")
    # every probe at once: one slow or closed port (a closed loopback port takes ~2 s to refuse on Windows) must not delay or
    # flip the others
    from concurrent.futures import ThreadPoolExecutor
    jobs = {name: (url, 3.0) for name, url in SERVERS.items()}
    jobs["_plugins"] = ("http://127.0.0.1:48916/plugins", 4.0)
    jobs["_health"] = ("http://127.0.0.1:8799/api/health", 6.0)
    jobs["_owner"] = ("http://127.0.0.1:8799/api/owner/state", 3.0)      # approvals waiting and busy: a private read, sent with the console token
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futs = {k: pool.submit(fetch, url, to) for k, (url, to) in jobs.items()}
        got = {k: f.result() for k, f in futs.items()}
    servers = {name: got[name] is not None for name in SERVERS}
    listing = got["_plugins"] or {}
    plugin_status = {p.get("id"): p.get("status") for p in listing.get("plugins", []) if isinstance(p, dict)} if servers["plugins"] else {}
    state = got["_owner"] or {}
    kernel = (got["_health"] or {}).get("kernel", {}) or {}
    approvals = len(state.get("approvals") or [])
    busy = state.get("busy") if servers["console"] else None
    watchdog = {}
    try:
        watchdog = json.load(open(watchdog_file, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    down = [n for n, ok in servers.items() if not ok] + [p for p in PLUGINS if plugin_status.get(p) != "running" and servers["plugins"]]
    # an answering server is not a ready one (completion H): the speech server and the privacy gateway say ready=false when a model is missing,
    # and the console says whether its kernel accepted the token
    for name in ("speech", "privacy"):
        h = got.get(name)
        if isinstance(h, dict) and (h.get("ready") is False or h.get("status") == "degraded"):
            down.append(name + " (degraded: " + ", ".join(str(x) for x in (h.get("degraded") or h.get("problems") or ["not ready"])[:3]) + ")")
    if servers["console"] and kernel.get("reachable") and not kernel.get("authorized"):
        down.append("kernel (token refused)")
    if servers["console"] and not kernel.get("reachable"):
        down.append("kernel (not reachable)")
    return {
        "time": time.strftime("%H:%M:%S"),
        "servers": servers,
        "plugins": {p: plugin_status.get(p, "unknown") for p in PLUGINS},
        "jarvis": {"reachable": bool(kernel.get("reachable")), "authorized": bool(kernel.get("authorized")),
                   "killed": bool(kernel.get("killed")), "busy": busy, "approvals_waiting": approvals},
        "task": newest_run(runs_dir),
        "pc": pc_busy(activity_file, ops_file),                  # a PC action the main agent does not know about (so "stop" and the wake word can see it)
        "watchdog": {"healthy": watchdog.get("healthy"), "gave_up_on": watchdog.get("gave_up_on") or [],
                     "checked": watchdog.get("checked")},
        "healthy": not down and bool(kernel.get("reachable")) and bool(kernel.get("authorized")) and not kernel.get("killed"),
        "problems": down,
    }


def entry_result(run_id, fetch=fetch_json):
    """What a finished plugin run's entry returned (its Ok value), from the runtime's export; None when it cannot be read.
    GET /runs/{id} has no result: the runtime stores it as the export item labelled "trigger_response" (json.data)."""
    ex = fetch(f"{RUNS}/{run_id}/export", 3.0)
    items = ex.get("items") if isinstance(ex, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("label") == "trigger_response" or (item.get("metadata") or {}).get("kind") == "trigger_response":
            env = item.get("json") if isinstance(item.get("json"), dict) else {}
            data = env.get("data")
            return data if isinstance(data, dict) else None
    return None


def _run_error(st, default):
    return (((st.get("error") or {}) if isinstance(st.get("error"), dict) else {}).get("message") or default)[:300]


def start_task(task, mode, fetch=fetch_json, runs_dir=None):
    """Start a main-agent task through the plugin runtime and say truthfully where it is: running (planned and started),
    still being planned (the outcome is then said in N.E.K.O's chat and shown on the icon), refused, or stopped."""
    res = fetch(RUNS, 15.0, {"plugin_id": "main_agent", "entry_id": "run_task",
                             "args": {"task": task, "mode": mode, "source": "widget"}})
    run_id = (res or {}).get("run_id")
    if not run_id:
        return {"ok": False, "error": "The main agent did not accept the task (is the plugin running?)."}
    for _ in range(12):                                # the entry answers within a few seconds, or it is planning
        time.sleep(0.5)
        st = fetch(f"{RUNS}/{run_id}", 3.0) or {}
        status = st.get("status")
        if status in ("failed", "canceled", "cancelled", "timeout"):
            return {"ok": False, "error": _run_error(st, "The task was refused.")}
        if status == "succeeded":
            data = entry_result(run_id, fetch) or {}
            state = str(data.get("state") or "running")
            out = {"ok": state != "cancelled", "run_id": str(data.get("run_id") or run_id), "state": state}
            if data.get("message"):
                out["message" if state != "cancelled" else "error"] = str(data["message"])[:300]
            return out
    out = {"ok": True, "run_id": run_id, "state": "planning",
           "note": "planning: the result will be said in the chat and shown on the icon"}
    task_now = newest_run(runs_dir or os.path.join(DATA, "main_agent_runs"))
    if task_now and task_now.get("state") == "planning" and task_now.get("id"):
        out["run_id"] = task_now["id"]
    return out


# ---------- Neko icon: one state from real signals, pushed live ----------

DELIVERABLES_ROOT = os.environ.get("DELIVERABLES_OUT_DIR", r"D:\jarvis\outputs\neko\deliverables")
WAKE_STATUS = "http://127.0.0.1:48945/status"
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
    if not isinstance(q, dict):
        return None
    qa = q.get("qa") if isinstance(q.get("qa"), dict) else {}
    checks = qa.get("checks") if isinstance(qa.get("checks"), dict) else {}
    passed = qa.get("passed") if isinstance(qa.get("passed"), bool) else None     # anything else is "not known", never a pass
    return {"file": q.get("file"), "folder": d, "name": os.path.basename(q.get("file") or d), "kind": q.get("kind"),
            "passed": passed, "failed_checks": [str(k)[:40] for k, ok in checks.items() if not ok][:8],
            "age_s": int(time.time() - os.path.getmtime(os.path.join(d, "qa.json")))}


def pc_activity(path=None):
    """What the desktop plugin is doing on the PC (inspecting, diagnosing, ...), written by plugin/plugins/desktop."""
    try:
        a = json.load(open(path or os.path.join(DATA, "neko", "activity.json"), encoding="utf-8"))
        return {"state": str(a.get("state") or ""), "detail": str(a.get("detail") or "")[:200], "age_s": time.time() - float(a.get("at") or 0)}
    except (OSError, ValueError, TypeError):
        return None


def cached_status(fetch=fetch_json, max_age=5.0):
    """The full status (nine probes) at most every few seconds; the fast signals below are read every second."""
    if _STATUS_CACHE["value"] is None or time.time() - _STATUS_CACHE["at"] > max_age:
        _STATUS_CACHE.update(value=build_status(fetch), at=time.time())
    return _STATUS_CACHE["value"]


def neko_state(fetch=fetch_json):
    import neko_state as ns
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        g = pool.submit(fetch, "http://127.0.0.1:48950/health", 1.5)
        sp = pool.submit(fetch, "http://127.0.0.1:48930/activity", 1.5)
        own = pool.submit(fetch, "http://127.0.0.1:8799/api/owner/state", 2.5)
        wk = pool.submit(fetch, WAKE_STATUS, 1.0)       # the Hey Neko listener (jarvis/wake); None when it is not running
        gateway, speech, owner, wake = g.result(), sp.result(), own.result(), wk.result()
    status = cached_status(fetch)
    approvals = (owner or {}).get("approvals") or []
    activity = pc_activity()
    out = ns.decide(status, approvals, gateway, speech, newest_deliverable(), wake, activity)
    out["voice"] = ns.voice_view(out, status, wake, activity)       # phase in the owner's vocabulary + the real microphone state
    out["time"] = time.strftime("%H:%M:%S")
    return out


def run_plugin_entry(plugin, entry, args, fetch=fetch_json, wait_s=8.0):
    """Run one plugin entry. On success "result" is what the entry returned (None when it could not be read)."""
    res = fetch(RUNS, 10.0, {"plugin_id": plugin, "entry_id": entry, "args": args})
    run_id = (res or {}).get("run_id")
    if not run_id:
        return {"ok": False, "error": f"the {plugin} plugin did not accept the request"}
    for _ in range(int(wait_s * 2)):
        time.sleep(0.5)
        st = fetch(f"{RUNS}/{run_id}", 3.0) or {}
        if st.get("status") == "succeeded":
            return {"ok": True, "result": entry_result(run_id, fetch)}
        if st.get("status") in ("failed", "canceled", "cancelled", "timeout"):
            return {"ok": False, "error": _run_error(st, "refused")[:200]}
    return {"ok": True, "note": "still running"}


def stop_main_task(fetch=fetch_json):
    """Stop the main agent's task and report what main_agent actually answered. ok only when what was running has really ended
    (main_agent waits a bounded time for that); a stop that was sent but not yet confirmed is `requested` with `still_running`,
    shown as "stop requested, N still running", never as ok."""
    res = run_plugin_entry("main_agent", "stop_task", {}, fetch, wait_s=14.0)
    if not res.get("ok"):
        return {"ok": False, "stopped": False, "error": res.get("error") or "the stop request failed"}
    if res.get("note") == "still running":
        return {"ok": False, "stopped": None, "error": "the stop request did not finish; whether the task stopped is not known"}
    data = res.get("result")
    if not isinstance(data, dict) or not isinstance(data.get("stopped"), bool):
        return {"ok": False, "stopped": None, "error": "the stop was sent, but whether anything stopped is not known"}
    message = str(data.get("message") or "")[:200]
    requested = data["requested"] if isinstance(data.get("requested"), bool) else data["stopped"]     # an older main_agent: stopped only
    still = data.get("still_running")
    n = len(still) if isinstance(still, list) else (still if isinstance(still, int) and not isinstance(still, bool) else 0)
    if requested and not data["stopped"]:
        left = f"{n} still running" if n else "not yet confirmed ended"
        return {"ok": False, "stopped": False, "requested": True, "still_running": n,
                "error": f"stop requested, {left}", "message": message}
    if not data["stopped"]:
        return {"ok": False, "stopped": False, "error": "nothing to stop", "message": message}
    return {"ok": True, "stopped": True, "requested": True, "still_running": 0, "already": bool(data.get("already")), "message": message}


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
        return stop_main_task(fetch)
    entry = {"open": "open_output", "folder": "show_in_folder", "regenerate": "regenerate"}[act]
    args = {"folder": os.path.dirname(clean["path"])} if act == "regenerate" else {"path": clean["path"]}
    return run_plugin_entry("deliverables", entry, args, fetch, wait_s=60.0 if act == "regenerate" else 8.0)


CONSOLE_CODE_URL = "http://127.0.0.1:8799/auth/code"


def with_secret(html: bytes, secret: str) -> bytes:
    """Put the SESSION's double-submit token into a page the server serves, as <meta name="jarvis-widget-secret"> (never the master token)."""
    tag = ('<meta name="jarvis-widget-secret" content="%s">' % secret).encode()
    low = html.lower()
    for marker in (b"</head>", b"<body"):
        i = low.find(marker)
        if i >= 0:
            return html[:i] + tag + html[i:]
    return html + tag


def make_handler(page_path, fetch=fetch_json, secret=None):
    """`secret`: this launch's control secret (a fresh random one when not given; read it back from `Handler.control_secret`)."""
    gate = control_auth.Gate("widget", control_auth.HEADER, "jarvis_widget_session")
    if secret:
        gate.master = secret
    control_secret = gate.master

    class Handler(BaseHTTPRequestHandler):
        server_version = "jarvis-widget"

        def _post_allowed(self):
            """Who may change anything: the master token, or a signed-in session plus its double-submit token; an exact loopback
            Host on this port; and no foreign Origin."""
            return gate.write_ok(self.headers, self.server.server_address[1])

        def _read_allowed(self):
            return gate.read_ok(self.headers, self.server.server_address[1])

        def _page(self, path):
            """An HTML page: only to a signed-in request (it then carries the session's double-submit token, never the master
            token); everyone else gets the login page, which holds no secret."""
            token = gate.page_token(self.headers)
            if token is None and not gate.master_ok(self.headers):
                return self._send(401, control_auth.login_page("JARVIS widget"), "text/html; charset=utf-8")
            try:
                return self._send(200, with_secret(open(path, "rb").read(), token or ""), "text/html; charset=utf-8")
            except OSError:
                return self._json(500, {"error": "page missing"})

        def _auth(self, method, path):
            if method == "GET" and path == "/auth/login.js":
                return self._send(200, control_auth.LOGIN_JS.encode(), "application/javascript; charset=utf-8")
            try:
                n = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                n = 0
            body = self.rfile.read(min(max(n, 0), 4096)) if method == "POST" else b""
            res = gate.auth_route(method, path, self.headers, body, self.server.server_address[1])
            if res is None:
                return self._json(404, {"error": "not found"})
            code, extra, reply = res
            return self._send(code, json.dumps(reply).encode(), "application/json", extra)

        def _open_console(self):
            """The widget's "Open console" link: a signed-in widget asks the console for a one-time launch code (with the console's
            master token from console.token) and sends the browser there. Same-origin navigations only (Sec-Fetch-Site), so another
            page cannot make the owner's browser open a signed-in console."""
            if self.headers.get("Sec-Fetch-Site") not in ("same-origin", "none"):
                return self._json(403, {"error": "open it from the widget page"})
            res = fetch(CONSOLE_CODE_URL, 4.0, {})
            code = (res or {}).get("code") if isinstance(res, dict) else None
            if not code:
                return self._json(503, {"error": "the console did not give a launch code (is it running, and is console.token readable?)"})
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:8799/#code={code}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", "frame-ancestors 'none'")      # no other site may frame the widget
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
            if not host_ok(control_auth.host_value(self.headers)):
                return self._json(403, {"error": "loopback only"})
            path = self.path.split("?", 1)[0]
            if path.startswith("/auth/"):
                return self._auth("GET", path)
            if path == "/health":
                return self._json(200, {"status": "ok", "service": "jarvis-widget", "liveness_only": True})    # this process answers; component health is /api/status (needs sign-in)
            if path in ("/", "/index.html"):
                return self._page(page_path)
            if path == "/neko":
                return self._page(os.path.join(HERE, "neko.html"))
            if path in ("/api/status", "/api/neko", "/api/events", "/open-console"):
                if not self._read_allowed():
                    return self._json(401, {"error": "not signed in: open the widget from JARVIS (a one-time link) or send the widget token",
                                            "login": True})
                if path == "/api/status":
                    return self._json(200, build_status(fetch))
                if path == "/api/neko":
                    return self._json(200, neko_state(fetch))
                if path == "/open-console":
                    return self._open_console()
                return self._events()
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path.split("?", 1)[0].startswith("/auth/"):
                return self._auth("POST", self.path.split("?", 1)[0])
            try:
                n = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                n = 0
            raw = self.rfile.read(min(max(n, 0), 64 * 1024))            # always consume the body before answering
            if not self._post_allowed():
                return self._json(403, {"error": "refused: loopback, same origin and this launch's control secret are all required",
                                        "reload": True})
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

    Handler.control_secret = control_secret          # the MASTER token, for tests and main(); never served by an API
    Handler.gate = gate
    return Handler


def main():
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    PORT = a.port
    handler = make_handler(os.path.join(HERE, "index.html"))
    server = ThreadingHTTPServer(("127.0.0.1", a.port), handler)
    try:
        control_auth.publish("widget", handler.control_secret)        # for the wake listener and the owner's own scripts
    except OSError as e:
        print(f"warning: could not publish the control token ({type(e).__name__}); first-party programs cannot start tasks", flush=True)
    print(f"jarvis-widget on http://127.0.0.1:{a.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        control_auth.unpublish("widget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
