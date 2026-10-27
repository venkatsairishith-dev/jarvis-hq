"""A pretend VM2 for the tests and for the reliability gate's takeover goals (D79): LABELLED FAKE, not a server.

`FakeLab` is a real HTTP server on 127.0.0.1 that speaks the lab control protocol exactly as lab_control.py does behind the hub (`X-Jarvis-Hub: 1` required; GET /control/state,
POST /control/take, POST /control/continue, GET /control/quarantine, GET /control/quarantine/<id>), and keeps a log of what it was told (`events`: "frozen", "continued", ...) so a
test can check that JARVIS's input REALLY stopped. The quarantine inspection is the REAL lab/quarantine.py. `FakeKernel` is the owner plane's `command` / `commands` pair as the
plugin's `core.owner` sees it. The "lab agent" is a few methods: `start_task`, `hit_wall` (the agent calls need_owner), `owner_solves`, `finish_task`, `download`.

Run it by hand to rehearse a goal:   python -m jarvis.evals.reliability.fake_vm2 --serve --port 18791 --config out.json
(writes a vm2.json for JARVIS_VM2_CONFIG; stop with Ctrl-C). Nothing here reads a secret or touches a real server.
"""
from __future__ import annotations

import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_Q = Path(__file__).resolve().parents[2] / "lab" / "quarantine.py"
_spec = importlib.util.spec_from_file_location("jarvis_lab_quarantine_under_test", _Q)
Q = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(Q)


