"""Durable automations: definitions in the kernel's database, fired by a pure tick(now), recorded as tasks and jobs.

Design (docs/research/05-memory-database-and-automations.md, section 7):
- The database is the schedule. tick(now) reads it, so a restart loses nothing; a missed slot is handled by an explicit
  catch-up policy, never by luck.
- At most one run per slot: UNIQUE(automation_id, run_key) is the fence, across threads, processes and restarts. A
  crash between "claimed" and "fired" leaves a visible 'claimed'/'unknown' row, not a silent second run.
- A run is a lab task (dispatched through the same path as an agent's lab_work), a deterministic node job (kinds that
  need no approval), or a command to the owner's own Hermes. Nothing runs unattended that would need an approval each
  time: such a definition is refused when it is created, and Jobs.create's authorize refuses it again at fire time.
- Limits per automation: runs per day, task attempts and time budget, an expiry, and auto-pause after N failures.
- Time zones are real (zoneinfo): next_run is stored as UTC epoch and computed from the local wall clock, so "08:00
  every day" stays 08:00 across DST.
Custom code, justified in docs/01_RESEARCH_AUDIT.md: Hermes cron keeps its jobs outside the kernel's authorization and
audit and has no outcome verification; APScheduler/Celery would add a second daemon and a second store. The cron
matcher below is ~60 lines and is checked against croniter in the tests when croniter is installed.
"""
import datetime as dt
import json
import os
import re
import secrets
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .jobs import JobError, LAB_AGENTS, _check_params, action_for
from .policy import decide

KINDS = ("lab_task", "job", "command")
TRIGGERS = ("cron", "interval", "once", "event")
JOB_KINDS_OK = {"project_check", "pc_open", "ui_windows"}     # unattended: policy must allow them with no approval
MIN_INTERVAL = 60
MAX_SLOTS = 100                                 # one tick enumerates at most this many due slots per automation
CLAIM_GRACE = 15 * 60                           # a 'claimed' run that never fired after this long is 'unknown'
NAME_MAX = 80
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
DOWS = {d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())}


class AutomationError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------- schedule math
def _field(text, lo, hi, names=None):
    out = set()
    for part in text.split(","):
        step, stepped = 1, False
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                raise AutomationError(f"bad step in cron field '{text}'")
            step, stepped = int(s), True
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = _num(x, names), _num(y, names)
        else:
            a = _num(part, names)
            b = hi if stepped else a                      # "5/15" runs from 5 to the end in steps of 15
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise AutomationError(f"cron field '{text}' is out of range {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def _num(s, names):
    s = s.strip().lower()
    if names and s in names:
        return names[s]
    if not s.isdigit():
        raise AutomationError(f"cannot read '{s}' in the cron expression")
    return int(s)


def parse_cron(expr: str):
    """Five fields: minute hour day-of-month month day-of-week. Lists, ranges, steps and month/day names. Like Vixie
    cron, when both day-of-month and day-of-week are restricted a day matches if either does."""
    f = str(expr).split()
    if len(f) != 5:
        raise AutomationError("a cron expression has five fields: minute hour day month weekday")
    dow = _field(f[4].replace("7", "0") if f[4].isdigit() else f[4], 0, 7, DOWS)
    if 7 in dow:
        dow = (dow - {7}) | {0}
    return {"min": _field(f[0], 0, 59), "hour": _field(f[1], 0, 23), "dom": _field(f[2], 1, 31), "mon": _field(f[3], 1, 12, MONTHS),
            "dow": dow, "dom_star": f[2].startswith("*"), "dow_star": f[4].startswith("*")}


def _day_ok(c, d: dt.date) -> bool:
    if d.month not in c["mon"]:
        return False
    dom_ok, dow_ok = d.day in c["dom"], ((d.weekday() + 1) % 7) in c["dow"]
    if c["dom_star"] and c["dow_star"]:
        return True
    if c["dom_star"]:
        return dow_ok
    if c["dow_star"]:
        return dom_ok
    return dom_ok or dow_ok


def next_cron(expr: str, after: float, tz: str) -> float:
    """The first scheduled instant strictly after `after` (UTC epoch), by the wall clock of `tz`. A local time that does
    not exist (spring-forward gap) fires once at the shifted time; one that happens twice fires once."""
    c, zone = parse_cron(expr), ZoneInfo(tz)
    start = dt.datetime.fromtimestamp(after, zone)
    day = start.date()
    times = sorted((h, m) for h in c["hour"] for m in c["min"])
    for off in range(0, 366 * 5):
        d = day + dt.timedelta(days=off)
        if not _day_ok(c, d):
            continue
        for h, m in times:
            ts = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=zone).timestamp()
            if ts > after:
                return ts
    raise AutomationError("this cron expression never fires")


