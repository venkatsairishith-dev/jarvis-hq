"""The reliability gate's runner (D59): every goal of every capability, N times, success judged by the verified effect.

    python -m jarvis.evals.reliability.runner --list                      what will be measured
    python -m jarvis.evals.reliability.runner --validate                  is goals.json sound
    python -m jarvis.evals.reliability.runner --selftest                  do the harness's own eyes and hands work on this PC (run FIRST)
    python -m jarvis.evals.reliability.runner --driver face               the full gate, N=10 per goal  (hours: see --list for the estimate)
    python -m jarvis.evals.reliability.runner --quick --capability screen_text,final_answer     a 3-repeat smoke of two capabilities
    python -m jarvis.evals.reliability.report                             the owner's page: each capability against 90%

Per run: a private folder, the scene set up (`setup`), a baseline of the PC, the goal typed to JARVIS (the driver), then the checks read the PC
itself; the outcome is decided by classify.py; the scene is cleaned up (only windows that opened during the run and only programs on the
`cleanup.closable` list, only files in this run's folder, only Recycle Bin items carrying this run's token). Results are written after EVERY run
to results/reliability-<date>.json, so a stopped session loses nothing and `--resume` (the default) continues where it stopped.

Safety brakes: nothing is ever approved by the runner (the PIN is the owner's); a CRITICAL unsafe outcome (severity 1) stops the whole run at once
(`--keep-going` overrides); a capability's goals can never touch a path outside the run folder because every path comes from {work}.

A goal's own driver call is also watched from OUTSIDE (`watchdog.py`, D81): if it hangs past `g.timeout_s` plus a grace margin (`--watchdog-grace`,
default 60 s) even though the driver's own internal timeout should have fired, the watchdog reads the PC (window list, a screen read, log tails),
writes one stuck-report under `results/stuck/`, recovers narrowly (closes only a new, matching window; cancels and, if tracked, ends a stuck child
process), and the run moves on to the next goal with that one marked invalid - a stuck goal never stops the whole gate.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from jarvis.plugins._shared import ask_form

from . import checks, classify, drivers, model, probes, watchdog
from .env import EnvError, LocalEnv, WindowsEnv
from .records import Baseline, CheckResult

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
SCHEMA = 1
# The kernel's file operations (create a folder, copy, rename, trash) only work inside Documents, Desktop, Downloads, Pictures, Videos, Music, outputs or a
# project folder. The first default, D:\jarvis\data\reliability\work, is outside all of them, so every file goal answered "the PC executor only makes folders
# inside ..." and the gate counted that as JARVIS failing (laptop 2026-10-27: sm-folder, sm-copy, sm-rename, sm-empty-file, the Save As goals). The run folder
# now sits in Documents (removed after each run; the Recycle Bin is purged by the run's token only).
# The kernel's create operations make a file or folder only DIRECTLY inside Documents (a run folder inside Documents was refused too: fa-file-created,
# pr-popup-mid-task, sm-empty-file, 2026-10-28), so on Windows {work} IS Documents: every name a goal makes carries the run's token and the cleanup removes
# exactly the entries whose name holds that token (never a folder as a whole).
DEFAULT_WORK = os.path.join(os.path.expanduser("~"), "Documents")
HARNESS_STREAK = 5            # this many harness failures in a row stop the run: the face or the tool server is down, not JARVIS wrong


def _sensitive_window(w) -> bool:
    """True for a password manager / wallet / bank / broker window (the node's own floor, D18-era): cleanup never auto-closes one, even if a goal
    somehow opened it, on the same rule the rest of JARVIS already refuses to read or act on such a window by. Missing on a non-Windows import
    path (this module runs on Linux too, e.g. in CI): then nothing is treated as sensitive, same as before this fix - it is a safety ADDITION, never
    a reason cleanup does less than it already did."""
    try:
        from jarvis.node.jarvis_node import sensitive
    except Exception:  # noqa: BLE001
        return False
    return bool(sensitive.why(w.process, w.title))


class PinSwitch:
    """The gateway's pin file (PROTOCOL of the on-screen bench): one line `local` / `groq` / ... Empty or missing = the normal chain. Restored at the end."""

    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self.current: str | None = None
        self._original: str | None = None
        if self.path and self.path.exists():
            self._original = self.path.read_text(encoding="utf-8")

    def set(self, value: str) -> None:
        if value == self.current:
            return
        if not self.path:
            if not value:
                return
            raise RuntimeError("this capability needs the gateway pinned to a model, but no pin file is set: start the gateway with JARVIS_GATEWAY_PIN_FILE=<file> "
                               "and pass --pin-file <file> (or set the same variable here)")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(value + ("\n" if value else ""), encoding="utf-8")
        self.current = value

    def restore(self) -> None:
        if self.path:
            if self._original is None:
                try:
                    self.path.unlink()
                except OSError:
                    pass
            else:
                self.path.write_text(self._original, encoding="utf-8")


def atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10, cwd=str(HERE)).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


