"""JARVIS footprint meter (D50): how much of the laptop JARVIS uses, against the owner's budget, and what it keeps outside D:.

Owner, 2026-10-25: "JARVIS should use only 20% of the whole laptop, and for storage use only the D: drive."
Budget (jarvis/supervisor/budget.py, overridable by JARVIS_BUDGET_* variables or D:\\jarvis\\data\\budget.json):
CPU 20% of the whole machine averaged over a minute, RAM 6554 MB for all JARVIS processes together, GPU dedicated memory 2048 MB,
disk writes 5 MB/s (a guess), and nothing outside D:.

    python jarvis/tools/footprint.py                 one 5 s sample, a one-screen verdict (exit 1 when any metric is OVER)
    python jarvis/tools/footprint.py --json          the same as JSON (for the HUD and the doctor)
    python jarvis/tools/footprint.py --watch         repeat; CPU is averaged over the budget window; the latest report is written to
                                                     D:\\jarvis\\data\\budget\\footprint.json (--out FILE to change)
    python jarvis/tools/footprint.py --scan-only     only the JARVIS files outside D: (names and sizes, never contents)

Measuring needs nothing new: psutil when the venv has it (agent-venv does), otherwise PowerShell/CIM (Win32_Process, and the
"GPU Process Memory" performance counters for the video memory). JARVIS processes are found by their program path or command line
(D:\\jarvis, D:\\jarvis-apps, D:\\APPS\\jarvis; the face; llama-swap and llama-server; the Playwright MCP node process; cua-driver) plus
everything those start. This program and its own children are never counted. Read only: it changes nothing and reads no file contents.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from jarvis.supervisor import budget as B  # noqa: E402

IS_WINDOWS = sys.platform == "win32"
MB = 1024 * 1024
JARVIS_ROOTS = ("d:/jarvis/", "d:/jarvis-apps/", "d:/apps/jarvis/")
INTERPRETERS = {"python.exe", "pythonw.exe", "python", "python3", "node.exe", "node", "powershell.exe", "pwsh.exe", "cmd.exe",
                "uv.exe", "pnpm.exe", "npm.exe", "npx.exe", "bash", "sh"}
# Never killed, never suspended, never slowed by duty-cycling (owner rule: the face, the kernel client and the supervisor stay up).
PROTECTED = frozenset({"face", "kernel", "console", "supervisor", "governor", "node-agent"})
DEFAULT_OUT = B.DATA_DIR + "\\budget\\footprint.json"


# ---- raw process list ---------------------------------------------------------------------------------------------------------------

@dataclass
class Proc:
    pid: int
    ppid: int
    name: str
    exe: str = ""
    cmdline: str = ""
    ws: int = 0              # working set, bytes
    private: int = 0         # private bytes
    cpu_s: float = 0.0       # cumulative user + kernel seconds
    io_write: int = 0        # cumulative bytes written
    gpu: int | None = None   # GPU dedicated bytes, None = not readable


def _norm(s: str | None) -> str:
    return (s or "").replace("\\", "/").lower()


def _procs_psutil() -> list[Proc]:
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "ppid", "name", "exe", "cmdline"]):
        try:
            i = p.info
            mi = p.memory_info()
            ct = p.cpu_times()
            try:
                w = p.io_counters().write_bytes
            except (psutil.Error, AttributeError, OSError):
                w = 0
            out.append(Proc(i["pid"], i["ppid"] or 0, i["name"] or "", i["exe"] or "", " ".join(i["cmdline"] or []), mi.rss,
                            getattr(mi, "private", None) or mi.rss, ct.user + ct.system, w))
        except (psutil.Error, OSError):
            continue
    return out


PS_PROCS = ("$p = @(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,"
            "WorkingSetSize,PrivatePageCount,KernelModeTime,UserModeTime,WriteTransferCount); "
            "ConvertTo-Json -Compress -InputObject $p")


def _run_ps(script: str, timeout: float = 60.0) -> str:
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def parse_ps_procs(text: str) -> list[Proc]:
    """Win32_Process rows as JSON (times are 100 ns units)."""
    data = json.loads(text) if text.strip() else []
    if isinstance(data, dict):
        data = [data]
    return [Proc(int(d["ProcessId"]), int(d.get("ParentProcessId") or 0), d.get("Name") or "", d.get("ExecutablePath") or "",
                 d.get("CommandLine") or "", int(d.get("WorkingSetSize") or 0), int(d.get("PrivatePageCount") or 0),
                 (int(d.get("KernelModeTime") or 0) + int(d.get("UserModeTime") or 0)) / 1e7, int(d.get("WriteTransferCount") or 0))
            for d in data]


def _procs_powershell() -> list[Proc]:
    return parse_ps_procs(_run_ps(PS_PROCS))


PS_GPU = ("ConvertTo-Json -Compress -InputObject @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory "
          "-ErrorAction Stop | Select-Object Name,DedicatedUsage)")
GPU_NAME = re.compile(r"pid_(\d+)_", re.I)


def parse_gpu(text: str) -> dict[int, int]:
    """'GPU Process Memory' counters (Name like pid_1234_luid_0x..._phys_0, DedicatedUsage in bytes), summed per pid."""
    data = json.loads(text) if text.strip() else []
    if isinstance(data, dict):
        data = [data]
    out: dict[int, int] = {}
    for d in data:
        m = GPU_NAME.search(str(d.get("Name", "")))
        if m:
            out[int(m.group(1))] = out.get(int(m.group(1)), 0) + int(d.get("DedicatedUsage") or 0)
    return out


def _gpu_windows() -> dict[int, int] | None:
    try:
        return parse_gpu(_run_ps(PS_GPU, 30.0))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _total_ram_mb() -> int | None:
    try:
        import psutil
        return int(psutil.virtual_memory().total // MB)
    except Exception:
        return None


@dataclass
class Sampler:
    """Where the numbers come from. Tests pass fakes; the default reads this machine."""
    procs: Callable[[], list[Proc]] = field(default=lambda: _procs_psutil() if _have_psutil() else _procs_powershell())
    gpu: Callable[[], dict[int, int] | None] = field(default=lambda: _gpu_windows() if IS_WINDOWS else None)
    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    threads: Callable[[], int] = field(default=lambda: os.cpu_count() or B.MACHINE_THREADS)
    ram_mb: Callable[[], int | None] = _total_ram_mb
    own_pid: int = field(default_factory=os.getpid)


def _have_psutil() -> bool:
    try:
        import psutil  # noqa: F401
        return True
    except ImportError:
        return False


# ---- which processes are JARVIS -----------------------------------------------------------------------------------------------------

def _under_roots(text: str) -> bool:
    return any(r in text for r in JARVIS_ROOTS)


def classify(p: Proc) -> str | None:
    """The JARVIS component a process belongs to by itself (program path, name, command line), or None."""
    exe, cmd, name = _norm(p.exe), _norm(p.cmdline), p.name.lower()
    text = exe + " " + cmd
    if "jarvis/tools/footprint.py" in text:
        return None
    if "supervisor/core.py" in text or "jarvis.supervisor" in text:
        return "supervisor"
    if "jarvis/tools/governor.py" in text or "jarvis.tools.governor" in text:
        return "governor"
    if name.startswith("llama-swap"):
        return "llama-swap"
    if name.startswith("llama-server") and (_under_roots(exe) or "llama.cpp" in exe or not exe):
        return "llama-server"
    if name.startswith("cua-driver"):
        return "cua-driver"
    if (name == "jarvis.exe" and (_under_roots(exe) or not exe)) or exe.startswith("d:/apps/jarvis/") or \
            ("stage-tamagotchi" in cmd and "electron" in text):
        return "face"
    if "playwright" in text and "mcp" in text and (name.startswith("node") or name in INTERPRETERS):
        return "playwright-mcp"
    for needle, comp in (("jarvis_kernel", "kernel"), ("jarvis_console", "console"), ("jarvis_node", "node-agent"),
                         ("privacy/gateway.py", "privacy-gateway"), ("local_speech_server", "local-speech"),
                         ("bridge.face_tools", "face-tools"), ("bridge/face_tools", "face-tools"), ("muse.runner", "muse-runner"),
                         ("scripts/watchdog.py", "watchdog"), ("openjev", "verdict")):
        if needle in text:
            return comp
    if _under_roots(exe):
        return "jarvis-other"
    if name in INTERPRETERS and re.search(r"d:/(jarvis|jarvis-apps|apps/jarvis)/[^ ]*\.(py|ps1|js|mjs|cjs)\b", cmd):
        return "jarvis-other"
    return None


def jarvis_processes(procs: Iterable[Proc], own_pid: int) -> dict[int, str]:
    """pid -> component for every JARVIS process: matched by itself, or started by one that is. This program's own tree is excluded."""
    plist = list(procs)
    by_pid = {p.pid: p for p in plist}
    kids: dict[int, list[int]] = {}
    for p in plist:
        kids.setdefault(p.ppid, []).append(p.pid)
    mine: set[int] = set()
    stack = [own_pid]
    while stack:
        pid = stack.pop()
        if pid not in mine:
            mine.add(pid)
            stack.extend(kids.get(pid, []))
    found: dict[int, str] = {}
    for p in plist:
        if p.pid in mine or p.pid in (0, 4):
            continue
        c = classify(p)
        if c:
            found[p.pid] = c
    stack = list(found)
    while stack:                                   # children inherit the component of the JARVIS process that started them
        pid = stack.pop()
        for k in kids.get(pid, []):
            if k not in found and k not in mine and k in by_pid and k not in (0, 4):
                found[k] = found[pid]
                stack.append(k)
    return found


