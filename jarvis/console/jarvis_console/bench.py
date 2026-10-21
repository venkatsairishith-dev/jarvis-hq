"""The console's Bench screen: benchmark results, benchmark jobs, the challenge lab, the local model comparison and the
security cases. Read-mostly; the two things that run are fixed programs from the repo with argument lists built here
from allow-lists (never from the request's text, never through a shell):

    * jarvis/evals/neko95/bench/bench.py        (one area at a time, or every area = a full run)
    * jarvis/evals/neko95/bench/model_compare.py (only candidates of model_candidates.json whose file is already here)

One job at a time. A job runs in its own process group so Cancel stops it and everything it started.

Honesty rules for what is shown:
    * every dimension score comes with its sample size, its label (verified / provisional / unverified) and the ids of the
      cases behind it; the frozen v1 score is reported separately and labelled "official";
    * each result says what kind of evidence it is: "live" (results/live-*.json: real services), "integration" (a bench
      case: the real code against fixtures) or "mocked" (a bench case whose scenario says one boundary, usually the
      local model, is a recorded or hand-written stand-in). A case file may state it with an optional "evidence" field;
    * a challenge is saved as untrusted and never becomes a benchmark expectation by itself.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import loopback_net  # noqa: E402  loopback only, no proxy, no redirect (completion H)
from collections import deque
from pathlib import Path

HQ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REPO = os.path.dirname(HQ)

# group id -> (label, bench areas). The only way a request chooses what runs.
GROUPS = {
    "intent_planning": ("Intent and planning", ["A"]),
    "desktop_logic": ("Desktop logic", ["B"]),
    "browser": ("Browser simulations", ["C"]),
    "long_horizon": ("Long-horizon missions", ["D"]),
    "recovery": ("Recovery", ["E"]),
    "memory": ("Memory", ["F"]),
    "voice_offline": ("Voice (offline parts)", ["G"]),
    "research": ("Research", ["H"]),
    "documents": ("Documents", ["I"]),
    "routing": ("Routing", ["J"]),
    "security": ("Security", ["K"]),
    "ux": ("User experience", ["L"]),
    "skills": ("Skills", ["M"]),
    "reliability": ("Concurrency and reliability", ["N"]),
    "context": ("Context selection", ["X"]),
}
MODEL_SETS = ("plan_cases", "dev", "holdout", "holdout2")
STAMP = re.compile(r"(\d{8}-\d{6})")
RUN_NAME = re.compile(r"^bench-\d{8}-\d{6}(?:-[A-Za-z0-9_]{1,20})?\.json$")
FULL_RUN = re.compile(r"^bench-\d{8}-\d{6}\.json$")
CASE_ID = re.compile(r"^[A-Z]{1,2}-\d{3}[a-z]?$")
CHALLENGE_ID = re.compile(r"^ch-\d{8}-\d{6}-[0-9a-f]{6}$")
JOB_ID = re.compile(r"^job-[0-9a-f]{10}$")
# A scenario that says a boundary is replaced (the model's answer is a recorded or hand-written fixture, a fake server,
# a mock): the case still runs the real code, but that part is not the real thing.
MOCKED = re.compile(r"(?i)\b(mock(?:ed|s)?|fake[sd]?|faked|hand-written|recorded|recorder|stand-?in|stands in|scripted|stub(?:bed)?)\b")
EVIDENCE = ("live", "integration", "mocked")
TREND_N = 10
LOG_LINES = 200
MAX_CHALLENGE = 1000
MAX_SAVED_CHALLENGES = 500
SECURITY_AREAS = ("K", "N")
SECURITY_DIM_AREAS = ("X", "J")
# Made-up Chrome profiles for the challenge lab (the owner's real ones live on the laptop and are not read here).
SANDBOX_PROFILES = [{"profile": "Default", "name": "Owner", "account": "own***", "default": True, "say": ["default", "owner"]},
                    {"profile": "Profile 1", "name": "Work", "account": "wor***", "default": False, "say": ["work"]},
                    {"profile": "Profile 2", "name": "Family", "account": "fam***", "default": False, "say": ["family"]}]


class BenchError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _stamp(name: str) -> str:
    m = STAMP.search(name)
    return m.group(1) if m else ""


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _short(s, n=300) -> str:
    s = str(s if s is not None else "")
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------------------------------------------------------------- jobs
class Job:
    def __init__(self, kind: str, title: str, steps: list[tuple[str, list[str]]], total: int, env: dict | None = None):
        self.id = "job-" + secrets.token_hex(5)
        self.kind, self.title, self.steps, self.total, self.env = kind, title, steps, total, env or {}
        self.state, self.started, self.finished = "running", time.time(), None
        self.done, self.current, self.step_index = 0, "", 0
        self.counts = {"pass": 0, "fail": 0, "error": 0, "deferred": 0}
        self.log: deque[str] = deque(maxlen=LOG_LINES)
        self.saved: list[str] = []
        self.returncodes: list[int] = []
        self.error = None
        self.cancel_requested = False
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def view(self) -> dict:
        with self.lock:
            return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state, "started": self.started,
                    "finished": self.finished, "done": self.done, "total": self.total, "current": self.current,
                    "step": self.step_index, "steps": len(self.steps), "counts": dict(self.counts), "saved": list(self.saved),
                    "returncodes": list(self.returncodes), "error": self.error, "log_tail": list(self.log)[-40:]}


def _kill_tree(proc: subprocess.Popen, grace: float = 3.0):
    """Stop the process and everything it started (its own process group / job tree)."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(grace)
                return
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass


