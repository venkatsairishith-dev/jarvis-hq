"""Runs the fixed job kinds the kernel has authorized. Commands come ONLY from this machine's project allowlist
(config/projects.json); nothing in a job message is ever executed. Read-only git commands plus the project's own test command.

Running a project's tests executes that project's code, so only projects the owner listed can be checked, and the test run
gets an environment with secret-looking variables removed.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))
from jarvis_kernel.scrub import scrub  # noqa: E402
from .paths import jarvis_home  # noqa: E402
from . import sensitive  # noqa: E402

NOWIN = 0x08000000 if os.name == "nt" else 0       # no console window for helper programs
SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|^GH_|^GITHUB_|^GIT_ASKPASS|^GIT_CONFIG|^GIT_SSH|^NETRC", re.I)   # completion H: GH_CONFIG_DIR/GIT_CONFIG_* point at credentials too
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


def _porcelain(lines: str) -> list:
    """Changed paths from `git status --porcelain` (project-relative; the new name of a rename)."""
    out = []
    for ln in lines.splitlines():
        if len(ln) > 3 and ln.strip():
            p = ln[3:].split(" -> ")[-1].strip().strip('"')
            out.append(p)
    return out


def _run_tests(spec: dict, root: str, python: str, runner=None) -> dict:
    """The project's own test command (from the allowlist only) in `root`, with secret-looking variables removed.
    Returns {"ran", "exit_code", "seconds", "summary", "tail", "failed", "cmd"} or {"ran": False, "why"}."""
    t = spec.get("test")
    if not t:
        return {"ran": False, "why": "no test command configured"}
    runner = runner or _run
    cwd = os.path.join(root, t.get("cwd", "."))
    cmd = [python if c == "{python}" else c for c in t["cmd"]]
    shown = " ".join("python" if c == "{python}" else str(c) for c in t["cmd"])[:200]
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"      # a check should not leave compiled files behind
    t0 = time.time()
    try:
        r = runner(cmd, cwd=cwd, timeout=int(t.get("timeout_s", 600)), env=env)
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        code = r.returncode
    except subprocess.TimeoutExpired:
        return {"ran": True, "exit_code": 124, "seconds": int(t.get("timeout_s", 600)), "summary": "timed out", "tail": [], "failed": [],
                "cmd": shown}
    except OSError:
        return {"ran": False, "why": "the test command could not be started"}
    lines = [ln for ln in out.splitlines() if ln.strip()]
    m = re.search(r"Ran (\d+) tests? in ([\d.]+)s", out)
    final = next((ln for ln in reversed(lines) if re.match(r"^(OK|FAILED)", ln)), "")
    summary = (f"Ran {m.group(1)} tests in {m.group(2)}s. {final}".strip() if m else (lines[-1] if lines else ""))
    failed = [_clean(ln, 160) for ln in lines if re.match(r"^(FAIL|ERROR): |^FAILED \S|^\S+Error: ", ln)][:10]
    return {"ran": True, "exit_code": code, "seconds": round(time.time() - t0), "summary": _clean(summary, 160),
            "tail": [_clean(ln, 120) for ln in lines[-5:]], "failed": failed, "cmd": shown}


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
        changed = _porcelain(git("status", "--porcelain").stdout)
        dirty = len(changed)
        ab = git("rev-list", "--left-right", "--count", "@{u}...HEAD")
        behind, ahead = (int(x) for x in ab.stdout.split()) if ab.returncode == 0 and len(ab.stdout.split()) == 2 else (0, 0)
        commits = [_clean(ln, 80) for ln in git("log", "-3", "--pretty=%h %s").stdout.splitlines() if ln.strip()]
        head = git("rev-parse", "HEAD").stdout.strip()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"ok": False, "error": "git did not respond"}
    # head and the changed paths (2026-10-18, projectfix): which commit failed, and which files a fix really changed
    result = {"ok": True, "git": {"branch": _clean(branch, 60), "dirty_files": dirty, "ahead": ahead, "behind": behind, "last_commits": commits,
                                  "head": head if re.fullmatch(r"[0-9a-f]{40}", head) else "",
                                  "changed": [_clean(p, 160) for p in changed[:50]]}}
    clone = spec.get("lab_clone")
    if isinstance(clone, str) and clone.strip():              # where the lab may clone it from (projectfix), if the owner set it
        result["lab_clone"] = _clean(clone, 300)
    if not spec.get("test"):
        result["tests"] = {"ran": False, "why": "no test command configured"}
        return result
    tests = _run_tests(spec, path, python)
    if tests.get("ran") and tests.get("summary") != "timed out":
        try:
            dirty_after = len([ln for ln in git("status", "--porcelain").stdout.splitlines() if ln.strip()])
        except (OSError, subprocess.TimeoutExpired):
            dirty_after = dirty
        tests["files_changed_by_tests"] = max(0, dirty_after - dirty)
    result["tests"] = tests
    return result


# ---------------------------------------------------------------- project_apply (2026-10-18, projectfix)
APPLY_MAX_FILES = 5


def _sha_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def project_apply(p: dict, projects: dict, python: str = None, backup=None) -> dict:
    """Apply a fix the OWNER approved (YELLOW_APPROVAL, bound to the patch digest) to an allowlisted project.

    Confined to the project's root, existing text files only. Before anything in the owner's folder changes, the fix is
    proven in a scratch clone of the same commit: the tests fail without it, pass with it, and each changed file is needed
    (with that file's change left out, the tests still fail). Then each file is backed up (repair backups: undo with
    `repair restore <id>`), the patch is applied with git apply, and the result is compared with the proven copy. The
    patch came from the public lab, so running its code needs the owner's approval first: that is what this job is."""
    import shutil
    import tempfile
    from jarvis_kernel.projectfix import TEST_FILE, patch_files
    from . import repair, runtime
    python = python or sys.executable
    backup = backup or repair.backup
    name = str(p.get("project", ""))
    spec = projects.get(name) if SAFE_NAME.match(name) else None
    if not spec:
        return {"ok": False, "error": "project is not in this machine's allowlist"}
    path = (spec.get("path") or {}).get(os.name)
    if not path or not os.path.isdir(path):
        return {"ok": False, "error": "project folder not found on this machine"}
    patch, base, files = p.get("patch"), str(p.get("base", "")), p.get("files")
    if not isinstance(patch, str) or hashlib.sha256(patch.encode()).hexdigest() != p.get("digest"):
        return {"ok": False, "error": "the patch does not match the digest the owner approved; nothing was changed"}
    parsed, why = patch_files(patch)
    if why or not isinstance(files, list) or sorted(parsed) != sorted(files) or len(parsed) > APPLY_MAX_FILES:
        return {"ok": False, "error": f"refused: {why or 'the patch changes other files than the approved list'}; nothing was changed"}
    if any(TEST_FILE.search(f) for f in parsed):
        return {"ok": False, "error": "refused: a fix may not change the tests; nothing was changed"}
    if not spec.get("test"):
        return {"ok": False, "error": "no test command configured, so the fix cannot be proven; nothing was changed"}
    root = os.path.realpath(path)
    for f in parsed:                                   # inside the project, an existing regular file, no link on the way
        full = os.path.join(root, *f.split("/"))
        if os.path.realpath(full) != os.path.normpath(full) or not os.path.realpath(full).startswith(root + os.sep) \
                or not os.path.isfile(full) or os.path.islink(full):
            return {"ok": False, "error": f"refused: {f} is not an existing file inside the project; nothing was changed"}

    def run(args, cwd=None, timeout=120, env=None):
        return runtime.run(args, timeout=timeout, env=env, cwd=cwd)

    def git(where, *a, t=120):
        return run(["git", "-C", where, *a], timeout=t)
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    if head != base:
        return {"ok": False, "error": f"the project moved since the fix was made (now at {head[:12]}, the fix is for {base[:12]}); "
                                      "nothing was changed"}
    mine = sorted(set(parsed) & set(_porcelain(git(root, "status", "--porcelain").stdout)))
    if mine:
        return {"ok": False, "error": f"you have uncommitted changes in {', '.join(mine)}; nothing was changed"}
    tmp = tempfile.mkdtemp(prefix="jarvis-fix-")
    trial = {"needed": [], "not_needed": []}
    try:
        pf = os.path.join(tmp, "fix.patch")
        with open(pf, "w", encoding="utf-8", newline="") as fh:
            fh.write(patch)
        clone = os.path.join(tmp, "copy")
        if run(["git", "clone", "-q", "--no-hardlinks", root, clone], timeout=300).returncode != 0 \
                or git(clone, "checkout", "-q", base).returncode != 0:
            return {"ok": False, "error": "a scratch copy of the project could not be made; nothing was changed"}
        before = _run_tests(spec, clone, python, runner=run)
        trial["without_fix"] = {k: before.get(k) for k in ("exit_code", "summary")}
        if before.get("ran") and before.get("exit_code") == 0:
            return {"ok": False, "trial": trial, "error": "at the last commit the tests already pass in a clean copy: the failure comes "
                                                        "from something not committed, which the fix does not address; nothing was changed"}
        if git(clone, "apply", "--check", pf).returncode != 0 or git(clone, "apply", pf).returncode != 0:
            return {"ok": False, "trial": trial, "error": "the patch does not apply to the project's last commit; nothing was changed"}
        with_fix = _run_tests(spec, clone, python, runner=run)
        trial["with_fix"] = {k: with_fix.get(k) for k in ("exit_code", "summary", "tail")}
        if not (with_fix.get("ran") and with_fix.get("exit_code") == 0):
            return {"ok": False, "trial": trial, "error": f"the fix does not make the tests pass (in a scratch copy: exit code "
                                                        f"{with_fix.get('exit_code')}; {with_fix.get('summary', '')}); nothing in your project was changed"}
        proven = {f: _sha_file(os.path.join(clone, *f.split("/"))) for f in parsed}
        for f in parsed:                               # each changed file is needed: without its change the tests still fail
            if len(parsed) == 1:
                trial["needed"].append(f)
                break
            git(clone, "checkout", "-q", "--", ".")
            if git(clone, "apply", f"--exclude={f}", pf).returncode != 0:
                trial["needed"].append(f)
                continue
            r = _run_tests(spec, clone, python, runner=run)
            (trial["not_needed"] if r.get("ran") and r.get("exit_code") == 0 else trial["needed"]).append(f)
        if trial["not_needed"]:
            return {"ok": False, "trial": trial, "error": f"refused: the tests pass without the change to {', '.join(trial['not_needed'])}, "
                                                        "so it is not part of the fix (only the files needed may change); nothing in your project was changed"}
        runtime.check()
        backups = [backup(os.path.join(root, *f.split("/")), f"project_apply {name} {p.get('digest', '')[:12]}") for f in parsed]
        undo = [f"repair restore {b}" for b in backups]
        if git(root, "apply", "--check", pf).returncode != 0 or git(root, "apply", pf).returncode != 0:
            return {"ok": False, "trial": trial, "backups": backups, "undo": undo,
                    "error": "git apply refused the patch in your project; git applies all or nothing, so nothing was changed"}
        now = {f: _sha_file(os.path.join(root, *f.split("/"))) for f in parsed}
        ok = now == proven
        return {"ok": ok, "applied": parsed, "backups": backups, "undo": undo, "trial": trial, "verified_on_disk": ok,
                "error": None if ok else "the files on disk differ from the proven copy after applying; undo with the backups"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


OUTPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}\.(md|txt|html|json|csv|log|py)$")   # same rule as the kernel
TEXT_VIEWER = {".md", ".txt", ".json", ".csv", ".log", ".py"}


