"""Detect this computer's hardware and pick a Neko model profile. Windows first; Linux and macOS work with fallbacks.

    python hwdetect.py            print the readings, the profile and why, and save them to <data>\\neko\\hardware.json
    python hwdetect.py --json     the same as JSON only

GPU memory comes from llama.cpp itself (`llama-server --list-devices`), which is what will actually run the models;
WMI's AdapterRAM is a 32-bit field and wrong above 4 GB. Profiles and their models: jarvis/docs/neko/NEKO_MODEL_MATRIX.md.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

LLAMA = os.environ.get("NEKO_LLAMA_SERVER", r"D:\jarvis\tools\llama.cpp\b11669\vulkan\llama-server.exe")
OUT = os.path.join(os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data"), "neko", "hardware.json")
PROFILES = {
    "remote_only": "under 6 GB of RAM: local models would crowd out everything else; routing stays local, models are remote",
    "cpu8": "CPU only, about 8 GB RAM: one small model at a time (2B router, 1.5B coder); planning and research remote",
    "cpu16": "CPU only, 12 GB RAM or more: 4B router plus a mixture-of-experts generalist on demand",
    "vram4": "a GPU with 3-6 GB free: specialists on the GPU one at a time, MoE experts in system RAM",
    "vram8plus": "a GPU with 6 GB free or more: 9B-class models on the GPU, larger MoE models with experts in RAM",
}


def cpu_info() -> dict:
    name, avx2 = platform.processor() or "", None
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                name = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
        try:
            import ctypes
            avx2 = bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))   # PF_AVX2_INSTRUCTIONS_AVAILABLE
        except (OSError, AttributeError):
            pass
    elif os.path.exists("/proc/cpuinfo"):
        txt = open("/proc/cpuinfo", encoding="utf-8", errors="replace").read()
        m = re.search(r"model name\s*:\s*(.+)", txt)
        name, avx2 = (m.group(1).strip() if m else name), (" avx2" in txt)
    elif sys.platform == "darwin":
        try:
            name = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import psutil
        cores, threads = psutil.cpu_count(logical=False), psutil.cpu_count(logical=True)
    except ImportError:
        cores, threads = None, os.cpu_count()
    return {"name": name, "cores": cores, "threads": threads, "avx2": avx2}


def memory_gb() -> dict:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {"total_gb": round(vm.total / 2**30, 1), "available_gb": round(vm.available / 2**30, 1)}
    except ImportError:
        return {"total_gb": None, "available_gb": None}


def gpus(llama: str = LLAMA, runner=subprocess.run) -> list[dict]:
    """Devices as llama.cpp sees them: [{"name", "backend", "total_mb", "free_mb"}]."""
    if not os.path.isfile(llama):
        return []
    try:
        r = runner([llama, "--list-devices"], capture_output=True, text=True, timeout=60,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_devices((r.stdout or "") + (r.stderr or ""))


def parse_devices(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"^\s*(Vulkan|CUDA|ROCm|Metal|SYCL)(\d+):\s*(.+?)\s*\((\d+) MiB,\s*(\d+) MiB free\)", text, re.M):
        out.append({"backend": m.group(1), "name": m.group(3), "total_mb": int(m.group(4)), "free_mb": int(m.group(5))})
    return out


def disks() -> dict:
    out = {}
    for d in (["C:\\", "D:\\"] if sys.platform == "win32" else ["/"]):
        try:
            u = shutil.disk_usage(d)
            out[d.rstrip("\\/") or "/"] = round(u.free / 2**30, 1)
        except OSError:
            pass
    return out


def choose(mem: dict, devices: list[dict]) -> tuple[str, str]:
    total = mem.get("total_gb") or 0
    free_vram = max((d["free_mb"] for d in devices), default=0)
    if total and total < 6:
        return "remote_only", f"{total} GB RAM"
    if free_vram >= 6000:
        return "vram8plus", f"{free_vram} MiB GPU memory free"
    if free_vram >= 3000:
        return "vram4", f"{free_vram} MiB GPU memory free"
    if total < 12:
        return "cpu8", f"no usable GPU, {total} GB RAM"
    return "cpu16", f"no usable GPU, {total} GB RAM"


def detect(runner=subprocess.run) -> dict:
    mem, devices = memory_gb(), gpus(runner=runner)
    profile, why = choose(mem, devices)
    override = os.environ.get("NEKO_MODEL_PROFILE", "").strip()
    if override in PROFILES:
        profile, why = override, f"set by NEKO_MODEL_PROFILE (detected: {why})"
    return {"checked": time.strftime("%Y-%m-%d %H:%M:%S"), "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "cpu": cpu_info(), "memory": mem, "gpus": devices, "disk_free_gb": disks(),
            "profile": profile, "why": why, "profile_meaning": PROFILES[profile]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    info = detect()
    if not a.no_save:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
    if a.json:
        print(json.dumps(info, indent=2))
        return 0
    c, m = info["cpu"], info["memory"]
    print(f"OS      : {info['os']}")
    print(f"CPU     : {c['name']} | {c['cores']} cores / {c['threads']} threads | AVX2 {c['avx2']}")
    print(f"RAM     : {m['total_gb']} GB total, {m['available_gb']} GB free")
    for g in info["gpus"] or [{"name": "none usable", "backend": "-", "total_mb": 0, "free_mb": 0}]:
        print(f"GPU     : {g['name']} ({g['backend']}) {g['total_mb']} MiB, {g['free_mb']} MiB free")
    print(f"Disk    : " + ", ".join(f"{k} {v} GB free" for k, v in info["disk_free_gb"].items()))
    print(f"Profile : {info['profile']} ({info['why']}): {info['profile_meaning']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