class FakeLab:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.mode = "agent"
        self.returned_at = None
        self.since = None
        self.tasks: list[dict] = []
        self.takeover: dict | None = None
        self.wall_stays = 0                  # after this many hand-backs the wall is still there (the owner did not really solve it)
        self.wall_kind = ""
        self.wall_host = ""
        self.files: dict[str, bytes] = {}
        self.events: list[str] = []
        self.requests: list[tuple] = []
        self._agains = 0
        self.refuse_take = False             # take control does not freeze (a broken VM2): the card must say so
        self.lock = threading.RLock()
        self.server: ThreadingHTTPServer | None = None
        self.url = ""

    # ---- the lab agent, as a few moves
    def start_task(self, goal: str = "task") -> None:
        with self.lock:
            self.tasks.append({"pid": 100 + len(self.tasks), "agent": "hermes", "goal": goal[:80], "seconds": 1, "paused": self.mode == "owner"})

    def finish_task(self) -> None:
        with self.lock:
            self.tasks = []

    def hit_wall(self, kind: str = "captcha", host: str = "files.example.test") -> None:
        """The agent called need_owner. Its desktop tools wait from now on (in the fake: nothing else happens until the owner hands back)."""
        with self.lock:
            self.wall_kind, self.wall_host = kind, host
            self.takeover = {"kind": kind, "host": host, "at": self.clock()}
            self.events.append("wall:" + kind)

    def owner_solves(self) -> None:
        with self.lock:
            self.wall_kind = ""
            self.wall_stays = 0

    def download(self, name: str, data: bytes) -> None:
        with self.lock:
            self.files[name] = data

    # ---- the control protocol (what lab_control.py answers)
    def state(self) -> dict:
        with self.lock:
            t = self.takeover
            if t and self.returned_at and t["at"] <= self.returned_at:
                t = None
            return {"mode": self.mode, "since": self.since, "returned_at": self.returned_at, "takeover": t, "tasks": [dict(x) for x in self.tasks], "recent": []}

    def take(self) -> dict:
        with self.lock:
            self.mode = "owner"
            self.since = self.clock()
            if not self.refuse_take:
                for t in self.tasks:
                    t["paused"] = True
                self.events.append("frozen")
            else:
                self.events.append("take-ignored")
            return self.state()

    def cont(self) -> dict:
        with self.lock:
            self.mode = "agent"
            self.returned_at = self.clock()
            for t in self.tasks:
                t["paused"] = False
            self.events.append("continued")
            if self.wall_kind and self.wall_stays > 0:          # the agent looks again and the wall is still there: it calls need_owner again
                self.wall_stays -= 1
                self._agains += 1                                  # strictly later each time, even when a fake clock has not moved
                self.takeover = {"kind": self.wall_kind, "host": self.wall_host, "at": self.returned_at + 0.001 * self._agains}
                self.events.append("wall-again")
            return self.state()

    def listing(self) -> dict:
        with self.lock:
            recs = []
            for n, data in self.files.items():
                r = Q.inspect_bytes(n, data)
                r["scan"] = {"tool": "", "result": "not_installed"}
                recs.append(Q.public(r))
            return {"files": recs, "max_bytes": Q.MAX_BYTES}

    def file(self, fid: str):
        with self.lock:
            for n, data in self.files.items():
                r = Q.inspect_bytes(n, data)
                if r["id"] == fid:
                    return r, data
        return None, b""

    # ---- the server
    def serve(self, port: int = 0) -> str:
        lab = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _ok(self):
                lab.requests.append((self.command, self.path, self.headers.get("X-Jarvis-Hub")))
                if self.headers.get("X-Jarvis-Hub") != "1":
                    self._json(403, {"error": "missing hub header"})
                    return False
                return True

            def do_GET(self):
                if not self._ok():
                    return
                p = self.path.split("?")[0].rstrip("/")
                if p == "/control/state":
                    return self._json(200, lab.state())
                if p == "/control/quarantine":
                    return self._json(200, lab.listing())
                if p.startswith("/control/quarantine/"):
                    rec, data = lab.file(p.rsplit("/", 1)[1])
                    if rec is None:
                        return self._json(404, {"error": "no such file in the quarantine"})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("X-Jarvis-Sha256", rec["sha256"])
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                if not self._ok():
                    return
                p = self.path.split("?")[0].rstrip("/")
                if p == "/control/take":
                    return self._json(200, lab.take())
                if p == "/control/continue":
                    return self._json(200, lab.cont())
                self._json(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self.url

    def close(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def config(self, **over) -> dict:
        """A vm2.json for this fake (viewer and control both on 127.0.0.1)."""
        base = {"enabled": True, "viewer_url": self.url + "/vnc.html?autoconnect=1&resize=scale", "control_url": self.url, "probe_timeout_s": 2, "probe_ttl_s": 5,
                "takeover_minutes": 1, "max_reasks": 3, "look_wait_s": 0.2, "poll_s": 0.05, "task_wait_minutes": 1, "quarantine_max_bytes": 5 * 1024 * 1024}
        base.update(over)
        return base


class FakeKernel:
    """The owner plane's `command` and `commands` as core.owner(method, name, body) answers them. A command starts a fake lab task; `done_after` polls later it is 'done'."""

    def __init__(self, lab: FakeLab, start_task: bool = True):
        self.lab, self.start = lab, start_task
        self.commands: list[dict] = []
        self.calls: list[tuple] = []

    def __call__(self, method: str, name: str, body: dict | None = None, timeout: float = 20.0):
        self.calls.append((method, name, dict(body or {})))
        if method == "POST" and name == "command":
            cid = f"c-{len(self.commands) + 1}"
            self.commands.append({"id": cid, "text": str((body or {}).get("text", "")), "state": "done"})
            if self.start:
                self.lab.start_task((body or {}).get("text", ""))
            return 202, {"id": cid, "state": "running"}
        if method == "GET" and name.startswith("commands"):
            return 200, {"commands": list(self.commands)}
        if method == "GET" and name == "site_grants":
            return 200, {"sites": []}
        return 404, {"error": "fake kernel: " + name}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=18791)
    ap.add_argument("--config", help="write a vm2.json for JARVIS_VM2_CONFIG here")
    a = ap.parse_args(argv)
    if not a.serve:
        ap.print_help()
        return 0
    lab = FakeLab()
    url = lab.serve(a.port)
    if a.config:
        Path(a.config).write_text(json.dumps(lab.config(), indent=1), encoding="utf-8")
        print("config written to", a.config)
    print("fake VM2 on", url, "(Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        lab.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
