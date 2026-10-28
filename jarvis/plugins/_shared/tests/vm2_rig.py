"""Test rig for the takeover and VM2 modules (D79): a fake clock, a scripted owner, a fake lab over real HTTP on 127.0.0.1, and a broker built from them."""
from __future__ import annotations

import json
from pathlib import Path

from jarvis.evals.reliability.fake_vm2 import FakeKernel, FakeLab
from jarvis.plugins._shared import takeover as TK
from jarvis.plugins._shared import vm2 as V


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class Owner:
    """The owner at the ask panel. `script` is a list of: a button id, None (nobody answered: the form times out), or a callable(spec) -> button id."""

    def __init__(self, clock: Clock, script=None):
        self.clock, self.script, self.forms = clock, list(script or []), []

    def __call__(self, spec: dict) -> dict:
        self.forms.append(spec)
        step = self.script.pop(0) if self.script else None
        btn = step(spec) if callable(step) else step
        if btn is None:
            self.clock.t += float(spec.get("timeout_s", 120))
            return {"answered": False, "reason": "timeout"}
        return {"answered": True, "button": btn}

    @property
    def titles(self):
        return [f["title"] for f in self.forms]


class Rig:
    def __init__(self, tmp_path: Path, script=None, *, with_lab: bool = True, config_over: dict | None = None, hidden: bool = False):
        self.tmp = Path(tmp_path)
        self.clock = Clock()
        self.lab = FakeLab(clock=self.clock) if with_lab else None
        self.url = self.lab.serve() if self.lab else "http://127.0.0.1:9"
        cfg = self.lab.config(**(config_over or {})) if self.lab else {"enabled": True, "control_url": self.url, "viewer_url": self.url + "/vnc.html", "probe_timeout_s": 0.5}
        self.cfg_path = self.tmp / "vm2.json"
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.cfg = V.load_config(self.cfg_path)
        self.port = V.Vm2Port(self.cfg, clock=self.clock)
        self.owner = Owner(self.clock, script)
        self.notices: list = []
        self.opened: list = []
        self.journal: list = []
        self.state = TK.State(self.tmp / "takeover_state.json", clock=self.clock)
        self.signal = TK.Signal(self.tmp / "takeover_signal.json", clock=self.clock)
        self.task_state = FakeTaskState()
        self.pc_actions: list = []                     # what a pretend input device would have received
        self.fronted: list = []
        self.typing = False
        pc = TK.PcTarget(self.task_state, front=lambda: self.fronted.append(1), typing=lambda: self.typing)
        self.tk = TK.Takeover({"pc": pc, "vm2": TK.Vm2Target(self.port)}, ask=self.owner, config=self.cfg, notify=lambda n, quiet: self.notices.append((n, quiet)),
                              opener=self.opened.append, journal=self.journal.append, state=self.state, signal=self.signal, hidden=lambda: hidden,
                              clock=self.clock, sleep=self.clock.sleep, grants_host=lambda h: h == "granted.example.test")
        self.kernel = FakeKernel(self.lab) if self.lab else None

    def close(self):
        if self.lab:
            self.lab.close()


class FakeTaskState:
    def __init__(self):
        self.updates: list = []

    def update(self, **kw):
        self.updates.append(kw)
