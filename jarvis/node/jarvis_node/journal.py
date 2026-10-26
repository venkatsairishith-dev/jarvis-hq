r"""The flight recorder (D61): an append-only, local journal of what JARVIS did on this PC for the owner, so he can see it, trust it, undo it, and
debug a failure afterwards.

    D:\jarvis\data\journal\journal-2026-10-26.jsonl     one JSON object per line: a step, or the undo of one
    D:\jarvis\data\journal\shots\<job>-b.jpg / -a.jpg    the window a step acted on, before and after (about 800 px wide)
    D:\jarvis\data\journal\settings.json                 optional: {"journal_screenshots": false}

What a step line holds: id, ts, task_id, goal_hash + goal_label (the owner's own words, kept here), step_label, tool, class (the kernel's effect class: A
ordinary and local, B changes something that was there or a setting, C a delete, a repair or held for the PIN), target label (an app or site name, cleaned: never typed text), result, verified (verified |
unconfirmed | failed), duration_ms, shots {before, after} (picture names), undo (a recipe, `cannot_undo`, or null). What each picture cost (seconds and
bytes) is appended to shots\timing.jsonl, so the owner can see what the pictures add to a step. Nothing in the journal is ever sent to a
model or over the network by this module; the face's Timeline panel reads it through tools of the local tool server (jarvis/bridge/timeline.py),
and the chat model gets labels, counts and flags only.

Screenshots are the owner's wish ("see what it did"): ONE window (PrintWindow of the window an action acted on, never the desktop), scaled to
800 px, stored as JPEG next to the journal on D:, kept 7 days, capped at 500 MB (oldest first), never read by a model, and not taken at all for
sensitive targets (password managers, banks, wallets, payment apps, AI chat sites, secret paths) or when `journal_screenshots` is off
(environment JARVIS_JOURNAL_SCREENSHOTS=0, then settings.json, then [config] journal_screenshots in the desktop plugin's plugin.toml; default on).

Limits: the journal is capped at 50 MB (a day's file is closed at 8 MB and the oldest closed files go first), screenshots at 500 MB. Storage is
D: only: on Windows a journal folder that is not on D: switches the recorder off (and says why); off Windows it exists only when
JARVIS_JOURNAL_DIR names a folder (tests).

Standard library only. The node's own jobs call `around()` (screenshots); the desktop plugin writes the steps (plugins/desktop/journal_client.py).
"""
from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import secrets
import threading
import time
import tomllib
from pathlib import Path
from typing import Any, Callable, Iterator

SCHEMA = 1
JOURNAL_CAP_BYTES = 50 * 1024 * 1024
SHOTS_CAP_BYTES = 500 * 1024 * 1024
SHOT_KEEP_S = 7 * 86400
FILE_ROTATE_BYTES = 8 * 1024 * 1024
ENFORCE_EVERY_S = 300.0
SHOT_WIDTH = 800
SHOT_QUALITY = 70
SHOT_TIMEOUT_S = 12
SHOT_MAX_BYTES = 600 * 1024            # a larger picture is not stored (a 800 px JPEG is 40 to 150 KB)
FLAG = "journal_screenshots"
ENV_FLAG = "JARVIS_JOURNAL_SCREENSHOTS"
ENV_DIR = "JARVIS_JOURNAL_DIR"
PLUGIN_TOML = Path(__file__).resolve().parents[2] / "plugins" / "desktop" / "plugin.toml"
VERIFIED = ("verified", "unconfirmed", "failed")
UNDONE_STATUSES = ("undone", "partly", "already")
CLASSES = ("A", "B", "C")
FILE_RE = re.compile(r"^journal-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.jsonl$")
SHOT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,70}\.jpg$")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff]")

try:                                    # the existing D: rule (D50); a node that cannot import the supervisor keeps its own copy
    from jarvis.supervisor.budget import DATA_DIR as _BUDGET_DATA, is_on_d