# ---- measuring ----------------------------------------------------------------------------------------------------------------------

@dataclass
class Snapshot:
    t: float
    procs: dict[int, Proc]          # JARVIS processes only
    comp: dict[int, str]
    gpu: dict[int, int] | None


def take(s: Sampler) -> Snapshot:
    plist = s.procs()
    comp = jarvis_processes(plist, s.own_pid)
    gpu = s.gpu()
    procs = {p.pid: p for p in plist if p.pid in comp}
    if gpu is not None:
        for pid, p in procs.items():
            p.gpu = gpu.get(pid, 0)
    return Snapshot(s.now(), procs, comp, gpu)


def diff(a: Snapshot, b: Snapshot, threads: int) -> dict:
    """Per-component numbers between two snapshots. CPU % is of the whole machine: cpu seconds / (seconds * logical processors).
    A process that appears in `b` only is counted from zero (it started inside the interval); one that exited is lost."""
    dt = max(b.t - a.t, 1e-6)
    comps: dict[str, dict] = {}
    for pid, p in b.procs.items():
        name = b.comp[pid]
        prev = a.procs.get(pid)
        d_cpu = max(p.cpu_s - (prev.cpu_s if prev else 0.0), 0.0)
        d_w = max(p.io_write - (prev.io_write if prev else 0), 0)
        c = comps.setdefault(name, {"name": name, "procs": 0, "pids": [], "cpu_s": 0.0, "ws": 0, "private": 0, "gpu": None, "write": 0,
                                    "protected": name in PROTECTED})
        c["procs"] += 1
        c["pids"].append(pid)
        c["cpu_s"] += d_cpu
        c["ws"] += p.ws
        c["private"] += p.private
        c["write"] += d_w
        if p.gpu is not None:
            c["gpu"] = (c["gpu"] or 0) + p.gpu
    rows = []
    for c in comps.values():
        rows.append({"name": c["name"], "procs": c["procs"], "pids": sorted(c["pids"]), "protected": c["protected"],
                     "cpu_pct": round(100.0 * c["cpu_s"] / (dt * threads), 2), "cpu_s": round(c["cpu_s"], 3),
                     "ws_mb": round(c["ws"] / MB, 1), "private_mb": round(c["private"] / MB, 1),
                     "vram_mb": None if c["gpu"] is None else round(c["gpu"] / MB, 1),
                     "write_mb_s": round(c["write"] / MB / dt, 3)})
    rows.sort(key=lambda r: -r["cpu_pct"])
    return {"dt": dt, "components": rows}


