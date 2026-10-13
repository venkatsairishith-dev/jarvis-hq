"""Read-only computer state collector for Windows. GREEN actions only: nothing here changes the machine.

Built on psutil (BSD-3-Clause, https://github.com/giampaolo/psutil) and the Windows registry (stdlib winreg): no
PowerShell or other helper program is started for the core data, so nothing can flash a console window. Only the
tool-version probe starts programs, hidden (CREATE_NO_WINDOW) and cached for 30 minutes.

Numbers are rounded and derived values (percentages) are computed HERE and stored, so a report can print only
values that exist in the state and every number can be traced back to its source.
"""
import os
import shutil
import socket
import subprocess
import time

import psutil

# Background helpers must not open a console window each time (this runs under pythonw): CREATE_NO_WINDOW.
NOWIN = 0x08000000 if os.name == "nt" else 0
TOOLS = ["git", "gh", "python", "node", "npm", "ollama", "docker", "tailscale", "ssh"]
VERSION_FLAG = {"ssh": "-V", "tailscale": "version"}  # default is --version
EXTRA_PATHS = {  # tools that are installed but not on PATH
    "tailscale": [r"C:\Program Files\Tailscale\tailscale.exe"],
    "ollama": [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")],
}
ENV_ALLOW = ("OLLAMA_MODELS", "HF_HOME", "PIP_CACHE_DIR", "npm_config_cache")  # paths only; never secrets
GB = 1024 ** 3


def _reg(root, path, name):
    try:
        import winreg
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except (OSError, ImportError):
        return None


def _hklm(path, name):
    import winreg
    return _reg(winreg.HKEY_LOCAL_MACHINE, path, name)


def _user_env(name):
    """The saved user-level value (what a new terminal will see), not this process's older copy."""
    import winreg
    return _reg(winreg.HKEY_CURRENT_USER, "Environment", name)


def _gpus() -> list:
    """Display adapters from the registry (the WMI AdapterRAM field is capped at 4 GB; the registry holds the real size)."""
    out = []
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
            for i in range(winreg.QueryInfoKey(k)[0]):
                sub = winreg.EnumKey(k, i)
                if not sub.isdigit():
                    continue
                name = _hklm(base + "\\" + sub, "DriverDesc")
                if not name:
                    continue
                mem = _hklm(base + "\\" + sub, "HardwareInformation.qwMemorySize")
                if isinstance(mem, bytes):
                    mem = int.from_bytes(mem[:8], "little")
                out.append({"name": name, "vram_gb": round((mem or 0) / GB, 1), "driver": _hklm(base + "\\" + sub, "DriverVersion") or ""})
    except OSError:
        pass
    return out


def _tool(name: str):
    path = shutil.which(name) or next((p for p in EXTRA_PATHS.get(name, []) if os.path.exists(p)), None)
    if not path:
        return {"present": False}
    ver = None
    try:
        out = subprocess.run([path, VERSION_FLAG.get(name, "--version")], capture_output=True, text=True, timeout=8,
                             stdin=subprocess.DEVNULL, creationflags=NOWIN)
        text = (out.stdout or out.stderr).strip()
        ver = text.splitlines()[0][:80] if text else None
    except Exception:
        ver = None
    return {"present": True, "version": ver}


_TOOLS_CACHE = {"at": 0.0, "value": None}
TOOLS_TTL = 1800  # installed tool versions almost never change: ask every 30 minutes, not every minute


def _tools() -> dict:
    if _TOOLS_CACHE["value"] is None or time.time() - _TOOLS_CACHE["at"] > TOOLS_TTL:
        _TOOLS_CACHE["value"], _TOOLS_CACHE["at"] = {t: _tool(t) for t in TOOLS}, time.time()
    return _TOOLS_CACHE["value"]


def _procs() -> list:
    rows = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            rows.append((p.info["memory_info"].rss, p.info["name"] or "?"))
        except (psutil.Error, AttributeError):
            continue
    rows.sort(reverse=True)
    return [{"name": n.removesuffix(".exe"), "mem_mb": round(m / 1024 ** 2)} for m, n in rows[:8]]


def _services() -> dict:
    running, stopped_auto = 0, []
    try:
        for s in psutil.win_service_iter():
            try:
                info = s.as_dict()
            except psutil.Error:
                continue
            if info["status"] == "running":
                running += 1
            elif info["start_type"] == "automatic" and len(stopped_auto) < 10:
                stopped_auto.append(info["name"])
    except (AttributeError, psutil.Error):
        pass
    return {"running": running, "stopped_auto": stopped_auto}


def _ports() -> list:
    seen, out = set(), []
    try:
        conns = psutil.net_connections(kind="tcp")
    except psutil.Error:
        conns = []
    for c in conns:
        if c.status != psutil.CONN_LISTEN or c.laddr.port in seen:
            continue
        seen.add(c.laddr.port)
        try:
            proc = psutil.Process(c.pid).name().removesuffix(".exe") if c.pid else "?"
        except psutil.Error:
            proc = "?"
        out.append({"port": int(c.laddr.port), "address": c.laddr.ip, "process": proc})
    out.sort(key=lambda r: r["port"])
    return out[:40]


def _disks() -> list:
    out = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        out.append({"drive": part.device.rstrip("\\"), "size_gb": round(u.total / GB, 1), "free_gb": round(u.free / GB, 1),
                    "free_pct": round(100 * u.free / u.total, 1) if u.total else 0.0})
    return out


def _outputs(root: str = None, limit: int = 40) -> list:
    """The newest files agents delivered to D:\\jarvis\\outputs (agent folder, name, size, time; never contents),
    for the hub's outputs gallery."""
    if root is None:
        from .jobs import outputs_root
        root = outputs_root()
    items = []
    try:
        agents = os.listdir(root)
    except OSError:
        return []
    for agent in agents:
        folder = os.path.join(root, agent)
        if not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            try:
                st = os.stat(os.path.join(folder, name))
            except OSError:
                continue
            if not os.path.isdir(os.path.join(folder, name)):
                items.append((st.st_mtime, agent, name, st.st_size))
    items.sort(reverse=True)
    return [{"agent": a, "name": n, "bytes": b, "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))}
            for t, a, n, b in items[:limit]]


def collect() -> dict:
    t0 = time.time()
    vm = psutil.virtual_memory()
    cur = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    cpu_key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
    bios = r"HARDWARE\DESCRIPTION\System\BIOS"
    total = round(vm.total / GB, 1)
    free = round(vm.available / GB, 1)   # "available": free memory plus cache that can be reused at once
    s = {
        "host": {"name": socket.gethostname().upper(), "model": _hklm(bios, "SystemProductName") or "", "manufacturer": _hklm(bios, "SystemManufacturer") or ""},
        "os": {"caption": ("Microsoft " + (_hklm(cur, "ProductName") or "Windows")), "build": str(_hklm(cur, "CurrentBuild") or ""),
               "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1)},
        "cpu": {"name": (_hklm(cpu_key, "ProcessorNameString") or "").strip(), "cores": psutil.cpu_count(logical=False) or 0,
                "threads": psutil.cpu_count(logical=True) or 0, "load_pct": int(round(psutil.cpu_percent(interval=0.5)))},
        "ram": {"total_gb": total, "free_gb": free, "used_pct": round(100 * (total - free) / total, 1) if total else 0.0},
        "gpu": _gpus(),
        "disks": _disks(),
        "processes_top": _procs(),
        "services": _services(),
        "ports": _ports(),
    }
    # Windows 11 reports ProductName "Windows 10 ..." in the registry; the build number tells them apart
    try:
        if int(s["os"]["build"]) >= 22000:
            s["os"]["caption"] = s["os"]["caption"].replace("Windows 10", "Windows 11")
    except ValueError:
        pass
    s["tools"] = _tools()
    s["outputs"] = _outputs()
    s["env_paths"] = {k: _user_env(k) for k in ENV_ALLOW}
    s["collected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    s["collect_seconds"] = round(time.time() - t0, 1)
    return s


if __name__ == "__main__":
    import json
    print(json.dumps(collect(), indent=1))
