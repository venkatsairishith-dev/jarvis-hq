"""Proactive PC monitor for JARVIS (Windows; 2026-10-16). Run by the desktop plugin's timer; it only reads and reports.

What it watches, each with a rule that has to hold on two checks in a row before it speaks (no alert on a blip):
  * crashes: new Application-log events 1000 (Application Error), 1002 (Application Hang), 1026 (.NET Runtime)
    since the last check (a record-id bookmark, so nothing old is re-announced after a restart). Only the program's
    name is reported, never the event's text.
  * disk: free space under a floor (C: 12 GB, the owner's rule; D: 3 GB), cleared once 1 GB above the floor.
  * services: a local JARVIS service port that answered before and now refuses twice in a row.
  * stuck tasks: a PC task record with no finish time, older than its time budget plus two minutes.
Alerts are kept per (kind, subject) with a 30-minute quiet period, so one fault speaks once. A program that keeps
crashing is one fault: it is announced once, and crashes inside the quiet period are counted and said with the next alert
(a program that stops responding is a different fault from one that crashes, so a hang after a crash is still said).
Each collector runs on its own: one that fails (an Event Log query error, say) does not stop the other checks, and its
failure is reported as its own alert ("monitor", collector name), with the same quiet period.
Reused: pywin32's EvtQuery (Windows Event Log API), psutil; delivery through the plugin SDK's push_message.
"""
from __future__ import annotations

import json
import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

CRASH_IDS = (1000, 1002, 1026)
DISK_FLOOR_GB = {"C:\\": 12.0, "D:\\": 3.0}
SERVICES = {"face tool server": 48970, "speech server": 48930,
            "privacy gateway": 48950, "llama-swap": 8080, "JARVIS console": 8799}
QUIET_S = 30 * 60
KEEP_HELD_S = 24 * 3600                                  # unsaid repeats of a crash are forgotten after a day
STUCK_GRACE_S = 120


@dataclass
class Alert:
    kind: str
    subject: str
    text: str


@dataclass
class State:
    bookmark: int = 0                                     # newest Application-log record already seen
    seen_up: dict = field(default_factory=dict)            # service -> True once it answered
    pending: dict = field(default_factory=dict)            # "kind|subject" -> consecutive checks the condition held
    last_alert: dict = field(default_factory=dict)         # "kind|subject" -> time of the last alert
    active: dict = field(default_factory=dict)             # "kind|subject" -> True while the condition holds
    suppressed: dict = field(default_factory=dict)         # "kind|subject" -> repeats held back by the quiet period

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            return cls(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__), encoding="utf-8")


# ---- collectors (each returns plain data; tests replace them) -------------------------------------------------------

def crash_events(after_record: int, limit: int = 200) -> list[dict]:
    """New crash/hang events, oldest first: {record, id, app, time}. App = the faulting program's file name."""
    import win32evtlog
    q = " or ".join(f"EventID={i}" for i in CRASH_IDS)
    h = win32evtlog.EvtQuery("Application", win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection,
                             f"*[System[({q}) and EventRecordID > {int(after_record)}]]")
    out = []
    while len(out) < limit:
        batch = win32evtlog.EvtNext(h, 50)
        if not batch:
            break
        for ev in batch:
            xml = win32evtlog.EvtRender(ev, win32evtlog.EvtRenderEventXml)
            rec = int(re.search(r"<EventRecordID>(\d+)<", xml).group(1))
            eid = int(re.search(r"<EventID[^>]*>(\d+)<", xml).group(1))
            data = re.findall(r"<Data[^>]*>([^<]*)</Data>", xml)
            if eid == 1026:                                   # .NET: "Application: X.exe" inside the text
                m = re.search(r"Application:\s*([^\s]+\.exe)", xml, re.I)
                app = m.group(1) if m else ".NET program"
            else:
                app = data[0] if data else "a program"
            out.append({"record": rec, "id": eid, "app": re.sub(r"[^\w.\- ]", "", app)[:60]})
    return sorted(out, key=lambda e: e["record"])


def newest_record() -> int:
    import win32evtlog
    h = win32evtlog.EvtQuery("Application", win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection, "*")
    batch = win32evtlog.EvtNext(h, 1)
    if not batch:
        return 0
    return int(re.search(r"<EventRecordID>(\d+)<", win32evtlog.EvtRender(batch[0], win32evtlog.EvtRenderEventXml)).group(1))


def disk_free_gb() -> dict:
    import psutil
    out = {}
    for d in DISK_FLOOR_GB:
        try:
            out[d] = psutil.disk_usage(d).free / 2**30
        except OSError:
            pass
    return out