class Runner:
    def __init__(self, cat: model.Catalog, env: LocalEnv, driver: Any, sampler: Any = None, *, work_root: str, outputs: str, results_path: Path,
                 repeats: int = 10, attended: bool = False, pin: PinSwitch | None = None, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print, within_s: float = 8.0,
                 forbid_within_s: float = 1.0, setup_settle_s: float = 2.0, stop_on_critical: bool = True, meta: dict | None = None,
                 watchdog_grace_s: float = watchdog.DEFAULT_GRACE_S):
        self.cat, self.env, self.driver, self.sampler = cat, env, driver, sampler
        self.work_root, self.outputs, self.path = work_root, outputs, Path(results_path)
        self.repeats, self.attended, self.pin = repeats, attended, pin
        self.wall, self.sleep, self.clock, self.log = wall, sleep, clock, log
        self.within_s, self.forbid_within_s, self.setup_settle_s, self.stop_on_critical = within_s, forbid_within_s, setup_settle_s, stop_on_critical
        self.ignore = tuple(cat.rules.get("ignore_processes", []))
        self.closable = list(cat.rules.get("cleanup", {}).get("closable", []))
        self.watchdog_grace_s = watchdog_grace_s          # D81: how far past g.timeout_s the external deadline waits before it steps in
        self.meta = meta or {}
        self.data: dict = {"schema": SCHEMA, "runs": []}
        self.stopped_for_safety = ""

    # ---- results file
    def load(self, fresh: bool = False) -> None:
        if self.path.exists() and not fresh:
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                self.path.rename(self.path.with_suffix(f".bad-{int(self.wall())}.json"))
        self.data.setdefault("runs", [])
        self.data.update({"schema": SCHEMA, "date": self.meta.get("date", time.strftime("%Y-%m-%d")), "driver": self.driver.name,
                          "repeats_requested": self.repeats, "host": platform.node(), "commit": git_commit(), "attended": self.attended,
                          "rules": self.cat.rules.get("gate", {})})
        self.data.setdefault("started", time.strftime("%Y-%m-%d %H:%M:%S"))

    def save(self) -> None:
        self.data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        atomic_write(self.path, self.data)

    def done(self) -> set[tuple[str, int]]:
        return {(r["goal"], r["rep"]) for r in self.data["runs"]}

    # ---- setup and cleanup
    def _setup(self, g: model.Goal, ctx: model.Context, base: Baseline) -> None:
        env, sep = self.env, ctx.sep
        env.mkdir(ctx.work)
        for op in g.setup:
            kind = op["op"]
            if kind == "mkdir":
                env.mkdir(op["path"])
            elif kind == "write_file":
                env.write_file(op["path"], op.get("text", "") * int(op.get("repeat", 1)), float(op.get("age_days", 0)))
                base.files[op["path"]] = env.sha(op["path"])
            elif kind == "write_pdf":
                env.write_pdf(op["path"], list(op.get("lines", [])), float(op.get("age_days", 0)))
                base.files[op["path"]] = env.sha(op["path"])
            elif kind == "make_junk":
                for i in range(int(op.get("count", 3))):
                    p = f"{op['dir']}{sep}junk-{ctx.tok}-{i}{op.get('ext', '.tmp')}"
                    env.write_file(p, "junk " * 20)
                    base.files[p] = env.sha(p)
            elif kind == "write_script":
                env.write_file(op["path"], f"Set-Content -LiteralPath '{op['marker']}' -Value 'ran'\n")
                base.files[op["path"]] = env.sha(op["path"])
            elif kind == "open_notepad_text":
                env.open_notepad_text(op["path"], op.get("text", ""))
            elif kind == "open_image_text":
                env.open_image_text(op["path"], op.get("text", ""))
            elif kind == "render_image":
                env.render_image(op["path"], op.get("text", ""))
            elif kind == "open_app":
                env.open_app(op["name"])
            elif kind == "message_box":
                env.message_box(op.get("title", "Message"), op.get("text", ""))
            elif kind == "recycle_file":
                env.write_file(op["path"], op.get("text", "recycled"))
                env.recycle_file(op["path"])
            elif kind == "minimize":
                self.sleep(1.0)                                          # the window has to exist before it can be minimised
                env.minimize_window(op["title_contains"])
            elif kind == "start_later":
                env.start_later(op["program"], list(op.get("args", [])), float(op.get("delay_s", 3)))
            elif kind == "message_box_later":
                env.message_box_later(op.get("title", "Message"), op.get("text", ""), float(op.get("delay_s", 3)))
            elif kind == "slow_form":
                env.slow_form(op["title"], op["marker"], float(op.get("hang_s", 8)))
            elif kind == "close_later":
                env.close_later(op["title_contains"], float(op.get("delay_s", 6)))
            elif kind == "type_unsaved":
                env.type_unsaved(op.get("text", ""))
        if any(op["op"] in ("open_notepad_text", "open_image_text", "open_app", "message_box", "minimize", "slow_form", "type_unsaved") for op in g.setup):
            self.sleep(self.setup_settle_s)

    def _snapshot(self, base: Baseline) -> None:
        wins = self.env.windows()
        base.window_list = wins
        base.windows = {(w.hwnd, w.title) for w in wins}
        base.bin = set(self.env.recycle_names())

    def _cleanup(self, ctx: model.Context, pre_hwnds: set[int], extras: list[str] | tuple = ()) -> int:
        """Close every window THIS RUN opened (owner, 2026-10-28: 'it is opening all apps and windows for tests but not
        closing them after'). Before this, only a window matching the hand-maintained `cleanup.closable` list in
        goals.json (just Notepad and Calculator) was closed, so VS Code, Chrome, Paint, Explorer, a judge page - anything
        D64/D76/D77 opens that wasn't on that short list - was left open after every run. Now: new since this goal
        started (not in `pre_hwnds`), not an ignored background process, and not a password manager/wallet/bank window
        (never auto-closed, even one this run somehow opened) all get closed; `closable` still widens the match (kept
        for any spec that is NOT a plain process/title window, e.g. a future non-window target) but is no longer the
        only way in."""
        closed = 0
        try:
            for w in self.env.windows():
                if w.hwnd in pre_hwnds or w.process.lower() in {p.lower() for p in self.ignore}:
                    continue
                if _sensitive_window(w):
                    continue
                self.env.close_window(w)
                closed += 1
            self.sleep(0.5)
            if os.path.normcase(ctx.work.rstrip(ctx.sep)) == os.path.normcase(ctx.docs.rstrip(ctx.sep)):
                for entry in self.env.list_dir(ctx.docs, "*" + ctx.tok + "*"):         # only what carries this run's token
                    self.env.remove_tree(entry)
            else:
                self.env.remove_tree(ctx.work)
            for extra in extras:
                # only what this run made: inside Documents and named with the run's token (the kernel makes folders only DIRECTLY in Documents, Desktop ...)
                if ctx.tok in extra and extra.lower().startswith(ctx.docs.lower() + ctx.sep):
                    self.env.remove_tree(extra)
            self.env.recycle_purge(ctx.tok)
        except EnvError as e:
            self.log(f"  cleanup problem: {e}")
        return closed

    # ---- one run
    def run_one(self, g0: model.Goal, rep: int) -> dict:
        cap = self.cat.capabilities[g0.cap]
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        row: dict = {"cap": g0.cap, "goal": g0.id, "rep": rep, "kind": g0.kind, "lang": g0.lang, "started": started, "driver": self.driver.name, "pin": cap.pin}
        if g0.kind == "probe":
            return self._run_probe(g0, rep, row)
        ctx = model.make_context(self.work_root, self.env.sep, self.outputs, f"{g0.id}:{rep}:{self.wall()}", self.wall())
        if self.env.sep == "\\" and os.path.normcase(self.work_root.rstrip("\\")) == os.path.normcase(ctx.docs.rstrip("\\")):
            ctx = dataclasses.replace(ctx, work=ctx.docs)           # flat: see DEFAULT_WORK
        g = model.render(g0, ctx)
        base = Baseline()
        pre = self.env.windows()
        pre_hwnds = {w.hwnd for w in pre}
        try:
            if self.pin is not None:
                self.pin.set(cap.pin)
            elif cap.pin:
                raise RuntimeError("this capability needs a gateway pin (--pin-file)")
            self._setup(g, ctx, base)
            self._snapshot(base)
            base.t0 = self.wall()
            # D81: the same call as before, but under an EXTERNAL deadline (g.timeout_s + a grace margin) that fires even if the driver's own
            # internal timeout is ignored or outran somewhere further down the chain; on a hang this diagnoses, reports, recovers (closes the
            # offending window / cancels / terminates a tracked process) and returns a normal harness_error Observation instead of blocking forever.
            obs = watchdog.run_with_recovery(self.driver, self.env, g.text, g.timeout_s, self.attended, pre_hwnds=pre_hwnds, closable=self.closable,
                                             ignore=self.ignore, grace_s=self.watchdog_grace_s, results_dir=self.path.parent, goal_id=g0.id, rep=rep)
        except (EnvError, RuntimeError, OSError) as e:
            row.update(outcome=classify.INVALID, cause=f"setup: {type(e).__name__}: {str(e)[:100]}", seconds=0.0, harness=True)
            self._cleanup(ctx, pre_hwnds, g.cleanup)
            return row
        ectx = checks.EvalCtx(self.env, obs, base, self.cat.panels, self.outputs, self.ignore, self.clock, self.sleep)
        verify_specs = list(g.verify)
        asked = ask_form.is_form(obs.ask_form) or obs.approval_pending
        if g.kind in ("act", "act_or_ask") and g.require_answer and not asked and not any(v["check"].startswith("answer") for v in verify_specs):
            verify_specs.append({"check": "answer_nonempty", "min_chars": 2})
        try:
            if obs.status == "harness_error":
                verify, hits = [], []
            else:
                verify = checks.run_all(verify_specs, ectx, self.within_s) if g.kind in ("act", "act_or_ask", "ask") else []     # D74: an 'ask' goal's verify (a form with its candidates) is reported too
                hits = []
                for spec in g.forbid:
                    r = checks.run_check(spec, ectx, self.forbid_within_s)
                    if r.ok:
                        hits.append((CheckResult(spec.get("label") or r.name, True, spec.get("label") or r.detail, r.cause), int(spec["severity"])))
            out = classify.classify(g.kind, obs, verify, hits)
        finally:
            watchdog.bounded(self.driver.cancel_all)         # not a bare call: after a hang the same server can hang this one too (D81 follow-up, laptop 2026-10-28)
            self._cleanup(ctx, pre_hwnds, g.cleanup)
        if obs.status == "harness_error":
            row["harness"] = True
        row.update(outcome=out.kind, cause=out.cause, severity=out.severity, false_claim=out.false_claim, asked=out.asked or asked,
                   seconds=round(obs.seconds, 2), requests=obs.requests, answer=(obs.final_text or "")[:300], tool_calls=obs.tool_calls[:8],
                   errors=obs.errors[:3], approved=obs.approved, checks=[r.as_dict() for r in verify], forbid_hits=[r.as_dict() | {"severity": s} for r, s in hits],
                   note=out.note[:160])
        return row

    def _run_probe(self, g: model.Goal, rep: int, row: dict) -> dict:
        if self.sampler is None:
            row.update(outcome=classify.INVALID, cause="no sampler (probes need --driver face on the laptop)", seconds=0.0)
            return row
        t0 = self.clock()
        r = probes.run_probe(g, self.sampler, self.driver, sleep=self.sleep)
        row.update(seconds=round(self.clock() - t0, 2), value=r.value, unit=r.unit, limit=r.limit, samples=[round(x, 1) for x in r.samples[:60]], note=r.detail[:200])
        if r.invalid:
            row.update(outcome=classify.INVALID, cause=r.invalid[:100])
        else:
            row.update(outcome=classify.PASS if r.ok else classify.FAIL, cause="" if r.ok else "over_limit")
        return row

    # ---- the whole run
    def run(self, cap_ids: list[str] | None = None, goal_ids: list[str] | None = None, repeats: int | None = None, fresh: bool = False,
            max_minutes: float | None = None, driver_kind: str = "") -> dict:
        self.load(fresh)
        n = repeats or self.repeats
        caps = [c for c in self.cat.capabilities.values() if not cap_ids or c.id in cap_ids]
        caps.sort(key=lambda c: c.priority == "low")                      # D76: the old toy goals run after the practical ones (a stable sort: the order inside each group stays)
        todo: list[tuple[model.Goal, int]] = []
        skipped: list[str] = []
        for rep in range(1, n + 1):                              # round robin: a stopped run has every goal measured a few times, not a few goals many times
            for c in caps:
                if c.needs_driver and driver_kind and c.needs_driver != driver_kind:
                    if c.id not in skipped:
                        skipped.append(c.id)
                    continue
                for g in c.goals:
                    if goal_ids and g.id not in goal_ids:
                        continue
                    if (g.id, rep) not in self.done():
                        todo.append((g, rep))
        if skipped:
            self.log(f"skipped (they need the face driver to be meaningful): {', '.join(skipped)}")
            self.data["skipped_capabilities"] = skipped
        t_end = self.clock() + max_minutes * 60 if max_minutes else None
        harness_streak = 0
        try:
            for i, (g, rep) in enumerate(todo, 1):
                if t_end and self.clock() > t_end:
                    self.log("time box used up; stopping (the next run continues from here)")
                    break
                row = self.run_one(g, rep)
                self.data["runs"].append(row)
                self.save()
                extra = f" [{row.get('cause')}]" if row.get("cause") else ""
                self.log(f"[{i}/{len(todo)}] {g.cap}/{g.id} #{rep} {row['outcome']}{extra} {row.get('seconds', 0)}s")
                harness_streak = harness_streak + 1 if row.get("harness") else 0
                if harness_streak >= HARNESS_STREAK:
                    self.data["stopped_for_harness"] = row.get("cause", "")
                    self.save()
                    self.log(f"STOPPED: {HARNESS_STREAK} runs in a row could not be run by the harness ({row.get('cause')}). Fix that, then run again: finished runs are kept.")
                    break
                if row["outcome"] == classify.UNSAFE:
                    self.log(f"  UNSAFE (severity {row.get('severity')}): {row.get('note')}")
                    if self.stop_on_critical and row.get("severity") == 1:
                        self.stopped_for_safety = f"{g.id} #{rep}: {row.get('note')}"
                        self.data["stopped_for_safety"] = self.stopped_for_safety
                        self.save()
                        self.log("STOPPED: a critical unsafe outcome. Look at the PC before running anything else.")
                        break
        finally:
            if self.pin is not None:
                self.pin.restore()
            self.data["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.save()
        return self.data


# ------------------------------------------------------------------------------------------------------------------ command line
def estimate_hours(cat: model.Catalog, repeats: int) -> float:
    total = 0.0
    for c in cat.capabilities.values():
        per = float(cat.latency.get(c.latency_class, {}).get("typical_s", 30))
        total += per * len(c.goals) * repeats
    return round(total / 3600, 1)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="exercise the harness's own primitives on this PC (run before a gate run)")
    ap.add_argument("--driver", choices=("face", "mcp"), default="face")
    ap.add_argument("--capability", default="", help="comma separated capability ids (default: all)")
    ap.add_argument("--goal", default="", help="comma separated goal ids")
    ap.add_argument("--repeats", type=int, default=10, help="repeats per goal (default 10)")
    ap.add_argument("--quick", action="store_true", help="3 repeats: a smoke run, never a gate result")
    ap.add_argument("--attended", action="store_true", help="the owner is at the PC and will approve PIN cards; verified effects of class-B goals can then be measured")
    ap.add_argument("--work-root", default=os.environ.get("JARVIS_RELIABILITY_WORK", DEFAULT_WORK if os.name == "nt" else "/tmp/jarvis-reliability"))
    ap.add_argument("--outputs", default=os.environ.get("DELIVERABLES_OUT_DIR", r"D:\jarvis\outputs\deliverables"))
    ap.add_argument("--out-dir", default=str(RESULTS))
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--fresh", action="store_true", help="ignore an existing results file for this date")
    ap.add_argument("--max-minutes", type=float, default=0, help="stop starting new runs after this many minutes")
    ap.add_argument("--keep-going", action="store_true", help="do not stop at a critical unsafe outcome")
    ap.add_argument("--watchdog-grace", type=float, default=float(os.environ.get("JARVIS_RELIABILITY_WATCHDOG_GRACE_S", watchdog.DEFAULT_GRACE_S)),
                   help="D81: seconds past a goal's own timeout before the external watchdog steps in and recovers (default 60)")
    ap.add_argument("--reset", choices=("inplace", "face", "none"), default="inplace", help="face driver: clear the chat in place (default), or restart the face before each run (slow, cleanest)")
    ap.add_argument("--pin-file", default=os.environ.get("JARVIS_GATEWAY_PIN_FILE", ""), help="the gateway's pin file (capabilities with a pin need it)")
    ap.add_argument("--mcp-url", default=drivers.MCP_URL)
    ap.add_argument("--catalogue-file", default="", help="a simple_tasks catalogue file to add to the simple_tasks capability (see catalogue.py)")
    ap.add_argument("--goals-file", default="")
    return ap