except Exception:                       # noqa: BLE001
    _BUDGET_DATA = "D:\\jarvis\\data"

    def is_on_d(path) -> bool:
        s = str(path or "").strip().strip('"').replace("/", "\\")
        if s.startswith("\\\\?\\"):
            s = s[4:]
        if len(s) < 3 or s[1] != ":" or s[2] != "\\":
            return False
        n = ntpath.normpath(s)
        return n[:3].upper() == "D:\\" or n.upper() == "D:"


# ------------------------------------------------------------------------------------------------------------------ where, and whether
def default_root(env: dict | None = None, nt: bool | None = None) -> tuple[Path | None, str]:
    """(folder, "") for the journal, or (None, why not). D: only on Windows; elsewhere only an explicit JARVIS_JOURNAL_DIR."""
    env = os.environ if env is None else env
    nt = (os.name == "nt") if nt is None else nt
    explicit = str(env.get(ENV_DIR) or "").strip()
    if explicit:
        base = explicit
    else:
        data = str(env.get("JARVIS_DATA_DIR") or "").strip() or _BUDGET_DATA
        base = (ntpath.join(data, "journal") if nt else os.path.join(data, "journal"))
        if not nt:
            return None, "the journal is written on the Windows laptop only (set JARVIS_JOURNAL_DIR to try it elsewhere)"
    if nt and not is_on_d(base):
        return None, "the journal lives on D: only"
    return Path(base), ""