def outputs_root() -> str:
    """D:\\jarvis\\outputs on the laptop (JARVIS_OUTPUTS overrides, e.g. in tests)."""
    return os.path.realpath(os.environ.get("JARVIS_OUTPUTS") or os.path.join(jarvis_home(), "outputs"))


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


def ui_action(p: dict, backend=None, lister=None) -> dict:
    """A PC action the owner approved (the kernel consumed the single-use approval before queueing this job).
    The Windows backend itself only launches allow-listed apps and refuses password fields. Never on a password manager,
    wallet, bank, broker or payment window (2026-10-18): the kernel only sees the target "process|element", so the node
    also looks at the titles of that process's windows here (a Chrome window titled "ICICI Bank" is refused)."""
    if backend is None:
        from .winbackend import BACKEND as backend
    op = str(p.get("op", ""))
    if op not in backend:
        return {"ok": False, "error": "unsupported PC action"}
    target, text = str(p.get("target", "")), str(p.get("text", ""))
    media = op == "key" and text.strip().lower() in _media_keys()      # a media key goes to whatever plays, not to a window
    why = sensitive.why(target)
    if not why and op != "launch" and not media:
        why = sensitive.sensitive_window(target.partition("|")[0], lister)
    if why:
        return {"ok": False, "error": "refused: that window belongs to a password manager, wallet, bank, broker or payment app"}
    from . import ownwin
    mine = ownwin.refuse_close(op, target, text, lister)                 # D74: JARVIS's own face / chat / console window is never closed by an action
    if mine:
        return {"ok": False, "error": mine}
    from . import savefile
    stuck = savefile.refuse_replace_answer(op, target, text, lister)     # D54: Yes on a Replace prompt is never JARVIS's to press
    if stuck:
        return {"ok": False, "error": stuck}
    try:
        seen = backend[op](str(p.get("target", "")), {"text": str(p.get("text", ""))} if p.get("text") else {})
    except savefile.SafeError as e:                         # a refusal worded by savefile.py itself (never screen text): reported as it is
        return {"ok": False, "error": e.public, **{k: v for k, v in e.extra.items() if isinstance(v, (str, bool)) and len(str(v)) <= 80}}
    except Exception as e:      # the kind of failure only: its text could contain screen content
        out = {"ok": False, "error": type(e).__name__}
        code = str(e)[len("ui-action-failed:"):] if isinstance(e, RuntimeError) and str(e).startswith("ui-action-failed:") else ""
        if code and len(code) <= 60 and code.replace(" ", "").isalpha():          # D65: the FIXED reason code of our own scripts ("no element", "no window", "target is not the foreground window"), never screen text
            out["reason"] = code
        return out
    # What the node itself checked before it said ok (audit PC-D11): a backend that read the result back (the typed text,
    # the foreground window, the window state) returns it; a click, a key press or a scroll has nothing to read, so the
    # plugin must call those "reported ok, not observed". Only short fixed keys are passed on, never screen content.
    obs = {k: v for k, v in (seen if isinstance(seen, dict) else {}).items()
           if k in ("observed", "window_state", "foreground", "saved", "folder", "file_ok", "title_ok", "content_ok", "renamed", "closed")
           and isinstance(v, (str, bool)) and len(str(v)) <= 80}
    return {"ok": True, "done": op, **obs}


