"""Caretaker run: detect, log, de-duplicate, and hand new findings to the lab as kernel outcome tasks.

    python -m jarvis.caretaker.run --dry-run [--machine M] [--root DIR] [--log-dir DIR] [--offline]
    python -m jarvis.caretaker.run --config FILE              # a real run, on the lab (never on the owner's machines)

1. The detectors (detectors.py) run; a detector that fails or is skipped is noted, never fatal.
2. Every finding is appended to <log dir>/<date>/findings.jsonl with the action taken. The log dir is
   <data root>/logs/caretaker unless --log-dir says otherwise, and it is refused if it lies inside the repository.
3. De-duplication: <log dir>/seen.json remembers every finding id with its first sighting and its task. A finding that
   is gone from a later complete detector run is marked resolved; if it comes back it counts as new again.
4. A finding above the severity threshold that may be fixed by a pull request (not owner_only) and has no task yet gets
   one through the kernel's real HTTP API, as an agent would: task create, then lab dispatch, each with an
   Idempotency-Key derived from the finding id (create: the id itself), so a re-run can never make a second task. The
   lab instructions come only from template.py. At most --max-tasks per run; the rest wait for the next run.
5. The run's summary (counts by kind and severity, task ids) is appended to the kernel's audit chain
   (agent action caretaker.report) and to <log dir>/runs.jsonl.
--dry-run prints the tasks it would create and touches neither the kernel nor seen.json (its log is findings.dryrun.jsonl).
The kernel token is read from --token-file and is never printed or logged.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import detectors as D
from . import template as T

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_KERNEL = "http://127.0.0.1:8790"
INCOMPLETE = ("skipped", "not checked", "answered", "failed", "timed out", "no full", "exited")   # notes of a partial detector run


class RunError(Exception):
    pass


def default_root() -> str:
    return os.environ.get("JARVIS_DATA_ROOT") or {"nt": r"D:\jarvis"}.get(os.name, "/srv/jarvis")


def default_log_dir(root=None) -> Path:
    return Path(root or default_root()) / "logs" / "caretaker"


def check_log_dir(log_dir, *repos) -> Path:
    p = Path(log_dir).expanduser().resolve()
    for repo in {REPO, *(Path(x) for x in repos if x)}:
        r = Path(repo).resolve()
        if p == r or r in p.parents:
            raise RunError(f"the caretaker log dir must be outside the repository ({p} is inside {r})")
    return p


def _atomic_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _append(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True, default=str) + "\n")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                        # a redirect would carry the bearer token to another address: never follow


# The kernel endpoint is loopback by default but can be a Tailscale address, so the host is not restricted here; what matters is that the
# bearer token never goes to an environment proxy (http_proxy/ALL_PROXY) and is never re-sent after a redirect (completion H, 2026-10-19).
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


class Kernel:
    """The kernel's agent action endpoint, with the caretaker's own token (public lane)."""

    def __init__(self, endpoint, token, timeout=30):
        self.endpoint, self._token, self.timeout = endpoint.rstrip("/"), token, timeout

    def action(self, body, key=None):
        h = {"Authorization": "Bearer " + self._token, "Content-Type": "application/json"}
        if key:
            h["Idempotency-Key"] = key
        r = urllib.request.Request(self.endpoint + "/v1/jarvis/actions", data=json.dumps(body).encode(), method="POST", headers=h)
        try:
            with _OPENER.open(r, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return 0, {"reason": type(e).__name__}


def collect(args, http, registry, lock):
    """[(detector name, findings, notes, complete)]."""
    out = []
    want = set(args.detectors.split(",")) if args.detectors else set(D.KINDS)
    ep = args.endpoints or {}
    runs = {
        "upstream_release": lambda: D.detect_upstream_release(lock, http, ep),
        "advisory": lambda: D.detect_advisory(Path(args.repo_root), registry, http, ep),
        "lock_drift": lambda: D.detect_lock_drift(args.machine, args.root or default_root(), args.lock),
        "test_failure": lambda: D.detect_test_failure(args.bench_results),
        "secret_scan": lambda: D.detect_secret_scan(Path(args.repo_root), paths=args.secret_scan_paths),
    }
    for name in D.KINDS:
        if name not in want:
            continue
        try:
            f, n = runs[name]()
            complete = not any(w in x for x in n for w in INCOMPLETE)
        except Exception as e:                                       # one detector failing never stops the others
            f, n, complete = [], [f"{name}: failed: {type(e).__name__}: {str(e)[:200]}"], False
        out.append((name, f, n, complete))
    return out


def create_task(kernel, f, rendered, agent_kind, existing=None):
    """(task id, job id, note). Idempotency-Key = the finding id for the create, '<id>.dispatch' for the dispatch. With
    `existing` (a task made by an earlier run whose dispatch did not go through) only the dispatch is sent again."""
    if existing:
        return dispatch(kernel, f, rendered, agent_kind, existing, "task from an earlier run")
    body = {"tool": "task", "op": "create", "target": rendered["goal"][:200],
            "params": {"request": rendered["request"], "goal": rendered["goal"], "criteria": rendered["criteria"],
                       "plan": ["record the old pin", "change only what the finding needs", "run the mapped tests",
                                "open a pull request to main for the owner"],
                       "source": "caretaker", "max_attempts": 2, "budget_s": 5400}}
    code, r = kernel.action(body, key=f["id"])
    tid = r.get("task") if isinstance(r, dict) else None
    if code != 200 and not (code == 409 and tid):
        return None, None, f"task create refused ({code}): {str(r.get('reason', ''))[:200]}"
    note = "replayed (already created)" if r.get("replayed") else ("existing open task" if code == 409 else "created")
    return dispatch(kernel, f, rendered, agent_kind, tid, note)


def dispatch(kernel, f, rendered, agent_kind, tid, note):
    d = {"tool": "task", "op": "dispatch", "target": tid,
         "params": {"instructions": rendered["instructions"], "criteria": rendered["criteria"], "agent": agent_kind, "public_ok": True}}
    code, r = kernel.action(d, key=f["id"] + ".dispatch")
    if code == 200:
        return tid, r.get("job"), note + "; dispatched" + (" (replayed)" if r.get("replayed") else "")
    if code == 409 and "already on this task" in str(r.get("reason", "")):
        return tid, None, note + "; the lab already has it"
    return tid, None, note + f"; dispatch refused ({code}): {str(r.get('reason', ''))[:200]}"


def run(args) -> dict:
    log_dir = check_log_dir(args.log_dir or default_log_dir(args.root), args.repo_root)
    now = dt.datetime.now(dt.timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    day = log_dir / now.strftime("%Y-%m-%d")
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    lock = json.loads(Path(args.lock).read_text(encoding="utf-8"))
    test_map = json.loads((HERE / "test_map.json").read_text(encoding="utf-8"))
    http = D.Http(cache_path=str(log_dir / "cache" / "http.json"), offline=args.offline)
    results = collect(args, http, registry, lock)
    http.save()

    seen_path = log_dir / "seen.json"
    seen = json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    kernel = None
    if not args.dry_run:
        if not args.token_file or not args.repo_url:
            raise RunError("a real run needs --token-file (the caretaker's kernel token) and --repo-url; use --dry-run to look")
        with open(os.path.expanduser(args.token_file), encoding="utf-8") as fh:
            kernel = Kernel(args.kernel, fh.read().strip())
    threshold = D.sev_rank(args.threshold)
    rows, made, planned, notes = [], [], [], []
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    current = set()
    for name, findings, n, complete in results:
        notes += n
        for f in findings:
            if f["id"] in current:                       # the same issue twice in one run is logged once
                continue
            current.add(f["id"])
            prev = seen.get(f["id"])
            new = prev is None or bool(prev.get("resolved"))
            stored = f if new or not prev.get("finding") else prev["finding"]      # the first sighting: replays stay identical
            entry = dict(prev or {}, finding=stored, last_seen=ts, resolved=False)
            entry.setdefault("first_seen", ts)
            if new:
                entry.pop("task", None)
                entry.pop("job", None)
            row = {"run": run_id, "time": ts, **f, "new": new}
            if f.get("owner_only"):
                row["action"] = "owner_only"
            elif D.sev_rank(f["severity"]) < threshold:
                row["action"] = "below_threshold"
            elif entry.get("task") and entry.get("dispatched"):
                row["action"], row["task"] = "task_exists", entry["task"]
            else:
                try:
                    rendered = T.render(stored, registry, test_map, args.repo_url or "<the repository URL from --repo-url>")
                except ValueError as e:
                    row["action"], row["note"] = "template_refused", str(e)[:300]
                    rendered = None
                if rendered is None:
                    pass
                elif args.dry_run:
                    row["action"], row["branch"] = "would_create_task", rendered["branch"]
                    planned.append((f, rendered))
                elif len(made) >= args.max_tasks:
                    row["action"] = "deferred_max_tasks"
                else:
                    tid, jid, note = create_task(kernel, stored, rendered, args.agent, entry.get("task"))
                    row.update(action="task_created" if tid else "task_error", task=tid, job=jid, note=note, branch=rendered["branch"])
                    if tid:
                        entry.update(task=tid, job=jid, dispatched=jid is not None or "lab already" in note)
                        made.append(tid)
            rows.append(row)
            seen[f["id"]] = entry
        if complete:                                     # what this complete detector no longer sees is resolved
            for fid, e in seen.items():
                if e.get("finding", {}).get("kind") == name and fid not in current and not e.get("resolved"):
                    e.update(resolved=True, resolved_at=ts)
    by_kind, by_sev = {}, {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + 1
    summary = {"run": run_id, "time": ts, "dry_run": bool(args.dry_run), "machine": args.machine, "findings": len(rows),
               "new": sum(1 for r in rows if r["new"]), "by_kind": by_kind, "by_severity": by_sev,
               "owner_only": sum(1 for r in rows if r.get("action") == "owner_only"), "tasks": made,
               "would_create": len(planned), "http_requests": http.calls, "notes": notes}
    _append(day / ("findings.dryrun.jsonl" if args.dry_run else "findings.jsonl"), rows)
    if not args.dry_run:
        _atomic_json(seen_path, seen)
        code, r = kernel.action({"tool": "caretaker", "op": "report",
                                 "params": {k: summary[k] for k in ("by_kind", "by_severity", "new", "owner_only", "tasks")} | {"run_id": run_id}},
                                key="caretaker-run." + run_id)
        summary["audit"] = r.get("audit_seq") if code == 200 else f"not recorded ({code}: {str(r.get('reason', ''))[:120]})"
    _append(log_dir / ("runs.dryrun.jsonl" if args.dry_run else "runs.jsonl"), [summary])
    summary["log"] = str(day)
    summary["planned"] = planned
    return summary


def _print(summary, out=None):
    w = lambda s="": print(s, file=out or sys.stdout)                            # noqa: E731
    w(f"caretaker run {summary['run']} ({'DRY RUN: nothing created' if summary['dry_run'] else 'live'})")
    w(f"findings {summary['findings']} (new {summary['new']}), by kind {json.dumps(summary['by_kind'], sort_keys=True)}, "
      f"by severity {json.dumps(summary['by_severity'], sort_keys=True)}, owner-only {summary['owner_only']}")
    if summary["dry_run"]:
        w(f"tasks it would create: {summary['would_create']}")
        for f, r in summary["planned"]:
            w(f"  - [{f['severity']}] {f['kind']}: {f['tool']} -> {r['branch']}")
            w(f"      goal: {r['goal'][:200]}")
            w(f"      suggested: {f['suggested_action'][:200]}")
    else:
        w(f"tasks created: {', '.join(summary['tasks']) or 'none'}; audit: {summary.get('audit')}")
    for n in summary["notes"]:
        w(f"  note: {n}")
    w(f"log: {summary['log']}; HTTP requests: {summary['http_requests']}")


def parse(argv=None):
    ap = argparse.ArgumentParser(description="JARVIS caretaker: find tool issues and hand them to the lab as pull-request tasks")
    ap.add_argument("--config", help="JSON file with any of the options below (command-line options win)")
    ap.add_argument("--dry-run", action="store_true", help="print the tasks it would create; create nothing")
    ap.add_argument("--machine", help="lock machine for lock_drift (laptop, linux-test, oracle-vm, wsl-lab, other-profile)")
    ap.add_argument("--root", help="data root for lock_drift and the default log dir (default: env JARVIS_DATA_ROOT or the OS default)")
    ap.add_argument("--log-dir", help="caretaker log dir (default <root>/logs/caretaker); never inside the repository")
    ap.add_argument("--kernel", help=f"kernel endpoint (default env JARVIS_ENDPOINT or {DEFAULT_KERNEL})")
    ap.add_argument("--token-file", help="file holding the caretaker's kernel agent token (public lane, name 'caretaker')")
    ap.add_argument("--repo-url", help="the repository the lab clones and opens pull requests on")
    ap.add_argument("--threshold", choices=D.SEVERITIES, help="lowest severity that becomes a task (default medium)")
    ap.add_argument("--max-tasks", type=int, help="most tasks one run creates (default 5)")
    ap.add_argument("--agent", choices=("hermes", "opencode", "goose"), help="lab agent (default hermes)")
    ap.add_argument("--detectors", help="comma-separated subset of " + ",".join(D.KINDS))
    ap.add_argument("--bench-results", help=argparse.SUPPRESS)
    ap.add_argument("--lock", help=argparse.SUPPRESS)               # tests: another lock, registry and repository root
    ap.add_argument("--registry", help=argparse.SUPPRESS)
    ap.add_argument("--repo-root", help=argparse.SUPPRESS)
    ap.add_argument("--offline", action="store_true", help="no network: upstream and advisory checks are skipped")
    a = ap.parse_args(argv)
    cfg = {}
    if a.config:
        with open(os.path.expanduser(a.config), encoding="utf-8") as fh:
            cfg = json.load(fh)
    dflt = {"kernel": os.environ.get("JARVIS_ENDPOINT") or DEFAULT_KERNEL, "threshold": "medium", "max_tasks": 5, "agent": "hermes",
            "lock": str(REPO / "jarvis" / "tools" / "jarvis.lock.json"), "registry": str(REPO / "jarvis" / "tools" / "registry.json"),
            "repo_root": str(REPO)}
    for k in ("machine", "root", "log_dir", "kernel", "token_file", "repo_url", "threshold", "max_tasks", "agent", "detectors",
              "bench_results", "dry_run", "offline", "lock", "registry", "repo_root"):
        if getattr(a, k) in (None, False):
            setattr(a, k, cfg.get(k, dflt.get(k, getattr(a, k))))
    a.endpoints = cfg.get("endpoints")
    a.secret_scan_paths = tuple(cfg.get("secret_scan_paths") or D.SECRET_SCAN_PATHS)
    return a


def main(argv=None) -> int:
    args = parse(argv)
    try:
        summary = run(args)
    except RunError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    _print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
