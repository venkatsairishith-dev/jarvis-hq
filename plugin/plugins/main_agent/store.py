"""Checkpoints of runs on disk, so a crash or restart loses nothing it should keep.

One small JSON file per run, written atomically after every step. On startup, a run that was still "running" is marked
interrupted and the step that was in flight becomes `unknown` (it may or may not have happened); nothing is re-run on its own.
Step results are what the model-facing steps returned (capped), so the folder sits with N.E.K.O's own data, not in the repo.
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


def default_dir() -> Path:
    explicit = os.environ.get("MAIN_AGENT_STATE_DIR", "").strip()
    if explicit:
        return Path(explicit)
    base = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", "").strip() or str(Path.home() / ".neko-main-agent")
    return Path(base) / "main_agent_runs"


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    return {"summary": plan.summary, "steps": [asdict(s) for s in plan.steps]}


def plan_from_dict(d: dict[str, Any]) -> Plan:
    steps = []
    for s in d.get("steps", []):
        steps.append(Step(id=str(s["id"]), kind=str(s["kind"]), goal=str(s["goal"]), ref=str(s.get("ref", "")),
                          args=dict(s.get("args") or {}), depends_on=[str(x) for x in s.get("depends_on", [])],
                          effect=str(s.get("effect", "none"))))
    return Plan(str(d.get("summary", "")), steps)


def result_to_dict(r: StepResult) -> dict[str, Any]:
    return asdict(r)


def result_from_dict(d: dict[str, Any]) -> StepResult:
    return StepResult(id=str(d["id"]), outcome=str(d.get("outcome", "failure")), text=str(d.get("text", "")),
                      error=str(d.get("error", "")), seconds=float(d.get("seconds", 0.0)), attempts=int(d.get("attempts", 1)),
                      held=bool(d.get("held", False)), waited=float(d.get("waited", 0.0)))


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
        return interrupted
