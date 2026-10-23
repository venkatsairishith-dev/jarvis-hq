"""Probe a declarative workflow on synthetic test cases, and check the results against explicit success criteria.

Each case runs in ``sandbox_runner.py`` in a new process: a fresh temporary folder, ``python -I``, an empty environment
(no proxy, no tokens, no PATH-based imports), a hard timeout, CPU and memory limits on Linux. The workflow language has no
network, process, import or eval operation, so a probe can only turn the case's input text into output files.

Workflows that need real code (a script, a program, an API) are not run here: they need the lab, where the kernel decides
(``needs_lab``). The probe result says so instead of pretending.

Checks (all explicit, all machine-decided):
  file_exists(file) | contains(file, text[, ignore_case]) | not_contains(file, text[, ignore_case]) | regex(file, pattern)
  | equals(file, text) (whitespace at the ends ignored) | line_count(file, op, n) | csv_columns(file, columns)
  | json_valid(file) | max_seconds(n)
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

RUNNER = Path(__file__).with_name("sandbox_runner.py")
TIMEOUT_S = 30
CHECKS = ("file_exists", "contains", "not_contains", "regex", "equals", "line_count", "csv_columns", "json_valid", "max_seconds")


class SandboxError(Exception):
    pass


def needs_lab(workflow: dict[str, Any]) -> bool:
    return bool(workflow.get("requires_code") or workflow.get("script") or workflow.get("language"))


def validate_workflow(workflow: Any) -> list[str]:
    """Problems with the workflow's shape (empty list = runnable here)."""
    from .sandbox_runner import OPS
    probs = []
    if not isinstance(workflow, dict):
        return ["the workflow must be an object"]
    if needs_lab(workflow):
        probs.append("this workflow needs code, which only the lab may run")
    steps = workflow.get("steps")
    if not isinstance(steps, list) or not steps:
        probs.append("the workflow has no steps")
        return probs
    for i, st in enumerate(steps[:120], 1):
        if not isinstance(st, dict) or st.get("op") not in OPS:
            probs.append(f"step {i}: unknown operation")
            continue
        extra = set(st) - set(OPS[st["op"]]) - {"op", "note"}
        if extra:
            probs.append(f"step {i}: {st['op']} does not take {sorted(extra)}")
    outs = workflow.get("outputs")
    if not isinstance(outs, list) or not outs:
        probs.append("the workflow declares no outputs")
    return probs