def totals(rows: list[dict]) -> dict:
    vr = [r["vram_mb"] for r in rows if r["vram_mb"] is not None]
    return {"procs": sum(r["procs"] for r in rows), "cpu_pct": round(sum(r["cpu_pct"] for r in rows), 2),
            "cpu_s": round(sum(r["cpu_s"] for r in rows), 3), "ws_mb": round(sum(r["ws_mb"] for r in rows), 1),
            "private_mb": round(sum(r["private_mb"] for r in rows), 1), "vram_mb": round(sum(vr), 1) if vr else None,
            "write_mb_s": round(sum(r["write_mb_s"] for r in rows), 3)}


# ---- verdict ------------------------------------------------------------------------------------------------------------------------

def verdicts(total: dict, cpu_avg: float, window_covered_s: float, bud: B.Budget, outside: dict | None, vram_readable: bool) -> list[dict]:
    """One row per metric: OK, OVER, CHECK (outside-D: only tool caches found) or N/A (could not be read)."""
    def row(metric: str, value, limit, unit: str, over: bool, note: str = "") -> dict:
        return {"metric": metric, "value": value, "limit": limit, "unit": unit, "status": "N/A" if value is None else ("OVER" if over else "OK"),
                "note": note}
    out = [row("cpu", round(cpu_avg, 2), bud.cpu_pct, "% of machine", cpu_avg > bud.cpu_pct,
               f"average over {int(window_covered_s)} s (now {total['cpu_pct']} %)"),
           row("ram", total["ws_mb"], bud.ram_mb, "MB working set", total["ws_mb"] > bud.ram_mb,
               f"private bytes {total['private_mb']} MB (the working-set sum counts shared pages once per process)"),
           row("vram", total["vram_mb"], bud.vram_mb, "MB GPU dedicated", (total["vram_mb"] or 0) > bud.vram_mb,
               "" if vram_readable else "GPU process counters not readable here"),
           row("disk_write", total["write_mb_s"], bud.disk_write_mb_s, "MB/s written", total["write_mb_s"] > bud.disk_write_mb_s,
               "guess, not an owner number")]
    if outside is not None:
        mine = outside["jarvis_mb"]
        suspects = outside["suspect_mb"]
        st = "OVER" if outside["jarvis_items"] else ("CHECK" if outside["suspect_items"] else "OK")
        out.append({"metric": "outside_d", "value": mine, "limit": 0, "unit": "MB JARVIS-owned outside D:", "status": st,
                    "note": f"{outside['jarvis_items']} item(s); tool caches that may be JARVIS's: {outside['suspect_items']} item(s), {suspects} MB"})
    return out