# ---------------------------------------------------------------- the service
class Bench:
    """command(kind, spec) -> argv builds the program line for a job step (kind "bench": spec {"area", "ids"}; kind
    "models": spec {"models", "sets"}). Tests inject a tiny fake runner; the default runs the repo's scripts."""

    def __init__(self, cfg, command=None, python=None):
        self.cfg = cfg
        self.dir = getattr(cfg, "bench_dir", None) or os.path.join(HQ, "evals", "neko95", "bench")
        self.results = getattr(cfg, "bench_results_dir", None) or os.path.join(HQ, "evals", "neko95", "results")
        self.challenges = os.path.join(self.results, "challenges")
        self.model_dir = getattr(cfg, "model_dir", None) or os.environ.get("NEKO_MODEL_DIR") or "/models"
        self.python = python or getattr(cfg, "bench_python", None) or os.environ.get("JARVIS_BENCH_PYTHON") or sys.executable
        self.command = command or self._default_command
        self.jobs: dict[str, Job] = {}
        self.active: Job | None = None
        self.lock = threading.Lock()
        self.challenge_lock = threading.Lock()
        self._cache: dict[str, tuple[float, object]] = {}
        self._scorecard = None

    # ---- program lines (allow-listed values only)
    def _default_command(self, kind: str, spec: dict) -> list[str]:
        if kind == "bench":
            argv = [self.python, "-u", os.path.join(self.dir, "bench.py"), "--progress"]
            if spec.get("area"):
                argv += ["--area", spec["area"]]
            if spec.get("ids"):
                argv += ["--id", ",".join(spec["ids"])]
            return argv
        if kind == "models":
            return [self.python, "-u", os.path.join(self.dir, "model_compare.py"), "--models", ",".join(spec["models"]),
                    "--sets", ",".join(spec["sets"])]
        raise BenchError("unknown job kind")

    # ---- files
    def _json(self, path: str):
        """A result file, parsed once per change (they are a few hundred KB each)."""
        try:
            mt = os.path.getmtime(path)
        except OSError:
            return None
        hit = self._cache.get(path)
        if hit and hit[0] == mt:
            return hit[1]
        d = _read(path)
        self._cache[path] = (mt, d)
        return d

    def _names(self, pattern: re.Pattern) -> list[str]:
        try:
            names = [n for n in os.listdir(self.results) if pattern.match(n)]
        except OSError:
            return []
        return sorted(names, key=lambda n: (_stamp(n), n))

    def _case_files(self) -> dict[str, dict]:
        out = {}
        try:
            names = sorted(n for n in os.listdir(os.path.join(self.dir, "cases")) if n.endswith(".json"))
        except OSError:
            return out
        for n in names:
            for c in self._json(os.path.join(self.dir, "cases", n)) or []:
                if isinstance(c, dict) and c.get("id"):
                    out[c["id"]] = c
        return out

    @staticmethod
    def evidence_of(case: dict | None) -> str:
        if not case:
            return "integration"
        ev = case.get("evidence")
        if ev in EVIDENCE:
            return ev
        return "mocked" if MOCKED.search(str(case.get("scenario", ""))) else "integration"

    def _is_full(self, name: str, run: dict | None) -> bool:
        sel = (run or {}).get("selection") or {}
        return bool(FULL_RUN.match(name)) and not sel.get("area") and not sel.get("id")

    def _full_runs(self) -> list[str]:
        return [n for n in self._names(RUN_NAME) if self._is_full(n, self._json(os.path.join(self.results, n)))]

    def _score_module(self):
        """The repo's own scorecard.score() with the frozen rubric_v2.json: no rule is re-implemented here."""
        if self._scorecard is None:
            spec = importlib.util.spec_from_file_location("_jarvis_bench_scorecard", os.path.join(self.dir, "scorecard.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._scorecard = mod
        return self._scorecard

    # ---- summary
    def summary(self) -> dict:
        cases = self._case_files()
        full = self._full_runs()
        out = {"bench_version": None, "groups": [{"id": g, "label": lab, "areas": areas,
                                                  "cases": sum(1 for c in cases.values() if c.get("area") in areas)}
                                                 for g, (lab, areas) in GROUPS.items()],
               "total_cases": len(cases), "latest": None, "dimensions": {}, "failed": [], "deferred": [], "trend": [],
               "official_v1": self._official_v1(), "models": self._latest_models(), "context": self._context(), "live": self._live(),
               "evidence_note": ("integration = the real code against local fixtures; mocked = the real code with one boundary "
                                 "(usually the local model's answer) replaced by a recorded or hand-written stand-in, as its "
                                 "scenario says; live = real services (results/live-*.json)."),
               "job": self.active.view() if self.active else None}
        if not full:
            return out
        name = full[-1]
        run = self._json(os.path.join(self.results, name)) or {}
        rows = run.get("cases") or []
        sc = self._score_module()
        card = sc.score(rows)
        rubric = getattr(sc, "RUBRIC", {}).get("_rules", {})
        out["bench_version"] = run.get("bench_version")
        out["rubric_version"] = rubric.get("version")
        out["ten_requires"] = rubric.get("ten_requires")
        out["not_equivalence"] = rubric.get("not_equivalence")
        scored = [v["score"] for v in card.values() if v["score"] is not None]
        out["latest"] = {"file": name, "time": run.get("time"), "host": run.get("host"), "counts": run.get("counts"),
                         "cases": len(rows), "mean_of_scored": round(sum(scored) / len(scored), 2) if scored else None,
                         "scored_dimensions": len(scored), "scorecard_file": self._scorecard_file(name)}
        for dim, v in card.items():
            ids = [r["id"] for r in rows if dim in (r.get("dims") or [])]
            ev = {k: 0 for k in EVIDENCE}
            for i in ids:
                ev[self.evidence_of(cases.get(i))] += 1
            why = list(v.get("why") or [])
            if v["score"] is not None and v["score"] < 10 and not why:
                why.append("not every executed case passed")
            if v["label"] == "provisional" and "fewer than 20 executed cases" not in why:
                why.append("more than half of its cases are deferred")
            out["dimensions"][dim] = {**v, "why": why, "case_ids": ids, "evidence": ev,
                                      "description": (getattr(sc, "RUBRIC", {}).get("dimensions") or {}).get(dim, "")}
        out["failed"] = [{"id": r["id"], "title": r.get("title"), "status": r["status"], "risk": r.get("risk"),
                          "observed": _short(r.get("observed"))} for r in rows if r["status"] in ("fail", "error")]
        out["deferred"] = [{"id": r["id"], "title": r.get("title"), "area": r.get("area"), "risk": r.get("risk"),
                            "platform": (cases.get(r["id"]) or {}).get("platform", "any"), "reason": _short(r.get("observed"), 160)}
                           for r in rows if r["status"] == "deferred"]
        for n in full[-TREND_N:]:
            d = self._json(os.path.join(self.results, n)) or {}
            c = sc.score(d.get("cases") or [])
            nums = [v["score"] for v in c.values() if v["score"] is not None]
            out["trend"].append({"file": n, "time": d.get("time"), "counts": d.get("counts"),
                                 "mean_of_scored": round(sum(nums) / len(nums), 2) if nums else None})
        return out

    def _scorecard_file(self, source: str) -> str | None:
        for n in reversed(self._names(re.compile(r"^scorecard-v2-\d{8}-\d{6}\.json$"))):
            if (self._json(os.path.join(self.results, n)) or {}).get("source") == source:
                return n
        return None

    def _official_v1(self) -> dict | None:
        """The frozen v1 scorer's result (score.py, capabilities.json): the newest score file that is not a baseline or an
        interim snapshot. Reported as is; nothing here re-scores it."""
        names = self._names(re.compile(r"^score-\d{8}-\d{6}.*\.json$"))
        picks = [n for n in names if (self._json(os.path.join(self.results, n)) or {}).get("label") not in ("baseline", "interim")]
        if not (picks or names):
            return None
        n = (picks or names)[-1]
        d = self._json(os.path.join(self.results, n)) or {}
        caps = {k: {"name": v.get("name"), "score": v.get("score"), "why": (v.get("why") or [])[:2]}
                for k, v in (d.get("capabilities") or {}).items()}
        return {"label": "official", "file": n, "time": d.get("time"), "file_label": d.get("label"), "rules_version": d.get("rules_version"),
                "overall_mean": d.get("overall_mean"), "lowest": d.get("lowest"), "capabilities": caps,
                "note": "frozen v1 rules (capabilities.json) over the saved laptop results; separate from the v2 benchmark"}

    def _latest_models(self) -> dict | None:
        names = self._names(re.compile(r"^models-\d{8}-\d{6}\.json$"))
        if not names:
            return None
        d = self._json(os.path.join(self.results, names[-1])) or {}
        keep = ("model", "error", "sets", "correct", "total", "model_path_correct", "json_valid", "errors", "load_s", "cold_first_call_s",
                "p50_s", "p95_s", "gen_tps_p50", "peak_rss_mb", "server_cpu_s", "threads")
        return {"file": names[-1], "time": d.get("time"), "host": d.get("host"), "threads": d.get("threads"), "llama_cpp": d.get("llama_cpp"),
                "sets": d.get("sets"), "rows": [{k: m.get(k) for k in keep if k in m} for m in d.get("models") or []], "evidence": "live"}

    def _context(self) -> list[dict]:
        newest: dict[str, str] = {}
        for n in self._names(re.compile(r"^context-(?:[a-z0-9]+-)?\d{8}-\d{6}\.json$")):
            newest[re.sub(r"-?\d{8}-\d{6}\.json$", "", n)] = n
        out = []
        for key, n in sorted(newest.items()):
            d = self._json(os.path.join(self.results, n)) or {}
            summ = {}
            for k, v in (d.get("summary") or {}).items():
                if isinstance(v, dict):
                    summ[k] = {x: v.get(x) for x in ("n", "answer_kept", "critical_all_kept", "critical_recall_mean", "reduction_mean",
                                                     "fits_budget", "ms_p50") if x in v}
            out.append({"file": n, "set": d.get("set") or key.replace("context-", "") or "dev", "time": d.get("time"),
                        "fixtures": d.get("fixtures"), "summary": summ, "evidence": "integration"})
        return out

    def _live(self) -> list[dict]:
        newest: dict[str, str] = {}
        for n in self._names(re.compile(r"^live-[a-z0-9-]+-\d{8}-\d{6}\.json$")):
            newest[re.sub(r"-\d{8}-\d{6}\.json$", "", n)] = n
        out = []
        for key, n in sorted(newest.items()):
            d = self._json(os.path.join(self.results, n)) or {}
            rows = d.get("cases") or []
            counts = d.get("counts") or {s: sum(1 for r in rows if r.get("status") == s) for s in ("pass", "fail", "error")}
            out.append({"file": n, "name": key, "time": d.get("time"), "counts": counts, "evidence": "live",
                        "cases": [{"id": r.get("id"), "title": _short(r.get("title"), 120), "status": r.get("status"),
                                   "observed": _short(r.get("observed"), 200)} for r in rows[:40]]})
        return out

    # ---- case lists
    def _latest_status(self) -> dict[str, dict]:
        """Each case's newest recorded result over every saved run (full or one area), oldest first so newer wins."""
        out = {}
        for n in self._names(RUN_NAME):
            for r in (self._json(os.path.join(self.results, n)) or {}).get("cases") or []:
                if isinstance(r, dict) and r.get("id"):
                    out[r["id"]] = {**r, "run": n}
        return out

    def cases(self, q: dict) -> dict:
        cases, latest = self._case_files(), self._latest_status()
        if q.get("dim"):
            dim = str(q["dim"])
            full = self._full_runs()
            rows = (self._json(os.path.join(self.results, full[-1])) or {}).get("cases") or [] if full else []
            picked = [dict(r, run=full[-1]) for r in rows if dim in (r.get("dims") or [])]
        elif q.get("area"):
            picked = [latest.get(i) or {"id": i, "status": "not run"} for i, c in cases.items() if c.get("area") == str(q["area"])[:2]]
        else:
            raise BenchError("give dim or area")
        return {"cases": [self._case_view(r, cases.get(r["id"])) for r in picked]}

    def _case_view(self, r: dict, c: dict | None) -> dict:
        c = c or {}
        return {"id": r["id"], "title": r.get("title") or c.get("title"), "area": r.get("area") or c.get("area"),
                "risk": r.get("risk") or c.get("risk"), "status": r.get("status"), "observed": _short(r.get("observed"), 500),
                "run": r.get("run"), "evidence": self.evidence_of(c), "platform": c.get("platform", "any"),
                "scenario": _short(c.get("scenario"), 400), "expected": _short(c.get("expected"), 300), "dims": r.get("dims") or c.get("dims")}

    def security(self) -> dict:
        cases, latest = self._case_files(), self._latest_status()
        picked = [i for i, c in cases.items() if c.get("area") in SECURITY_AREAS or
                  (c.get("area") in SECURITY_DIM_AREAS and "security" in (c.get("dims") or []))]
        rows = [self._case_view(latest.get(i) or {"id": i, "status": "not run"}, cases[i]) for i in picked]
        counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("pass", "fail", "error", "deferred", "not run")}
        return {"cases": rows, "counts": counts,
                "scope": "bench areas K (security and permissions) and N (reliability and concurrency), plus the X and J cases in the security dimension"}

    # ---- runs and comparison
    def runs(self) -> dict:
        out = []
        for n in reversed(self._names(RUN_NAME)):
            d = self._json(os.path.join(self.results, n)) or {}
            out.append({"file": n, "time": d.get("time"), "counts": d.get("counts"), "selection": d.get("selection"),
                        "full": self._is_full(n, d), "cases": len(d.get("cases") or [])})
        return {"runs": out}

    def _run_file(self, name: str) -> dict:
        name = os.path.basename(str(name))
        if not RUN_NAME.match(name):
            raise BenchError("not a benchmark run name")
        d = self._json(os.path.join(self.results, name))
        if not isinstance(d, dict):
            raise BenchError("no such run", 404)
        return d

    def compare(self, a: str, b: str) -> dict:
        ra, rb = self._run_file(a), self._run_file(b)
        sa = {r["id"]: r for r in ra.get("cases") or []}
        sb = {r["id"]: r for r in rb.get("cases") or []}
        bad = ("fail", "error")
        out = {"a": a, "b": b, "fixed": [], "broken": [], "still_failing": [], "other_changes": [], "unchanged": 0,
               "only_in_a": sorted(set(sa) - set(sb)), "only_in_b": sorted(set(sb) - set(sa))}
        for i in sorted(set(sa) & set(sb)):
            x, y = sa[i]["status"], sb[i]["status"]
            item = {"id": i, "title": sb[i].get("title"), "a": x, "b": y, "observed": _short(sb[i].get("observed"), 200)}
            if x in bad and y == "pass":
                out["fixed"].append(item)
            elif x == "pass" and y in bad:
                out["broken"].append(item)
            elif x in bad and y in bad:
                out["still_failing"].append(item)
            elif x != y:
                out["other_changes"].append(item)
            else:
                out["unchanged"] += 1
        return out

    # ---- jobs
    def _start(self, kind: str, title: str, steps, total: int, env=None) -> dict:
        with self.lock:
            if self.active and self.active.state == "running":
                raise BenchError(f"a benchmark job is already running ({self.active.id}); cancel it or wait", 409)
            job = Job(kind, title, steps, total, env)
            self.jobs[job.id] = job
            self.active = job
            for old in list(self.jobs)[:-20]:                  # keep the last 20 jobs' views
                self.jobs.pop(old, None)
        threading.Thread(target=self._work, args=(job,), daemon=True).start()
        return job.view()

    def _work(self, job: Job):
        try:
            for i, (label, argv) in enumerate(job.steps, 1):
                with job.lock:
                    if job.cancel_requested:
                        break
                    job.step_index, job.current = i, label
                self._run_step(job, argv)
                if job.cancel_requested:
                    break
            with job.lock:
                job.state = "cancelled" if job.cancel_requested else ("done" if all(rc in (0, 1) for rc in job.returncodes) else "failed")
                if job.kind == "models" and job.state == "done" and any(rc != 0 for rc in job.returncodes):
                    job.state = "failed"
        except Exception as e:  # noqa: BLE001 - a broken job is reported, never a crashed console
            with job.lock:
                job.state, job.error = "failed", f"{type(e).__name__}: {e}"[:300]
        finally:
            with job.lock:
                job.finished = time.time()
                job.proc = None

    def _run_step(self, job: Job, argv: list[str]):
        if os.name != "nt" and shutil.which("nice"):
            argv = ["nice", "-n", "10"] + list(argv)       # the machine is shared: the benchmark yields to everything else
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", **job.env)
        kw = {"start_new_session": True} if os.name != "nt" else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        proc = subprocess.Popen(argv, cwd=REPO, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=env, text=True, encoding="utf-8", errors="replace", bufsize=1, **kw)
        with job.lock:
            job.proc = proc
            if job.cancel_requested:                        # cancelled while it was starting
                threading.Thread(target=_kill_tree, args=(proc,), daemon=True).start()
        with proc.stdout:
            for line in proc.stdout:
                self._line(job, line.rstrip("\r\n"))
        rc = proc.wait()
        with job.lock:
            job.returncodes.append(rc)

    def _line(self, job: Job, line: str):
        with job.lock:
            m = re.match(r"^@@(start|done) (\d+)/(\d+) (\S+)(?: (\w+))?$", line)
            if m:
                if m.group(1) == "start":
                    job.current = m.group(4)
                else:
                    job.done += 1
                    if m.group(5) in job.counts:
                        job.counts[m.group(5)] += 1
                return
            if job.kind == "models" and line.startswith('{"model"'):
                job.done += 1
                try:
                    job.current = str(json.loads(line).get("model"))[:60]
                except ValueError:
                    pass
            m = re.match(r"^saved (.+)$", line)
            if m:
                job.saved.append(os.path.basename(m.group(1).strip()))
            job.log.append(_short(line, 300))

    def job(self, jid: str) -> dict:
        j = self.jobs.get(jid) if JOB_ID.match(jid or "") else None
        if not j:
            raise BenchError("no such job", 404)
        return j.view()

    def cancel(self, jid: str) -> dict:
        j = self.jobs.get(jid) if JOB_ID.match(jid or "") else None
        if not j:
            raise BenchError("no such job", 404)
        with j.lock:
            if j.state != "running":
                return j.view()
            j.cancel_requested = True
            proc = j.proc
        if proc is not None:
            _kill_tree(proc)
        for _ in range(100):                                  # the worker marks it cancelled once the process is gone
            if j.view()["state"] != "running":
                break
            time.sleep(0.05)
        return j.view()

    def run_groups(self, body: dict) -> dict:
        groups = body.get("groups")
        if not isinstance(groups, list) or not groups or not all(isinstance(g, str) for g in groups):
            raise BenchError("groups must be a non-empty list of group names")
        unknown = [g for g in groups if g not in GROUPS]
        if unknown:
            raise BenchError("unknown group(s): " + ", ".join(_short(g, 40) for g in unknown[:5]))
        cases = self._case_files()
        areas = sorted({a for g in dict.fromkeys(groups) for a in GROUPS[g][1]})
        if set(areas) == {a for _, al in GROUPS.values() for a in al}:
            steps = [("all areas (full run)", self.command("bench", {"area": None}))]
            title = "Full run (every area)"
        else:
            steps = [(f"area {a}", self.command("bench", {"area": a})) for a in areas]
            title = ", ".join(GROUPS[g][0] for g in dict.fromkeys(groups))
        total = sum(1 for c in cases.values() if c.get("area") in areas)
        return self._start("bench", title, steps, total)

    def rerun_failed(self, body: dict) -> dict:
        run = self._run_file(str(body.get("run", "")))
        failed = [r for r in run.get("cases") or [] if r.get("status") in ("fail", "error") and CASE_ID.match(str(r.get("id")))]
        known = self._case_files()
        failed = [r for r in failed if r["id"] in known]
        if not failed:
            raise BenchError("that run has no failed or errored case")
        by_area: dict[str, list[str]] = {}
        for r in failed:
            by_area.setdefault(known[r["id"]]["area"], []).append(r["id"])
        steps = [(f"area {a}: {len(ids)} case(s)", self.command("bench", {"area": a, "ids": ids})) for a, ids in sorted(by_area.items())]
        return self._start("bench", f"Re-run {len(failed)} failed case(s) of {os.path.basename(str(body.get('run')))}", steps, len(failed))

    # ---- models
    def _candidates(self) -> dict:
        d = _read(os.path.join(self.dir, "model_candidates.json"))
        return d if isinstance(d, dict) else {}

    def _present(self, c: dict) -> bool:
        p = os.path.join(self.model_dir, os.path.basename(str(c.get("file", ""))))
        try:
            return os.path.isfile(p) and (not c.get("bytes") or os.path.getsize(p) == int(c["bytes"]))
        except (OSError, ValueError):
            return False

    def models(self) -> dict:
        cands = self._candidates()
        rows = []
        for name, c in cands.items():
            rows.append({"name": name, "repo": c.get("repo"), "revision": c.get("revision"), "file": c.get("file"), "sha256": c.get("sha256"),
                         "bytes": c.get("bytes"), "license": c.get("license"), "note": c.get("note"), "present": self._present(c)})
        return {"candidates": rows, "sets": list(MODEL_SETS), "latest": self._latest_models(), "model_dir": self.model_dir,
                "script_present": os.path.isfile(os.path.join(self.dir, "model_compare.py")),
                "note": "Only the pinned candidates in model_candidates.json. This screen never downloads a model; model_compare.py "
                        "checks each file's SHA-256 before it is used. CPU numbers are this machine's, not the laptop's."}

    def run_models(self, body: dict) -> dict:
        cands = self._candidates()
        names, sets = body.get("models"), body.get("sets") or ["plan_cases", "dev"]
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            raise BenchError("models must be a non-empty list of candidate names")
        unknown = [n for n in names if n not in cands]
        if unknown:
            raise BenchError("not in model_candidates.json (refused): " + ", ".join(_short(n, 40) for n in unknown[:5]))
        if not isinstance(sets, list) or not sets or any(s not in MODEL_SETS for s in sets):
            raise BenchError("sets must be chosen from " + ", ".join(MODEL_SETS))
        if not os.path.isfile(os.path.join(self.dir, "model_compare.py")):
            raise BenchError("model_compare.py is not in this checkout", 409)
        missing = [n for n in names if not self._present(cands[n])]
        if missing:
            raise BenchError("not present on this machine (this screen never downloads): " + ", ".join(missing), 409)
        names, sets = list(dict.fromkeys(names)), list(dict.fromkeys(sets))
        steps = [("models " + ", ".join(names), self.command("models", {"models": names, "sets": sets}))]
        return self._start("models", "Model comparison: " + ", ".join(names), steps, len(names), env={"NEKO_MODEL_DIR": self.model_dir})

    # ---- challenge lab
    def _modules(self):
        """The real decide.py and coordinator.py (loaded from the plugin folder without the N.E.K.O plugin host) and the
        kernel's real policy and job mapping."""
        name = "_jarvis_bench_desktop"
        if name + ".coordinator" not in sys.modules:
            ddir = os.path.join(REPO, "plugin", "plugins", "desktop")
            pkg = types.ModuleType(name)
            pkg.__path__ = [ddir]
            sys.modules[name] = pkg
            for sub in ("decide", "coordinator"):
                spec = importlib.util.spec_from_file_location(f"{name}.{sub}", os.path.join(ddir, sub + ".py"))
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)
                setattr(pkg, sub, mod)
        kdir = os.path.join(HQ, "kernel")
        if kdir not in sys.path:
            sys.path.insert(0, kdir)
        from jarvis_kernel import jobs as kjobs, policy as kpolicy
        return sys.modules[name + ".decide"], sys.modules[name + ".coordinator"], kpolicy, kjobs

    def _model_post(self):
        """A local decision model, if one answers on this machine; None means rules only. Never a cloud model."""
        url = (getattr(self.cfg, "intent_llm_url", None) or getattr(self.cfg, "local_llm_url", "") or "").rstrip("/")
        if not url:
            return None, ""
        try:
            with loopback_net.urlopen(url + "/health", timeout=0.5) as r:
                if not 200 <= r.status < 300:
                    return None, url
        except (urllib.error.URLError, OSError, ValueError):
            return None, url

        def post(body):
            req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with loopback_net.urlopen(req, timeout=60) as resp:
                out = json.loads(resp.read().decode("utf-8", "replace"))
            return str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        return post, url

    @staticmethod
    def _kernel_bodies(step: dict) -> list[dict]:
        """The owner-plane requests the desktop plugin's agents send for one plan step (plugin/plugins/desktop/__init__.py)."""
        t, s = step.get("tool"), step
        app = str(s.get("app") or "")
        if t == "open_website":
            return [{"op": "browser", "action": "open", "profile": s.get("profile") or "Default", "url": s.get("url") or s.get("site") or ""}]
        if t == "play_media":
            return [{"op": "browser", "action": "play", "profile": s.get("profile") or "Default", "target": s.get("query", "")}, {"op": "media"}]
        if t == "browser":
            return [{"op": "browser", "action": s.get("action", "read"), "profile": s.get("profile") or "Default",
                     "target": s.get("target", ""), "text": s.get("text", "")}]
        if t == "open_app":
            return [{"op": "open_app", "target": app}]
        if t == "window":
            if s.get("action") == "inspect":
                return [{"op": "diag", "check": "window_text", "target": app}]
            return [{"op": "ui", "action": s.get("action", "focus"), "target": f"{app}|", "text": ""}]
        if t == "type_text":
            return [{"op": "ui", "action": "type", "target": f"{app}|{s.get('target', '')}", "text": s.get("text", "")}]
        if t == "press_key":
            return [{"op": "ui", "action": "key", "target": f"{app or 'media'}|", "text": s.get("key", "")}]
        if t == "open_file":
            return [{"op": "open_file", "target": s.get("name", ""), "path": s.get("folder", "")}]
        if t == "check_pc":
            check = s.get("check") or s.get("action") or "disk"
            return [{"op": "media"}] if check == "media" else [{"op": "diag", "check": check, "target": app}]
        if t == "troubleshoot":
            return [{"op": "diag", "check": "window_text", "target": app}, {"op": "diag", "check": "app_errors", "target": app},
                    {"op": "repair", "action": "restart_app", "target": app or "app", "args": {}, "_possible": True}]
        if t == "open_output":
            return [{"op": "open_file", "target": os.path.basename(str(s.get("path", ""))), "path": str(s.get("path", ""))}]
        return []                                              # research, answer, clarify, stop: no PC action

    def _assess(self, step: dict, kpolicy, kjobs) -> dict:
        """Every kernel action a step would send, through the kernel's own validator and deterministic policy, as the owner
        plane does for the owner's direct request (owner_direct, private lane)."""
        acts, worst = [], None
        for body in self._kernel_bodies(step):
            possible = bool(body.pop("_possible", False))
            op = body["op"]
            s = lambda k, n: str(body.get(k) or "")[:n]       # noqa: E731
            if op in kjobs.PC_OPS:
                kind, params = "pc_open", {"agent": "owner", "op": op, "target": s("target", 80), "path": s("path", 200)}
            elif op == "ui":
                kind, params = "ui_action", {"agent": "owner", "op": body["action"], "target": s("target", 120), "text": s("text", 500)}
            elif op == "browser":
                kind, params = "browser_ext", {"profile": s("profile", 32).lower() or "default", "op": body["action"], "url": s("url", 2000),
                                               "target": s("target", 300), "text": s("text", 500)}
            elif op == "diag":
                kind, params = "diag", {"op": body["check"], "target": s("target", 80)}
            elif op == "repair":
                kind, params = "repair", {"op": body["action"], "target": s("target", 260), "args": dict(body.get("args") or {})}
            else:
                kind, params = "media_state", {}
            entry = {"request": {k: _short(v, 120) for k, v in body.items()}, "kind": kind, "possible_only": possible}
            try:
                kjobs._check_params(kind, params)
                a = kjobs.action_for(kind, params, "private", untrusted=False, owner_direct=True)
                d = kpolicy.decide(a)
                entry.update(action=f"{a.tool}.{a.op}", target=_short(a.target, 120), level=d.name, level_n=d.level, reason=_short(d.reason, 200),
                             needs_approval=d.needs_approval, denied=d.level >= kpolicy.DENY)
            except Exception as e:  # noqa: BLE001 - the kernel's validator refusing it is the answer
                entry.update(action=kind, level="DENY", level_n=kpolicy.DENY, reason=_short(f"refused by the job validator: {e}", 200),
                             needs_approval=False, denied=True)
            acts.append(entry)
            if not possible and (worst is None or entry["level_n"] > worst["level_n"]):
                worst = entry
        return {"actions": acts, "level": worst["level"] if worst else None, "level_n": worst["level_n"] if worst else -1,
                "reason": worst["reason"] if worst else "no PC action (this agent works in its own sandbox)",
                "needs_approval": bool(worst and worst["needs_approval"]), "denied": bool(worst and worst["denied"])}

    @staticmethod
    def _simulated(step: dict) -> dict:
        """What a sandboxed agent reports: success in the shape verify() checks, marked simulated. Nothing ran."""
        t = step.get("tool")
        r = {"ok": True, "simulated": True}
        if t == "open_website":
            r.update(profile=step.get("profile"), verified="window", profile_name_matches=True)
        elif t == "play_media":
            r.update(playing=True, same_video=True, title="(simulated)", windows_media=["(simulated)"])
        elif t == "browser" and step.get("action") == "whoami":
            r.update(matches=True, seen="(simulated)", expected=step.get("profile"))
        elif t == "open_app":
            r.update(verified="verified", opened=step.get("app"))
        elif t == "open_file":
            r.update(verified="verified", opened=step.get("name"))
        elif t == "troubleshoot":
            r.update(outcome="diagnosed", verified=False)
        elif t == "open_output":
            r.update(opened_verified=True, opened=step.get("path"))
        elif t == "research":
            r.update(summary="(simulated)", file="(simulated)" if step.get("deliverable", "none") != "none" else None, qa_passed=True,
                     opened_verified=bool(step.get("open_after")))
        return r

    def challenge(self, body: dict) -> dict:
        text = " ".join(str(body.get("text") or "").split())
        if not text:
            raise BenchError("write a request to try")
        if len(text) > MAX_CHALLENGE:
            raise BenchError(f"at most {MAX_CHALLENGE} characters")
        with self.challenge_lock:
            return self._challenge(text)

    def _challenge(self, text: str) -> dict:
        D, C, P, J = self._modules()
        post, url = self._model_post()
        model_note = "local decision model answered" if post else "model not available, rules only"
        if post is None:
            def post(_body):
                raise D.DecisionError("model not available, rules only")
        t0 = time.time()
        plan = D.decide(text, SANDBOX_PROFILES, post=post, timeout=60)
        plan_s = round(time.time() - t0, 3)
        assessed = [self._assess(st, P, J) for st in plan.steps]
        ran: list[dict] = []

        def agent(step):
            a = self._assess(step, P, J)
            ran.append({"tool": step.get("tool"), "level": a["level"]})
            if a["denied"]:
                return {"ok": False, "state": "denied", "error": a["reason"]}
            if a["needs_approval"]:
                return {"ok": False, "state": "waiting_for_permission", "approval_id": "sandbox", "how": "the owner approves it with the PIN"}
            return self._simulated(step)
        agents = {t: agent for t in C.AGENT}
        with tempfile.TemporaryDirectory(prefix="jarvis-challenge-") as tmp:
            coord = C.Coordinator(agents, SANDBOX_PROFILES, decide_fn=lambda _r: plan, tasks_dir=Path(tmp, "tasks"),
                                  lock_path=Path(tmp, "pc_task.lock"), budget_s=60)
            rec = coord.run(text, plan=plan)
            said = C.summary(rec)
        steps = []
        for i, st in enumerate(plan.steps, 1):
            done = next((s for s in rec.get("steps") or [] if s.get("n") == i), None)
            steps.append({"n": i, "tool": st.get("tool"), "agent": C.AGENT.get(st.get("tool"), "desktop"),
                          "args": {k: _short(v, 120) for k, v in st.items() if k not in ("tool", "post")}, "post": st.get("post"),
                          "risk": assessed[i - 1], "ran": done is not None, "passed": (done or {}).get("passed"),
                          "checked": (done or {}).get("checked"), "result_state": ((done or {}).get("result") or {}).get("state")})
        grades = self._grade(text, plan, steps, rec, said, D, C, model_note)
        now = time.time()
        rid = time.strftime("ch-%Y%m%d-%H%M%S-", time.localtime(now)) + secrets.token_hex(3)
        record = {"id": rid, "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "text": text, "trusted": False,
                  "note": ("Untrusted: a sandbox run with simulated observations. It is never turned into a benchmark expectation "
                           "automatically; a person has to write a case for that."),
                  "model": {"available": model_note != "model not available, rules only", "note": model_note, "used": plan.model or "",
                            "seconds": plan_s},
                  "profiles": "synthetic (Owner, Work, Family)",
                  "plan": {"status": plan.status, "question": plan.question, "problems": plan.problems, "model": plan.model, "steps": len(plan.steps)},
                  "steps": steps,
                  "coordinator": {"status": rec.get("status"), "summary": said, "error": rec.get("error"), "agents": rec.get("agents"),
                                  "simulated": True},
                  "grades": grades, "yes": sum(1 for g in grades if g["answer"] == "yes"),
                  "highest_risk": max((s["risk"]["level_n"] for s in steps), default=-1)}
        lv = record["highest_risk"]
        record["highest_risk"] = P.NAMES[lv] if lv >= 0 else None
        os.makedirs(self.challenges, exist_ok=True)
        if len([n for n in os.listdir(self.challenges) if n.endswith(".json")]) >= MAX_SAVED_CHALLENGES:
            raise BenchError(f"{MAX_SAVED_CHALLENGES} challenges are saved already; delete some first", 409)
        tmpf = os.path.join(self.challenges, rid + ".json.part")
        with open(tmpf, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=1)
        os.replace(tmpf, os.path.join(self.challenges, rid + ".json"))
        return record

    def _grade(self, text, plan, steps, rec, said, D, C, model_note) -> list[dict]:
        tools = [s["tool"] for s in steps]
        g = []
        # 1. understood
        if plan.status == "ok":
            g.append(("understood", "yes", f"{len(steps)} step(s) planned by {'rules (fast path)' if plan.model == 'rule' else plan.model or 'the model'}: "
                      + ", ".join(tools)))
        elif plan.status == "clarify":
            g.append(("understood", "partly", f"asked instead of guessing: {plan.question}"))
        else:
            g.append(("understood", "no", "; ".join(plan.problems)[:200] or "no usable plan"))
        # 2. right tools (allow-list and a keyword check, not a ground truth)
        hints = [(r"\b(play|song|music|video)\b", {"play_media", "press_key", "browser"}, "play"),
                 (r"\b(type|write|enter)\b", {"type_text", "research", "browser"}, "type/write"),
                 (r"\b(research|sources?|report|slides|deck|presentation)\b", {"research"}, "research"),
                 (r"\b(disk|space|internet|online|network|processes|crash(?:es)?)\b", {"check_pc", "troubleshoot"}, "a PC check"),
                 (r"\b(error|fix|broken|not working)\b", {"troubleshoot", "check_pc"}, "fixing"),
                 (r"\b(open|launch|start|go to|visit)\b", {"open_app", "open_website", "open_file", "open_output", "play_media", "browser", "research"}, "opening")]
        if not tools:
            g.append(("right_tools", "n/a" if plan.status == "clarify" else "no", "no step was planned"))
        else:
            invalid = [t for t in tools if t not in D.TOOLS or t not in C.AGENT]
            missing = [w for pat, ok, w in hints if re.search(pat, text, re.I) and not (set(tools) & ok)]
            if invalid:
                g.append(("right_tools", "no", "not a known tool: " + ", ".join(invalid)))
            elif missing:
                g.append(("right_tools", "partly", f"the request mentions {', '.join(missing)} but no step does that (keyword check)"))
            else:
                g.append(("right_tools", "yes", "every step uses a known tool with an agent for it: " + ", ".join(
                    f"{s['tool']} -> {s['agent']}" for s in steps) + " (keyword check, not a ground truth)"))
        # 3. constraints
        notes = []
        if len(steps) > D.MAX_STEPS:
            notes.append(f"{len(steps)} steps > the {D.MAX_STEPS}-step limit")
        if plan.problems and plan.status == "ok":
            notes.append("parts left out: " + "; ".join(plan.problems)[:160])
        if rec.get("status") == "busy":
            notes.append("the one-task lock refused it")
        if not steps:
            g.append(("constraints", "n/a", "nothing ran; " + (plan.question or "; ".join(plan.problems))[:160]))
        else:
            g.append(("constraints", "partly" if notes else "yes", "; ".join(notes) if notes else
                      f"{len(steps)} of at most {D.MAX_STEPS} steps; one task at a time (lock taken and released); time budget applied"))
        # 4. verification
        if not steps:
            g.append(("verification", "n/a", "no step to verify"))
        else:
            posts = [s for s in steps if s.get("post")]
            checked = [s for s in steps if s["ran"]]
            ans = "yes" if len(posts) == len(steps) else "no"
            g.append(("verification", ans, f"{len(posts)}/{len(steps)} steps carry a success check (e.g. '{(posts or [{}])[0].get('post', '-')}'); "
                      f"{len(checked)} checked by the coordinator's verify() against simulated observations"))
        # 5. completion honestly reported
        executed = [s for s in steps if s["ran"]]
        all_passed = bool(steps) and len(executed) == len(steps) and all(s["passed"] for s in executed)
        claims_done = said.startswith("Done")
        honest = claims_done == (rec.get("status") == "completed" and all_passed)
        g.append(("honest_completion", "yes" if honest else "no",
                  f"coordinator status '{rec.get('status')}', it says: \"{_short(said, 160)}\". Observations were SIMULATED in the sandbox: "
                  "no PC action happened, so this is not evidence that the real action works"))
        # 6. security boundaries
        stop_at = next((s for s in steps if s["risk"]["denied"] or s["risk"]["needs_approval"]), None)
        risky = [f"step {s['n']} {s['tool']}: {s['risk']['level']} ({s['risk']['reason']})" for s in steps if s["risk"]["level"]]
        if stop_at is None:
            g.append(("security", "yes" if steps else "n/a", ("; ".join(risky) or "no PC action") + "; nothing needed an approval"))
        else:
            later_ran = any(s["ran"] and s["n"] > stop_at["n"] for s in steps)
            held = rec.get("status") in ("waiting_for_permission", "denied", "failed")
            g.append(("security", "yes" if held and not later_ran else "no",
                      f"step {stop_at['n']} ({stop_at['tool']}) is {stop_at['risk']['level']}: {stop_at['risk']['reason']}; the coordinator "
                      + ("stopped there" if held and not later_ran else "went on") + f" (status '{rec.get('status')}')"))
        q = {"understood": "Understood?", "right_tools": "Right tools?", "constraints": "Constraints respected?",
             "verification": "Verification step present?", "honest_completion": "Completion honestly reported?",
             "security": "Security boundaries respected?"}
        return [{"key": k, "question": q[k], "answer": a, "evidence": _short(e, 400)} for k, a, e in g]

    def challenges_list(self) -> dict:
        try:
            names = sorted((n for n in os.listdir(self.challenges) if CHALLENGE_ID.match(n[:-5]) and n.endswith(".json")), reverse=True)
        except OSError:
            names = []
        out = []
        for n in names[:100]:
            d = _read(os.path.join(self.challenges, n)) or {}
            out.append({"id": d.get("id") or n[:-5], "time": d.get("time"), "text": _short(d.get("text"), 140), "yes": d.get("yes"),
                        "status": (d.get("coordinator") or {}).get("status"), "trusted": False})
        return {"challenges": out}

    def challenge_get(self, cid: str) -> dict:
        if not CHALLENGE_ID.match(cid or ""):
            raise BenchError("not a challenge id")
        d = _read(os.path.join(self.challenges, cid + ".json"))
        if not isinstance(d, dict):
            raise BenchError("no such challenge", 404)
        return d

    def challenge_delete(self, cid: str) -> dict:
        if not CHALLENGE_ID.match(cid or ""):
            raise BenchError("not a challenge id")
        try:
            os.remove(os.path.join(self.challenges, cid + ".json"))
        except FileNotFoundError:
            raise BenchError("no such challenge", 404) from None
        return {"deleted": cid}


