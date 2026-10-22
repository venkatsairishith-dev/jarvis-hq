"""Episodes: what the main agent tried, and what happened. The ground floor of learning from use (2026-10-22).

One line per finished task in `episodes.jsonl` (owner-only folder next to the run files):
    when, source, mode, a fingerprint of the request, the SHAPE of the plan (agent kinds and plugin entries, in order), the outcome of each
    step as a class (success / failure / unknown / not_executed) with an ERROR CLASS (rate_limit, no_match, timeout ...), seconds, and
    whether the task asked a question or was held for an untrusted-content approval.

What is never stored: the request text, any step goal or argument, any result text, any error text. The fingerprint is a set of
salted hashes of the request's words (digits, e-mail-like and very long words dropped), so it can say "this request looks like that
one" but cannot be turned back into words. The salt lives in an owner-only file beside the log. Forget everything with
`forget_learning`; entries older than `max_age_days` or beyond `keep` lines are dropped on every write.

What it is used for:
  * hints(): before planning, the plan shapes that worked (or failed, and why) for SIMILAR earlier requests, as one short line for the
    planner. Shapes only, so nothing private from an old task can reach a cloud model through it, and the planner still has to
    produce a plan that parse_plan accepts against the live catalogue.
  * stats(): success rate per plan shape and per helper, the commonest error classes, whether the last runs are better than the ones
    before: the measurable side of "it improves with use".
  * (next stage) candidates for the desktop skill gate and bounded self-correction: see jarvis/docs/neko/SELF_IMPROVEMENT_2026-10-22.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

FILE = "episodes.jsonl"
SALT_FILE = "salt.key"
DAY = 86400.0
STOP_WORDS = frozenset("""a an the and or but if then so to of in on at by for with from as is are was were be been it its this that these those
i me my mine you your we our they them he she his her do does did can could will would should please just now open show tell give make get put
what when where who why how which there here about into out up down over under again also very more most some any each all not no yes ok okay hey""".split())
_ADDRESS = re.compile(r"\S+@\S+|(?:https?://|www\.)\S+")
_WORD = re.compile(r"[^\s.,;:!?\"'()\[\]{}<>/\\|=+*#~`^]+")

ERROR_PATTERNS = (                       # first match wins; the raw error text is never kept, only this class
    ("rate_limit", re.compile(r"ratelimit|rate limit|too many requests|\b429\b|\bbusy\b", re.I)),
    ("planner_format", re.compile(r"not valid json|did not return a plan|wrong shape|no steps list|wrong format", re.I)),
    ("no_helper", re.compile(r"none of the available helpers|cannot do that request", re.I)),
    ("needs_owner", re.compile(r"needs your approval or answer|waiting for you|needs the owner", re.I)),
    ("key", re.compile(r"api key|unauthorized|\b40[13]\b", re.I)),
    ("timeout", re.compile(r"timed out|did not answer in time|\btimeout\b", re.I)),
    ("no_match", re.compile(r"no single file|no window|not found|no match|nothing matches", re.I)),
    ("cannot_do", re.compile(r"cannot create|can't create|not supported|no such capability", re.I)),
    ("cancelled", re.compile(r"cancel|stopped", re.I)),
    ("unreachable", re.compile(r"not reachable|connection|refused|unavailable|did not answer", re.I)),
)


def error_class(text: Any) -> str:
    low = str(text or "")
    if not low.strip():
        return ""
    for name, pat in ERROR_PATTERNS:
        if pat.search(low):
            return name
    return "other"


def shape_of(step: Any) -> str:
    """One plan step as a coarse label: 'think', 'browser', 'computer', 'jarvis' or 'plugin:<plugin>:<entry>'. No goal, no arguments."""
    get = step.get if isinstance(step, dict) else (lambda k, d="": getattr(step, k, d))
    kind, ref = str(get("kind", "") or ""), str(get("ref", "") or "")
    return f"plugin:{ref}" if kind == "plugin" and ref else kind


class Episodes:
    def __init__(self, directory: str | os.PathLike, clock: Callable[[], float] = time.time, keep: int = 2000, max_age_days: float = 180.0):
        self.dir = Path(directory)
        self.clock, self.keep, self.max_age = clock, int(keep), float(max_age_days) * DAY
        self._lock = threading.Lock()

    # ---- the fingerprint -------------------------------------------------------------------------------------------------------------
    def _salt(self) -> bytes:
        path = self.dir / SALT_FILE
        try:
            return bytes.fromhex(path.read_text().strip())
        except (OSError, ValueError):
            self.dir.mkdir(parents=True, exist_ok=True)
            salt = secrets.token_bytes(16)
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(salt.hex())
            os.replace(tmp, path)
            return salt

    def tokens(self, text: str) -> list[str]:
        """Salted hashes of the request's content words. Digits, e-mail-like or very long words, and stop words are dropped first."""
        salt, out = self._salt(), set()
        text = _ADDRESS.sub(" ", str(text or "").lower())          # e-mail addresses and links go first, so no piece of one survives
        for raw in _WORD.findall(text):
            w = raw.strip("-_")
            if len(w) < 2 or len(w) > 24 or w in STOP_WORDS or "@" in w or any(c.isdigit() for c in w):
                continue
            out.add(hashlib.sha256(salt + w.encode("utf-8")).hexdigest()[:10])
        return sorted(out)

    # ---- the log ----------------------------------------------------------------------------------------------------------------------
    def _path(self) -> Path:
        return self.dir / FILE

    def all(self) -> list[dict[str, Any]]:
        rows = []
        try:
            with open(self._path(), encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue                                     # a half-written line is skipped, never fatal
                    if isinstance(row, dict) and isinstance(row.get("shape"), list):
                        rows.append(row)
        except OSError:
            pass
        return rows

    def record(self, *, run_id: str, task: str, source: str, mode: str, state: str, shape: list[str], steps: list[dict[str, str]],
               seconds: float, asked: bool = False, held_untrusted: bool = False, planning_error: str = "", attempt: int = 1) -> dict[str, Any]:
        tok = self.tokens(task)
        row = {"t": round(self.clock(), 1), "run": str(run_id)[:20], "src": str(source or "")[:12], "mode": str(mode or "")[:8],
               "tok": tok, "words": len(tok), "shape": [str(s)[:80] for s in shape][:12], "state": str(state)[:12],
               "steps": [{"ref": str(s.get("ref", ""))[:80], "out": str(s.get("out", ""))[:14], "err": str(s.get("err", ""))[:16]}
                         for s in steps][:12],
               "sec": round(float(seconds or 0.0), 1), "asked": bool(asked), "held_untrusted": bool(held_untrusted),
               "plan_err": error_class(planning_error), "attempt": int(attempt)}
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._prune_locked()
        return row

    def _prune_locked(self) -> None:
        rows = self.all()
        now = self.clock()
        kept = [r for r in rows if now - float(r.get("t", 0)) <= self.max_age][-self.keep:]
        if len(kept) != len(rows):
            tmp = self._path().with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in kept)
            os.replace(tmp, self._path())

    def forget(self) -> int:
        """Erase the whole log and the salt (a new salt is made on the next write). Returns how many episodes were removed."""
        with self._lock:
            n = len(self.all())
            for name in (FILE, SALT_FILE):
                try:
                    (self.dir / name).unlink()
                except OSError:
                    pass
            return n

    # ---- what it teaches --------------------------------------------------------------------------------------------------------------
    def similar(self, text: str, k: int = 5, min_shared: int = 2, min_score: float = 0.34) -> list[tuple[float, dict[str, Any]]]:
        mine = set(self.tokens(text))
        if len(mine) < min_shared:
            return []
        scored = []
        for row in self.all():
            theirs = set(row.get("tok") or [])
            shared = len(mine & theirs)
            if shared < min_shared:
                continue
            score = shared / len(mine | theirs)
            if score >= min_score:
                scored.append((score, row))
        scored.sort(key=lambda t: (t[0], t[1].get("t", 0)), reverse=True)
        return scored[:k]

    def hints(self, text: str, allowed: set[str]) -> str:
        """One short line for the planner: plan shapes that worked / failed for similar earlier requests. '' when there is nothing to say.
        Only shapes made of helpers that exist right now are mentioned; the line never contains words from any earlier request."""
        near = [r for _, r in self.similar(text)]
        if not near:
            return ""

        def usable(shape: list[str]) -> bool:
            return bool(shape) and all(s in allowed for s in shape)
        good = Counter(" > ".join(r["shape"]) for r in near if r.get("state") == "done" and usable(r["shape"]))
        bad: dict[str, Counter] = defaultdict(Counter)
        for r in near:
            if r.get("state") in ("failed", "partial") and r["shape"]:
                cls = next((s.get("err") for s in r.get("steps", []) if s.get("err")), "") or r.get("plan_err") or "other"
                bad[" > ".join(r["shape"])][cls] += 1
        parts = []
        if good:
            shape, n = good.most_common(1)[0]
            parts.append(f"for similar requests this plan shape succeeded {n} of {len(near)} times: {shape}")
        for shape, causes in sorted(bad.items(), key=lambda kv: -sum(kv[1].values()))[:1]:
            if shape not in good:
                parts.append(f"this shape failed {sum(causes.values())} times for similar requests ({causes.most_common(1)[0][0]}): {shape}")
        if not parts:
            return ""
        return ("Experience from this owner's earlier runs (plan shapes only, never their words; a preference, not a rule; the catalogue "
                "above is the only thing you may use): " + "; ".join(parts) + ".")

    def stats(self, now: float | None = None) -> dict[str, Any]:
        rows = self.all()
        now = now or self.clock()
        out: dict[str, Any] = {"episodes": len(rows)}
        if not rows:
            return out | {"success_rate": None, "by_shape": [], "by_helper": [], "errors": [], "trend": None, "weak": [],
                          "self_correction": {"tried": 0, "rescued": 0}}
        done = [r for r in rows if r.get("state") == "done"]
        out["success_rate"] = round(len(done) / len(rows), 3)
        out["median_seconds"] = sorted(r.get("sec", 0.0) for r in rows)[len(rows) // 2]
        shapes: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        helpers: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        errors: Counter = Counter()
        for r in rows:
            key = " > ".join(r["shape"]) or "(no plan)"
            shapes[key][0] += 1
            shapes[key][1] += r.get("state") == "done"
            if r.get("plan_err"):
                errors[r["plan_err"]] += 1
            for s in r.get("steps", []):
                helpers[s.get("ref", "")][0] += 1
                helpers[s.get("ref", "")][1] += s.get("out") == "success"
                if s.get("err"):
                    errors[s["err"]] += 1
        def rows_of(d):
            return sorted(({"name": k, "runs": v[0], "ok": v[1], "rate": round(v[1] / v[0], 2)} for k, v in d.items()),
                          key=lambda x: -x["runs"])[:12]
        out["by_shape"], out["by_helper"] = rows_of(shapes), rows_of(helpers)
        out["errors"] = [{"class": c, "count": n} for c, n in errors.most_common(6)]
        out["weak"] = [h for h in out["by_helper"] if h["runs"] >= 3 and h["rate"] < 0.5]
        last, before = rows[-20:], rows[-40:-20]
        def rate(rs):
            return round(sum(r.get("state") == "done" for r in rs) / len(rs), 2) if rs else None
        out["trend"] = {"last20": rate(last), "previous20": rate(before)} if before else None
        out["last_7_days"] = sum(1 for r in rows if now - float(r.get("t", 0)) <= 7 * DAY)
        second = [r for r in rows if r.get("attempt") == 2]            # tasks that got the one different plan after a definite failure
        out["self_correction"] = {"tried": len(second), "rescued": sum(1 for r in second if r.get("state") == "done")}
        return out