def overall(vs: list[dict]) -> str:
    return "OVER" if any(v["status"] == "OVER" for v in vs) else ("CHECK" if any(v["status"] == "CHECK" for v in vs) else "OK")


def offenders(rows: list[dict], vs: list[dict], n: int = 3) -> dict[str, list[str]]:
    key = {"cpu": "cpu_pct", "ram": "ws_mb", "vram": "vram_mb", "disk_write": "write_mb_s"}
    out = {}
    for v in vs:
        k = key.get(v["metric"])
        if k and v["status"] == "OVER":
            top = [r for r in sorted(rows, key=lambda r: -(r[k] or 0)) if (r[k] or 0) > 0][:n]
            out[v["metric"]] = [f"{r['name']} {r[k]}" + ("*" if r["protected"] else "") for r in top]
    return out


class Meter:
    """Keeps the CPU samples of the last `window_s` seconds so --watch can average over the budget window."""

    def __init__(self, sampler: Sampler | None = None, bud: B.Budget | None = None) -> None:
        self.s = sampler or Sampler()
        self.bud = bud or B.load_budget()
        self.hist: deque = deque()          # (dt, cpu_s_total)
        self.prev: Snapshot | None = None

    def sample(self, interval_s: float = 5.0, outside: dict | None = None) -> dict:
        threads = self.s.threads()
        if self.prev is None:
            self.prev = take(self.s)
            self.s.sleep(interval_s)
        else:                                  # --watch: the previous sample is the start of this one
            self.s.sleep(max(0.0, interval_s - (self.s.now() - self.prev.t)))
        cur = take(self.s)
        d = diff(self.prev, cur, threads)
        self.prev = cur
        tot = totals(d["components"])
        self.hist.append((d["dt"], tot["cpu_s"]))
        while len(self.hist) > 1 and sum(h[0] for h in self.hist) - self.hist[0][0] >= self.bud.window_s:
            self.hist.popleft()
        covered = sum(h[0] for h in self.hist)
        cpu_avg = 100.0 * sum(h[1] for h in self.hist) / (covered * threads)
        ram = self.s.ram_mb()
        notes = list(self.bud.notes)
        if threads != self.bud.machine_threads:
            notes.append(f"this machine has {threads} logical processors, the budget was set for {self.bud.machine_threads}; CPU % is of THIS machine")
        if ram and abs(ram - self.bud.machine_ram_mb) > 1024:
            notes.append(f"this machine has {ram} MB RAM, the RAM budget {self.bud.ram_mb} MB was set for {self.bud.machine_ram_mb} MB")
        vs = verdicts(tot, cpu_avg, covered, self.bud, outside, cur.gpu is not None)
        return {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "interval_s": round(d["dt"], 2), "window_s": round(covered, 1),
                "machine": {"threads": threads, "ram_mb": ram},
                "budget": {k: getattr(self.bud, k) for k in ("cpu_pct", "ram_mb", "vram_mb", "disk_write_mb_s", "threads", "window_s")},
                "components": d["components"], "total": tot, "verdicts": vs, "overall": overall(vs), "offenders": offenders(d["components"], vs),
                "outside_d": outside, "notes": notes}


