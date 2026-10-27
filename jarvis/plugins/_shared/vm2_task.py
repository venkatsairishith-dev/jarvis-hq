"""A web or download task on VM2: hand it to the lab agent, watch for walls, bring the downloads back only if the owner agrees (D79, 2026-10-27).

WHY THE LAB AGENT AND NOT THE BROWSER AGENT LOOP. The browser agent (plugins/desktop/browser_agent.py) drives the owner's Chrome through Playwright MCP on the laptop. VM2
has Firefox, xdotool, cua-driver on a local socket and the lab agents (Hermes), and NO browser automation endpoint that the laptop can reach; inventing one would be a new
server, which this change must not do. The existing contract on VM2 is the lab: the owner's JARVIS (through the kernel's own command path) dispatches an outcome task, the
lab runner works it as the unprivileged agent user on display :1, and the agent's desktop tools (jarvis_mcp.py) are the hands. So a VM2 task is: dispatch the lab task
(`POST owner/command`, the kernel's guarded path), poll the lab control service (`/control/state`), hand every wall the agent reports (`need_owner`) to the owner through the
takeover protocol, and when the work is over look at the quarantine. The lab agent does its own "fresh look" after a hand-back: the desktop tools tell it that the owner had
the screen and it must look again; if the wall is still there it calls need_owner again, and that is the laptop's cue to say so once and wait (at most 3 re-asks).

What is NOT verified here and needs the real VM2: how the owner's agent words the lab task, whether Hermes follows "call need_owner at a wall", and how long a task takes.
Every call is injected so the whole flow is tested against a fake lab (evals/reliability/fake_vm2.py).
"""
from __future__ import annotations

import re
import secrets
import time
from typing import Any, Callable

from . import takeover as TK
from . import vm2_downloads as DL
from . import vm2_routing as RT
from .vm2 import Vm2Config, Vm2Port

INSTRUCTION = ("[{token}] Lab task on the Agent PC (VM2). Use Firefox on the desktop. {request}\n"
               "Rules: never type or ask for a password; never solve a CAPTCHA, bot check, verification code or payment page: the moment one appears call need_owner (with its kind and the "
               "site) and wait. Every download stays in the Firefox download folder (it is the quarantine); never open, run or unpack a downloaded file. Write a short text report of what "
               "you found or downloaded (file names and types only). Acceptance: that report exists and no downloaded file was opened or run.")
DONE_QUIET_POLLS = 2
IDLE_GRACE_POLLS = 20            # the command is finished but no agent task was ever seen running: wait this many polls before calling it done (the runner picks jobs up every 10 s)


class Look:
    """The fresh look for VM2: after the agents ran again, did one of them report the wall once more? (They look at their own screen; `need_owner` is how they say so.)"""

    def __init__(self, port: Vm2Port, after: float, wait_s: float, poll_s: float, clock: Callable[[], float], sleep: Callable[[float], Any]):
        self.port, self.after, self.wait_s, self.poll_s, self.clock, self.sleep = port, after, wait_s, poll_s, clock, sleep

    def __call__(self):
        end = self.clock() + self.wait_s
        while True:
            rep = self.port.reported_takeover()
            if rep and rep["at"] > self.after:
                self.after = rep["at"]
                return rep
            if self.clock() >= end:
                return None
            self.sleep(min(self.poll_s, max(0.0, end - self.clock())))


def command_state(kernel: Callable[..., tuple], cid: str) -> str:
    """running | done | failed | cancelled of the owner command that started the lab task, or '' when unknown."""
    try:
        code, body = kernel("GET", "commands?limit=20")
    except Exception:  # noqa: BLE001
        return ""
    if code != 200 or not isinstance(body, dict):
        return ""
    for c in body.get("commands") or []:
        if isinstance(c, dict) and c.get("id") == cid:
            return str(c.get("state") or "")
    return ""