def screenshots_on(env: dict | None = None, root: Path | None = None, toml_path: Path | str | None = None) -> bool:
    """journal_screenshots: environment (0/1), then settings.json beside the journal, then plugin.toml [config], default ON."""
    env = os.environ if env is None else env
    raw = str(env.get(ENV_FLAG, "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    if root is not None:
        try:
            data = json.loads((Path(root) / "settings.json").read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and FLAG in data:
                return data[FLAG] is not False
        except (OSError, ValueError):
            pass
    try:
        data = tomllib.loads(Path(toml_path or PLUGIN_TOML).read_text(encoding="utf-8"))
        return (data.get("config") or {}).get(FLAG, True) is not False
    except (OSError, ValueError):
        return True


# ------------------------------------------------------------------------------------------------------------------------- small helpers
def text(value: Any, limit: int = 160) -> str:
    """One short plain line: no control or direction characters, no line breaks."""
    return " ".join(CONTROL.sub(" ", str(value if value is not None else "")).split())[:limit]


def goal_hash(goal: str) -> str:
    """A stable short hash of the owner's goal (so two runs of the same words group, and the words need not be compared)."""
    return hashlib.sha256(" ".join(str(goal or "").lower().split()).encode("utf-8", "replace")).hexdigest()[:16]


def new_id(ts: float | None = None) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(ts if ts is not None else time.time())) + "-" + secrets.token_hex(3)


def stamp(path: str) -> dict | None:
    """size and mtime of a file, and a short hash of both: what a later undo compares to prove the file is still the one that was recorded.
    A folder: the sum of its files' sizes, the newest mtime and the number of entries inside (at most 5000); the folder's own mtime does not count, so
    a new folder that had something put in it and taken out again is still the empty folder that was recorded. None when it does not exist."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    size, mtime, seen = st.st_size, st.st_mtime_ns, None
    if os.path.isdir(path) and not os.path.islink(path):
        size, mtime, seen = 0, 0, 0
        for base, dirs, files in os.walk(path):
            for name in dirs + files:
                seen += 1
                if seen > 5000:
                    break
                try:
                    s = os.lstat(os.path.join(base, name))
                except OSError:
                    continue
                mtime = max(mtime, s.st_mtime_ns)
                if name in files:
                    size += s.st_size
            if seen > 5000:
                break
    return {"size": size, "mtime_ns": mtime, "hash": hashlib.sha1(f"{size}:{mtime}".encode() + (b"" if seen is None else f":{seen}".encode())).hexdigest()[:16]}


def make_step(*, task_id: str, goal: str, step_label: str, tool: str, cls: str, target: str = "", result: str = "", verified: str = "failed",
              duration_ms: int = 0, shots: dict | None = None, undo: dict | None = None, ts: float | None = None, goal_label: str | None = None,
              extra: dict | None = None) -> dict:
    """A well-formed step line (not yet written). Every text is cut and cleaned here, whoever calls."""
    cls = cls if cls in CLASSES else "B"
    verified = verified if verified in VERIFIED else "failed"
    ts = time.time() if ts is None else float(ts)
    out = {"k": "step", "v": SCHEMA, "id": new_id(ts), "ts": round(ts, 3), "task_id": text(task_id, 60), "goal_hash": goal_hash(goal),
           "goal_label": text(goal if goal_label is None else goal_label, 90), "step_label": text(step_label, 90), "tool": text(tool, 40),
           "class": cls, "target": text(target, 80), "result": text(result, 200), "verified": verified,
           "duration_ms": max(0, int(duration_ms or 0)), "shots": shots or None, "undo": undo}
    if extra:
        out.update({k: v for k, v in extra.items() if k not in out})
    return out


# ------------------------------------------------------------------------------------------------------------------------- screenshots
class Shots:
    """The picture folder: names are `<job>-b.jpg` / `<job>-a.jpg`; retention 7 days, 500 MB, oldest first."""

    def __init__(self, folder: Path, clock: Callable[[], float] = time.time, cap: int = SHOTS_CAP_BYTES, keep_s: float = SHOT_KEEP_S):
        self.folder, self.clock, self.cap, self.keep_s = Path(folder), clock, cap, keep_s

    @staticmethod
    def ref(job: str, which: str) -> str:
        job = re.sub(r"[^A-Za-z0-9_-]", "", str(job))[:64]
        return f"{job}-{'b' if which == 'before' else 'a'}.jpg"

    def path(self, ref: str) -> Path | None:
        """The file for a ref, or None when the ref is not one of ours (no folders, no dots but the extension)."""
        return self.folder / ref if isinstance(ref, str) and SHOT_REF.match(ref) else None

    def save(self, ref: str, data: bytes) -> str | None:
        p = self.path(ref)
        if p is None or not data or len(data) > SHOT_MAX_BYTES or data[:3] != b"\xff\xd8\xff":
            return None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        except OSError:
            return None
        return ref

    def exists(self, ref: str) -> bool:
        p = self.path(ref)
        return bool(p and p.is_file())

    def read(self, ref: str) -> bytes | None:
        p = self.path(ref)
        try:
            return p.read_bytes() if p and p.is_file() else None
        except OSError:
            return None

    def purge(self) -> int:
        """Delete pictures older than the retention, then the oldest until the folder fits the cap. Returns how many went."""
        try:
            files = [(e.stat().st_mtime, e.stat().st_size, e.path) for e in os.scandir(self.folder) if e.is_file()]
        except OSError:
            return 0
        now, gone = self.clock(), 0
        keep = []
        for mtime, size, path in sorted(files):
            if now - mtime > self.keep_s or path.endswith(".part") and now - mtime > 3600:
                gone += _remove(path)
            else:
                keep.append((mtime, size, path))
        total = sum(s for _, s, _ in keep)
        for mtime, size, path in keep:                       # oldest first
            if total <= self.cap:
                break
            gone += _remove(path)
            total -= size
        return gone

    def total(self) -> int:
        try:
            return sum(e.stat().st_size for e in os.scandir(self.folder) if e.is_file())
        except OSError:
            return 0


def _remove(path: str) -> int:
    try:
        os.remove(path)
        return 1
    except OSError:
        return 0


# ---------------------------------------------------------------------------------------------------------------------------- the journal
class Journal:
    """The append-only store. `root` is the journal folder; `clock` and the caps are injectable for tests."""

    def __init__(self, root: Path | str, clock: Callable[[], float] = time.time, journal_cap: int = JOURNAL_CAP_BYTES, rotate_at: int = FILE_ROTATE_BYTES,
                 shots_cap: int = SHOTS_CAP_BYTES, shots_keep_s: float = SHOT_KEEP_S, env: dict | None = None):
        self.root = Path(root)
        self.clock, self.journal_cap, self.rotate_at = clock, journal_cap, rotate_at
        self.shots = Shots(self.root / "shots", clock, shots_cap, shots_keep_s)
        self.env = env
        self._lock = threading.RLock()
        self._last_enforce = 0.0
        self.listeners: list[Callable[[dict], None]] = []     # called after every append (the panel's long poll wakes up)

    # -- config
    def screenshots_on(self) -> bool:
        return screenshots_on(self.env, self.root)

    # -- writing
    def _day_file(self, ts: float) -> Path:
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        path = self.root / f"journal-{day}.jsonl"
        try:
            if path.stat().st_size >= self.rotate_at:
                n = 1
                while (self.root / f"journal-{day}.{n}.jsonl").exists():
                    n += 1
                os.replace(path, self.root / f"journal-{day}.{n}.jsonl")
        except OSError:
            pass
        return path

    def append(self, entry: dict) -> dict:
        """Write one line. The entry must already be made by `make_step` (or `make_undo`); an unusable one raises ValueError. Returns it."""
        if not isinstance(entry, dict) or entry.get("k") not in ("step",) or not entry.get("id"):
            raise ValueError("not a journal entry")
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self._day_file(float(entry.get("ts") or self.clock())), "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
            if self.clock() - self._last_enforce > ENFORCE_EVERY_S:
                self.enforce()
        for fn in list(self.listeners):
            try:
                fn(entry)
            except Exception:  # noqa: BLE001 - a listener never stops the recorder
                pass
        return entry

    # -- retention and caps
    def files(self) -> list[tuple[str, int, Path]]:
        """(sort key, size, path) of the journal files, oldest first."""
        out = []
        try:
            for e in os.scandir(self.root):
                m = FILE_RE.match(e.name)
                if m and e.is_file():
                    out.append(((m.group(1), int(m.group(2) or 10**9)), e.stat().st_size, Path(e.path)))   # the open day file (no number) sorts last
        except OSError:
            return []
        return [(f"{k[0]}.{k[1]:09d}", s, p) for k, s, p in sorted(out)]

    def enforce(self) -> dict:
        """Journal over its cap: the oldest closed files go first (the newest file is never removed). Pictures: 7 days, 500 MB."""
        with self._lock:
            self._last_enforce = self.clock()
            files = self.files()
            total = sum(s for _, s, _ in files)
            removed = 0
            for _, size, path in files[:-1]:
                if total <= self.journal_cap:
                    break
                removed += _remove(str(path))
                total -= size
            return {"journal_files_removed": removed, "journal_bytes": total, "shots_removed": self.shots.purge()}

    # -- reading
    def iter_lines(self) -> Iterator[dict]:
        """Every entry, newest first (a damaged line is skipped)."""
        for _, _, path in reversed(self.files()):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for raw in reversed(lines):
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("id") and d.get("k") == "step":
                    yield d

    def steps(self, limit: int = 400, since: float | None = None) -> list[dict]:
        """Newest first. Each step carries `undone` when a later undo entry names it ({id, ts, status}); an undo entry has `undo_of`."""
        out, undone, tried = [], {}, {}
        for d in self.iter_lines():
            if since is not None and float(d.get("ts") or 0) < since:
                break
            ref = d.get("undo_of")
            if ref:
                status = d.get("undo_status") or d.get("verified")
                seen = undone if status in UNDONE_STATUSES else tried
                seen.setdefault(ref, {"id": d["id"], "ts": d.get("ts"), "status": status})
            out.append(d)
            if len(out) >= limit:
                break
        for d in out:
            if d["id"] in undone:
                d["undone"] = undone[d["id"]]
            elif d["id"] in tried:
                d["undo_tried"] = tried[d["id"]]                    # an attempt that did not undo it (refused, failed): the step stays undoable
        return out

    def get(self, step_id: str) -> dict | None:
        want = str(step_id or "")
        for d in self.steps(limit=5000):
            if d["id"] == want:
                return d
        return None

    def tasks(self, limit_steps: int = 400, since: float | None = None) -> list[dict]:
        """Steps grouped by task, newest task first; each task: {task_id, goal_label, ts, last_ts, steps (newest first)}."""
        groups: dict[str, dict] = {}
        for d in self.steps(limit_steps, since):
            g = groups.setdefault(d.get("task_id") or d["id"], {"task_id": d.get("task_id") or d["id"], "goal_label": d.get("goal_label", ""),
                                                                 "last_ts": d.get("ts"), "ts": d.get("ts"), "steps": []})
            g["steps"].append(d)
            g["ts"] = d.get("ts")
            if d.get("goal_label"):
                g["goal_label"] = g["goal_label"] or d["goal_label"]
        return list(groups.values())

    def undoable(self, n: int = 1, task_id: str = "") -> list[dict]:
        """The newest `n` steps an undo may touch, newest first: steps that changed something (their `undo` is a recipe or `cannot_undo`),
        are not themselves an undo, and were not undone yet. `task_id` limits it to one task (and n = all of them)."""
        out = []
        for d in self.steps(limit=2000):
            if task_id and d.get("task_id") != task_id:
                continue
            if d.get("undo_of") or d.get("undone") or not d.get("undo"):
                continue
            out.append(d)
            if not task_id and len(out) >= max(1, int(n)):
                break
        return out

    def summary(self, since: float | None = None) -> dict:
        """Counts for a content-free answer: {steps, tasks, failed, unconfirmed, undoable, undone}."""
        steps = [d for d in self.steps(limit=2000, since=since)]
        real = [d for d in steps if not d.get("undo_of")]
        return {"steps": len(real), "tasks": len({d.get("task_id") for d in real}), "failed": sum(1 for d in real if d.get("verified") == "failed"),
                "unconfirmed": sum(1 for d in real if d.get("verified") == "unconfirmed"),
                "undoable": sum(1 for d in real if (d.get("undo") or {}).get("kind") not in (None, "cannot_undo") and not d.get("undone")),
                "undone": sum(1 for d in real if d.get("undone"))}


def make_undo_entry(target: dict, *, status: str, detail: str, task_id: str = "", duration_ms: int = 0, goal: str = "", ts: float | None = None) -> dict:
    """The journal entry for an undo (it is itself a step: tool `undo`, class B, `undo_of` the step it reversed). status: undone | partly |
    refused | failed | cannot | already."""
    ok = status in ("undone", "already")
    entry = make_step(task_id=task_id or target.get("task_id", ""), goal=goal or target.get("goal_label", ""), goal_label=target.get("goal_label", ""),
                      step_label="Undo: " + str(target.get("step_label", "a step")), tool="undo", cls="B", target=target.get("target", ""),
                      result=detail, verified="verified" if ok else ("unconfirmed" if status == "partly" else "failed"), duration_ms=duration_ms, ts=ts,
                      extra={"undo_of": target["id"], "undo_status": status})
    return entry


def public_view(step: dict) -> dict:
    """A step for the owner's panel: the line without anything a model could misuse (no recipe paths, no raw refs)."""
    return {k: step.get(k) for k in ("id", "ts", "task_id", "goal_label", "step_label", "tool", "class", "target", "result", "verified", "duration_ms",
                                      "undone", "undo_tried", "undo_of", "undo_status") if k in step}


# ---------------------------------------------------------------------------------------------------- screenshots taken by the node's own jobs
SHOT_SCRIPT = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public class PW { [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  public struct RECT { public int L, T, R, B; } }
'@
__PICK__
$h = $p.MainWindowHandle
if ([PW]::IsIconic($h)) { throw 'window is minimized' }
$r = New-Object PW+RECT; [void][PW]::GetWindowRect($h, [ref]$r)
$w = $r.R - $r.L; $hh = $r.B - $r.T
if ($w -le 0 -or $hh -le 0 -or $w -gt 8000 -or $hh -gt 8000) { throw 'bad window size' }
$bmp = New-Object System.Drawing.Bitmap $w, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
[void][PW]::PrintWindow($h, $dc, 2)               # PW_RENDERFULLCONTENT: this window's own pixels, even if covered; never the desktop
$g.ReleaseHdc($dc); $g.Dispose()
$tw = [Math]::Min($w, [int]$env:JARVIS_SHOT_W); $th = [Math]::Max(1, [int]($hh * $tw / $w))
$small = New-Object System.Drawing.Bitmap $tw, $th
$g2 = [System.Drawing.Graphics]::FromImage($small)
$g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g2.DrawImage($bmp, 0, 0, $tw, $th); $g2.Dispose(); $bmp.Dispose()
$enc = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' } | Select-Object -First 1
$ep = New-Object System.Drawing.Imaging.EncoderParameters 1
$ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality), ([long]$env:JARVIS_SHOT_Q)
$small.Save($env:JARVIS_SHOT, $enc, $ep); $small.Dispose()
@{ width = $tw; height = $th } | ConvertTo-Json -Compress
"""
SECRET_PATH = re.compile(r"(?i)(^|[\\/])\.env(\.|[\\/]|$)|jarvis-secrets|(^|[\\/])\.(ssh|aws|gnupg|kube)([\\/]|$)|credential|passw|secret|\.kdbx|\.pem$|\.pfx$|id_rsa|id_ed25519")
UI_SHOT_OPS = {"click", "type", "key", "scroll", "click_at", "focus", "minimize", "maximize", "restore", "launch"}
BROWSER_SHOT_OPS = {"open", "click", "type", "press", "play"}


def skip_reason(texts: list[str], windows_of: Callable[[str], str] | None = None, proc: str = "") -> str:
    """Why this target gets no picture ("" when it may): a password manager, wallet, bank, broker, payment app, AI chat site or secret path, in the
    names given or in any open window of the process."""
    from . import sensitive
    names = [t for t in texts if t]
    why = sensitive.sensitive_target(names) if names else ""
    if why:
        return "sensitive target"
    for t in names:
        if SECRET_PATH.search(t):
            return "secret path"
        if sensitive.ai_chat_site(t):
            return "ai chat site"
    if proc:
        w = (windows_of or sensitive.sensitive_window)(proc)
        if w:
            return "sensitive window"
    return ""


def plan_for_job(job: dict) -> dict | None:
    """What to photograph for this node job, or None: {'before': proc|None, 'after': proc|None, 'texts': [names for the sensitivity check]}.
    Only jobs that change something in a window; reads and file jobs get nothing."""
    kind, p = job.get("kind"), job.get("params") or {}
    if kind == "ui_action" and p.get("op") in UI_SHOT_OPS:
        proc = str(p.get("target", "")).partition("|")[0].strip()
        if not proc or proc.lower() == "media" or not re.fullmatch(r"[A-Za-z0-9_.\- ]{1,80}", proc):
            return None
        return {"before": None if p.get("op") == "launch" else proc, "after": proc, "texts": [str(p.get("target", "")).replace("|", " ")]}
    # D64: a browser-agent step (it carries a task id) takes no window picture: a 40-step flow would add two PowerShell runs to each click. The agent writes its own
    # content-free journal line per action (browser_agent.py), and its page pictures stay in the local-only folder.
    if kind == "browser_ext" and p.get("op") in BROWSER_SHOT_OPS and not p.get("task"):
        return {"before": None if p.get("op") == "open" else "chrome", "after": "chrome", "texts": [str(p.get("url", "")), str(p.get("profile", ""))]}
    if kind == "pc_open" and p.get("op") in ("open_app", "open_folder", "open_file"):
        return {"before": None, "after": "@result", "texts": [str(p.get("target", "")), str(p.get("path", ""))]}
    return None


def capture_window_jpeg(proc: str, path: str, runner: Callable | None = None, width: int = SHOT_WIDTH, quality: int = SHOT_QUALITY) -> tuple[bool, str]:
    """PrintWindow of the ONE window of `proc` (windowpick's rule: the one in front, else the newest; never a guess for a change), scaled to
    `width`, saved as JPEG at `path`. (True, "") or (False, why). `runner(script, env, timeout)` is the test seam."""
    if not re.fullmatch(r"[A-Za-z0-9_.\- ]{1,80}", proc or ""):
        return False, "bad target"
    from . import windowpick
    from .winbackend import PICK
    env = {"JARVIS_UI_PROC": proc, "JARVIS_SHOT": path, "JARVIS_SHOT_W": str(int(width)), "JARVIS_SHOT_Q": str(int(quality)),
           **windowpick.pick_env(proc, mutating=False)}
    if runner is None:
        from . import runtime
        runner = runtime.powershell
    r = runner(SHOT_SCRIPT.replace("__PICK__", PICK), env, SHOT_TIMEOUT_S)
    if getattr(r, "returncode", 1) or not os.path.isfile(path):
        err = getattr(r, "stderr", "") or ""
        return False, "no window" if "no window" in err else ("minimized" if "minimized" in err else "capture failed")
    return True, ""


class NodeRecorder:
    """Takes the before/after picture of the window a node job acts on. Never raises; a failed picture is a missing picture."""

    def __init__(self, journal: Journal, capture: Callable[[str, str], tuple[bool, str]] | None = None, skip: Callable[[list, str], str] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.j, self.clock = journal, clock
        self.capture = capture or (lambda proc, path: capture_window_jpeg(proc, path))
        self.skip = skip or (lambda texts, proc: skip_reason(texts, None, proc))

    def _shoot(self, job_id: str, which: str, proc: str, texts: list[str]) -> tuple[str | None, int]:
        t0 = self.clock()
        try:
            if self.skip(texts, proc):
                return None, 0
            ref = Shots.ref(job_id, which)
            tmp = self.j.shots.folder / (ref + ".tmp")
            self.j.shots.folder.mkdir(parents=True, exist_ok=True)
            ok, _why = self.capture(proc, str(tmp))
            if ok and tmp.is_file():
                data = tmp.read_bytes()
                _remove(str(tmp))
                saved = self.j.shots.save(ref, data)
                ms = int((self.clock() - t0) * 1000)
                self._time(job_id, which, ms, len(data) if saved else 0)
                return saved, ms
            _remove(str(tmp))
        except Exception as e:  # noqa: BLE001 - the action matters, the picture does not
            from . import runtime
            if isinstance(e, runtime.Cancelled):
                raise
        return None, int((self.clock() - t0) * 1000)

    def _time(self, job_id: str, which: str, ms: int, size: int) -> None:
        """One line per picture in shots\\timing.jsonl (job, which, ms, bytes); the file is trimmed to its last 2000 lines."""
        try:
            path = self.j.shots.folder / "timing.jsonl"
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"job": job_id, "which": which, "ms": ms, "bytes": size}) + "\n")
            if path.stat().st_size > 200_000:
                lines = path.read_text(encoding="utf-8").splitlines()[-2000:]
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def around(self, job: dict, run: Callable[[], dict]) -> dict:
        plan = plan_for_job(job)
        jid = str(job.get("id") or "")
        if not plan or not jid or not self.j.screenshots_on():
            return run()
        if plan["before"]:
            self._shoot(jid, "before", plan["before"], plan["texts"])
        result = run()
        after = plan["after"]
        if after == "@result":
            win = (result or {}).get("window") if isinstance(result, dict) else None
            after = (win or {}).get("process") if isinstance(win, dict) else None
        if after and isinstance(result, dict) and result.get("ok"):
            self._shoot(jid, "after", str(after), plan["texts"])
        return result


_NODE: dict[str, Any] = {"rec": None, "tried": False}


def node_recorder() -> NodeRecorder | None:
    """The node's recorder for this process (None when the journal is off here)."""
    if not _NODE["tried"]:
        _NODE["tried"] = True
        root, _why = default_root()
        if root is not None:
            _NODE["rec"] = NodeRecorder(Journal(root))
    return _NODE["rec"]


def around(job: dict, run: Callable[[], dict]) -> dict:
    """Run the node job `run()`; when the job changes something in a window and the journal may take pictures, take the before and after picture of that
    window around it. The picture files are named after the job id: the desktop plugin finds them from the job ids of a step."""
    rec = node_recorder()
    return rec.around(job, run) if rec is not None else run()