# ---- files outside D: ---------------------------------------------------------------------------------------------------------------

# Names that look like secrets are neither counted nor listed (a size is the only thing this program takes from a file, but they stay out anyway).
SECRET_FILE = re.compile(r"(^|/)(\.env.*|id_[a-z0-9]+(\.pub)?|.*(token|secret|credential|password|passwd|auth|api[_-]?key).*|.*\.(pem|key|p12|pfx|keystore|kdbx))$", re.I)
# (label, base variable or "home", relative folder, kind). kind "jarvis" = JARVIS's own; "tool-cache" = a tool's default cache that a JARVIS tool
# fills when the D: variables are not set (it may be shared with the owner's other uses, so it only raises CHECK).
KNOWN_PLACES = (
    ("face profile (Electron user data)", "APPDATA", "JARVIS", "jarvis"),
    ("face profile (older name)", "APPDATA", "jarvis", "jarvis"),
    ("face profile (AIRI)", "APPDATA", "airi", "jarvis"),
    ("face profile (AIRI, dev)", "APPDATA", "@proj-airi", "jarvis"),
    ("face profile (dev name)", "APPDATA", "@proj-airi/stage-tamagotchi", "jarvis"),
    ("JARVIS local data", "LOCALAPPDATA", "jarvis", "jarvis"),
    ("JARVIS local data", "LOCALAPPDATA", "JARVIS", "jarvis"),
    ("AIRI local data", "LOCALAPPDATA", "airi", "jarvis"),
    ("face install on C:", "LOCALAPPDATA", "Programs/JARVIS", "jarvis"),
    ("face install on C:", "LOCALAPPDATA", "Programs/jarvis", "jarvis"),
    ("face updater", "LOCALAPPDATA", "jarvis-updater", "jarvis"),
    ("pnpm home and store", "LOCALAPPDATA", "pnpm", "tool-cache"),
    ("npm cache", "LOCALAPPDATA", "npm-cache", "tool-cache"),
    ("uv cache and Python", "LOCALAPPDATA", "uv", "tool-cache"),
    ("pip cache", "LOCALAPPDATA", "pip/Cache", "tool-cache"),
    ("Playwright browsers", "LOCALAPPDATA", "ms-playwright", "tool-cache"),
    ("Electron download cache", "LOCALAPPDATA", "electron/Cache", "tool-cache"),
    ("electron-builder cache", "LOCALAPPDATA", "electron-builder/Cache", "tool-cache"),
    ("llama.cpp download cache", "LOCALAPPDATA", "llama.cpp", "tool-cache"),
    ("npm cache (roaming)", "APPDATA", "npm-cache", "tool-cache"),
    ("JARVIS settings in the home folder", "home", ".jarvis", "jarvis"),
    ("Hermes agent data", "home", ".hermes", "jarvis"),
    ("Hugging Face cache", "home", ".cache/huggingface", "tool-cache"),
    ("uv cache", "home", ".cache/uv", "tool-cache"),
    ("pip cache", "home", ".cache/pip", "tool-cache"),
    ("llama.cpp cache", "home", ".cache/llama.cpp", "tool-cache"),
    ("Playwright cache", "home", ".cache/ms-playwright", "tool-cache"),
    ("JARVIS cache", "home", ".cache/jarvis", "jarvis"),
    ("Ollama models", "home", ".ollama", "tool-cache"),
)
TEMP_NAMES = re.compile(r"^(jarvis|airi|llama|playwright|ms-playwright|pip-|uv-|pnpm|npm-|electron-|hf-|huggingface)", re.I)


