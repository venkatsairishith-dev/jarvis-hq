"""JARVIS footprint budget (D50): the numbers, in one place, and the D:-only storage rule.

Owner, 2026-10-25: "JARVIS should use only 20% of the whole laptop, and for storage use only the D: drive."
Updated the same day: VRAM 2 GB of the 4 GB card.

Everything that measures (jarvis/tools/footprint.py), enforces (jarvis/tools/governor.py, jarvis/supervisor/jobobject.py) or
starts a service (supervisor, manifests, jarvis-start.ps1 through d-env.ps1) reads this module. Stdlib only. Paths are handled
as Windows paths on every OS, so the tests run off Windows.

Overrides, strongest first: environment variables JARVIS_BUDGET_<NAME>, then D:\\jarvis\\data\\budget.json, then the defaults:
    {"cpu_pct": 20, "ram_mb": 6554, "vram_mb": 2048, "disk_write_mb_s": 5, "threads": 3}
"""
from __future__ import annotations

import json
import ntpath
import os
from dataclasses import dataclass, field, replace
from typing import Mapping

D_ROOT = "D:\\jarvis"
DATA_DIR = D_ROOT + "\\data"
BUDGET_FILE = DATA_DIR + "\\budget.json"
# The machine the numbers were set for (owner's laptop). footprint.py measures the real values and warns when they differ.
MACHINE_THREADS = 16
MACHINE_RAM_MB = 32768


@dataclass(frozen=True)
class Budget:
    cpu_pct: float = 20.0           # of the WHOLE machine, averaged over window_s (about 3 of 16 threads)
    ram_mb: int = 6554              # 20% of 32 GB (6.4 GB), summed working set of ALL JARVIS processes
    vram_mb: int = 2048             # GPU dedicated memory of all JARVIS processes (owner, 2026-10-25: 2 GB of the 4 GB card)
    disk_write_mb_s: float = 5.0    # average write rate over the window; a GUESS, not an owner number
    threads: int = 3                # llama-server -t (3 of 16 threads is 18.75%, just under cpu_pct)
    window_s: int = 60              # averaging window for the CPU verdict
    over_grace_s: int = 20          # the governor acts when over budget for longer than this
    job_mem_factor: float = 1.25    # Job Object memory backstop = ram_mb * factor (committed bytes; a GUESS to verify live)
    machine_threads: int = MACHINE_THREADS
    machine_ram_mb: int = MACHINE_RAM_MB
    notes: tuple = field(default=(), compare=False)   # problems found in the overrides

    @property
    def cpu_threads_equiv(self) -> float:
        return self.cpu_pct / 100.0 * self.machine_threads

    @property
    def job_mem_bytes(self) -> int:
        return int(self.ram_mb * self.job_mem_factor * 1024 * 1024)


_FIELDS = {"cpu_pct": float, "ram_mb": int, "vram_mb": int, "disk_write_mb_s": float, "threads": int, "window_s": int,
           "over_grace_s": int, "job_mem_factor": float}


def load_budget(env: Mapping[str, str] | None = None, json_path: str | None = BUDGET_FILE) -> Budget:
    """Defaults, then the json file, then JARVIS_BUDGET_<FIELD> variables. A bad value is ignored and noted, never fatal."""
    env = os.environ if env is None else env
    values: dict = {}
    notes: list[str] = []
    if json_path:
        try:
            with open(json_path, encoding="utf-8-sig") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                raise ValueError("not an object")
            src = {k: v for k, v in raw.items() if not k.startswith("_")}
        except FileNotFoundError:
            src = {}
        except (OSError, ValueError) as e:
            src = {}
            notes.append(f"{json_path}: {e}")
        for k, v in src.items():
            _take(values, notes, k, v, json_path)
    for k in _FIELDS:
        v = env.get("JARVIS_BUDGET_" + k.upper())
        if v not in (None, ""):
            _take(values, notes, k, v, "JARVIS_BUDGET_" + k.upper())
    return replace(Budget(), notes=tuple(notes), **values)


def _take(values: dict, notes: list, key: str, raw, where: str) -> None:
    if key not in _FIELDS:
        notes.append(f"{where}: unknown key {key!r}")
        return
    try:
        v = _FIELDS[key](raw)
        if isinstance(raw, bool) or not v > 0:
            raise ValueError
        if key == "cpu_pct" and v > 100:
            raise ValueError
    except (TypeError, ValueError):
        notes.append(f"{where}: {key}={raw!r} is not a positive number; default kept")
        return
    values[key] = v