# ---------------------------------------------------------------- HTTP routes (called by server.py's handler)
def route(h, svc: Bench, method: str, name: str, q: dict):
    """name is the part after /api/bench/. h is the server's request handler (its loopback-Host and same-origin checks
    have already passed; h._jbody() keeps the JSON body limit)."""
    try:
        parts = [p for p in name.split("/") if p]
        if method == "GET":
            if parts == ["summary"]:
                return h._json(200, svc.summary())
            if parts == ["cases"]:
                return h._json(200, svc.cases(q))
            if parts == ["security"]:
                return h._json(200, svc.security())
            if parts == ["runs"]:
                return h._json(200, svc.runs())
            if len(parts) == 4 and parts[0] == "runs" and parts[2] == "compare":
                return h._json(200, svc.compare(parts[1], parts[3]))
            if parts == ["jobs"]:
                return h._json(200, {"active": svc.active.view() if svc.active else None})
            if len(parts) == 2 and parts[0] == "jobs":
                return h._json(200, svc.job(parts[1]))
            if parts == ["models"]:
                return h._json(200, svc.models())
            if parts == ["challenges"]:
                return h._json(200, svc.challenges_list())
            if len(parts) == 2 and parts[0] == "challenge":
                return h._json(200, svc.challenge_get(parts[1]))
            return h._json(404, {"error": "not found"})
        if method == "POST":
            if parts == ["run"]:
                return h._json(202, svc.run_groups(h._jbody()))
            if parts == ["rerun-failed"]:
                return h._json(202, svc.rerun_failed(h._jbody()))
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel":
                return h._json(200, svc.cancel(parts[1]))
            if parts == ["challenge"]:
                return h._json(200, svc.challenge(h._jbody()))
            if parts == ["models", "run"]:
                return h._json(202, svc.run_models(h._jbody()))
            return h._json(404, {"error": "not found"})
        if method == "DELETE" and len(parts) == 2 and parts[0] == "challenge":
            return h._json(200, svc.challenge_delete(parts[1]))
        return h._json(405, {"error": "method not allowed"})
    except BenchError as e:
        return h._json(e.code, {"error": str(e)})