def run(request: str, *, cfg: Vm2Config, port: Vm2Port, kernel: Callable[..., tuple], ask: Callable[[dict], dict], takeover: TK.Takeover, stop: Callable[[], bool] = lambda: False,
        trusted_hosts=(), outputs: str | None = None, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] = time.sleep, resume: dict | None = None,
        route: RT.Route | None = None, touch: Callable[[str], Any] | None = None) -> dict:
    """Run one VM2 task. Never raises. The result holds verified facts and short reasons only: no address, no page text, no local path."""
    request = " ".join(str(request or "").split())[:600]
    route = route or RT.resolve(request, cfg, port, trusted_hosts)
    host = (route.hosts or [""])[0]
    if route.target != "vm2":
        return {"ok": False, "route": "pc", "error": "this task belongs on your PC, not VM2", "reason": route.why}
    if route.blocked:                                     # never a silent fallback to the PC
        return {"ok": False, "route": "vm2", "needs_clarification": True, "error": route.say, "ask": route.ask, "reason": route.why, "vm2": route.state}
    token = (resume or {}).get("task") or "vm2-" + secrets.token_hex(4)
    before = set((resume or {}).get("before") or [])
    if not resume:
        try:
            before = {str(r.get("id")) for r in port.quarantine() if r.get("id")}
        except Exception:  # noqa: BLE001 - an unreadable quarantine now means "treat what is there later as new"; the owner is asked about each file anyway
            before = set()
        text = INSTRUCTION.format(token=token, request=request)
        code, body = kernel("POST", "command", {"text": text, "source": "vm2"})
        if code >= 400:
            return {"ok": False, "route": "vm2", "error": "the task could not be handed to the lab: " + str((body or {}).get("error") or f"HTTP {code}")[:160]}
        cid = str((body or {}).get("id") or "")
    else:
        cid = str((resume or {}).get("command") or "")
    deadline = clock() + cfg.task_wait_minutes * 60.0
    last_wall, quiet, misses, polls, seen_running, idle = 0.0, 0, 0, 0, bool((resume or {}).get("seen_running")), 0
    if resume:
        last_wall = float(resume.get("wall_at") or 0.0)
    while True:
        if stop():
            return {"ok": False, "route": "vm2", "stopped": True, "error": "stopped by the owner", "summary": "stopped before the VM2 task finished"}
        if clock() > deadline:
            return {"ok": False, "route": "vm2", "error": f"the VM2 task did not finish within {int(cfg.task_wait_minutes)} minutes", "task": token}
        if touch:
            touch("waiting for VM2")
        st = port.state()
        if st is None:
            misses += 1
            if misses >= 5:
                return {"ok": False, "route": "vm2", "error": "VM2 stopped answering while the task ran", "task": token, "vm2": "offline"}
            sleep(cfg.poll_s)
            continue
        misses = 0
        rep = port.reported_takeover(st)
        if rep and rep["at"] > last_wall:
            last_wall = rep["at"]
            req = TK.make_request("vm2", rep["kind"], rep["host"] or host)
            look = Look(port, last_wall, cfg.look_wait_s, cfg.poll_s, clock, sleep)
            out = takeover.hand_over(req, look)
            last_wall = max(last_wall, look.after)                    # the re-reports the fresh looks saw are handled too
            if out.status == "stop":
                return {"ok": False, "route": "vm2", "stopped": True, "error": "stopped by the owner", "summary": "stopped at a step only you could do"}
            if out.status != "resolved":
                rec = {"id": req.id, "target": "vm2", "kind": req.kind, "host": req.host, "status": out.status, "reasks": out.reasks, "site": req.host, "milestones": [],
                       "task": token, "command": cid, "seen_running": seen_running, "before": sorted(before), "wall_at": last_wall, "at": time.time()}
                return {"ok": False, "route": "vm2", "task": token, "needs_owner": {"kind": "takeover", "site": req.host, "detail": out.detail[:160], "takeover": rec},
                        "error": out.detail[:200], "summary": "Waiting for you: " + out.detail[:160]}
            continue
        running = [t for t in st.get("tasks") or [] if isinstance(t, dict) and not t.get("paused")]
        seen_running = seen_running or bool(running)
        quiet = 0 if (running or st.get("mode") == "owner") else quiet + 1
        polls += 1
        cmd = command_state(kernel, cid) if (cid and polls % 3 == 1) else ""
        if cmd in ("failed", "cancelled"):
            return {"ok": False, "route": "vm2", "error": f"the lab task was {cmd} before it finished", "task": token}
        if cmd == "done":
            idle += 1
        if quiet >= DONE_QUIET_POLLS and (seen_running or idle >= 1 and polls >= IDLE_GRACE_POLLS):
            break
        sleep(cfg.poll_s)
    found = DL.review(port, ask, host, outputs, only=lambda it: it.id not in before)
    copied, skipped, refused = found.get("copied", []), found.get("skipped", []), found.get("refused", [])
    facts = [f"the lab agent finished on VM2", f"{len(copied)} file(s) copied to your PC quarantine after your approval, {len(skipped)} left on VM2, {len(refused)} refused"]
    return {"ok": True, "route": "vm2", "task": token, "reason": route.why, "facts": facts, "copied": copied, "skipped": skipped, "refused": refused, "opened_on_pc": False,
            "summary": "Done on VM2: " + "; ".join(facts) + ". Nothing was opened or run on your PC.", **({"error_files": found["error"]} if found.get("error") else {})}
