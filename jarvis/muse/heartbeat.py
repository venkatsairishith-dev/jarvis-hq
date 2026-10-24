"""JARVIS speaks first (plan: proactive JARVIS, owner "yes" 2026-10-24): decides WHEN to say something and WHAT, from local data only.

Sources (all optional): tasks waiting for approval or failed (taskstate), unread notices (page-watch hits and the like, notify.Inbox), the
watchdog's verdict (services down), and the calendar when a source is given. A tick turns them into short spoken notices, at most a
sentence or two each, and queues them in <muse folder>/spoken.jsonl. The face (or anything else) takes them one at a time with `take`.

Manners, in code so they are tested: no notice at night (quiet hours) unless it is urgent; no do-not-disturb (a file the owner makes) unless
urgent; at least MIN_GAP_S between spoken notices; at most MAX_PER_DAY a day; the same thing is not said twice within REPEAT_S; one
greeting a day, the first tick in the morning window, and one evening wrap-up only when something is still open. Urgent means a service
down that the watchdog gave up on, or a failed action whose outcome is uncertain.

Everything from outside (watch summaries, event titles, task errors) is shown as one cleaned line (brief.clean): it is data, and the face
frames each spoken notice as information from JARVIS's own systems that the owner has not seen, never as an instruction. JARVIS only
says things here; doing anything still goes through the kernel's approvals.

    python -m jarvis.muse.heartbeat --data D:\\jarvis\\data\\muse [--tasks D:\\jarvis\\data\\muse\\tasks.db] [--once] [--every 60]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from jarvis.muse.brief import clean
from jarvis.muse.notify import Inbox

QUIET_FROM, QUIET_TO = 22, 7           # local hours: [22:00, 07:00)
MORNING = (7, 11)                       # the greeting is made by the first tick in this window
EVENING = (20, 22)
MIN_GAP_S = 20 * 60
MAX_PER_DAY = 6
REPEAT_S = 12 * 3600
EVENT_LEAD_S = (5 * 60, 30 * 60)       # an event is announced when it starts in 5 to 30 minutes
MAX_TEXT = 200
URGENT, NORMAL, LOW = "urgent", "normal", "low"


def _hour(t: float) -> int:
    return time.localtime(t).tm_hour


def in_quiet_hours(t: float) -> bool:
    h = _hour(t)
    return h >= QUIET_FROM or h < QUIET_TO


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


class Heartbeat:
    def __init__(self, data: str | Path, *, tasks: Any = None, inbox: Inbox | None = None, events: Callable[[], list[dict]] | None = None,
                 status_file: str | Path | None = None, clock: Callable[[], float] = time.time):
        self.dir = Path(data)                                  # the muse folder (tasks.db, inbox.jsonl, watches.json live here too)
        self.queue, self.state_file, self.dnd = self.dir / "spoken.jsonl", self.dir / "heartbeat_state.json", self.dir / "dnd"
        self.tasks, self.inbox, self.events, self.clock = tasks, inbox, events, clock
        self.status_file = Path(status_file) if status_file else self.dir.parent / "logs" / "watchdog_status.json"
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---- state -----------------------------------------------------------------------------------------------------------
    def _state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"said": {}, "last_at": 0, "day": "", "count": 0, "greeted": "", "wrapped": ""}

    def _save(self, st: dict) -> None:
        tmp = self.state_file.with_name(self.state_file.name + ".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, self.state_file)

    # ---- what there is to say --------------------------------------------------------------------------------------------
    def candidates(self, now: float) -> list[dict]:
        out: list[dict] = []
        if self.status_file.exists():
            try:
                s = json.loads(self.status_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                s = {}
            gave_up = [clean(x, 40) for x in s.get("gave_up_on", [])]
            if gave_up:
                out.append({"key": _key("down", *sorted(gave_up)), "urgency": URGENT,
                            "text": f"{', '.join(gave_up)} stopped working and I could not restart it. Please look at it when you can."})
        if self.tasks is not None:
            waiting = self.tasks.list(states=("waiting_approval",))
            if waiting:
                out.append({"key": _key("waiting", *sorted(t["id"] for t in waiting)), "urgency": NORMAL,
                            "text": f"{_n(len(waiting), 'task is', 'tasks are')} waiting for your approval in the console."})
            for t in self.tasks.list(states=("failed",), since=now - 3600)[:3]:
                unsure = bool(t.get("outcome_uncertain"))
                out.append({"key": _key("failed", t["id"]), "urgency": URGENT if unsure else NORMAL,
                            "text": f"A task failed: {clean(t.get('kind'), 60)}." + (" It may or may not have happened, so please check before retrying." if unsure else "")})
        if self.inbox is not None:
            for r in self.inbox.unread()[:5]:
                n = r["notice"]
                if n.get("kind") == "brief":
                    continue
                out.append({"key": _key("notice", r["key"]), "urgency": NORMAL, "text": f"New notice: {clean(n.get('summary') or n.get('kind'), 140)}."})
        if self.events is not None:
            for e in self.events():
                start = e.get("start")
                if start is not None and EVENT_LEAD_S[0] <= start - now <= EVENT_LEAD_S[1]:
                    out.append({"key": _key("event", e.get("title"), start), "urgency": NORMAL,
                                "text": f"{clean(e.get('title', 'An event'), 100)} starts in {int((start - now) // 60)} minutes."})
        return out

    def _framing_lines(self, now: float, st: dict, open_items: list[dict]) -> list[dict]:
        """The daily greeting and evening wrap-up."""
        out, day, h = [], _day(now), _hour(now)
        if MORNING[0] <= h < MORNING[1] and st.get("greeted") != day:
            out.append({"key": f"greeting-{day}", "urgency": LOW, "greeting": True,
                        "text": "Good morning." + (f" {_n(len(open_items), 'thing needs', 'things need')} you today." if open_items else " Nothing needs you right now.")})
        if EVENING[0] <= h < EVENING[1] and st.get("wrapped") != day and open_items:
            out.append({"key": f"wrap-{day}", "urgency": LOW, "wrap": True,
                        "text": f"Before the night: {_n(len(open_items), 'thing is', 'things are')} still open for you."})
        return out

    # ---- one tick --------------------------------------------------------------------------------------------------------
    def tick(self) -> list[dict]:
        """Queue what is worth saying now (maybe nothing). Returns the notices queued."""
        now, st = self.clock(), self._state()
        if st.get("day") != _day(now):
            st["day"], st["count"] = _day(now), 0
        st["said"] = {k: t for k, t in st.get("said", {}).items() if now - t < REPEAT_S}
        cands = [c for c in self.candidates(now) if c["key"] not in st["said"]]
        open_items = [c for c in self.candidates(now) if c["urgency"] != LOW]
        cands += [c for c in self._framing_lines(now, st, open_items) if c["key"] not in st["said"]]
        quiet = in_quiet_hours(now) or self.dnd.exists()
        queued: list[dict] = []
        for c in sorted(cands, key=lambda c: {URGENT: 0, NORMAL: 1, LOW: 2}[c["urgency"]]):
            urgent = c["urgency"] == URGENT
            if st["count"] >= MAX_PER_DAY and not urgent:
                continue
            if not urgent and (quiet or now - st["last_at"] < MIN_GAP_S):
                continue
            if urgent and now - st["last_at"] < 60:
                continue
            row = {"key": c["key"], "at": now, "urgency": c["urgency"], "text": clean(c["text"], MAX_TEXT)}
            with open(self.queue, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            st["said"][c["key"]], st["last_at"], st["count"] = now, now, st["count"] + 1
            if c.get("greeting"):
                st["greeted"] = _day(now)
            if c.get("wrap"):
                st["wrapped"] = _day(now)
            queued.append(row)
            if not urgent:
                break                                     # one spoken notice per tick; the gap does the rest
        self._save(st)
        return queued

    # ---- the consumer ----------------------------------------------------------------------------------------------------
    def take(self, limit: int = 1) -> list[dict]:
        """Hand out the oldest queued notices once each (the face asks every few seconds)."""
        try:
            rows = [json.loads(x) for x in self.queue.read_text(encoding="utf-8").splitlines() if x.strip()]
        except (OSError, ValueError):
            return []
        rows = [r for r in rows if now_ok(r, self.clock())]
        give, keep = rows[:limit], rows[limit:]
        tmp = self.queue.with_name(self.queue.name + ".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
        os.replace(tmp, self.queue)
        return give


class ReadOnlyTasks:
    """The task store as the heartbeat needs it, opened read-only: this loop only listens, it never writes the store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def list(self, states: tuple[str, ...] | None = None, since: float | None = None, limit: int = 200) -> list[dict]:
        import sqlite3
        if not self.path.exists():
            return []
        where, args = [], []
        if states:
            where.append(f"state IN ({', '.join('?' * len(states))})")
            args += list(states)
        if since is not None:
            where.append("updated >= ?")
            args.append(since)
        con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated DESC LIMIT ?"
            return [dict(r) for r in con.execute(sql, (*args, int(limit))).fetchall()]
        except sqlite3.Error:
            return []
        finally:
            con.close()