# Window identity for "did a NEW window appear?" (audit PC-D1, 2026-10-18). Raw handles are not sent to the kernel; each
# window gets an opaque id instead: a keyed hash of its handle and process id. The key lives only in this node process,
# so an id is stable for as long as the window and this process live, and id_epoch names that process: ids from two
# different epochs (the node restarted in between) must not be compared.
_WINDOW_ID_KEY = secrets.token_bytes(16)
WINDOW_ID_EPOCH = secrets.token_hex(6)
MAX_WINDOW_IDS = 400


def window_id(w: dict) -> str | None:
    try:
        hwnd, pid = int(w.get("hwnd")), int(w.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if hwnd <= 0:
        return None
    return "w" + hmac.new(_WINDOW_ID_KEY, f"{hwnd}:{pid}".encode(), hashlib.sha256).hexdigest()[:16]


def ui_windows(p: dict, lister=None, input_state=None) -> dict:
    """The visible top-level windows right now (titles scrubbed): what is actually on screen. Each described window has
    its id; `ids` lists the ids of all windows (not only the first 40 described), complete only when every window had one."""
    if lister is None:
        from .windows import list_windows as lister
    ws = lister()
    ids = [window_id(w) for w in ws]
    out = {"ok": True, "count": len(ws), "id_epoch": WINDOW_ID_EPOCH,
           "ids": [i for i in ids if i][:MAX_WINDOW_IDS], "ids_complete": all(ids) and len(ids) <= MAX_WINDOW_IDS,
           "windows": [dict({k: w[k] for k in ("title", "process", "foreground", "minimized", "w", "h")}, id=ids[n],
                            **{k: bool(w[k]) for k in ("hung", "cloaked") if k in w})        # D65: "(Not Responding)" and "on another virtual desktop"
                       for n, w in enumerate(ws[:40])]}
    try:                                                                     # D65: has the OWNER touched the keyboard or mouse (the plugin compares it with its own last action)
        if input_state is None:
            from .windows import input_state
        st = input_state() or {}
    except Exception:  # noqa: BLE001 - a missing reading is "not known", never a failed listing
        st = {}
    if isinstance(st.get("idle_ms"), int):
        out["idle_ms"] = st["idle_ms"]
    if isinstance(st.get("cursor"), (list, tuple)) and len(st["cursor"]) == 2:
        out["cursor"] = [int(st["cursor"][0]), int(st["cursor"][1])]
    return out


SENSITIVE_PROC = sensitive.FLOOR          # the node's floor; the check itself is sensitive.why (the kernel's list as well)


def _media_keys():
    try:
        from .winbackend import MEDIA_KEYS
        return MEDIA_KEYS
    except Exception:
        return {}


def ui_snapshot(p: dict, snap=None) -> dict:
    """The accessibility tree of one window (the named process, or the foreground window): text only, scrubbed."""
    if snap is None:
        from .ui import snapshot as snap
    target = str(p.get("target", ""))
    if not re.match(r"^[A-Za-z0-9_.-]{0,40}$", target) or sensitive.why(target):
        return {"ok": False, "error": "refused: bad or sensitive process name"}
    try:
        s = snap(target, 60, 4, False)
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}
    if s.get("error"):
        return {"ok": False, "error": str(s["error"])[:60]}
    if sensitive.why(str(s.get("process", "")), str(s.get("window", ""))):
        return {"ok": False, "error": "refused: that window belongs to a password manager, wallet or bank"}
    return {"ok": True, "window": s.get("window"), "process": s.get("process"), "count": len(s.get("elements") or []),
            "truncated": s.get("truncated"), "elements": [{k: e.get(k) for k in ("depth", "type", "name", "id", "cls", "enabled")}
                                                         for e in (s.get("elements") or [])[:60]]}


