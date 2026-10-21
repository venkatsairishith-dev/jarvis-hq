"""Test doubles for the screen-text tests (desktop, main_agent, research, _shared). LABELLED FAKES, not the real privacy gateway:
FakeGateway speaks the gateway's HTTP surface (/health, /redact) on a real local socket and swaps the made-up canaries below for
placeholders, so the plugins' plumbing can be checked at the HTTP edge. The real redactor is exercised in
jarvis/privacy/test_privacy.py and in research/tests/test_gateway_required.py::TestRealGateway (they need Presidio + spaCy).

All canaries are synthetic; none is real data."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

C_OCR = "CANARY-OCR-9d41-Priyanka-SYNTH"
C_PATH = r"C:\Users\SYNTH-Owner\Salary-2026.xlsx"
C_FILE = "Salary-2026.xlsx"
C_KEY = "sk-" + "CANARY9d41SYNTHKEY" + "0123456789abcdef"          # API-key shaped; built at run time
C_EMAIL = "priyanka.synth9d41@example.invalid"
C_STREET = "CANARY-9d41-12-Elm-Street-SYNTH"
ALL = (C_OCR, C_PATH, C_FILE, C_KEY, C_EMAIL, C_STREET, "SYNTH-Owner")

READY = {"ok": True, "ready": True, "degraded": [], "pii_model": True, "names": False, "public_egress": True}
# the gateway's own answer to /redact, as the real one gives it when names and the PII model were active
CONFIRMED = {"names": True, "deep": True, "ready": True}


class FakeGateway:
    def __init__(self, health=None, redact_extra=None, raw_health=None, swap=None):
        self.health, self.raw_health = (dict(READY) if health is None else health), raw_health
        self.redact_extra = dict(CONFIRMED) if redact_extra is None else redact_extra
        self.swap = swap if swap is not None else {C_OCR: "[PERSON_1]", C_STREET: "[LOCATION_1]", C_EMAIL: "[EMAIL_1]",
                                                   C_PATH: "[PATH_1]", C_FILE: "[FILE_1]", C_KEY: "[API_KEY_1]", "SYNTH-Owner": "[PERSON_2]"}
        self.redact_requests, self.health_requests = [], 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, raw):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                outer.health_requests += 1
                self._send(200, outer.raw_health if outer.raw_health is not None else json.dumps(outer.health).encode())

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.redact_requests.append(req)
                text = req["text"]
                for k, v in outer.swap.items():
                    text = text.replace(k, v)
                self._send(200, json.dumps({"text": text, "kinds": {}, **outer.redact_extra}).encode())
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def free_port() -> int:
    """A port nothing listens on (the gateway is down)."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Gateways:
    """pytest helper: make(...) starts a FakeGateway and points JARVIS_PRIVACY_PORT at it; down() points it at a dead port."""

    def __init__(self, monkeypatch):
        self.mp, self.made = monkeypatch, []

    def make(self, **kw) -> FakeGateway:
        g = FakeGateway(**kw)
        self.made.append(g)
        self.mp.setenv("JARVIS_PRIVACY_PORT", str(g.port))
        return g

    def down(self) -> None:
        self.mp.setenv("JARVIS_PRIVACY_PORT", str(free_port()))

    def close(self) -> None:
        for g in self.made:
            g.close()


def leaked(blob) -> list[str]:
    """Which canaries appear in anything (str, bytes, or JSON-able). Empty list = nothing leaked."""
    if isinstance(blob, bytes):
        text = blob.decode("utf-8", "replace")
    elif isinstance(blob, str):
        text = blob
    else:
        text = json.dumps(blob, default=str, ensure_ascii=False)
    # JSON escapes backslashes: look for both spellings of the path
    return [c for c in ALL if c in text or c.replace("\\", "\\\\") in text]


# ---- every way a text may fail to be cleared when the flag is on: (id, setup(gateways, monkeypatch)) ----
def _down(gw, mp):
    gw.down()


def _degraded(gw, mp):
    gw.make(health={"ok": True, "ready": False, "degraded": ["pii_model_missing"], "pii_model": False})


def _no_model(gw, mp):
    gw.make(health={"ok": True, "ready": True, "pii_model": False})


def _names_not_confirmed(gw, mp):
    gw.make(redact_extra={"names": False, "deep": True, "ready": True})


def _answer_unusable(gw, mp):
    gw.make(redact_extra={"deep": True})                 # an old gateway: no "names" in its answer


def _helper_raises(gw, mp):
    gw.make()                                           # the gateway is fine; the shared helper itself blows up
    from plugin.plugins._shared import screen_text

    def boom(*a, **k):
        raise RuntimeError(f"internal error holding {C_OCR}")
    mp.setattr(screen_text, "release", boom)


NOT_CLEARED = [("gateway down", _down), ("gateway degraded", _degraded), ("gateway without its PII model", _no_model),
               ("gateway does not confirm names", _names_not_confirmed), ("gateway answer unusable", _answer_unusable),
               ("the helper raises", _helper_raises)]