def make_driver(kind: str, args: argparse.Namespace) -> Any:
    client = drivers.McpClient(args.mcp_url)
    if kind == "mcp":
        return drivers.McpDriver(client)
    reset = None
    if args.reset == "face":
        sh = HERE.parent / "onscreen_bench" / "driver" / "face_reset.sh"
        reset = ["bash", str(sh)]
    return drivers.FaceDriver(client=client, reset_cmd=reset)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cat = model.load(args.goals_file or None)
    if args.catalogue_file:
        from . import catalogue
        catalogue.merge(cat, args.catalogue_file)
    problems = model.validate(cat)
    if args.validate or problems:
        for p in problems:
            print("PROBLEM:", p)
        if args.validate:
            print(f"{sum(len(c.goals) for c in cat.capabilities.values())} goals in {len(cat.capabilities)} capabilities;", "sound" if not problems else f"{len(problems)} problem(s)")
            return 1 if problems else 0
        return 2
    repeats = 3 if args.quick else args.repeats
    if args.list:
        print(f"{'capability':24} {'goals':>5}  {'est.':>5}  needs   name")
        for c in cat.capabilities.values():
            est = "-" if c.estimate is None else f"{round(c.estimate * 100)}%"
            print(f"{c.id:24} {len(c.goals):>5}  {est:>5}  {c.needs_driver or 'any':6}  {c.name}")
        total = sum(len(c.goals) for c in cat.capabilities.values())
        print(f"\n{total} goals x {repeats} repeats = {total * repeats} runs, roughly {estimate_hours(cat, repeats)} hours of typing and waiting")
        return 0
    env: LocalEnv = WindowsEnv() if os.name == "nt" else LocalEnv()
    if args.selftest:
        from . import selftest
        return selftest.run(env, make_driver(args.driver, args), args)
    driver = make_driver(args.driver, args)
    sampler = probes.NodeSampler(cfg=cat.probes, env=env) if args.driver == "face" else None
    results = Path(args.out_dir) / f"reliability-{args.date}.json"
    pin = PinSwitch(args.pin_file or None)
    runner = Runner(cat, env, driver, sampler, work_root=args.work_root, outputs=args.outputs, results_path=results, repeats=repeats, attended=args.attended,
                    pin=pin, stop_on_critical=not args.keep_going, meta={"date": args.date, "quick": args.quick}, watchdog_grace_s=args.watchdog_grace)
    caps = [c for c in args.capability.split(",") if c]
    for c in caps:
        if c not in cat.capabilities:
            print(f"unknown capability {c!r}; --list shows them")
            return 2
    data = runner.run(caps or None, [g for g in args.goal.split(",") if g] or None, repeats, args.fresh, args.max_minutes or None, args.driver)
    from . import gate
    result = gate.evaluate(cat, data)
    print()
    print(gate.render_text(result))
    md = results.with_suffix(".md")
    md.write_text(gate.render_markdown(result, args.date), encoding="utf-8")
    print(f"\nresults {results}\npage    {md}" + ("\n(--quick: a smoke run is never a gate result)" if args.quick else ""))
    return 3 if runner.stopped_for_safety else 0


if __name__ == "__main__":
    sys.exit(main())
