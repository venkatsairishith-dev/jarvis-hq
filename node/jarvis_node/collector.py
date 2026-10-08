"""Read-only computer state collector for Windows. GREEN actions only: nothing here changes the machine.

Numbers are rounded and derived values (percentages) are computed HERE and stored, so a report can
print only values that exist in the state and every number can be traced back to its source.
"""
import json
import os
import shutil
import subprocess
import time

PS = r"""
$ErrorActionPreference='SilentlyContinue'
$os=Get-CimInstance Win32_OperatingSystem; $cs=Get-CimInstance Win32_ComputerSystem; $cpu=Get-CimInstance Win32_Processor | Select-Object -First 1
$o=[ordered]@{}
$o.host=[ordered]@{name=$env:COMPUTERNAME; model=$cs.Model; manufacturer=$cs.Manufacturer}
$o.os=[ordered]@{caption=$os.Caption; build=$os.BuildNumber; uptime_hours=[math]::Round(((Get-Date)-$os.LastBootUpTime).TotalHours,1)}
$o.cpu=[ordered]@{name=$cpu.Name.Trim(); cores=[int]$cpu.NumberOfCores; threads=[int]$cpu.NumberOfLogicalProcessors; load_pct=[int]$cpu.LoadPercentage}
$tot=[math]::Round($os.TotalVisibleMemorySize/1MB,1); $free=[math]::Round($os.FreePhysicalMemory/1MB,1)
$o.ram=[ordered]@{total_gb=$tot; free_gb=$free; used_pct=[math]::Round(100*($tot-$free)/$tot,1)}
$o.gpu=@(Get-CimInstance Win32_VideoController | ForEach-Object { [ordered]@{name=$_.Name; vram_gb=[math]::Round($_.AdapterRAM/1GB,1); driver=$_.DriverVersion} })
$o.disks=@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object { [ordered]@{drive=$_.DeviceID; size_gb=[math]::Round($_.Size/1GB,1); free_gb=[math]::Round($_.FreeSpace/1GB,1); free_pct=[math]::Round(100*$_.FreeSpace/$_.Size,1)} })
$o.processes_top=@(Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 8 | ForEach-Object { [ordered]@{name=$_.ProcessName; mem_mb=[math]::Round($_.WorkingSet64/1MB,0)} })
$svc=Get-CimInstance Win32_Service
$o.services=[ordered]@{running=@($svc | Where-Object State -eq 'Running').Count; stopped_auto=@($svc | Where-Object { $_.StartMode -eq 'Auto' -and $_.State -ne 'Running' } | Select-Object -First 10 -ExpandProperty Name)}
$o.ports=@(Get-NetTCPConnection -State Listen | Sort-Object LocalPort -Unique | Select-Object -First 40 | ForEach-Object { $p=(Get-Process -Id $_.OwningProcess).ProcessName; [ordered]@{port=[int]$_.LocalPort; address=$_.LocalAddress; process=$p} })
$o | ConvertTo-Json -Depth 5 -Compress
"""

# Background helpers must not open a console window each time (this runs under pythonw): CREATE_NO_WINDOW.
NOWIN = 0x08000000 if os.name == "nt" else 0
TOOLS = ["git", "gh", "python", "node", "npm", "ollama", "docker", "tailscale", "ssh"]
VERSION_FLAG = {"ssh": "-V", "tailscale": "version"}  # default is --version
EXTRA_PATHS = {  # tools that are installed but not on PATH
    "tailscale": [r"C:\Program Files\Tailscale\tailscale.exe"],
    "ollama": [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")],
}
ENV_ALLOW = ("OLLAMA_MODELS", "HF_HOME", "PIP_CACHE_DIR", "npm_config_cache")  # paths only; never secrets


def _user_env(name):
    """The saved user-level value (what a new terminal will see), not this process's older copy."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _ps() -> dict:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS], capture_output=True, text=True, timeout=90, creationflags=NOWIN)
    return json.loads(r.stdout)


def _tool(name: str):
    path = shutil.which(name) or next((p for p in EXTRA_PATHS.get(name, []) if os.path.exists(p)), None)
    if not path:
        return {"present": False}
    ver = None
    try:
        out = subprocess.run([path, VERSION_FLAG.get(name, "--version")], capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL, creationflags=NOWIN)
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


def collect() -> dict:
    t0 = time.time()
    s = _ps()
    s["tools"] = _tools()
    s["env_paths"] = {k: _user_env(k) for k in ENV_ALLOW}
    s["collected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    s["collect_seconds"] = round(time.time() - t0, 1)
    return s


if __name__ == "__main__":
    print(json.dumps(collect(), indent=1))