def run_case(workflow: dict[str, Any], case: dict[str, Any], timeout: int = TIMEOUT_S) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="wb-probe-") as tmp:
        job = Path(tmp)
        (job / "job.json").write_text(json.dumps({"workflow": workflow, "case": case}), encoding="utf-8")
        t0 = time.time()
        try:
            env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")} if sys.platform == "win32" else {}   # nothing else: no tokens, no proxy
            subprocess.run([sys.executable, "-I", "-S", str(RUNNER), str(job)], cwd=str(job), env=env,
                           capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timed out after {timeout} s", "seconds": timeout, "outputs": {}}
        res = {"ok": False, "error": "the probe produced no result"}
        try:
            res = json.loads((job / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        outs = {}
        out_dir = job / "out"
        if out_dir.is_dir():
            for p in out_dir.iterdir():
                if p.is_file() and not p.is_symlink() and p.stat().st_size <= 2_000_000:
                    outs[p.name] = p.read_text(encoding="utf-8", errors="replace")
        res["outputs"] = outs
        res["wall_seconds"] = round(time.time() - t0, 3)
        return res


def check(c: dict[str, Any], outputs: dict[str, str], seconds: float) -> tuple[bool, str]:
    kind = c.get("check")
    if kind not in CHECKS:
        return False, f"unknown check {kind!r}"
    if kind == "max_seconds":
        ok = seconds <= float(c.get("n", 0))
        return ok, "" if ok else f"took {seconds:.2f} s (limit {c.get('n')})"
    f = str(c.get("file", ""))
    if f not in outputs:
        return False, f"{f} was not written"
    text = outputs[f]
    ic = bool(c.get("ignore_case"))
    hay = text.lower() if ic else text
    if kind == "file_exists":
        return True, ""
    if kind in ("contains", "not_contains"):
        needle = str(c.get("text", ""))
        found = (needle.lower() if ic else needle) in hay
        ok = found if kind == "contains" else not found
        return ok, "" if ok else f"{f} {'lacks' if kind == 'contains' else 'contains'} {needle[:60]!r}"
    if kind == "regex":
        try:
            ok = bool(re.search(str(c.get("pattern", "")), text, re.M | (re.I if ic else 0)))
        except re.error:
            return False, "bad pattern in the check"
        return ok, "" if ok else f"{f} does not match {str(c.get('pattern'))[:60]!r}"
    if kind == "equals":
        ok = text.strip() == str(c.get("text", "")).strip()
        return ok, "" if ok else f"{f} is {text.strip()[:60]!r}, expected {str(c.get('text', '')).strip()[:60]!r}"
    if kind == "line_count":
        n = len([x for x in text.splitlines() if x.strip()])
        want, op = int(c.get("n", 0)), c.get("op", "==")
        ok = {"==": n == want, ">=": n >= want, "<=": n <= want}.get(op, False)
        return ok, "" if ok else f"{f} has {n} lines (want {op} {want})"
    if kind == "csv_columns":
        try:
            header = next(csv.reader(io.StringIO(text)))
        except StopIteration:
            header = []
        missing = [x for x in c.get("columns") or [] if x not in header]
        return not missing, "" if not missing else f"{f} lacks columns {missing}"
    if kind == "json_valid":
        try:
            json.loads(text)
            return True, ""
        except ValueError:
            return False, f"{f} is not valid JSON"
    return False, "unreachable"


def probe(workflow: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Run every case. Result: passes, total checks, errors, per-case detail, time, step count."""
    problems = validate_workflow(workflow)
    if problems:
        return {"runnable": False, "problems": problems, "passed_cases": 0, "cases": len(cases), "checks_passed": 0,
                "checks_total": sum(len(c.get("checks") or []) for c in cases), "errors": len(cases), "detail": [],
                "seconds": 0.0, "steps": len(workflow.get("steps") or []) if isinstance(workflow, dict) else 0}
    detail, passed_cases, cp, ct, errors, secs = [], 0, 0, 0, 0, 0.0
    for case in cases:
        r = run_case(workflow, case)
        secs += float(r.get("seconds") or 0)
        failures = []
        if not r.get("ok"):
            errors += 1
            failures.append(f"error: {r.get('error')}")
        n_ok = 0
        for c in case.get("checks") or []:
            ok, why = check(c, r.get("outputs") or {}, float(r.get("seconds") or 0))
            n_ok += ok
            if not ok:
                failures.append(why)
        ct += len(case.get("checks") or [])
        cp += n_ok
        all_ok = r.get("ok") and n_ok == len(case.get("checks") or []) and (case.get("checks") or [])
        passed_cases += bool(all_ok)
        detail.append({"case": str(case.get("name", ""))[:60], "passed": bool(all_ok), "failures": failures[:10],
                       "outputs": {k: v[:400] for k, v in (r.get("outputs") or {}).items()}})
    return {"runnable": True, "passed_cases": passed_cases, "cases": len(cases), "checks_passed": cp, "checks_total": ct,
            "errors": errors, "detail": detail, "seconds": round(secs, 4), "steps": len(workflow["steps"])}


def quality(p: dict[str, Any]) -> tuple:
    """Observed behaviour only (cases, checks, errors). Whether an edit HELPED is decided on this, never on time or size."""
    return (p["runnable"] and p["passed_cases"] == p["cases"] and p["cases"] > 0, p["passed_cases"], p["checks_passed"], -p["errors"])


def score(p: dict[str, Any]) -> tuple:
    """Higher is better: every case passed, then cases, then checks, then fewer errors, fewer steps, less time."""
    return (p["runnable"] and p["passed_cases"] == p["cases"] and p["cases"] > 0, p["passed_cases"], p["checks_passed"],
            -p["errors"], -p["steps"], -p["seconds"])