def dir_size(path: Path, max_files: int = 200000, max_s: float = 20.0) -> tuple[int, int, bool]:
    """(bytes, files, complete) of a folder, from file sizes only (os.scandir). Secret-looking names are skipped, links are not followed."""
    total = files = 0
    t0 = time.monotonic()
    stack = [str(path)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if files >= max_files or time.monotonic() - t0 > max_s:
                        return total, files, False
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif not SECRET_FILE.search(e.name):
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, files, True


def scan_outside_d(env: Mapping[str, str] | None = None, home: str | None = None, places=KNOWN_PLACES,
                   size_fn: Callable[[Path], tuple[int, int, bool]] = dir_size) -> dict:
    """JARVIS-owned (and JARVIS-tool cache) folders that are not on D:. Names and sizes only. ~/.ssh and the secrets folder are never visited."""
    env = os.environ if env is None else env
    home = home or os.path.expanduser("~")
    bases = {"APPDATA": env.get("APPDATA"), "LOCALAPPDATA": env.get("LOCALAPPDATA"), "home": home}
    items: list[dict] = []
    seen: set[str] = set()

    def add(label: str, p: Path, kind: str) -> None:
        key = str(p).lower()
        if key in seen or B.is_on_d(str(p)) or not p.exists():
            return
        seen.add(key)
        if p.is_dir():
            size, files, complete = size_fn(p)
        else:
            try:
                size, files, complete = p.stat().st_size, 1, True
            except OSError:
                size, files, complete = 0, 0, False
        items.append({"label": label, "path": str(p), "kind": kind, "mb": round(size / MB, 1), "files": files, "complete": complete})

    for label, base, rel, kind in places:
        root = bases.get(base)
        if root:
            add(label, Path(root) / rel, kind)
    for tvar in ("TEMP", "TMP"):
        t = env.get(tvar)
        if t and not B.is_on_d(t) and Path(t).is_dir():
            try:
                for e in sorted(os.scandir(t), key=lambda e: e.name):
                    if TEMP_NAMES.match(e.name):
                        add("temp entry", Path(e.path), "jarvis" if e.name.lower().startswith(("jarvis", "airi")) else "tool-cache")
            except OSError:
                pass
    mine = [i for i in items if i["kind"] == "jarvis"]
    sus = [i for i in items if i["kind"] != "jarvis"]
    return {"items": items, "jarvis_items": len(mine), "jarvis_mb": round(sum(i["mb"] for i in mine), 1),
            "suspect_items": len(sus), "suspect_mb": round(sum(i["mb"] for i in sus), 1),
            "env_problems": B.storage_problems(env)}


# ---- output -------------------------------------------------------------------------------------------------------------------------

def render(rep: dict) -> str:
    m = rep["machine"]
    L = [f"JARVIS footprint  {rep['time']}   sampled {rep['interval_s']} s   machine {m['threads']} threads"
         + (f" / {m['ram_mb']} MB" if m["ram_mb"] else "") + f"        OVERALL: {rep['overall']}"]
    for v in rep["verdicts"]:
        val = "n/a" if v["value"] is None else f"{v['value']:,}"
        L.append(f"  {v['metric']:<11} {v['status']:<6} {val:>10} / {v['limit']:,} {v['unit']:<28} {v['note']}")
    L.append("")
    L.append(f"  {'component':<17}{'procs':>5}{'cpu%':>7}{'ws MB':>9}{'priv MB':>9}{'vram MB':>9}{'write MB/s':>11}")
    for r in rep["components"]:
        vr = "n/a" if r["vram_mb"] is None else f"{r['vram_mb']:.0f}"
        L.append(f"  {r['name'] + ('*' if r['protected'] else ''):<17}{r['procs']:>5}{r['cpu_pct']:>7.1f}{r['ws_mb']:>9.0f}{r['private_mb']:>9.0f}{vr:>9}{r['write_mb_s']:>11.2f}")
    t = rep["total"]
    L.append(f"  {'TOTAL':<17}{t['procs']:>5}{t['cpu_pct']:>7.1f}{t['ws_mb']:>9.0f}{t['private_mb']:>9.0f}{'n/a' if t['vram_mb'] is None else format(t['vram_mb'], '.0f'):>9}{t['write_mb_s']:>11.2f}")
    if rep["offenders"]:
        L.append("")
        L.append("  top offenders (* = protected, never stopped):")
        for k, v in rep["offenders"].items():
            L.append(f"    {k}: " + ", ".join(v))
    o = rep.get("outside_d")
    if o and o["items"]:
        L.append("")
        L.append("  outside D: (names and sizes only):")
        for i in o["items"][:12]:
            L.append(f"    {i['kind']:<10}{i['mb']:>9.1f} MB  {i['path']}" + ("" if i["complete"] else "  (partial)"))
    if o and o["env_problems"]:
        L.append("  this shell's storage variables not on D: " + "; ".join(o["env_problems"]))
    for n in rep["notes"]:
        L.append("  note: " + n)
    return "\n".join(L)


def write_report(rep: dict, out: str) -> None:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def report_once(seconds: float = 5.0, scan: bool = True, sampler: Sampler | None = None, bud: B.Budget | None = None) -> dict:
    """One report (for callers such as the doctor or the HUD)."""
    return Meter(sampler, bud).sample(seconds, scan_outside_d() if scan else None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print JSON instead of the text screen")
    ap.add_argument("--watch", action="store_true", help="repeat until interrupted; CPU is averaged over the budget window")
    ap.add_argument("--seconds", type=float, default=5.0, help="sample length (default 5)")
    ap.add_argument("--every", type=float, default=0.0, help="--watch: pause between samples (default none: back to back)")
    ap.add_argument("--no-scan", action="store_true", help="skip the outside-D: file scan")
    ap.add_argument("--scan-only", action="store_true", help="only the outside-D: file scan")
    ap.add_argument("--out", default=None, help="--watch: where the latest report is written (default " + DEFAULT_OUT + ")")
    args = ap.parse_args(argv)
    if args.scan_only:
        o = scan_outside_d()
        print(json.dumps(o, indent=1) if args.json else "\n".join(f"{i['kind']:<10}{i['mb']:>9.1f} MB  {i['path']}" for i in o["items"]) or "nothing outside D:")
        return 1 if o["jarvis_items"] else 0
    meter = Meter()
    scan_every, last_scan, outside = 12, 0, None          # the scan walks folders: in --watch only every 12th sample
    n = 0
    try:
        while True:
            if not args.no_scan and (n % scan_every == 0):
                outside = scan_outside_d()
            rep = meter.sample(args.seconds, outside)
            n += 1
            if args.watch:
                write_report(rep, args.out or DEFAULT_OUT)
            print(json.dumps(rep) if args.json else (("\033[2J\033[H" if args.watch and sys.stdout.isatty() else "") + render(rep)), flush=True)
            if not args.watch:
                return 1 if rep["overall"] == "OVER" else 0
            if args.every:
                time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
