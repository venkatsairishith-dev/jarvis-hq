"""Runs the fixed job kinds the kernel has authorized. Commands come ONLY from this machine's project allowlist
(config/projects.json); nothing in a job message is ever executed. Read-only git commands plus the project's own test command.

Running a project's tests executes that project's code, so only projects the owner listed can be checked, and the test run
gets an environment with secret-looking variables removed.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))
from jarvis_kernel.scrub import scrub  # noqa: E402

NOWIN = 0x08000000 if os.name == "nt" else 0       # no console window for helper programs
SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH", re.I)
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def load_projects(path=None) -> dict:
    path = path or os.path.join(os.path.dirname(__file__), "..", "..", "config", "projects.json")
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)["projects"]


def _run(args, cwd=None, timeout=30, env=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          stdin=subprocess.DEVNULL, creationflags=NOWIN, env=env)


def _clean(s: str, limit: int = 100) -> str:
    return scrub(s.strip(), deep=True)[0][:limit]


def project_check(name: str, projects: dict, python: str = None) -> dict:
    python = python or sys.executable
    spec = projects.get(name) if SAFE_NAME.match(name or "") else None
    if not spec:
        return {"ok": False, "error": "project is not in this machine's allowlist"}
    path = (spec.get("path") or {}).get(os.name)
    if not path or not os.path.isdir(path):
        return {"ok": False, "error": "project folder not found on this machine"}
    git = lambda *a, t=30: _run(["git", "-C", path, *a], timeout=t)
    try:
        if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            return {"ok": False, "error": "not a git repository"}
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "?"
        dirty = len([ln for ln in git("status", "--porcelain").stdout.splitlines() if ln.strip()])
        ab = git("rev-list", "--left-right", "--count", "@{u}...HEAD")
        behind, ahead = (int(x) for x in ab.stdout.split()) if ab.returncode == 0 and len(ab.stdout.split()) == 2 else (0, 0)
        commits = [_clean(ln, 80) for ln in git("log", "-3", "--pretty=%h %s").stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"ok": False, "error": "git did not respond"}
    result = {"ok": True, "git": {"branch": _clean(branch, 60), "dirty_files": dirty, "ahead": ahead, "behind": behind, "last_commits": commits}}
    t = spec.get("test")
    if not t:
        result["tests"] = {"ran": False, "why": "no test command configured"}
        return result
    cwd = os.path.join(path, t.get("cwd", "."))
    cmd = [python if c == "{python}" else c for c in t["cmd"]]
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"      # a check should not leave compiled files behind
    import time
    t0 = time.time()
    try:
        r = _run(cmd, cwd=cwd, timeout=int(t.get("timeout_s", 600)), env=env)
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        code = r.returncode
    except subprocess.TimeoutExpired:
        result["tests"] = {"ran": True, "exit_code": 124, "seconds": int(t.get("timeout_s", 600)), "summary": "timed out", "tail": []}
        return result
    except OSError:
        result["tests"] = {"ran": False, "why": "the test command could not be started"}
        return result
    try:
        dirty_after = len([ln for ln in git("status", "--porcelain").stdout.splitlines() if ln.strip()])
    except (OSError, subprocess.TimeoutExpired):
        dirty_after = dirty
    lines = [ln for ln in out.splitlines() if ln.strip()]
    m = re.search(r"Ran (\d+) tests? in ([\d.]+)s", out)
    final = next((ln for ln in reversed(lines) if re.match(r"^(OK|FAILED)", ln)), "")
    summary = (f"Ran {m.group(1)} tests in {m.group(2)}s. {final}".strip() if m else (lines[-1] if lines else ""))
    result["tests"] = {"ran": True, "exit_code": code, "seconds": round(time.time() - t0), "summary": _clean(summary, 160),
                       "tail": [_clean(ln, 120) for ln in lines[-5:]], "files_changed_by_tests": max(0, dirty_after - dirty)}
    return result


OUTPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}\.(md|txt|html|json|csv|log|py)$")   # same rule as the kernel
TEXT_VIEWER = {".md", ".txt", ".json", ".csv", ".log", ".py"}


def outputs_root() -> str:
    """D:\\jarvis\\outputs on the laptop (JARVIS_OUTPUTS overrides, e.g. in tests)."""
    return os.path.realpath(os.environ.get("JARVIS_OUTPUTS") or os.path.join(os.path.dirname(__file__), "..", "..", "..", "outputs"))


def _output_path(agent: str, name: str, root: str = None):
    """The file inside outputs/<agent>/, or None if the names are unsafe or the path would leave that folder."""
    root = root or outputs_root()
    if not SAFE_NAME.match(agent or "") or not OUTPUT_NAME.match(name or "") or ".." in name:
        return None
    folder = os.path.realpath(os.path.join(root, agent))
    path = os.path.realpath(os.path.join(folder, name))
    if not path.startswith(folder + os.sep) or not folder.startswith(root + os.sep):
        return None
    return path


def output_save(p: dict, root: str = None) -> dict:
    path = _output_path(str(p.get("agent", "")), str(p.get("name", "")), root)
    content = p.get("content")
    if not path or not isinstance(content, str) or len(content) > 200_000:
        return {"ok": False, "error": "refused: bad name or content"}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    base, ext = os.path.splitext(path)
    final, n = path, 2
    while os.path.exists(final):            # never overwrite an earlier output
        final, n = f"{base} ({n}){ext}", n + 1
    with open(final, "x", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return {"ok": True, "saved": os.path.basename(final), "folder": os.path.dirname(final), "chars": len(content)}


def output_show(p: dict, root: str = None, opener=None) -> dict:
    path = _output_path(str(p.get("agent", "")), str(p.get("name", "")), root)
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": "no such saved output"}
    if opener is None:
        def opener(fp):
            if os.path.splitext(fp)[1].lower() in TEXT_VIEWER:
                subprocess.Popen(["notepad.exe", fp], creationflags=0)    # a normal window on the owner's desktop
            else:
                os.startfile(fp)                                          # .html: the default browser
    opener(path)
    return {"ok": True, "shown": os.path.basename(path)}


def ui_action(p: dict, backend=None) -> dict:
    """A PC action the owner approved (the kernel consumed the single-use approval before queueing this job).
    The Windows backend itself only launches allow-listed apps and refuses password fields."""
    if backend is None:
        from .winbackend import BACKEND as backend
    op = str(p.get("op", ""))
    if op not in backend:
        return {"ok": False, "error": "unsupported PC action"}
    try:
        backend[op](str(p.get("target", "")), {"text": str(p.get("text", ""))} if p.get("text") else {})
    except Exception as e:      # the kind of failure only: its text could contain screen content
        return {"ok": False, "error": type(e).__name__}
    return {"ok": True, "done": op}


def run_job(job: dict, projects: dict = None) -> dict:
    """Run one job. Unknown kinds are refused."""
    kind, params = job.get("kind"), job.get("params") or {}
    try:
        if kind == "project_check":
            projects = projects if projects is not None else load_projects()
            return project_check(str(params.get("project", "")), projects)
        if kind == "output_save":
            return output_save(params)
        if kind == "output_show":
            return output_show(params)
        if kind == "ui_action":
            return ui_action(params)
        if kind == "pc_open":
            from .pcopen import pc_open
            return pc_open(params)
        return {"ok": False, "error": "unknown job kind"}
    except Exception as e:   # report the kind of failure only
        return {"ok": False, "error": type(e).__name__}