def port_up(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def open_tasks(folder: Path) -> list[dict]:
    out = []
    for f in sorted(folder.glob("*.json"))[-50:]:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not r.get("finished"):
            out.append({"id": r.get("id", f.stem), "request": str(r.get("request", ""))[:60], "started": r.get("started", 0),
                        "status": r.get("status", "")})
    return out


# ---- the rules -------------------------------------------------------------------------------------------------------

class Monitor:
    def __init__(self, state: State, *, crashes=crash_events, newest=newest_record, disks=disk_free_gb, port=port_up,
                 tasks=lambda: [], services: dict | None = None, floors: dict | None = None, budget_s: float = 600,
                 clock=time.time, confirm: int = 2):
        self.s, self.crashes, self.newest, self.disks, self.port, self.tasks = state, crashes, newest, disks, port, tasks
        self.services = SERVICES if services is None else services
        self.floors = DISK_FLOOR_GB if floors is None else floors
        self.budget_s, self.clock, self.confirm = budget_s, clock, confirm

    def _hold(self, key: str, holds: bool, need: int) -> bool:
        """True when the condition has held `need` checks in a row; resets when it clears."""
        if not holds:
            self.s.pending.pop(key, None)
            self.s.active.pop(key, None)
            return False
        self.s.pending[key] = self.s.pending.get(key, 0) + 1
        return self.s.pending[key] >= need

    def _emit(self, out: list, kind: str, subject: str, text: str):
        key = f"{kind}|{subject}"
        now = self.clock()
        if self.s.active.get(key) and now - self.s.last_alert.get(key, 0) < QUIET_S:
            return
        self.s.active[key] = True
        self.s.last_alert[key] = now
        out.append(Alert(kind, subject, text))

    def _emit_event(self, out: list, kind: str, subject: str, text: str, again: str, count: int = 1, fault: str = ""):
        """For faults that arrive as events and never "clear" (a crash, a collector error): at most one alert per quiet
        period per fault. Repeats inside it are counted, and the next alert says how many there were (`again` takes the
        count). `fault` tells apart faults of one subject (a program that crashes vs one that stops responding)."""
        key = f"{kind}|{subject}" + (f"|{fault}" if fault else "")
        now = self.clock()
        if key in self.s.last_alert and now - self.s.last_alert[key] < QUIET_S:
            self.s.suppressed[key] = self.s.suppressed.get(key, 0) + count
            return
        held = self.s.suppressed.pop(key, 0)
        self.s.last_alert[key] = now
        out.append(Alert(kind, subject, text + (again.format(n=held) if held else "")))

    def _collector_failed(self, out: list, name: str, what: str, exc: Exception):
        self._emit_event(out, "monitor", name, f"The PC monitor could not check {what} ({type(exc).__name__}); "
                                               f"the other checks still ran.", " It failed {n} more times since the last alert.")

    def check(self) -> list[Alert]:
        out: list[Alert] = []
        for name, what, run in (("crashes", "for program crashes", self._check_crashes), ("disk", "disk space", self._check_disks),
                                ("services", "the local services", self._check_services), ("tasks", "for stuck PC tasks", self._check_tasks)):
            try:
                run(out)
            except Exception as exc:  # noqa: BLE001 - one collector's failure must not hide the others' alerts
                self._collector_failed(out, name, what, exc)
        self._prune()
        return out

    def _check_crashes(self, out: list):
        # crashes: a fresh bookmark starts at "now" so history is not re-announced
        if not self.s.bookmark:
            self.s.bookmark = self.newest()
            return
        evs = self.crashes(self.s.bookmark)
        by_app: dict[str, list] = {}
        for e in evs:
            by_app.setdefault(e["app"], []).append(e)
            self.s.bookmark = max(self.s.bookmark, e["record"])
        for app, es in by_app.items():
            what = "stopped responding" if all(e["id"] == 1002 for e in es) else "crashed"
            n = f" ({len(es)} times)" if len(es) > 1 else ""
            self._emit_event(out, "crash", app, f"{app} {what}{n}. Say 'check {app}' and I'll look at why.",
                             f" It had {what} {{n}} more times since the last alert.", len(es),
                             fault="hang" if what == "stopped responding" else "")

    def _check_disks(self, out: list):
        for d, free in self.disks().items():
            floor = self.floors.get(d)
            if floor is None:
                continue
            key = f"disk|{d}"
            low = free < floor or (self.s.active.get(key) and free < floor + 1.0)
            if self._hold(key, bool(low), 1):
                self._emit(out, "disk", d, f"{d[:2]} is low on space: {free:.1f} GB free (floor {floor:.0f} GB).")

    def _check_services(self, out: list):
        # services: only ones seen answering before, and down on two checks in a row
        for name, port in self.services.items():
            up = self.port(port)
            if up:
                self.s.seen_up[name] = True
            if self._hold(f"service|{name}", (not up) and self.s.seen_up.get(name, False), self.confirm):
                self._emit(out, "service", name, f"The {name} (port {port}) stopped answering.")

    def _check_tasks(self, out: list):
        now = self.clock()
        open_ids = set()
        for t in self.tasks():
            open_ids.add(f"stuck|{t['id']}")
            age = now - float(t.get("started") or now)
            if self._hold(f"stuck|{t['id']}", age > self.budget_s + STUCK_GRACE_S, 1):
                self._emit(out, "stuck", t["id"], f"The PC task '{t['request']}' has been {t['status'] or 'open'} for "
                                                  f"{age / 60:.0f} minutes. Say 'stop the task' to end it.")
        # a task that finished (or whose record is gone) is not open any more: forget its keys, or they pile up forever.
        # Only after the task list was read: if reading failed, nothing is known and nothing is dropped.
        for d in (self.s.pending, self.s.active, self.s.last_alert, self.s.suppressed):
            for key in [k for k in d if k.startswith("stuck|") and k not in open_ids]:
                d.pop(key, None)

    def _prune(self):
        """Event-style keys (crashes, collector errors) matter only during their quiet period; after it they are dropped,
        so a long list of programs that crashed once does not grow the state file without end. Repeats that were held
        back and not said yet are kept for a day, so the next alert for that key can still say them."""
        now = self.clock()
        for key in [k for k, at in self.s.last_alert.items()
                    if k.split("|", 1)[0] in ("crash", "monitor") and now - at >= QUIET_S
                    and (not self.s.suppressed.get(k) or now - at >= KEEP_HELD_S)]:
            for d in (self.s.last_alert, self.s.active, self.s.suppressed):
                d.pop(key, None)
