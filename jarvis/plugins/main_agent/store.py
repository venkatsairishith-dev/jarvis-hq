"""Checkpoints of runs on disk, so a crash or restart loses nothing it should keep.

One small JSON file per run, written atomically after every step. On startup, a run that was still "running" is marked
interrupted and the step that was in flight becomes `unknown` (it may or may not have happened); nothing is re-run on its own.

A run that is being planned has no plan and no step yet, so it gets no run file. Instead one small marker file
(ACTIVE_MARKER, not *.json, so it is never listed as a run) says "a task is being planned" and, if it never starts, how it
ended (stopped, or the plan failed and why). Status views read it; it is removed when the
run starts and its run file takes over.
Step results are what the model-facing steps returned (capped), so the folder sits with the face's own data, not in the repo.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .executor import StepResult
from .planner import Plan, Step

KEEP_RUNS = 20
ACTIVE_MARKER = "active_run.state"


def default_dir() -> Path:
    explicit = os.environ.get("MAIN_AGENT_STATE_DIR", "").strip()
    if explicit:
        return Path(explicit)
    base = os.environ.get("JARVIS_DATA_DIR", "").strip() or str(Path.home() / ".jarvis-main-agent")
    return Path(base) / "main_agent_runs"


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    return {"summary": plan.summary, "steps": [asdict(s) for s in plan.steps]}


def plan_from_dict(d: dict[str, Any]) -> Plan:
    steps = []
    for s in d.get("steps", []):
        steps.append(Step(id=str(s["id"]), kind=str(s["kind"]), goal=str(s["goal"]), ref=str(s.get("ref", "")),
                          args=dict(s.get("args") or {}), depends_on=[str(x) for x in s.get("depends_on", [])],
                          effect=str(s.get("effect", "none")), memory_influenced=bool(s.get("memory_influenced", False))))
    return Plan(str(d.get("summary", "")), steps)


SCREEN_NOT_KEPT = "[screen text is not kept on disk; read it again if it is needed]"


def result_to_dict(r: StepResult) -> dict[str, Any]:
    """A result as it is written to the run file. Screen- or disk-derived text (StepResult.screen) is NOT written: the desktop
    plugin deletes the node's local copy as soon as it has read it, and a run file must not become the place it is kept."""
    d = asdict(r)
    if r.screen and d.get("text"):
        d["text"] = SCREEN_NOT_KEPT
    return d


def result_from_dict(d: dict[str, Any]) -> StepResult:
    return StepResult(id=str(d["id"]), outcome=str(d.get("outcome", "failure")), text=str(d.get("text", "")),
                      error=str(d.get("error", "")), seconds=float(d.get("seconds", 0.0)), attempts=int(d.get("attempts", 1)),
                      held=bool(d.get("held", False)), waited=float(d.get("waited", 0.0)), screen=bool(d.get("screen", False)),
                      untrusted=bool(d.get("untrusted", False)))


class RunStore:
    def __init__(self, directory: Path | None = None, keep: int = KEEP_RUNS):
        self.dir = Path(directory) if directory else default_dir()
        self.keep = keep

    def _path(self, run_id: str) -> Path:
        safe = "".join(c for c in str(run_id) if c.isalnum() or c in "-_")[:40]
        if not safe:
            raise ValueError("bad run id")
        return self.dir / f"{safe}.json"

    def save(self, record: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        record["updated"] = time.time()
        path = self._path(record["run_id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def load(self, run_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(run_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def ids(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        files = sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        return [p.stem for p in files]

    def prune(self) -> None:
        for rid in self.ids()[: -self.keep]:
            try:
                self._path(rid).unlink()
            except OSError:
                pass

    def mark(self, record: dict[str, Any]) -> None:
        """Write the planning marker atomically (run id, state, summary, error; never the task text)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        keep = {k: record.get(k) for k in ("run_id", "state", "mode", "source", "started", "error") if record.get(k) is not None}
        keep["updated"] = time.time()
        path = self.dir / ACTIVE_MARKER
        tmp = path.with_name(ACTIVE_MARKER + ".tmp")
        tmp.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def read_mark(self) -> dict[str, Any] | None:
        try:
            data = json.loads((self.dir / ACTIVE_MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def clear_mark(self) -> None:
        try:
            (self.dir / ACTIVE_MARKER).unlink()
        except OSError:
            pass

    def recover(self) -> list[str]:
        """Mark runs that were running when the process stopped as interrupted. Returns their ids."""
        interrupted = []
        for rid in self.ids():
            rec = self.load(rid)
            if not rec or rec.get("state") != "running":
                continue
            plan = plan_from_dict(rec.get("plan") or {})
            effects = {s.id: s.effect for s in plan.steps}
            results = rec.setdefault("results", {})
            for sid in list(rec.get("running") or []):
                if sid in results and results[sid].get("outcome") == "success":
                    continue
                held = effects.get(sid, "none") == "non_idempotent"
                results[sid] = result_to_dict(StepResult(
                    sid, "unknown", error="interrupted: the process stopped while this step was running", held=held))
            rec["running"] = []
            rec["state"] = "interrupted"
            self.save(rec)
            interrupted.append(rid)
        mark = self.read_mark()
        if mark and mark.get("state") == "planning":       # stopped while planning: nothing ran, nothing to resume
            mark.update(state="failed", error="the app stopped while this task was being planned; nothing was run")
            self.mark(mark)
        return interrupted
