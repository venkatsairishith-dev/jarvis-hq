"""Push gate (plan P6 step 8, item 15): the checklist the KERNEL runs before agent work may move a protected branch (main).

Agents never hold the credential that moves main. They push work branches; the kernel evaluates the candidate here and, only
when every check passes and the owner has switched the gate on, fast-forwards main with its own identity. Otherwise the work
waits as a pull request for the owner. This module only evaluates and plans: it reads the repository with git, writes nothing,
and holds no credential. The push itself and the smoke-test-and-revert step are separate and owner-enabled.

Checks (all must pass):
  kill_switch    the kernel's kill switch is off
  enabled        the owner switched the gate on for this repository (config, console only)
  fast_forward   the candidate is a descendant of the protected branch (no rewrites, no merges of unrelated history)
  size           at most MAX_LINES changed lines and MAX_FILES files in the whole range
  protected      nothing under tests, fixtures, thresholds or baselines, CI, agent rules, MCP config, the kernel's safety
                 files or secret-looking paths changed, unless the owner approved exactly this range at RED with the PIN
  trailer        every commit in the range carries "Agent-Run: <id>"
  tests          the full required suite passed on this exact commit, run by the kernel (not reported by the agent)
  daily_cap      fewer than DAILY_CAP gate merges today for this repository
  one_at_a_time  no other gate merge is in progress for this repository

Provenance: the rules follow GitHub's guidance for its coding agent (it can push only its own branches and cannot merge its own
pull requests) and the s1ngularity incident (installed AI CLIs harvested more than 1,000 GitHub tokens), per the plan's item 15.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MAX_LINES = 300
MAX_FILES = 10
DAILY_CAP = 3
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TRAILER_RE = re.compile(r"^Agent-Run:\s*[A-Za-z0-9._:-]{4,80}\s*$", re.M)
PROTECTED = re.compile(
    r"(^|/)(tests?|__tests__|spec|fixtures?|testdata)(/|$)|(^|/)test_[^/]*\.py$|_test\.py$|\.(spec|test)\.[jt]sx?$"
    r"|(^|/)conftest\.py$|threshold|baseline|(^|/)\.github(/|$)|(^|/)\.claude(/|$)|(^|/)claude\.md$|(^|/)agents\.md$"
    r"|(^|/)\.mcp\.json$|(^|/)hooks\.json$|(^|/)settings(\.local)?\.json$|(^|/)\.gitattributes$|(^|/)\.gitmodules$"
    r"|(^|/)(policy|audit|store|push_gate|gitleaks_gate|llm_proxy|projectfix|cdp_relay|adminctl|sitegrants)\.py$"
    r"|(^|/)(sensitive_sites|admin_verbs|providers|auto_click|specialists|simple_tasks|trusted_sites|identity)\.json$|(^|/)\.env|secret|credential|\.pem$|\.key$|(^|/)id_(rsa|ed25519)"
    r"|(^|/)config/(site_playbooks/|ppt_sites\.json|cookie_banners\.json)",
    re.I)
Git = Callable[..., str]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Verdict:
    repo: str
    branch: str
    base: str
    candidate: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def checklist(self) -> str:
        """The lines shown on screen and on the phone (no file contents)."""
        head = (f"Push gate for {self.repo} {self.branch}: {self.base[:8]} -> {self.candidate[:8]}: "
                + ("ALL CHECKS PASS" if self.ok else "BLOCKED, waits for the owner as a pull request"))
        return "\n".join([head] + [f"[{'x' if c.ok else ' '}] {c.name}: {c.detail}" for c in self.checks])


def make_git(repo: str | Path) -> Git:
    def git(*args: str) -> str:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed")
        return r.stdout
    return git


@dataclass
class GateState:
    """What the kernel knows outside the repository (all from the kernel's own records, never from the agent)."""
    killed: bool
    enabled: bool
    red_approved_range: str | None = None          # "base..candidate" the owner approved at RED, if any
    test_runs: dict[str, bool] = field(default_factory=dict)   # sha -> required suite passed (kernel-run)
    merges_today: int = 0
    in_progress: bool = False


def evaluate(repo: str, branch: str, candidate: str, state: GateState, git: Git) -> Verdict:
    v = Verdict(repo, branch, "", candidate)
    add = lambda name, ok, detail: v.checks.append(Check(name, bool(ok), detail))   # noqa: E731
    add("kill_switch", not state.killed, "off" if not state.killed else "the kill switch is on: nothing moves")
    add("enabled", state.enabled, "the owner switched the gate on" if state.enabled else "the gate is off for this repository (owner decides)")
    if not SHA_RE.match(candidate or ""):
        add("candidate", False, "the candidate must be a full commit id")
        return v
    try:
        base = git("rev-parse", f"refs/heads/{branch}").strip()
        v.base = base
        git("cat-file", "-e", f"{candidate}^{{commit}}")
        ff = subprocess_ok(git, "merge-base", "--is-ancestor", base, candidate)
        add("fast_forward", ff and base != candidate,
            "the candidate builds on the current branch" if ff and base != candidate else
            ("nothing new to move" if base == candidate else "not a fast-forward: history would be rewritten or merged"))
        if not ff or base == candidate:
            return v
        rng = f"{base}..{candidate}"
        numstat = [ln.split("\t") for ln in git("diff", "--numstat", "-M", rng).splitlines() if ln.strip()]
        lines = sum((int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0) for a, d, *_ in numstat)
        files = [p[-1] for p in numstat]
        add("size", lines <= MAX_LINES and len(files) <= MAX_FILES, f"{lines} changed lines in {len(files)} files "
            f"(limit {MAX_LINES} lines, {MAX_FILES} files)")
        names = git("diff", "--name-only", "--no-renames", rng).split("\n")      # old AND new paths of renames
        hits = sorted({n for n in names if n and PROTECTED.search(n)})
        red_ok = state.red_approved_range == rng
        add("protected", not hits or red_ok,
            "no tests, CI, agent rules, safety files or secrets changed" if not hits else
            (f"{len(hits)} protected path(s) changed, approved at RED for exactly this range" if red_ok else
             f"protected path(s) changed without the owner's RED approval: {', '.join(hits[:5])}"))
        commits = [c for c in git("rev-list", rng).split() if c]
        missing = [c[:8] for c in commits if not TRAILER_RE.search(git("log", "-1", "--format=%B", c))]
        add("trailer", not missing, f"all {len(commits)} commit(s) carry Agent-Run" if not missing else
            f"commit(s) without an Agent-Run trailer: {', '.join(missing[:5])}")
    except RuntimeError as e:
        add("repository", False, str(e))
        return v
    passed = state.test_runs.get(candidate)
    add("tests", passed is True, "the kernel ran the required suite on this exact commit and it passed" if passed is True else
        ("the kernel's run on this commit failed" if passed is False else "the kernel has not run the required suite on this commit"))
    add("daily_cap", state.merges_today < DAILY_CAP, f"{state.merges_today} of {DAILY_CAP} gate merges used today")
    add("one_at_a_time", not state.in_progress, "no other gate merge in progress" if not state.in_progress else "another gate merge is in progress")
    return v


def subprocess_ok(git: Git, *args: str) -> bool:
    try:
        git(*args)
        return True
    except RuntimeError:
        return False


class Ledger:
    """Gate merges, one JSON line each, for the daily cap and the in-progress lock (the kernel's data dir)."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.path, self.clock = Path(path), clock

    def _rows(self) -> list[dict]:
        try:
            return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]
        except (OSError, ValueError):
            return []

    def merges_today(self, repo: str) -> int:
        day = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        return sum(1 for r in self._rows() if r.get("repo") == repo and r.get("state") == "merged"
                   and time.strftime("%Y-%m-%d", time.localtime(r.get("at", 0))) == day)

    def in_progress(self, repo: str) -> bool:
        last: dict[str, str] = {}
        for r in self._rows():
            if r.get("repo") == repo:
                last[r.get("candidate", "")] = r.get("state", "")
        return any(s == "started" for s in last.values())

    def record(self, repo: str, candidate: str, state: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"repo": repo, "candidate": candidate, "state": state, "at": self.clock()}) + "\n")


def revert_plan(base: str, candidate: str) -> list[list[str]]:
    """The git steps the kernel runs if the smoke tests fail after a gate merge: revert the whole range in one commit."""
    return [["git", "revert", "--no-edit", "--no-commit", f"{base}..{candidate}"],
            ["git", "commit", "-m", f"Revert gate merge {base[:8]}..{candidate[:8]}: smoke tests failed\n\nAgent-Run: kernel-revert"]]