def next_after(a: dict, after: float):
    """Next slot after `after` for a definition dict, or None when it has no more."""
    k = a["trigger_kind"]
    if k == "cron":
        return next_cron(a["schedule"], after, a["tz"])
    if k == "interval":
        n = int(a["schedule"])
        base = a.get("anchor") or a["created"]
        return base + ((int(after - base) // n) + 1) * n
    return None                       # once and event have no successor


def iso_utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- engine
class Automations:
    def __init__(self, kernel, tasks, jobs, owner=None, dispatch=None, clock=time.time):
        """dispatch(owner_name, lane, task_id, params) -> (http_status, reply): the lab_work path of the kernel."""
        self.k, self.tasks, self.jobs, self.owner, self.dispatch, self.clock = kernel, tasks, jobs, owner, dispatch, clock
        kernel.db.execute(
            "CREATE TABLE IF NOT EXISTS automations(id TEXT PRIMARY KEY, name TEXT, owner TEXT, action_kind TEXT, action TEXT, "
            "trigger_kind TEXT, schedule TEXT, tz TEXT, node TEXT, event_spec TEXT, enabled INT DEFAULT 1, state TEXT DEFAULT 'scheduled', "
            "paused_reason TEXT, next_run REAL, anchor REAL, catchup TEXT DEFAULT 'one', grace_s INT DEFAULT 300, overlap TEXT DEFAULT 'skip', "
            "repeat_times INT, repeat_done INT DEFAULT 0, max_runs_per_day INT DEFAULT 24, pause_after_failures INT DEFAULT 3, "
            "failure_streak INT DEFAULT 0, expires REAL, created_by TEXT, created REAL, updated REAL, version INT DEFAULT 1)")
        kernel.db.execute("CREATE INDEX IF NOT EXISTS automations_due ON automations(enabled, state, next_run)")
        kernel.db.execute("CREATE TABLE IF NOT EXISTS owner_commands(id TEXT PRIMARY KEY, ts REAL, source TEXT, text TEXT, "
                          "state TEXT, reply TEXT, error TEXT, finished REAL)")    # same table the owner plane writes; settle() reads it
        kernel.db.execute("CREATE TABLE IF NOT EXISTS watch_events(id TEXT PRIMARY KEY, node TEXT, watch TEXT, rel TEXT, size INT, "
                          "mtime_ns INT, ts REAL, handled INT DEFAULT 0)")
        kernel.db.execute(
            "CREATE TABLE IF NOT EXISTS automation_runs(id INTEGER PRIMARY KEY, automation_id TEXT NOT NULL, run_key TEXT NOT NULL, "
            "scheduled_for REAL, fired_at REAL, source TEXT, status TEXT NOT NULL, task_id TEXT, job_id TEXT, command_id TEXT, "
            "detail TEXT, finished REAL, UNIQUE(automation_id, run_key))")
        kernel.db.execute("CREATE INDEX IF NOT EXISTS automation_runs_a ON automation_runs(automation_id, id DESC)")

    # ---- validation and preview
    def normalize(self, d: dict, owner: str = "owner") -> dict:
        if not isinstance(d, dict):
            raise AutomationError("the definition must be an object")
        name = " ".join(str(d.get("name", "")).split())[:NAME_MAX]
        if not name:
            raise AutomationError("an automation needs a name")
        kind, trig = str(d.get("action_kind", "")), str(d.get("trigger_kind", ""))
        if kind not in KINDS:
            raise AutomationError(f"action_kind must be one of {', '.join(KINDS)}")
        if trig not in TRIGGERS:
            raise AutomationError(f"trigger_kind must be one of {', '.join(TRIGGERS)}")
        tz = str(d.get("tz") or "UTC")
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise AutomationError(f"unknown time zone '{tz}' (use an IANA name such as Asia/Kolkata)")
        sched, anchor = str(d.get("schedule", "")).strip(), None
        if trig == "cron":
            parse_cron(sched)
            next_cron(sched, self.clock(), tz)           # raises when it never fires
        elif trig == "interval":
            if not sched.isdigit() or int(sched) < MIN_INTERVAL:
                raise AutomationError(f"an interval is a number of seconds, at least {MIN_INTERVAL}")
            anchor = self.clock()
        elif trig == "once":
            sched = str(self._once_epoch(sched, tz))
        spec = None
        if trig == "event":
            ev = d.get("event_spec") if isinstance(d.get("event_spec"), dict) else {}
            watch, glob = str(ev.get("watch", "")), str(ev.get("glob") or "*")
            if not re.match(r"^[A-Za-z0-9_.-]{1,40}$", watch):
                raise AutomationError("a file-arrival trigger names one of this laptop's watched folders")
            if len(glob) > 60 or not re.match(r"^[A-Za-z0-9_.*?\[\] -]+$", glob):
                raise AutomationError("the file pattern may use letters, digits, dots, spaces and * ?")
            spec = {"watch": watch, "glob": glob}
            sched = ""
        a = {"name": name, "owner": str(owner)[:40], "action_kind": kind, "trigger_kind": trig, "schedule": sched, "tz": tz,
             "node": "laptop" if trig == "event" else "server", "event_spec": spec, "anchor": anchor, "action": self._check_action(kind, d.get("action"))}
        a["catchup"] = str(d.get("catchup") or ("skip" if trig == "once" else "one"))
        a["overlap"] = str(d.get("overlap") or "skip")
        if a["catchup"] not in ("skip", "one", "all") or a["overlap"] not in ("skip", "queue"):
            raise AutomationError("catchup is skip, one or all; overlap is skip or queue")
        for key, lo, hi, dflt in (("grace_s", 0, 86400, 300), ("max_runs_per_day", 1, 1000, 24), ("pause_after_failures", 1, 20, 3)):
            v = d.get(key, dflt)
            if not isinstance(v, int) or not lo <= v <= hi:
                raise AutomationError(f"{key} must be a whole number from {lo} to {hi}")
            a[key] = v
        rt = d.get("repeat_times")
        if rt is not None and (not isinstance(rt, int) or not 1 <= rt <= 10000):
            raise AutomationError("repeat_times must be 1 to 10000, or left empty for no limit")
        a["repeat_times"] = rt
        exp = d.get("expires")
        a["expires"] = float(exp) if isinstance(exp, (int, float)) else None
        return a

    def _once_epoch(self, s: str, tz: str) -> float:
        try:
            return float(s)
        except ValueError:
            pass
        try:
            t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            raise AutomationError("a one-time schedule is an ISO date-time such as 2026-10-12T08:00 (in the automation's time zone)")
        if t.tzinfo is None:
            t = t.replace(tzinfo=ZoneInfo(tz))
        return t.timestamp()

    def _check_action(self, kind: str, a):
        if not isinstance(a, dict):
            raise AutomationError("the action must be an object")
        if kind == "command":
            text = str(a.get("text", "")).strip()
            if not 3 <= len(text) <= 2000:
                raise AutomationError("a command is 3 to 2000 characters")
            return {"text": text}
        if kind == "job":
            k, params = str(a.get("kind", "")), a.get("params")
            if k not in JOB_KINDS_OK:
                raise AutomationError(f"an unattended job may be one of: {', '.join(sorted(JOB_KINDS_OK))}")
            try:
                _check_params(k, params)
            except JobError:
                raise AutomationError("the job parameters are not valid for that kind")
            d = decide(action_for(k, params, "private", False), None, self.k.protected)
            if d.denied or d.needs_approval:
                raise AutomationError(f"policy says '{d.name}' for this action: it would need approval every time, so it cannot run unattended")
            return {"kind": k, "params": params, "node": str(a.get("node") or "laptop")[:30]}
        from .llm_proxy import OWNER_PATH
        goal, instr = str(a.get("goal", "")).strip()[:1000], str(a.get("instructions", "")).strip()[:8000]
        crit = [str(c)[:500] for c in (a.get("criteria") or [])][:20]
        agent = str(a.get("agent") or "hermes")
        if not goal or not instr or not crit:
            raise AutomationError("a lab task needs a goal, instructions and at least one acceptance criterion")
        if agent not in LAB_AGENTS:
            raise AutomationError(f"agent must be one of {', '.join(sorted(LAB_AGENTS))}")
        if OWNER_PATH.search("\n".join([instr, *crit])):
            raise AutomationError("the lab cannot see your computer: remove D:\\, C:\\ and /mnt/ paths; files arrive in outputs\\task-<id>\\")
        ma, bs = a.get("max_attempts", 2), a.get("budget_s", 1800)
        if not isinstance(ma, int) or not 1 <= ma <= 5 or not isinstance(bs, int) or not 120 <= bs <= 5400:
            raise AutomationError("max_attempts is 1-5 and budget_s is 120-5400")
        return {"goal": goal, "instructions": instr, "criteria": crit, "agent": agent, "max_attempts": ma, "budget_s": bs}

    def preview(self, d: dict, owner="owner") -> dict:
        """What would happen, without creating anything: the checked definition, the next runs and every side effect."""
        a = self.normalize(d, owner)
        runs, cur = [], self.clock()
        if a["trigger_kind"] == "once":
            runs = [float(a["schedule"])]
        elif a["trigger_kind"] == "event":
            runs = []
        else:
            for _ in range(3):
                cur = next_after(dict(a, created=self.clock()), cur)
                runs.append(cur)
        zone = ZoneInfo(a["tz"])
        effects = {"lab_task": ["creates an outcome task and dispatches it to the lab (public-lane models; files are delivered to outputs\\task-<id>\\ after independent verification)"],
                   "job": [f"queues a '{a['action'].get('kind')}' job on node '{a['action'].get('node')}' (fixed kind, checked by policy at run time)"],
                   "command": ["sends the command text to JARVIS (your own agent), which acts only through kernel-gated tools"]}[a["action_kind"]]
        if a["trigger_kind"] == "event":
            effects = [f"runs once for each file that arrives in the laptop's '{a['event_spec']['watch']}' folder matching '{a['event_spec']['glob']}' "
                       "(a file must stop changing for a few seconds first; the same file is never run twice)"] + effects
        return {"definition": a, "next_runs": [{"utc": iso_utc(t), "local": dt.datetime.fromtimestamp(t, zone).strftime("%Y-%m-%d %H:%M %Z")} for t in runs],
                "side_effects": effects,
                "limits": {"runs_per_day": a["max_runs_per_day"], "pause_after_failures": a["pause_after_failures"],
                           "catchup": a["catchup"], "overlap": a["overlap"], "expires": a["expires"]}}

    # ---- CRUD
    def create(self, d: dict, owner="owner", enabled=True, created_by="owner") -> dict:
        a = self.normalize(d, owner)
        aid, now = "a-" + secrets.token_hex(4), self.clock()
        first = (float(a["schedule"]) if a["trigger_kind"] == "once" else None if a["trigger_kind"] == "event"
                 else next_after(dict(a, created=now), now))
        state = "scheduled" if enabled else "paused"
        self.k.db.execute(
            "INSERT INTO automations(id,name,owner,action_kind,action,trigger_kind,schedule,tz,node,event_spec,enabled,state,paused_reason,"
            "next_run,anchor,catchup,grace_s,overlap,repeat_times,max_runs_per_day,pause_after_failures,expires,created_by,created,updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, a["name"], a["owner"], a["action_kind"], json.dumps(a["action"]), a["trigger_kind"], a["schedule"], a["tz"], a["node"],
             json.dumps(a["event_spec"]) if a["event_spec"] else None, 1 if enabled else 0, state, None if enabled else "waiting for the owner to review and enable it", first, a["anchor"],
             a["catchup"], a["grace_s"], a["overlap"], a["repeat_times"], a["max_runs_per_day"], a["pause_after_failures"], a["expires"],
             created_by[:40], now, now))
        self.k.audit.append("automation_created", {"id": aid, "kind": a["action_kind"], "trigger": a["trigger_kind"], "by": created_by[:40],
                                                   "enabled": enabled})
        return self.get(aid)

    def get(self, aid: str):
        r = self.k.db.execute("SELECT * FROM automations WHERE id=?", (str(aid)[:20],)).fetchone()
        if not r:
            return None
        cols = [c[1] for c in self.k.db.execute("PRAGMA table_info(automations)").fetchall()]
        a = dict(zip(cols, r))
        a["action"] = json.loads(a["action"] or "{}")
        a["event_spec"] = json.loads(a["event_spec"]) if a.get("event_spec") else None
        a["runs"] = self.runs(aid, 8)
        return a

    def list(self):
        return [self.get(r[0]) for r in self.k.db.execute("SELECT id FROM automations ORDER BY created DESC LIMIT 200").fetchall()]

    def runs(self, aid: str, limit=20):
        cols = ("id", "run_key", "scheduled_for", "fired_at", "source", "status", "task_id", "job_id", "command_id", "detail", "finished")
        rows = self.k.db.execute(f"SELECT {','.join(cols)} FROM automation_runs WHERE automation_id=? ORDER BY id DESC LIMIT ?", (aid, limit)).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def set_enabled(self, aid: str, enabled: bool, reason=None) -> dict:
        a = self.get(aid)
        if not a:
            raise AutomationError("no such automation", 404)
        if a["state"] == "completed":
            raise AutomationError("this automation has finished its runs", 409)
        nxt = a["next_run"]
        if enabled and a["trigger_kind"] in ("cron", "interval"):
            nxt = next_after(a, self.clock())            # resuming never replays what was missed while paused
        self.k.db.execute("UPDATE automations SET enabled=?, state=?, paused_reason=?, next_run=?, failure_streak=CASE WHEN ?=1 THEN 0 ELSE failure_streak END, "
                          "updated=?, version=version+1 WHERE id=?",
                          (1 if enabled else 0, "scheduled" if enabled else "paused", None if enabled else (reason or "paused by the owner"),
                           nxt, 1 if enabled else 0, self.clock(), aid))
        self.k.audit.append("automation_resumed" if enabled else "automation_paused", {"id": aid})
        return self.get(aid)

    def update(self, aid: str, d: dict, expected_version=None) -> dict:
        cur = self.get(aid)
        if not cur:
            raise AutomationError("no such automation", 404)
        if expected_version is not None and int(expected_version) != cur["version"]:
            raise AutomationError("the automation changed since you read it; reload and try again", 409)
        merged = {k: cur[k] for k in ("name", "action_kind", "trigger_kind", "schedule", "tz", "catchup", "overlap", "grace_s",
                                      "max_runs_per_day", "pause_after_failures", "repeat_times", "expires")}
        merged["action"] = cur["action"]
        merged["event_spec"] = cur["event_spec"]
        merged.update({k: v for k, v in d.items() if k in merged or k == "event_spec"})
        a = self.normalize(merged, cur["owner"])
        now = self.clock()
        first = (float(a["schedule"]) if a["trigger_kind"] == "once" else None if a["trigger_kind"] == "event"
                 else next_after(dict(a, created=now), now))
        r = self.k.db.execute(
            "UPDATE automations SET name=?, action_kind=?, action=?, trigger_kind=?, schedule=?, tz=?, anchor=?, catchup=?, grace_s=?, overlap=?, "
            "repeat_times=?, max_runs_per_day=?, pause_after_failures=?, expires=?, next_run=?, event_spec=?, updated=?, version=version+1 WHERE id=? AND version=?",
            (a["name"], a["action_kind"], json.dumps(a["action"]), a["trigger_kind"], a["schedule"], a["tz"], a["anchor"], a["catchup"],
             a["grace_s"], a["overlap"], a["repeat_times"], a["max_runs_per_day"], a["pause_after_failures"], a["expires"], first,
             json.dumps(a["event_spec"]) if a["event_spec"] else None, now, aid, cur["version"]))
        if r.rowcount != 1:
            raise AutomationError("the automation changed while updating; try again", 409)
        self.k.audit.append("automation_updated", {"id": aid})
        return self.get(aid)

    def delete(self, aid: str) -> dict:
        if not self.get(aid):
            raise AutomationError("no such automation", 404)
        with self.k.db.tx():
            self.k.db.execute("DELETE FROM automation_runs WHERE automation_id=?", (aid,))
            self.k.db.execute("DELETE FROM automations WHERE id=?", (aid,))
            self.k.audit.append("automation_deleted", {"id": aid})
        return {"deleted": aid, "verified": self.get(aid) is None}

    # ---- firing
    def _claim(self, a: dict, key: str, slot, source: str, status="claimed", detail=None):
        cur = self.k.db.execute("INSERT OR IGNORE INTO automation_runs(automation_id, run_key, scheduled_for, source, status, detail) "
                                "VALUES (?,?,?,?,?,?)", (a["id"], key, slot, source, status, detail))
        return cur.rowcount == 1

    def _fire(self, a: dict, key: str, context: str = "") -> str:
        """Create the task/job/command for a claimed run. Returns the run's new status."""
        act, now = a["action"], self.clock()
        # The fence: only one caller moves a claimed run to 'firing', so a retry loop in another process cannot fire it again.
        got = self.k.db.execute("UPDATE automation_runs SET status='firing', fired_at=? WHERE automation_id=? AND run_key=? AND status='claimed'",
                                (now, a["id"], key))
        if got.rowcount != 1:
            return "skipped"
        row = {"status": "fired", "task_id": None, "job_id": None, "command_id": None, "detail": None}
        try:
            if a["action_kind"] == "command" and self.owner is not None and self.owner.busy.locked():
                self.k.db.execute("UPDATE automation_runs SET status='claimed', detail='waiting: JARVIS is busy with another command' "
                                  "WHERE automation_id=? AND run_key=? AND status='firing'", (a["id"], key))
                return "claimed"
            if a["action_kind"] == "job":
                row["job_id"] = self.jobs.create(act.get("node") or "laptop", act["kind"], act["params"], owner=None, lane="private")
            elif a["action_kind"] == "command":
                if self.owner is None:
                    raise AutomationError("JARVIS's agent is not reachable from the kernel")
                row["command_id"] = self.owner.command(f"[automation '{a['name']}'{context}] {act['text']}", source="automation")["id"]
            else:
                if self.dispatch is None:
                    raise AutomationError("the lab dispatch path is not available")
                tid = self.tasks.create("automation", "public", f"automation '{a['name']}' ({key})", act["goal"], None, act["criteria"],
                                        "schedule", act["max_attempts"], act["budget_s"])
                row["task_id"] = tid
                code, reply = self.dispatch("automation", "public", tid, {"instructions": (act["instructions"] + (f"\n\nTrigger: {context.strip(' ,')}" if context else ""))[:8000],
                                                                         "criteria": act["criteria"],
                                                                         "agent": act["agent"], "public_ok": True})
                if code != 200:
                    self.tasks.update(None, tid, state="failed", result="not dispatched: " + str(reply.get("reason", ""))[:200])
                    raise AutomationError(str(reply.get("reason", "dispatch refused"))[:200])
        except AutomationError as e:
            row["status"], row["detail"] = "refused", str(e)[:300]
        except Exception as e:
            from .store import Killed
            row["status"] = "refused"
            row["detail"] = "kill switch is on" if isinstance(e, Killed) else f"{type(e).__name__}: {str(e)[:200]}"
        self.k.db.execute("UPDATE automation_runs SET status=?, fired_at=?, task_id=?, job_id=?, command_id=?, detail=? "
                          "WHERE automation_id=? AND run_key=? AND status='firing'", (row["status"], now, row["task_id"], row["job_id"],
                                                                                      row["command_id"], row["detail"], a["id"], key))
        self.k.audit.append("automation_fired", {"id": a["id"], "key": key[:60], "status": row["status"]})
        if row["status"] == "refused":
            self._note_failure(a["id"])
        return row["status"]

    def run_now(self, aid: str) -> dict:
        a = self.get(aid)
        if not a:
            raise AutomationError("no such automation", 404)
        if self.k.killed():
            raise AutomationError("the kill switch is on", 503)
        key = f"{aid}:manual:{secrets.token_hex(4)}"
        self._claim(a, key, self.clock(), "manual")
        st = self._fire(a, key)
        return {"run_key": key, "status": st, "run": next(r for r in self.runs(aid, 5) if r["run_key"] == key)}

    def tick(self, now=None) -> dict:
        """Fire what is due, settle what finished. Safe to call from any thread or process, any number of times."""
        now = now or self.clock()
        out = {"fired": [], "skipped": [], "settled": 0}
        if self.k.killed():
            return out
        due = [r[0] for r in self.k.db.execute("SELECT id FROM automations WHERE enabled=1 AND state='scheduled' AND next_run IS NOT NULL "
                                               "AND next_run<=? ORDER BY next_run LIMIT 50", (now,)).fetchall()]
        for aid in due:
            a = self.get(aid)
            if not a or not a["enabled"] or a["next_run"] > now:
                continue
            slots, cur = [], a["next_run"]
            while cur is not None and cur <= now and len(slots) < MAX_SLOTS:
                slots.append(cur)
                cur = None if a["trigger_kind"] == "once" else next_after(a, cur)
            late = now - slots[-1]
            if a["catchup"] == "all":
                missed, skipped_slots = 0, []
            elif a["catchup"] == "skip" and late > a["grace_s"]:
                missed, skipped_slots, slots = len(slots), list(slots), []      # too late: nothing runs, every slot is recorded
            else:                                                                # 'one' (or 'skip' within its grace): only the latest
                missed, skipped_slots, slots = len(slots) - 1, slots[:-1], slots[-1:]
            # advance first: the next run is decided before any work starts (a crash cannot re-fire this slot)
            done_once = a["trigger_kind"] == "once"
            with self.k.db.tx():
                self.k.db.execute("UPDATE automations SET next_run=?, state=CASE WHEN ? THEN 'completed' ELSE state END, "
                                  "updated=? WHERE id=? AND next_run=?", (cur, 1 if done_once else 0, now, aid, a["next_run"]))
            if a["expires"] and now > a["expires"]:
                self.k.db.execute("UPDATE automations SET enabled=0, state='completed', paused_reason='expired' WHERE id=?", (aid,))
                continue
            if missed:
                self._claim(a, f"{aid}:missed:{iso_utc(a['next_run'])}", a["next_run"], "catchup", "skipped_missed",
                            f"{missed} missed slot(s) skipped (late {int(late)} s)")
                out["skipped"].append(aid)
            for s in slots:
                key = f"{aid}:{iso_utc(s)}"
                if not self._claim(a, key, s, "schedule" if s == slots[-1] and not missed else "catchup"):
                    continue                                            # another tick or process already has this slot
                today = self.k.db.execute("SELECT COUNT(*) FROM automation_runs WHERE automation_id=? AND fired_at>? AND status NOT LIKE 'skipped%'",
                                          (aid, now - 86400)).fetchone()[0]
                busy = self.k.db.execute("SELECT 1 FROM automation_runs WHERE automation_id=? AND status='fired' AND run_key!=? LIMIT 1",
                                         (aid, key)).fetchone()
                if today >= a["max_runs_per_day"]:
                    self.k.db.execute("UPDATE automation_runs SET status='skipped_limit', detail='runs per day reached' WHERE automation_id=? AND run_key=?", (aid, key))
                elif busy and a["overlap"] == "skip":
                    self.k.db.execute("UPDATE automation_runs SET status='skipped_overlap', detail='the previous run is still going' "
                                      "WHERE automation_id=? AND run_key=?", (aid, key))
                else:
                    self._fire(a, key)
                    out["fired"].append(key)
                    n = a["repeat_done"] + 1
                    self.k.db.execute("UPDATE automations SET repeat_done=? WHERE id=?", (n, aid))
                    if a["repeat_times"] and n >= a["repeat_times"]:
                        self.k.db.execute("UPDATE automations SET enabled=0, state='completed', paused_reason='repeat limit reached' WHERE id=?", (aid,))
        out["events"] = self.process_events(now)
        out["retried"] = self._retry_claimed(now)
        out["settled"] = self.settle(now)
        return out

    def process_events(self, now) -> list:
        """File-arrival events the laptop node reported (kernel table watch_events): one run per matching automation per
        file. The event id is the dedupe key, so a repeated report never runs twice; an event with no taker is kept
        for a while (an automation created a minute later still sees nothing: it only acts on new arrivals)."""
        import fnmatch
        out = []
        rows = self.k.db.execute("SELECT id, node, watch, rel, size FROM watch_events WHERE handled=0 ORDER BY ts LIMIT 50").fetchall()
        for eid, node, watch, rel, size in rows:
            for (aid,) in self.k.db.execute("SELECT id FROM automations WHERE trigger_kind='event' AND enabled=1 AND state='scheduled'").fetchall():
                a = self.get(aid)
                spec = a["event_spec"] or {}
                if spec.get("watch") != watch or not fnmatch.fnmatchcase(os.path.basename(rel).lower(), str(spec.get("glob", "*")).lower()):
                    continue
                if a["created"] > now or self.k.db.execute("SELECT 1 FROM watch_events WHERE id=? AND ts<?", (eid, a["created"])).fetchone():
                    continue                                             # arrived before this automation existed
                key = f"{aid}:ev:{eid[:24]}"
                if not self._claim(a, key, now, "event"):
                    continue
                today = self.k.db.execute("SELECT COUNT(*) FROM automation_runs WHERE automation_id=? AND fired_at>? AND status NOT LIKE 'skipped%'",
                                          (aid, now - 86400)).fetchone()[0]
                busy = self.k.db.execute("SELECT 1 FROM automation_runs WHERE automation_id=? AND status='fired' AND run_key!=? LIMIT 1", (aid, key)).fetchone()
                if today >= a["max_runs_per_day"]:
                    self.k.db.execute("UPDATE automation_runs SET status='skipped_limit', detail='runs per day reached' WHERE automation_id=? AND run_key=?", (aid, key))
                elif busy and a["overlap"] == "skip":
                    self.k.db.execute("UPDATE automation_runs SET status='skipped_overlap', detail='the previous run is still going' WHERE automation_id=? AND run_key=?", (aid, key))
                else:
                    self._fire(a, key, f", file arrived in '{watch}': {os.path.basename(rel)[:80]}")
                    out.append(key)
            self.k.db.execute("UPDATE watch_events SET handled=1 WHERE id=?", (eid,))
        return out

    def _retry_claimed(self, now) -> int:
        """A command run that found JARVIS busy waits as 'claimed' with a 'waiting' note; try again until it fires. A run
        stuck in claimed/firing without that note (the process died between the steps) becomes 'unknown': its side effects
        cannot be known, so it is surfaced and never re-run silently."""
        n = 0
        rows = self.k.db.execute("SELECT automation_id, run_key, scheduled_for, status, detail, COALESCE(fired_at, scheduled_for) "
                                 "FROM automation_runs WHERE status IN ('claimed','firing')").fetchall()
        for aid, key, sched, status, detail, since in rows:
            a = self.get(aid)
            waiting = status == "claimed" and str(detail or "").startswith("waiting")
            if not waiting:
                if now - (since or now) > CLAIM_GRACE:
                    self.k.db.execute("UPDATE automation_runs SET status='unknown', detail='claimed but never fired (the process stopped in between)', "
                                      "finished=? WHERE automation_id=? AND run_key=? AND status IN ('claimed','firing')", (now, aid, key))
                    if a:
                        self._note_failure(aid)
                continue
            if not a:
                continue
            if now - (sched or now) > CLAIM_GRACE:
                self.k.db.execute("UPDATE automation_runs SET status='failed', detail='JARVIS stayed busy too long', finished=? "
                                  "WHERE automation_id=? AND run_key=? AND status='claimed'", (now, aid, key))
                self._note_failure(aid)
                continue
            if self._fire(a, key) == "fired":
                n += 1
        return n

    def settle(self, now=None) -> int:
        """Copy the end state of fired runs back (done / failed) and keep the failure streak."""
        now, n = now or self.clock(), 0
        for rid, aid, tid, jid, cid in self.k.db.execute("SELECT id, automation_id, task_id, job_id, command_id FROM automation_runs "
                                                         "WHERE status='fired'").fetchall():
            end = None
            if tid:
                s = self.k.db.execute("SELECT state FROM tasks WHERE id=?", (tid,)).fetchone()
                end = None if not s else {"completed": "done", "failed": "failed", "cancelled": "failed"}.get(s[0])
            elif jid:
                s = self.k.db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()
                end = None if not s else {"done": "done", "failed": "failed", "expired": "failed"}.get(s[0])
            elif cid:
                s = self.k.db.execute("SELECT state FROM owner_commands WHERE id=?", (cid,)).fetchone()
                end = None if not s else {"done": "done", "failed": "failed", "cancelled": "failed"}.get(s[0])
            if end:
                self.k.db.execute("UPDATE automation_runs SET status=?, finished=? WHERE id=?", (end, now, rid))
                n += 1
                if end == "done":
                    self.k.db.execute("UPDATE automations SET failure_streak=0 WHERE id=?", (aid,))
                else:
                    self._note_failure(aid)
        return n

    def _note_failure(self, aid: str):
        self.k.db.execute("UPDATE automations SET failure_streak=failure_streak+1 WHERE id=?", (aid,))
        r = self.k.db.execute("SELECT failure_streak, pause_after_failures, state FROM automations WHERE id=?", (aid,)).fetchone()
        if r and r[0] >= r[1] and r[2] == "scheduled":
            self.k.db.execute("UPDATE automations SET enabled=0, state='paused', paused_reason=? WHERE id=?",
                              (f"paused automatically after {r[0]} failed runs in a row", aid))
            self.k.audit.append("automation_auto_paused", {"id": aid, "failures": r[0]})

    # ---- natural language (deterministic common phrases; anything else is created through JARVIS or by cron)
    @staticmethod
    def from_phrase(text: str):
        """'every day at 8:30', 'every morning', 'weekdays at 6pm', 'every monday at 7', 'every 30 minutes', 'every hour'
        -> (trigger_kind, schedule) or None. Times are read as the automation's own time zone."""
        t = " ".join(text.lower().split())
        m = re.search(r"every (\d+) (minute|hour)s?\b", t)
        if m:
            return "interval", str(int(m.group(1)) * (60 if m.group(2) == "minute" else 3600))
        if re.search(r"\bevery hour\b|\bhourly\b", t):
            return "interval", "3600"

        def hm(default):
            m2 = re.search(r"\bat (\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t)
            if not m2:
                return default
            h, mi = int(m2.group(1)), int(m2.group(2) or 0)
            if m2.group(3) == "pm" and h < 12:
                h += 12
            if m2.group(3) == "am" and h == 12:
                h = 0
            return (h, mi) if h < 24 and mi < 60 else default
        full = {"sun": "sunday", "mon": "monday", "tue": "tuesday", "wed": "wednesday", "thu": "thursday", "fri": "friday", "sat": "saturday"}
        day = next((d for d in DOWS if re.search(r"\b" + full[d] + r"s?\b", t)), None)
        if "weekday" in t:
            h, mi = hm((9, 0))
            return "cron", f"{mi} {h} * * 1-5"
        if day:
            h, mi = hm((9, 0))
            return "cron", f"{mi} {h} * * {DOWS[day]}"
        if re.search(r"\bevery (day|morning|evening|night)\b|\bdaily\b", t):
            dflt = (8, 0) if "morning" in t or "day" in t or "daily" in t else (18, 0) if "evening" in t else (22, 0)
            h, mi = hm(dflt)
            return "cron", f"{mi} {h} * * *"
        return None
