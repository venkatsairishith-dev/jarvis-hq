"""Wires the takeover protocol (plugins/_shared/takeover.py) to the real parts of the desktop plugin (D79): the ask panel, the journal, the task state file, the node's
window focus, the VM2 port. Everything here is plumbing; the rules live in the shared module and are tested there with fakes.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from jarvis.plugins._shared import takeover as TK
from jarvis.plugins._shared import vm2 as V

TYPING_MS = 1500        # he touched the keyboard or mouse this recently: do not bring a window forward over his typing


def broker(*, ask: Callable[[dict], dict], state: Any = None, journal: Callable[[dict], Any] | None = None, stop: Callable[[], bool] | None = None,
           front: Callable[[], Any] | None = None, typing: Callable[[], bool] | None = None, config: V.Vm2Config | None = None, port: V.Vm2Port | None = None,
           grants: Any = None, notify=TK.default_notify, opener=None, sleep=time.sleep, clock=time.monotonic) -> TK.Takeover:
    """The takeover broker for one task: the PC target (the engine waits; the window comes forward unless he is typing) and the VM2 target (the lab's own control)."""
    cfg = config or V.load_config()
    port = port or V.Vm2Port(cfg)
    targets = {"pc": TK.PcTarget(state, front, typing), "vm2": TK.Vm2Target(port)}
    return TK.Takeover(targets, ask=ask, config=cfg, notify=notify, opener=opener, journal=journal, hidden=TK.face_hidden, stop=stop, sleep=sleep, clock=clock,
                       grants_host=(lambda h: bool(grants and grants.has(h, "signin_existing_session"))) if grants is not None else None)


def pc_hook(tk: TK.Takeover, idle_ms: Callable[[Any], int | None] | None = None) -> Callable:
    """The callable PcAgent takes as `takeover` (kind, obs, look) -> Outcome. A UAC prompt or an unreadable dialog on the owner's own PC."""
    def hook(kind: str, obs: Any, look: Callable[[], Any]) -> TK.Outcome:
        host = ""
        req = TK.make_request("pc", kind, host)
        return tk.hand_over(req, look)
    return hook


def pc_granted_hosts(owner: Callable[..., tuple]) -> list:
    """The hosts the owner granted for the PC (kernel/sitegrants.py, target pc): they count as trusted for the routing rule. VM2's own grants are not in this list."""
    try:
        code, body = owner("GET", "site_grants")
    except Exception:  # noqa: BLE001
        return []
    if code != 200 or not isinstance(body, dict):
        return []
    return [str(s["host"]) for s in body.get("sites") or [] if isinstance(s, dict) and s.get("host") and s.get("target", "pc") == "pc"]


def on_mission_resume(record: dict, port: V.Vm2Port | None = None) -> None:
    """A mission paused for a takeover is continued: a VM2 handover gives the desktop back to the agents first (the lab's `continue task`)."""
    if isinstance(record, dict) and record.get("target") == "vm2":
        (port or V.Vm2Port()).resume()
