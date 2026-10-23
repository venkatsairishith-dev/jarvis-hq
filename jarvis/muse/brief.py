"""Morning brief (plan P9 step 3): one note a day in the vault saying what needs the owner, and one content-free ping.

The brief is put together from what JARVIS already knows locally: tasks waiting for approval, tasks that failed (marked when the
outcome of an action is uncertain, so nobody blindly retries), page-watch hits and paused watches, unread notices, the posture check
and, when a calendar source is given, today's events. It is written to <vault>/daily/<date>-brief.md (agents write only daily/,
inbox/ and decisions/) and filed in the inbox once per day; the phone gets the usual "JARVIS: 1 new notice", never the brief.

Text that came from outside (event titles, watch summaries, task errors) is shown as data: one line, control characters and
Markdown markers stripped, cut to MAX_FIELD characters, so a hostile calendar invite cannot add headings or instructions to the note.

Scheduling is not done here: the supervisor (DBOS schedule in the plan) runs the CLI once a morning, by default 07:30 local time.

    python -m jarvis.muse.brief --vault D:\\jarvis\\vault --inbox <inbox.jsonl> [--watches <watches.json>] [--tasks <tasks.db>]
        [--posture] [--push-topic-file D:\\jarvis-secrets\\ntfy.topic] [--print]
"""
from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from jarvis.muse import watch as W
from jarvis.muse.notify import Inbox, Notifier, NtfyPush

DAY_S = 86_400
MAX_FIELD = 160
MAX_ITEMS = 15                       # per section; the rest is counted, not listed
Event = dict[str, Any]               # {"start": epoch seconds or None for all-day, "title": str}


def clean(text: Any, limit: int = MAX_FIELD) -> str:
    """One safe line of outside text: no control characters, no Markdown structure, bounded."""
    s = re.sub(r"[\x00-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]+", " ", str(text))
    s = re.sub(r"[`#<>\[\]|]", "", s)                   # headings, HTML, links, code and tables; emphasis is harmless
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _hhmm(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def _section(title: str, lines: list[str]) -> list[str]:
    if not lines:
        return []
    shown = lines[:MAX_ITEMS] + ([f"- … and {len(lines) - MAX_ITEMS} more"] if len(lines) > MAX_ITEMS else [])
    return [f"### {title}", *shown, ""]


def compose(now: float, *, inbox: Inbox | None = None, watches: list[W.Watch] | None = None, tasks: Any = None,
            posture: Iterable[Any] | None = None, events: list[Event] | None = None) -> str:
    date = time.strftime("%Y-%m-%d", time.localtime(now))
    since = now - DAY_S
    out = [f"## Morning brief, {date}", "", f"Made by JARVIS at {_hhmm(now)} from local data. Outside text is shown as data.", ""]
    needs_you = 0

    if events is not None:
        lines = []
        for e in sorted(events, key=lambda e: (e.get("start") is not None, e.get("start") or 0)):
            when = "all day" if e.get("start") is None else _hhmm(float(e["start"]))
            lines.append(f"- {when}: {clean(e.get('title', '(no title)'))}")
        out += _section("Today", lines) or ["### Today", "- Nothing on the calendar.", ""]

    if tasks is not None:
        waiting = tasks.list(states=("waiting_approval",))
        failed = tasks.list(states=("failed",), since=since)
        needs_you += len(waiting) + len(failed)
        out += _section("Waiting for your approval (approve in the JARVIS console with your PIN)",
                        [f"- {clean(t['kind'])} (task {t['id'][:8]}), waiting since {_hhmm(t['updated'])}" for t in waiting])
        out += _section("Failed in the last day", [
            f"- {clean(t['kind'])} (task {t['id'][:8]}): {clean(t.get('error') or 'no reason recorded')}"
            + (" — an action may or may not have happened; check before retrying" if t.get("outcome_uncertain") else "")
            for t in failed])

    if watches is not None:
        paused = [w for w in watches if w.state == "paused"]
        needs_you += len(paused)
        hits = []
        for w in watches:
            for h in w.history:
                if h.get("ok") and h.get("at", 0) >= since and h.get("summary") not in (None, "no changes", "first check"):
                    hits.append(f"- {clean(w.id)} changed at {_hhmm(h['at'])}: {clean(h['summary'])}")
        out += _section("Page watches that changed", hits)
        out += _section("Page watches paused after repeated failures (resume them in the console)", [f"- {clean(w.id)}" for w in paused])
        if not hits and not paused and watches:
            out += [f"### Page watches", f"- {len(watches)} watched, no changes in the last day.", ""]

    if inbox is not None:
        unread = [r for r in inbox.unread() if r["notice"].get("kind") != "brief"]
        if unread:
            kinds: dict[str, int] = {}
            for r in unread:
                k = clean(r["notice"].get("kind", "notice"), 40)
                kinds[k] = kinds.get(k, 0) + 1
            out += ["### Unread notices", "- " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())), ""]

    if posture is not None:
        bad = [f for f in posture if getattr(f, "level", None) in ("fail", "warn")]
        fails = sum(1 for f in bad if f.level == "fail")
        needs_you += fails
        out += _section("Safety posture (python jarvis/tools/doctor.py)",
                        [f"- {f.level.upper()} {clean(f.check, 30)}: {clean(f.detail, 240)}" for f in bad]) \
            or ["### Safety posture", "- All checks passed.", ""]

    out.insert(4, ("**Needs you:** " + (f"{needs_you} item(s), listed below." if needs_you else "nothing today.")))
    out.insert(5, "")
    return "\n".join(out).rstrip() + "\n"


def write_note(vault: str | Path, now: float, text: str) -> Path:
    """Atomically write <vault>/daily/<date>-brief.md (re-running on the same day replaces that day's brief only)."""
    date = time.strftime("%Y-%m-%d", time.localtime(now))
    path = Path(vault) / "daily" / f"{date}-brief.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def announce(notifier: Notifier, now: float, path: Path) -> int:
    """File the brief in the inbox once per day; the ping that follows carries only a count."""
    date = time.strftime("%Y-%m-%d", time.localtime(now))
    return notifier.deliver([{"kind": "brief", "key": f"brief-{date}", "summary": f"Morning brief for {date}", "path": str(path)}])


def main(argv=None, clock: Callable[[], float] = time.time) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--watches")
    ap.add_argument("--tasks", help="task state database (sqlite)")
    ap.add_argument("--posture", action="store_true", help="include the posture check (jarvis/tools/doctor.py)")
    ap.add_argument("--push-topic-file")
    ap.add_argument("--print", action="store_true", help="also print the brief")
    args = ap.parse_args(argv)
    now = clock()
    inbox = Inbox(args.inbox)
    store = None
    if args.tasks:
        from jarvis.muse.taskstate import TaskStore
        store = TaskStore(args.tasks)
    findings = None
    if args.posture:
        from jarvis.tools import doctor
        findings = doctor.run_all()
    try:
        text = compose(now, inbox=inbox, watches=W.load(args.watches) if args.watches else None, tasks=store, posture=findings)
    finally:
        if store is not None:
            store.close()
    path = write_note(args.vault, now, text)
    notifier = Notifier(inbox, NtfyPush(args.push_topic_file) if args.push_topic_file else None)
    announce(notifier, now, path)
    if args.print:
        print(text)
    print(f"wrote {path}")
    for err in notifier.push_errors:
        print(f"push: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
