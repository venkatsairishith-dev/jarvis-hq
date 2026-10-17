"""Coordinator for PC tasks (2026-10-16): one owner request -> local decision -> bounded steps by the right agent ->
kernel-gated actions -> verified outcome, with one shared task record. Logic only (no N.E.K.O imports); the plugin wires
the real agents in.

    request -> decide.decide (local model + deterministic resolution)
            -> for each step: AGENT[tool] runs it (browser / desktop / troubleshooter / research), every action through
               the JARVIS kernel (Pc), which alone decides what is allowed
            -> VERIFY[tool] checks the observed state against that step's success criterion
            -> record: plan, actions, observations, verdicts, uncertainty (D:\\jarvis\\N.E.K.O-data\\neko\\pc_tasks)

Rules: one PC task at a time (execution lock, so two agents never type into the same window); at most MAX_STEPS steps,
TOTAL_BUDGET_S seconds and one retry of a step, and only for a transient failure (a timeout or a lost browser connection),
never for a refusal, a denial or a wrong result; a step that needs the owner (approval, clarification) ends the task with
that question instead of guessing; "success" is reported only for steps whose check passed.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Callable

from . import decide

DATA = Path(os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data"))
TASKS = DATA / "neko" / "pc_tasks"
LOCK = DATA / "neko" / "pc_task.lock"
TOTAL_BUDGET_S = 600
MAX_RETRIES = 1
AGENT = {"open_website": "browser", "play_media": "browser", "browser": "browser", "open_app": "desktop", "window": "desktop",
         "type_text": "desktop", "press_key": "desktop", "open_file": "desktop", "check_pc": "desktop",
         "troubleshoot": "troubleshooter", "research": "research", "open_output": "desktop", "stop": "coordinator"}
TRANSIENT = re.compile(r"(?i)timeout|timed out|connection failed|not answering|temporarily|busy")
# Steps that change nothing when run twice (open, focus, read, check). Typing, keys, clicks, playing, repairs and
# research are not: a failure reported as a timeout may come after they already acted, so they are never re-run blindly.
REPEAT_SAFE = {"open_website", "open_app", "window", "check_pc", "open_file"}
REPEAT_SAFE_BROWSER = {"read", "find", "tabs", "whoami"}


def repeat_safe(step: dict) -> bool:
    t = step.get("tool")
    return t in REPEAT_SAFE or (t == "browser" and step.get("action") in REPEAT_SAFE_BROWSER)


class Busy(Exception):
    pass


# ---------------------------------------------------------------- execution lock
def _alive(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    if os.name != "nt":
        # Not Windows (tests in the Linux container): the Windows call below always failed here, so every lock owner
        # looked alive and a crashed task's lock was never taken over. A zombie has exited, so it is not alive.
        import psutil
        try:
            return psutil.Process(int(pid)).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except Exception:  # noqa: BLE001 - unsure: keep the lock (never take a live task's lock)
            return True
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                                # STILL_ACTIVE
    except Exception:  # noqa: BLE001
        return True


UNWRITTEN_GRACE_S = 30          # a lock file still empty after this long belongs to a writer that died mid-create


def _held(path: Path) -> dict:
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
        return held if isinstance(held, dict) else {}
    except (OSError, ValueError):
        return {}


def _stale(held: dict, path: Path, budget: float) -> bool:
    if not held:
        # Empty or half-written: another task created the file a moment ago and is writing it (bench B-014: 8 threads,
        # 3 "won", because an empty lock read as pid 0 = dead). Only an old unwritten file is a dead writer's leftover.
        try:
            return time.time() - path.stat().st_mtime > UNWRITTEN_GRACE_S
        except OSError:
            return True
    return not _alive(held.get("pid", 0)) or time.time() - float(held.get("at", 0)) > budget + 60


def _take_over(path: Path, held: dict) -> None:
    """Remove a stale lock, but only the very lock judged stale and only one taker at a time: two takers that both read
    the stale lock must not remove each other's fresh one."""
    guard = path.with_name(path.name + ".takeover")
    try:
        os.close(os.open(str(guard), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        try:
            if time.time() - guard.stat().st_mtime > UNWRITTEN_GRACE_S:      # a taker that died: clear its guard
                guard.unlink()
        except OSError:
            pass
        return
    try:
        if _held(path) == held:
            path.unlink()
    except OSError:
        pass
    finally:
        try:
            guard.unlink()
        except OSError:
            pass


def acquire(task_id: str, path: Path = LOCK, budget: float = TOTAL_BUDGET_S) -> None:
    """One PC task at a time. A lock whose owner died, or that outlived its time budget, is taken over (and said so)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"task": task_id, "pid": os.getpid(), "at": time.time()}).encode())
            os.close(fd)
            return
        except FileExistsError:
            held = _held(path)
            if not _stale(held, path, budget):
                raise Busy(f"another PC task is running ({held.get('task') or 'starting'}); stop it first or wait")
            _take_over(path, held)
    raise Busy("the PC task lock could not be taken")


def release(task_id: str, path: Path = LOCK) -> None:
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("task") == task_id:
            path.unlink()
    except (OSError, ValueError):
        pass


def current_task(path: Path = LOCK) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("task")
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- verification: what counts as done, per tool
def verify(step: dict, result: dict) -> tuple[bool, str, str]:
    """(passed, what was checked, uncertainty). Only the observed result is used, never the step's intent."""
    r = result or {}
    t = step["tool"]
    if r.get("state") in ("waiting_for_permission", "denied"):
        return False, f"held for the owner ({r.get('state')})", ""
    if r.get("ok") is not True:                         # only boolean True: {"ok": "false"} is not success
        return False, "the action did not succeed", str(r.get("error") or r.get("state") or "")[:200]
    if t == "open_website":
        ok = r.get("profile") == step.get("profile") and r.get("verified") == "window"
        unc = "" if r.get("profile_name_matches") else "the profile button shows only a display name; the account itself is checked by whoami"
        return ok, f"Chrome window appeared in profile {r.get('profile')}", unc if ok else "no Chrome window was seen"
    if t == "play_media":
        ok = bool(r.get("playing")) and bool(r.get("same_video"))
        media = " ".join(r.get("windows_media") or [])
        unc = "" if (r.get("title") or "").replace(" - YouTube", "")[:30].lower() in media.lower() else "Windows' media session did not confirm the title"
        return ok, f"video '{(r.get('title') or '')[:60]}' playing, same id as chosen", unc
    if t == "open_app":
        return r.get("verified") == "verified", f"a window for {r.get('opened')} {'appeared' if r.get('verified') == 'verified' else 'was not seen'}", \
            "" if r.get("verified") == "verified" else str(r.get("note") or "")
    if t == "open_file":                                   # the node looks for a window showing the file or folder
        seen = r.get("verified") == "verified"
        return seen, f"{r.get('opened') or step.get('name')} " + ("opened (a window showing it appeared)" if seen else
                                                                "was sent to open, but no window showing it was seen"), \
            "" if seen else str(r.get("note") or "")
    if t == "browser" and step.get("action") == "whoami":
        seen = r.get("seen")
        seen = ", ".join(map(str, seen)) if isinstance(seen, (list, tuple)) else seen
        return bool(r.get("matches")), f"signed-in account {seen} vs expected {r.get('expected')}", ""
    if t == "check_pc":
        return True, f"{step.get('check')} read", ""
    if t == "type_text":                                   # the node reads the field back and fails if the text is absent
        return True, f"the text was typed into {r.get('process') or step.get('app')} and read back from the field", ""
    if t == "open_output":
        return bool(r.get("opened_verified")), f"{r.get('opened') or step.get('path')} " + (
            "opened (a window showing it appeared)" if r.get("opened_verified") else "was sent to open, but no window showing it was seen"), ""
    if t == "troubleshoot":
        ok = r.get("outcome") in ("fixed and verified", "diagnosed")
        return ok, f"troubleshooter outcome: {r.get('outcome')}", "" if r.get("verified") else "the cause is a hypothesis"
    if t == "research":
        made = step.get("deliverable", "none") != "none"
        if made and not r.get("file"):
            return False, f"no {step.get('deliverable')} file was made", "the research came back without the file"
        if made and r.get("qa_passed") is False:
            return False, f"the {step.get('deliverable')} failed its quality checks", str(r.get("file") or "")
        if step.get("open_after") and made and not r.get("opened_verified"):
            return False, f"{step.get('deliverable')} made but no window showing it appeared", str(r.get("file") or "")
        what = f"; {step.get('deliverable')} {r.get('file')}" + (" opened" if r.get("opened_verified") else "") if made else ""
        return bool(r.get("summary")), "a report with checked citations came back" + what, ""
    return True, "the action reported success", ""


# ---------------------------------------------------------------- the run
FOLLOW_UP_S = 600
FOLLOW_CUE = re.compile(r"(?i)\b(instead|actually|again|also|too|as well|same|that one|the other|it|that|this|them|there|"
                        r"rather|switch|change it|not that|no,)\b")


def previous(tasks_dir: Path = TASKS, now: float | None = None, within_s: float = FOLLOW_UP_S) -> dict | None:
    """The owner's last PC task if it was recent: its request and what it produced (a made file, a page, an app), so a
    follow-up ("open it", "use the Satish profile instead") can refer to it. Only checked results count as produced."""
    now = time.time() if now is None else now
    files = sorted(tasks_dir.glob("*.json"))[-5:] if tasks_dir.is_dir() else []
    for f in files[-1:]:
        # only the NEWEST task counts: if its record is damaged, "it" is unknown, and an older task's file is not it
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            at = float(rec.get("finished") or rec.get("started") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            try:
                recent = now - f.stat().st_mtime <= within_s
            except OSError:
                recent = True
            return {"request": "", "status": "unreadable"} if recent else None
        if now - at > within_s:
            return None
        out = {"request": rec.get("request", ""), "status": rec.get("status")}
        for s in rec.get("steps", []):
            r, a = s.get("result") or {}, s.get("args") or {}
            if not s.get("passed"):
                continue
            if r.get("file"):
                out["file"] = r["file"]
            if s["tool"] == "open_website" and a.get("url"):
                out.update(url=a["url"], profile=a.get("profile"))
            if s["tool"] == "open_app" and a.get("app"):
                out["app"] = a["app"]
        return out
    return None


class Coordinator:
    def __init__(self, agents: dict[str, Callable[[dict], dict]], profiles: list[dict], decide_fn=None, clock=time.time,
                 on_progress: Callable[[str, str], None] | None = None, tasks_dir: Path = TASKS, lock_path: Path = LOCK,
                 budget_s: float = TOTAL_BUDGET_S, cancelled: Callable[[], bool] = lambda: False):
        self.agents, self.profiles, self.clock = agents, profiles, clock

        def with_context(req):
            # the previous request is shown to the model only when this one reads like a follow-up: otherwise the model
            # mixes the two ("minimize notepad" after "type hello into notepad" came back with the typing again; live
            # run 2026-10-16 15:5x). "open it" is resolved without the model (decide.follow_up).
            prev = previous(tasks_dir, self.clock())
            ctx = (prev or {}).get("request", "") if FOLLOW_CUE.search(req or "") else ""
            return decide.decide(req, profiles, context=ctx, last=prev)
        self.decide_fn = decide_fn or with_context
        self.on_progress = on_progress or (lambda s, d: None)
        self.tasks_dir, self.lock_path, self.budget_s, self.cancelled = tasks_dir, lock_path, budget_s, cancelled

    def _save(self, rec: dict):
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        (self.tasks_dir / f"{rec['id']}.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")

    def run(self, request: str, plan: decide.Plan | None = None) -> dict:
        tid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        rec = {"id": tid, "request": request, "started": self.clock(), "status": "planning", "steps": [], "agents": []}
        self.on_progress("planning", "Deciding what to do")
        plan = plan or self.decide_fn(request)
        rec["plan"] = {"status": plan.status, "steps": plan.steps, "model": plan.model, "seconds": plan.seconds,
                       "problems": plan.problems, "question": plan.question}
        if plan.status == "clarify":
            rec.update(status="needs_clarification", question=plan.question, finished=self.clock())
            self._save(rec)
            return rec
        if plan.status != "ok":
            rec.update(status="not_understood", error="; ".join(plan.problems)[:300], finished=self.clock())
            self._save(rec)
            return rec
        if plan.steps and plan.steps[0]["tool"] == "answer":
            rec.update(status="not_a_pc_task", route="answer", finished=self.clock())
            self._save(rec)
            return rec
        if plan.steps and plan.steps[0]["tool"] == "stop":
            res = (self.agents.get("stop") or (lambda s: {"ok": True}))({"tool": "stop"})
            res = res if isinstance(res, dict) else {"ok": False, "error": f"the stop agent returned {type(res).__name__}"}
            rec.update(status="stopped" if res.get("ok") is True else "failed", steps=[{"tool": "stop", "result": res}], finished=self.clock())
            self._save(rec)
            return rec
        try:
            acquire(tid, self.lock_path, self.budget_s)
        except Busy as e:
            rec.update(status="busy", error=str(e), finished=self.clock())
            self._save(rec)
            return rec
        try:
            for i, step in enumerate(plan.steps, 1):
                if self.cancelled():
                    rec["status"] = "cancelled"
                    break
                if self.clock() - rec["started"] > self.budget_s:
                    rec.update(status="failed", error=f"time budget of {self.budget_s:.0f} s used up before step {i}")
                    break
                who = AGENT.get(step["tool"], "desktop")
                rec["agents"] = sorted(set(rec["agents"]) | {who})
                self.on_progress("working", f"{who} agent: step {i} of {len(plan.steps)} ({step['tool']})")
                entry = {"n": i, "tool": step["tool"], "agent": who, "args": {k: v for k, v in step.items() if k != "tool"},
                         "attempts": 0, "started": self.clock()}
                rec["steps"].append(entry)
                fn = self.agents.get(step["tool"])
                if fn is None:
                    entry.update(result={"ok": False, "error": "no agent for this step"}, passed=False, checked="", uncertainty="")
                    rec.update(status="failed", error=f"no agent can do {step['tool']}")
                    break
                while True:
                    entry["attempts"] += 1
                    try:
                        res = fn(step) or {}
                        if not isinstance(res, dict):          # an HTML error page, a list, a number: not a result
                            res = {"ok": False, "error": f"the agent returned a {type(res).__name__}, not a result object"}
                        passed, checked, unc = verify(step, res)
                    except Exception as e:  # noqa: BLE001 - a failed agent is a failed step, not a crashed coordinator
                        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
                        passed, checked, unc = False, "the action did not succeed", res["error"]
                    entry.update(result=_slim(res), passed=passed, checked=checked, uncertainty=unc, finished=self.clock())
                    transient = not passed and TRANSIENT.search(str(res.get("error") or "")) and res.get("state") not in (
                        "waiting_for_permission", "denied", "cancelled")
                    if transient and repeat_safe(step) and entry["attempts"] <= MAX_RETRIES and not self.cancelled():
                        self.on_progress("working", f"{who} agent: retrying step {i} once ({str(res.get('error'))[:60]})")
                        continue
                    break
                if res.get("state") == "waiting_for_permission":
                    rec.update(status="waiting_for_permission", approval_id=res.get("approval_id"), how=res.get("how"))
                    break
                if not passed and (res.get("state") == "cancelled" or self.cancelled()):
                    rec.update(status="cancelled", error=f"stopped by the owner during step {i} ({step['tool']})")
                    break                                  # the owner's stop, not a failure of the step
                if not passed:
                    rec.update(status="failed" if res.get("state") != "denied" else "denied",
                               error=f"step {i} ({step['tool']}): {unc or checked}"[:300])
                    break
            else:
                rec["status"] = "completed"
        finally:
            release(tid, self.lock_path)
        rec["finished"] = self.clock()
        rec["seconds"] = round(rec["finished"] - rec["started"], 1)
        rec["uncertain"] = [f"step {s['n']}: {s['uncertainty']}" for s in rec["steps"] if s.get("uncertainty")]
        self.on_progress("idle", "")
        self._save(rec)
        return rec


def _slim(r: dict) -> dict:
    """What the record keeps of a result: no local file paths with screen or page text, nothing long."""
    out = {}
    for k, v in (r or {}).items():
        if k in ("local_result", "page"):
            continue
        out[k] = v if not isinstance(v, str) else v[:300]
    return out


def summary(rec: dict) -> str:
    """One honest sentence for the owner."""
    st = rec.get("status")
    if st == "completed":
        done = "; ".join(s["checked"] for s in rec["steps"] if s.get("passed"))
        unc = (" (not certain: " + "; ".join(rec.get("uncertain") or []) + ")") if rec.get("uncertain") else ""
        # parts of the request the plan had to leave out (an unknown or unsupported step) were NOT done: say so
        dropped = [p for p in (rec.get("plan") or {}).get("problems") or [] if p]
        left = (" Not done: " + "; ".join(dropped)[:200] + ".") if dropped else ""
        return f"Done: {done}{unc}.{left}"
    if st == "needs_clarification":
        return rec.get("question") or "Could you say a bit more?"
    if st == "stopped":
        return "Stopped, as you asked."
    if st == "waiting_for_permission":
        # nothing resumes the task by itself: name what waits and what the owner does next
        last = (rec.get("steps") or [{}])[-1]
        args = last.get("args") or {}
        what = " ".join(str(x) for x in (last.get("tool"), args.get("action"), args.get("target") or args.get("app") or args.get("site")) if x)
        return (f"This needs your approval first{': ' + what if what else ''}. Approve it in the JARVIS console with your PIN, "
                f"then ask me to continue" + (f" (approval {rec['approval_id']})" if rec.get("approval_id") else "") + ".")
    if st == "busy":
        return rec.get("error", "Another PC task is running.")
    if st == "not_a_pc_task":
        return "That is not a PC task; passing it on."
    if st == "cancelled":
        # what already happened stays on the PC (a window opened, a file saved): the owner must hear it
        before = "; ".join(s["checked"] for s in rec.get("steps") or [] if s.get("passed") and s.get("checked"))
        return "Stopped." + (f" Before the stop: {before}." if before else "")
    return f"I could not finish: {rec.get('error') or st}."
