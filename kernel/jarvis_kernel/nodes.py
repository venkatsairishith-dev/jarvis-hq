"""Node registry, pairing and the HTTP endpoint nodes report to. Nodes connect outbound; the kernel never connects to them.

Pairing: the operator creates a one-time code on the kernel host; the node trades it once for a bearer token.
Only a hash of each token and code is stored.
"""
import hashlib
import hmac
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OFFLINE_AFTER = 45  # seconds without a heartbeat (nodes report every 15 s)
MAX_BODY = 256 * 1024


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class Nodes:
    def __init__(self, kernel):
        self.k = kernel
        kernel.db.execute("CREATE TABLE IF NOT EXISTS pair_codes(h TEXT PRIMARY KEY, expires REAL, used INT)")
        kernel.db.execute(
            "CREATE TABLE IF NOT EXISTS nodes(name TEXT PRIMARY KEY, token_h TEXT, last_seen REAL, state TEXT, killed_ack INT)"
        )
        kernel.db.commit()

    def new_code(self, ttl=600) -> str:
        code = secrets.token_urlsafe(9)
        self.k.db.execute("INSERT INTO pair_codes VALUES (?,?,0)", (_hash(code), time.time() + ttl))
        self.k.db.commit()
        self.k.audit.append("pair_code_created", {})
        return code

    def pair(self, code: str, name: str):
        r = self.k.db.execute("SELECT expires, used FROM pair_codes WHERE h=?", (_hash(code),)).fetchone()
        if not r or r[1] or r[0] < time.time() or not name or len(name) > 64:
            self.k.audit.append("pair_refused", {"name": name[:64] if isinstance(name, str) else ""})
            return None
        self.k.db.execute("UPDATE pair_codes SET used=1 WHERE h=?", (_hash(code),))
        token = secrets.token_urlsafe(32)
        self.k.db.execute("INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,0)", (name, _hash(token), time.time(), "{}"))
        self.k.db.commit()
        self.k.audit.append("node_paired", {"name": name})
        return token

    def report(self, token: str, state: dict):
        th = _hash(token)
        for name, h in self.k.db.execute("SELECT name, token_h FROM nodes"):
            if hmac.compare_digest(h, th):
                self.k.db.execute("UPDATE nodes SET last_seen=?, state=? WHERE name=?", (time.time(), json.dumps(state), name))
                self.k.db.commit()
                self.k.audit.append("node_report", {"name": name, "keys": sorted(state.keys())[:20]})  # summary only
                return name
        self.k.audit.append("report_refused", {})
        return None

    def identify(self, token: str):
        th = _hash(token)
        for name, h in self.k.db.execute("SELECT name, token_h FROM nodes"):
            if hmac.compare_digest(h, th):
                return name
        return None

    def list(self):
        out = []
        for name, last, state in self.k.db.execute("SELECT name, last_seen, state FROM nodes"):
            age = time.time() - last
            out.append({"name": name, "online": age <= OFFLINE_AFTER, "seconds_since_report": round(age, 1), "state": json.loads(state)})
        return out


def make_handler(kernel):
    nodes = Nodes(kernel)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep output quiet; the audit log is the record
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n <= 0 or n > MAX_BODY:
                    return self._json(413, {"error": "bad size"})
                data = json.loads(self.rfile.read(n))
                if self.path == "/pair":
                    tok = nodes.pair(str(data.get("code", "")), str(data.get("name", "")))
                    return self._json(200, {"token": tok}) if tok else self._json(403, {"error": "refused"})
                if self.path == "/report":
                    auth = self.headers.get("Authorization", "")
                    name = nodes.report(auth[7:] if auth.startswith("Bearer ") else "", data.get("state", {}))
                    if not name:
                        return self._json(401, {"error": "unauthorized"})
                    return self._json(200, {"ok": True, "killed": kernel.killed()})
                if self.path in ("/jobs/next", "/jobs/result"):
                    auth = self.headers.get("Authorization", "")
                    name = nodes.identify(auth[7:] if auth.startswith("Bearer ") else "")
                    if not name:
                        kernel.audit.append("report_refused", {"path": self.path})
                        return self._json(401, {"error": "unauthorized"})
                    from .jobs import Jobs
                    jobs = Jobs(kernel)
                    if self.path == "/jobs/next":
                        if kernel.killed():
                            return self._json(200, {"job": None, "killed": True})
                        return self._json(200, {"job": jobs.next_for(name)})
                    res = data.get("result")
                    ok = isinstance(data.get("id"), str) and isinstance(res, dict) and jobs.finish(name, data["id"], res)
                    return self._json(200, {"accepted": bool(ok)})
                if self.path in ("/request", "/authorize"):
                    auth = self.headers.get("Authorization", "")
                    name = nodes.identify(auth[7:] if auth.startswith("Bearer ") else "")
                    if not name:
                        kernel.audit.append("report_refused", {"path": self.path})
                        return self._json(401, {"error": "unauthorized"})
                    from .policy import Action
                    from .store import Killed
                    raw = data.get("action", {})
                    a = Action(tool=str(raw.get("tool", "")), op=str(raw.get("op", "")), target=str(raw.get("target", "")),
                               params=raw.get("params") if isinstance(raw.get("params"), dict) else {},
                               lane=str(raw.get("lane", "private")), untrusted_session=bool(raw.get("untrusted_session", False)))
                    if self.path == "/request":
                        d, aid = kernel.request(a)
                        return self._json(200, {"level": d.name, "reason": d.reason, "approval_id": aid})
                    try:
                        d = kernel.authorize(a, str(data.get("approval_id") or "") or None)
                    except Killed:
                        return self._json(200, {"allowed": False, "level": "KILLED", "reason": "kill switch is on"})
                    return self._json(200, {"allowed": d.allowed, "level": d.name, "reason": d.reason})
                self._json(404, {"error": "not found"})
            except Exception:
                self._json(400, {"error": "bad request"})

    return H, nodes


def serve(kernel, bind: str, port: int):
    H, _ = make_handler(kernel)
    ThreadingHTTPServer((bind, port), H).serve_forever()