def screenshot(p: dict, root: str = None, shooter=None) -> dict:
    """A real picture of the desktop, written under outputs/screens/. The picture itself never goes into the result."""
    if shooter is None:
        from .windows import screenshot_png as shooter
    folder = os.path.join(root or outputs_root(), "screens")
    path = os.path.join(folder, time.strftime("screen-%Y%m%d-%H%M%S") + ".png")
    try:
        info = shooter(path)
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}
    return {"ok": True, "saved": os.path.basename(path), "folder": folder, **info}


def _verify_open(result: dict, params: dict, lister=None, before=None, timeout=6.0, path=None, locations=None) -> dict:
    """After an open action reports success, look at the desktop: did a window for it really appear? The answer is added
    to the result as `verified` (a window matched), `unconfirmed` (opened, but nothing matched in time) or `skipped`."""
    if not result.get("ok") or params.get("op") == "list_folder":
        return result
    from . import windows
    if lister is None:
        if not windows.ON_WINDOWS:
            return dict(result, verified="skipped")
        lister = windows.list_windows
    shown = str(result.get("opened") or "")
    want = os.path.basename(shown.replace("\\", "/").rstrip("/")) or shown
    if params.get("op") == "open_file":
        want = os.path.splitext(want)[0]
    before = before or set()

    def match(x):
        named = windows.titles_match(want, x["title"]) or windows.titles_match(want, x.get("process", ""))
        # a window that was already open counts only if it is in front now: Explorer re-uses and raises an open folder
        return named and (x["hwnd"] not in before or bool(x.get("foreground")))
    w = windows.wait_for(match, timeout=timeout, lister=lister)
    if not w and path and params.get("op") == "open_folder":
        # Explorer re-uses a window that already shows the folder and may not raise it (focus rules): that window IS the open folder
        try:
            shown_now = (locations or windows.explorer_locations)()
        except Exception:
            shown_now = []
        if windows.normalize_path(path) in shown_now:
            return dict(result, verified="verified", note="a File Explorer window already showing the folder is open")
    if w:
        out = dict(result, verified="verified", window={"title": w["title"], "process": w.get("process")})
        if w["hwnd"] in before:
            out["note"] = "the window was already open and is now in front"
        return out
    return dict(result, verified="unconfirmed", note=f"opened, but no window titled like '{want[:40]}' appeared within {int(timeout)} s")