# ---- storage: nothing outside D: ----------------------------------------------------------------------------------------------------

class StorageError(RuntimeError):
    pass


def is_on_d(path: str | os.PathLike | None) -> bool:
    """True for an absolute path on drive D: (either slash). Relative paths, UNC, other drives and `..` escapes are False."""
    if path is None:
        return False
    s = str(path).strip().strip('"').replace("/", "\\")
    if s.startswith("\\\\?\\"):
        s = s[4:]
    if len(s) < 3 or s[1] != ":" or s[2] != "\\":
        return False
    n = ntpath.normpath(s)
    return n[:3].upper() == "D:\\" or n.upper() == "D:"


def storage_env(root: str = D_ROOT) -> dict[str, str]:
    """The environment that forces every temp file and cache onto D:. One source: d-env.ps1 mirrors it (test_budget.py checks).

    TEMP, TMP and the caches keep the folders that d-env.ps1 has used since 2026-10-21 (D:\\jarvis\\tmp, D:\\jarvis\\cache\\*), so
    what is already downloaded (speech models in cache\\hf) stays valid; the new ones sit beside them.
    """
    j = root.rstrip("\\/")
    tmp = j + "\\tmp"
    c = j + "\\cache"
    return {
        "TEMP": tmp, "TMP": tmp, "TMPDIR": tmp,
        "HF_HOME": c + "\\hf", "HF_HUB_CACHE": c + "\\hf\\hub", "XDG_CACHE_HOME": c + "\\xdg",
        "PIP_CACHE_DIR": c + "\\pip", "UV_CACHE_DIR": c + "\\uv",
        "npm_config_cache": c + "\\npm",
        "PNPM_HOME": j + "\\tools\\pnpm", "npm_config_store_dir": c + "\\pnpm-store", "PNPM_STORE_DIR": c + "\\pnpm-store",
        "PLAYWRIGHT_BROWSERS_PATH": c + "\\ms-playwright",
        "ELECTRON_CACHE": c + "\\electron", "ELECTRON_BUILDER_CACHE": c + "\\electron-builder",
        "OLLAMA_MODELS": j + "\\models\\ollama",
        "LLAMA_CACHE": c + "\\llama",
        # the face's Electron profile (main process: app.setPath, see face .../main/jarvis/storage-lock.ts)
        "APP_USER_DATA_PATH": j + "\\data\\face\\user-data",
    }


# Variables whose value, when set, must be on D: for a JARVIS service to start. JARVIS_DATA_DIR and the secrets reference are
# the owner's own settings; D:\\jarvis-secrets is on D: too.
STORAGE_VARS = tuple(storage_env()) + ("JARVIS_DATA_DIR", "JARVIS_VAULT_DIR", "HOME_JARVIS_CACHE", "JARVIS_SECRET_REF_ROOTS",
                                      "JARVIS_FACE_USER_DATA")


def face_args(root: str = D_ROOT) -> list[str]:
    """Command-line flag for a face started by hand or by a shortcut (the main process also sets it with app.setPath)."""
    return ["--user-data-dir=" + storage_env(root)["APP_USER_DATA_PATH"]]


def storage_problems(env: Mapping[str, str], names: tuple = STORAGE_VARS) -> list[str]:
    """One line for each storage variable in `env` that is set to something not on D: (empty values are ignored)."""
    out = []
    for name in names:
        v = env.get(name)
        if v and not is_on_d(v):
            out.append(f"{name}={v}")
    return out


def require_d_drive(env: Mapping[str, str], who: str) -> None:
    """Startup check: refuse to start `who` when a configured data/cache path is not on D:. One line, no secrets (paths only)."""
    bad = storage_problems(env)
    if bad:
        raise StorageError(f"{who}: refusing to start, not on the D: drive: " + "; ".join(bad))


def ensure_dirs(env: Mapping[str, str]) -> None:
    for k, v in env.items():
        if k in ("TEMP", "TMP", "TMPDIR", "APP_USER_DATA_PATH") or k.endswith(("_CACHE", "_CACHE_DIR", "_HOME", "_PATH", "_MODELS", "store_dir")):
            if is_on_d(v) and os.name == "nt":
                os.makedirs(v, exist_ok=True)


if __name__ == "__main__":   # python budget.py   -> prints the budget and the storage environment (no secrets in either)
    b = load_budget()
    print(json.dumps({"budget": {k: getattr(b, k) for k in _FIELDS}, "notes": list(b.notes), "storage_env": storage_env()}, indent=2))
