"""JARVIS governor (D50): keeps the whole of JARVIS inside the footprint budget (jarvis/supervisor/budget.py).

Two layers, both for processes started by the supervisor AND by jarvis-start.ps1:

1. Hard limits (Windows, set once per process, no polling needed)
   - every JARVIS process: BELOW_NORMAL priority class and EcoQoS (PROCESS_POWER_THROTTLING), so JARVIS yields to the owner's apps;
   - one Job Object ("adoption job") with a CPU rate HARD CAP = the CPU budget (CpuRate in 1/100 percent of the whole machine) and a job
     memory ceiling a little above the RAM budget as a backstop. It has NO kill-on-close (closing it must not kill the face): the caps
     stay for as long as any member lives. The supervisor's own tree gets the same limits in supervisor/jobobject.py.
   - a process that already sits in another job (the supervisor's, or one Windows or a launcher made) can only join a nested job on
     Windows 8+ and only when the hierarchy allows it. When Windows refuses (error 5, access denied) this is logged ONCE per process
     and that process is left to layer 2. Nothing is claimed as capped that was not.

2. Soft governor (a loop, samples the total every 5 s with footprint.Meter)
   Over budget (CPU of the last sample, RAM, VRAM) for longer than 20 s -> a ladder, one rung per grace period:
     rung 1  ask llama-swap to unload its models (POST /api/models/unload, else GET /unload), at most once a minute;
     rung 2  lower llama-server's threads for the NEXT load by one (minimum 2): written to the threads file and llama-swap is restarted
             through local-models.ps1 -Threads N, only when no model is loaded (so no request is cut);
     rung 3  duty-cycle the heavy llama-server: suspend it for a short slice every sample, resume it in a finally.
   Only llama-server is ever suspended or unloaded. The face, the kernel client (node-agent), the console, the supervisor and this governor
   are protected: if they are the offenders the governor logs it (at most every 5 minutes) and does nothing to them. Nothing is killed.
   Every action goes to the log (D:\\jarvis\\data\\logs\\governor.jsonl, one JSON per line).

    python jarvis/tools/governor.py              run the loop (one copy only; jarvis-start.ps1 starts it hidden)
    python jarvis/tools/governor.py --once       set priority / EcoQoS / job on every JARVIS process now, print what happened, exit
    python jarvis/tools/governor.py --dry-run    do not change anything, log what it would do
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from jarvis.supervisor import budget as B  # noqa: E402
from jarvis.supervisor import jobobject as J  # noqa: E402
from jarvis.tools import footprint as F  # noqa: E402

LLAMA_SWAP = "http://127.0.0.1:8080"
LOG_FILE = B.DATA_DIR + "\\logs\\governor.jsonl"
THREADS_FILE = B.DATA_DIR + "\\budget\\llama_threads.json"
LOCAL_MODELS_PS1 = str(ROOT / "jarvis" / "scripts" / "local-models.ps1")
MIN_THREADS = 2
HYSTERESIS = 0.9                 # below 90% of a limit counts as "back under"
UNLOAD_COOLDOWN_S = 60
PROTECTED_LOG_EVERY_S = 300


class JsonLog:
    def __init__(self, path: str | None = None, echo: bool = True) -> None:
        self.path, self.echo = path, echo

    def __call__(self, kind: str, **detail) -> dict:
        rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **detail}
        line = json.dumps(rec, ensure_ascii=False)
        if self.echo:
            print(line, flush=True)
        if self.path:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass
        return rec


# ---- layer 1: hard limits -----------------------------------------------------------------------------------------------------------

# Windows the owner clicks and watches (owner, 2026-10-26: "the Windows widget is very non responsive"). BELOW_NORMAL + EcoQoS + the CPU-capped job
# starved the face while a model ran, so these components are left alone: normal priority, no efficiency mode, outside the capped job.
INTERACTIVE = frozenset({"face"})


class Enforcer:
    """Priority, EcoQoS and the adoption job for every JARVIS pid it is shown, each pid once. Interactive windows (INTERACTIVE) are never throttled."""

    def __init__(self, bud: B.Budget, log: Callable = JsonLog(echo=False), dry_run: bool = False, mem_cap: bool = True,
                 throttle=J.throttle_pid, adopt=J.adopt_pid, in_job=J.in_job, make_job=J.create_job, windows: bool = J.IS_WINDOWS) -> None:
        self.bud, self.log, self.dry = bud, log, dry_run
        self.mem_cap = mem_cap
        self._throttle, self._adopt, self._in_job, self._make_job = throttle, adopt, in_job, make_job
        self.windows = windows
        self.job = None
        self.job_problems: list[str] = []
        self.done: dict[int, dict] = {}

    def _job_handle(self):
        if self.job is None and self.windows and not self.dry:
            limits = J.JobLimits(cpu_percent=self.bud.cpu_pct, job_memory_bytes=self.bud.job_mem_bytes if self.mem_cap else None,
                                 kill_on_close=False, throttle=True)
            self.job, self.job_problems = self._make_job(limits, "Local\\JarvisBudgetJob")
            self.log("job", cpu_rate_1_100_pct=J.cpu_rate_value(self.bud.cpu_pct),
                     memory_ceiling_mb=round(self.bud.job_mem_bytes / F.MB) if self.mem_cap else None, problems=self.job_problems)
        return self.job

    def apply(self, pids: dict[int, str]) -> dict:
        """Returns {"new": n, "throttled": n, "in_job": n, "job_refused": {pid: win32 error}}."""
        res = {"new": 0, "throttled": 0, "in_job": 0, "job_refused": {}}
        for pid, comp in pids.items():
            if pid in self.done:
                continue
            res["new"] += 1
            rec = {"comp": comp, "priority": False, "eco": False, "job": None}
            if comp in INTERACTIVE:
                rec["interactive"] = True
                if not self.dry and self.windows:
                    self.log("enforce", pid=pid, **rec)
                self.done[pid] = rec
                continue
            if self.dry:
                self.log("would-enforce", pid=pid, comp=comp)
            elif self.windows:
                t = self._throttle(pid)
                rec["priority"], rec["eco"] = t["priority"], t["eco"]
                job = self._job_handle()
                if job is not None:
                    if self._in_job(job, pid):
                        rec["job"] = True
                    else:
                        ok, err = self._adopt(job, pid)
                        rec["job"] = ok
                        if not ok:
                            rec["job_error"] = err
                            res["job_refused"][pid] = err
                self.log("enforce", pid=pid, **rec)
            res["throttled"] += int(rec["priority"] and rec["eco"])
            res["in_job"] += int(bool(rec["job"]))
            self.done[pid] = rec
        return res

    def forget(self, alive: set[int]) -> None:
        for pid in [p for p in self.done if p not in alive]:
            del self.done[pid]


# ---- layer 2: soft governor ---------------------------------------------------------------------------------------------------------

class RealActions:
    """What the soft governor can do to the world. Tests replace this with a fake."""

    def __init__(self, log: Callable, dry_run: bool = False, base: str = LLAMA_SWAP, threads_file: str = THREADS_FILE) -> None:
        self.log, self.dry, self.base, self.threads_file = log, dry_run, base, threads_file
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # loopback, never through a proxy

    def _call(self, method: str, path: str, timeout: float = 10.0) -> tuple[int, str]:
        req = urllib.request.Request(self.base + path, method=method, data=b"" if method == "POST" else None)
        try:
            with self._opener.open(req, timeout=timeout) as r:
                return r.status, r.read(65536).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except (urllib.error.URLError, OSError) as e:
            return 0, str(e)

    def running_models(self) -> list | None:
        """Models llama-swap has loaded, [] when none, None when llama-swap does not answer."""
        code, body = self._call("GET", "/running")
        if code != 200:
            return None
        try:
            data = json.loads(body)
        except ValueError:
            return None
        items = data.get("running", data) if isinstance(data, dict) else data
        return list(items) if isinstance(items, list) else []

    def unload_models(self) -> str:
        """llama-swap's unload endpoint. The path moved between releases (POST /api/models/unload in recent ones, GET /unload in older
        ones; neither was checked against v261 off the laptop), so both are tried and the one that answered is logged."""
        if self.dry:
            return "dry-run"
        for method, path in (("POST", "/api/models/unload"), ("GET", "/unload")):
            code, _ = self._call(method, path)
            if 200 <= code < 300:
                return f"{method} {path} -> {code}"
        return "no unload endpoint answered"

    def restart_llama_swap(self, threads: int) -> str:
        if self.dry:
            return "dry-run"
        write_threads(threads, self.threads_file)
        ps = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", LOCAL_MODELS_PS1]
        try:
            subprocess.run(ps + ["-Stop"], capture_output=True, timeout=60)
            r = subprocess.run(ps + ["-Threads", str(threads)], capture_output=True, text=True, timeout=120)
            return f"local-models.ps1 -Threads {threads} exit {r.returncode}"
        except (OSError, subprocess.SubprocessError) as e:
            return f"restart failed: {e}"

    def duty_cycle(self, pids: list[int], seconds: float) -> str:
        if self.dry:
            return "dry-run"
        stopped = []
        try:
            for pid in pids:
                if J.suspend_pid(pid):
                    stopped.append(pid)
            time.sleep(seconds)
        finally:
            for pid in stopped:
                J.resume_pid(pid)
        return f"suspended {len(stopped)} of {len(pids)} for {seconds:.1f} s"


def write_threads(n: int, path: str = THREADS_FILE) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"threads": int(n), "set": int(time.time())}, fh)


def read_threads(default: int, path: str = THREADS_FILE, max_age_s: float = 3600.0, now: float | None = None) -> int:
    """The thread count the governor last asked for, if it is less than an hour old; otherwise the default. (local-models.ps1 does the same.)"""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if (now if now is not None else time.time()) - float(d["set"]) <= max_age_s and MIN_THREADS <= int(d["threads"]) <= default:
            return int(d["threads"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return default


def measure(rep: dict) -> dict:
    """The numbers the ladder needs, from a footprint report."""
    comps = {c["name"]: c for c in rep["components"]}
    llama = comps.get("llama-server")
    return {"cpu": rep["total"]["cpu_pct"], "ram": rep["total"]["ws_mb"], "vram": rep["total"]["vram_mb"],
            "llama_pids": list(llama["pids"]) if llama else [],
            "top": [f"{c['name']}{'*' if c['protected'] else ''} cpu {c['cpu_pct']} ws {c['ws_mb']}" for c in rep["components"][:3]]}


class Governor:
    def __init__(self, bud: B.Budget, actions, log: Callable = JsonLog(echo=False), now: Callable[[], float] = time.monotonic,
                 tick_s: float = 5.0) -> None:
        self.bud, self.act, self.log, self.now, self.tick_s = bud, actions, log, now, tick_s
        self.over_since: float | None = None
        self.last_unload = -1e9
        self.last_protected_log = -1e9
        self.unloaded_at: float | None = None
        self.threads = read_threads(bud.threads)
        self.rung = 0
        self.rung2_done = False

    def reasons(self, m: dict) -> tuple[list[str], bool]:
        """(metrics over, all clear). 'clear' needs every metric below 90% of its limit."""
        over, clear = [], True
        for name, val, lim in (("cpu", m["cpu"], self.bud.cpu_pct), ("ram", m["ram"], self.bud.ram_mb), ("vram", m["vram"], self.bud.vram_mb)):
            if val is None:
                continue
            if val > lim:
                over.append(f"{name} {val} > {lim}")
            if val >= lim * HYSTERESIS:
                clear = False
        return over, clear

    def step(self, m: dict) -> list[dict]:
        t = self.now()
        over, clear = self.reasons(m)
        if clear:
            if self.over_since is not None:
                self.log("back-under-budget", after_s=round(t - self.over_since, 1))
            self.over_since, self.rung, self.unloaded_at, self.rung2_done = None, 0, None, False
            return []
        if not over:
            return []                         # between 90% and 100%: hold the state
        if self.over_since is None:
            self.over_since = t
        if t - self.over_since <= self.bud.over_grace_s:
            return []
        out = []
        if not m["llama_pids"] and not self._models_loaded():
            if t - self.last_protected_log >= PROTECTED_LOG_EVERY_S:
                self.last_protected_log = t
                out.append(self.log("over-budget-no-action", over=over, top=m["top"],
                                    why="nothing the governor may touch is running; the offenders are protected or not llama"))
            return out
        # rung 1: unload the models
        if t - self.last_unload >= UNLOAD_COOLDOWN_S and (self.rung < 2 or self.unloaded_at is None):
            r = self.act.unload_models()
            self.last_unload, self.unloaded_at, self.rung = t, t, max(self.rung, 1)
            return out + [self.log("unload-models", over=over, result=r)]
        # rung 2: fewer threads for the next load (only with nothing loaded, so no request is cut)
        if self.unloaded_at is not None and t - self.unloaded_at > self.bud.over_grace_s and not self.rung2_done:
            running = self.act.running_models()
            if running == [] and self.threads > MIN_THREADS:
                self.threads -= 1
                self.rung2_done = True
                self.rung = 2
                return out + [self.log("lower-threads", threads=self.threads, result=self.act.restart_llama_swap(self.threads), over=over)]
            if running:
                self.log("lower-threads-skipped", why="a model is loaded", running=running)
            else:
                self.rung2_done = True
        # rung 3: duty-cycle llama-server
        if self.unloaded_at is not None and t - self.unloaded_at > 2 * self.bud.over_grace_s and m["llama_pids"]:
            self.rung = 3
            secs = max(0.5, min(3.0, self.tick_s * 0.5))
            return out + [self.log("duty-cycle", pids=m["llama_pids"], result=self.act.duty_cycle(m["llama_pids"], secs), over=over)]
        return out

    def _models_loaded(self) -> bool:
        r = self.act.running_models()
        return bool(r)


def enforce_once(bud: B.Budget | None = None, dry_run: bool = False, log: Callable | None = None, sampler: F.Sampler | None = None,
                 enforcer: Enforcer | None = None) -> dict:
    """Layer 1 now, for every JARVIS process that exists: callable from the supervisor or a launcher. Returns Enforcer.apply's result
    plus the component of each pid."""
    bud = bud or B.load_budget()
    log = log or JsonLog(LOG_FILE if J.IS_WINDOWS else None)
    s = sampler or F.Sampler()
    snap = F.take(s)
    enf = enforcer or Enforcer(bud, log, dry_run)
    res = enf.apply(snap.comp)
    res["components"] = dict(snap.comp)
    return res


def _single_instance() -> bool:
    if not J.IS_WINDOWS:
        return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\JarvisGovernor")
    return ctypes.windll.kernel32.GetLastError() != 183


def run(args: argparse.Namespace) -> int:
    bud = B.load_budget()
    log = JsonLog(args.log or (LOG_FILE if J.IS_WINDOWS else None))
    if args.once:
        print(json.dumps(enforce_once(bud, args.dry_run, log), indent=1))
        return 0
    if not _single_instance():
        print("governor already running")
        return 0
    meter = F.Meter(bud=bud)
    enf = Enforcer(bud, log, args.dry_run, mem_cap=not args.no_mem_cap)
    gov = Governor(bud, RealActions(log, args.dry_run), log, tick_s=args.tick)
    log("start", budget={k: getattr(bud, k) for k in ("cpu_pct", "ram_mb", "vram_mb", "threads", "over_grace_s")}, dry_run=args.dry_run,
        notes=list(bud.notes))
    try:
        while True:
            rep = meter.sample(args.tick)
            snap = meter.prev
            res = enf.apply(snap.comp)
            enf.forget(set(snap.comp))
            if res["job_refused"]:
                for pid, err in res["job_refused"].items():
                    log("job-refused", pid=pid, comp=snap.comp.get(pid), win32_error=err,
                        note="already in another job; left to the soft governor" if err == 5 else "")
            gov.step(measure(rep))
    except KeyboardInterrupt:
        log("stop")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true", help="enforce priority/EcoQoS/job once and exit")
    ap.add_argument("--dry-run", action="store_true", help="change nothing, log what would be done")
    ap.add_argument("--tick", type=float, default=5.0, help="seconds between samples (default 5)")
    ap.add_argument("--log", default=None, help="log file (default " + LOG_FILE + ")")
    ap.add_argument("--no-mem-cap", action="store_true", help="do not put the memory ceiling on the adoption job")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