def start_background(data: str | Path, every: int = 60) -> "Heartbeat":
    """A Heartbeat on the owner's data folder, ticking in a daemon thread (the face's tool server starts it)."""
    import logging
    import threading
    hb = Heartbeat(data, tasks=ReadOnlyTasks(Path(data) / "tasks.db"), inbox=Inbox(Path(data) / "inbox.jsonl"))

    def loop():
        while True:
            try:
                hb.tick()
            except Exception:                                  # one bad tick must not end the loop
                logging.getLogger("jarvis.heartbeat").exception("heartbeat tick failed")
            time.sleep(max(10, every))

    threading.Thread(target=loop, name="jarvis-heartbeat", daemon=True).start()
    return hb


def now_ok(row: dict, now: float, max_age_s: float = 2 * 3600) -> bool:
    """A notice nobody collected for two hours is stale (the face was closed): it is dropped, not read out late."""
    return row.get("urgency") == URGENT or now - row.get("at", 0) <= max_age_s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True)
    ap.add_argument("--tasks")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=60)
    a = ap.parse_args(argv)
    hb = Heartbeat(a.data, tasks=ReadOnlyTasks(a.tasks) if a.tasks else None, inbox=Inbox(Path(a.data) / "inbox.jsonl"))
    while True:
        for row in hb.tick():
            print(f"queued [{row['urgency']}]: {row['text']}", flush=True)
        if a.once:
            return 0
        time.sleep(max(10, a.every))


if __name__ == "__main__":
    raise SystemExit(main())
