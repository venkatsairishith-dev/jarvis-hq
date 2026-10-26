"""The real Port (perceive.Port) of the desktop plugin: every method is ONE kernel job through the plugin's `pc()` (policy, approvals, audit and the PC lock apply as
for every other step). It is a thin translation layer and holds no logic; the tests for the loop use fake_desktop.py instead, and the wiring tests use fakewin.py,
which runs the real kernel and the real node code behind this same class.

    windows   ui_windows        windows, focus, "(Not Responding)", other-desktop (cloaked), and the owner's input state (idle_ms, cursor)
    uia       diag window_text  the accessibility tree of the app's windows and dialogs (text and buttons)
    ocr       diag window_ocr
    vision    diag window_vision "app|*"      the small vision model's reading (the node runs it locally; the text stays in a local file)
    locate    diag window_vision "app|label"  the OCR point and the vision point, separately, for the cascade's agreement check
    act       any body       a kernel job as it is (the caller adds ctx for dialogs; Pc adds the owner's goal)
    app_info  diag app_info  whether the app responds
"""
from __future__ import annotations

import re
from typing import Callable

from . import core

NAME = re.compile(r"^[A-Za-z0-9_.\- :()]{1,80}$")


class PcPort:
    def __init__(self, pc: Callable[..., dict]):
        self.pc = pc                                     # pc(body, timeout=90, what="", keep_local=False) -> flat result dict (the plugin's own helper)

    def _safe(self, app: str) -> str:
        a = str(app or "").strip()
        if not NAME.match(a):
            raise ValueError("bad app name")
        return a

    def windows(self) -> dict:
        r = self.pc({"op": "windows"}, 30)
        return {"windows": [w for w in r.get("windows") or [] if isinstance(w, dict)], "idle_ms": r.get("idle_ms"), "cursor": r.get("cursor")}

    def uia(self, app: str) -> list[dict]:
        r = self.pc({"op": "diag", "check": "window_text", "target": self._safe(app)}, 60, keep_local=True)
        return core.rows(r.get("_local")) if r.get("ok") else []

    def ocr(self, app: str) -> str:
        r = self.pc({"op": "diag", "check": "window_ocr", "target": self._safe(app)}, 90, keep_local=True)
        return "\n".join(core.ocr_lines(r.get("_local"))) if r.get("ok") else ""

    def vision(self, app: str) -> str:
        r = self.pc({"op": "diag", "check": "window_vision", "target": f"{self._safe(app)}|*"}, 150, keep_local=True)
        local = r.get("_local")
        return str(local.get("inferred") or "") if r.get("ok") and isinstance(local, dict) else ""

    def locate(self, app: str, label: str) -> dict:
        label = " ".join(str(label or "").split())[:100]
        if not label or re.search(r"[|\r\n]", label):
            return {}
        r = self.pc({"op": "diag", "check": "window_vision", "target": f"{self._safe(app)}|{label}"}, 150)
        out = {}
        for k, key in (("ocr", "ocr_point"), ("vision", "vision_point")):
            p = r.get(key)
            if r.get("ok") and isinstance(p, dict) and isinstance(p.get("x"), (int, float)) and isinstance(p.get("y"), (int, float)):
                out[k] = (p["x"], p["y"])
        return out

    def act(self, body: dict) -> dict:
        try:
            return self.pc(dict(body), 120)
        except core.DesktopError as e:                  # the kernel refused it (policy, a bad request): the loop sees a denied step with the console's own words
            return {"ok": False, "state": "denied", "error": str(e)[:200]}

    def app_info(self, app: str) -> dict:
        r = self.pc({"op": "diag", "check": "app_info", "target": self._safe(app)}, 60)
        rows = [x for x in r.get("rows") or [] if isinstance(x, dict)]
        return {"responding": rows[0].get("responding") if rows else None, "busy": False}