def run_job(job: dict, projects: dict = None) -> dict:
    """Run one job. Unknown kinds are refused. While it runs, runtime knows its id, so the owner can cancel it: every
    process it started is killed and the result says cancelled (2026-10-16)."""
    from . import runtime
    runtime.begin(job.get("id"))
    try:
        runtime.check()                       # cancelled between being handed over and starting: no side effect at all
        from . import journal
        result = journal.around(job, lambda: _run_job(job, projects))      # D61: a before/after picture of the one window a changing job acts on
    except runtime.Cancelled:
        result = {}
    finally:
        was_cancelled = runtime.cancelled()
        runtime.end()
    if was_cancelled:
        return {"ok": False, "cancelled": True, "error": "cancelled by the owner; everything it had started was stopped",
                "partial": {k: v for k, v in (result or {}).items() if k in ("backup", "changed", "opened")}}
    return result


def _run_job(job: dict, projects: dict = None) -> dict:
    kind, params = job.get("kind"), job.get("params") or {}
    try:
        if kind == "project_check":
            projects = projects if projects is not None else load_projects()
            return project_check(str(params.get("project", "")), projects)
        if kind == "project_apply":              # an owner-approved fix to an allowlisted project (projectfix)
            projects = projects if projects is not None else load_projects()
            return project_apply(params, projects)
        if kind == "output_save":
            return output_save(params)
        if kind == "output_show":
            return output_show(params)
        if kind == "ui_action":
            return ui_action(params)
        if kind == "pc_open":
            from .pcopen import pc_open
            before = None
            if params.get("op") in ("open_app", "open_folder", "open_file", "open_code"):
                try:
                    from . import windows
                    before = {w["hwnd"] for w in windows.list_windows()}
                except Exception:
                    before = None
            from . import windowpick
            windowpick.take_launched()                      # nothing left over from an earlier job
            out = pc_open(params)
            from .pcopen import take_path
            res = _verify_open(out, params, before=before, path=take_path())
            launched = windowpick.take_launched()
            if launched and res.get("ok") and res.get("verified") == "verified" and not res.get("note"):
                res["launched"] = launched[:3]          # D61: the window(s) this job opened (process, pid): the journal's undo closes exactly those
            return res
        if kind == "pc_undo":                    # D61: one journaled step reversed; the node re-checks it (undo.py)
            from .undo import run as undo_run
            return undo_run(params)
        if kind in ("file_op", "setting", "clipboard"):       # D54: simple tasks (taskops.py): the kernel authorized exactly this job
            from .taskops import run as task_run
            return task_run(kind, params)
        if kind == "ui_windows":
            return ui_windows(params)
        if kind == "ui_snapshot":
            return ui_snapshot(params)
        if kind == "screenshot":
            return screenshot(params)
        if kind == "connector_op":               # a local MCP server the owner allowed on this machine (connectors.py)
            from .connectors import handle
            return handle(params)
        if kind == "browser_ext":                # the owner's existing Chrome profiles (browserext.py)
            from .browserext import run as browser_run
            return browser_run(params)
        if kind == "diag":                       # read-only checks (diag.py)
            from .diag import run as diag_run
            return diag_run(params)
        if kind == "repair":                     # approved repairs, backup first (repair.py)
            from .repair import run as repair_run
            return repair_run(params)
        if kind == "media_state":                # what Windows is playing (media.py)
            from .media import state
            return state(params)
        return {"ok": False, "error": "unknown job kind"}
    except Exception as e:   # report the kind of failure only
        from .runtime import Cancelled
        if isinstance(e, Cancelled):
            raise
        return {"ok": False, "error": type(e).__name__}
