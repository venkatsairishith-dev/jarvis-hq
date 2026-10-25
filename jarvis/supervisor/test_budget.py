"""Footprint budget (D50): the numbers, the D:-only storage rule, the manifest and supervisor checks, the Job Object helpers.
Run: python -m pytest jarvis/supervisor/test_budget.py -q -p no:cacheprovider   (off Windows too: paths are handled as Windows paths)"""
from __future__ import annotations

import ctypes
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
JARVIS = HERE.parent
sys.path.insert(0, str(HERE))
import budget  # noqa: E402
import core as supervisor  # noqa: E402
import jobobject  # noqa: E402
import manifest  # noqa: E402

PY = sys.executable
SLEEP = [PY, "-c", "import time; time.sleep(30)"]


def test_the_default_numbers_are_the_owners():
    b = budget.Budget()
    assert b.cpu_pct == 20.0 and b.vram_mb == 2048             # owner 2026-10-25 (VRAM changed from 0.8 GB to 2 GB the same day)
    assert b.ram_mb == 6554 and abs(b.ram_mb - 0.2 * b.machine_ram_mb) < 1      # 20% of 32 GB = 6.4 GB
    assert b.threads == 3 and b.threads / b.machine_threads * 100 <= b.cpu_pct   # 3 of 16 threads = 18.75%
    assert abs(b.cpu_threads_equiv - 3.2) < 1e-9
    assert b.job_mem_bytes == int(6554 * 1.25 * 1024 * 1024) and b.job_mem_bytes > b.ram_mb * 1024 * 1024


def test_overrides_env_beats_json_beats_defaults(tmp_path):
    f = tmp_path / "budget.json"
    f.write_text(json.dumps({"_note": "x", "cpu_pct": 15, "ram_mb": 4000}), encoding="utf-8")
    b = budget.load_budget({"JARVIS_BUDGET_RAM_MB": "5000", "JARVIS_BUDGET_VRAM_MB": "1024"}, str(f))
    assert (b.cpu_pct, b.ram_mb, b.vram_mb) == (15.0, 5000, 1024)
    assert b.notes == ()
    assert budget.load_budget({}, str(tmp_path / "missing.json")) == budget.Budget()


def test_bad_overrides_are_ignored_and_noted_never_fatal(tmp_path):
    f = tmp_path / "budget.json"
    f.write_text(json.dumps({"cpu_pct": -3, "ram_mb": "lots", "colour": 1, "vram_mb": True}), encoding="utf-8")
    b = budget.load_budget({"JARVIS_BUDGET_CPU_PCT": "250"}, str(f))
    assert (b.cpu_pct, b.ram_mb, b.vram_mb) == (20.0, 6554, 2048)
    assert len(b.notes) == 5
    g = tmp_path / "broken.json"
    g.write_text("{not json", encoding="utf-8")
    assert budget.load_budget({}, str(g)).cpu_pct == 20.0 and budget.load_budget({}, str(g)).notes


@pytest.mark.parametrize("path", ["D:\\jarvis\\tmp", "d:/jarvis/tmp", "D:\\", "\\\\?\\D:\\jarvis", "D:\\jarvis\\a\\..\\b", '"D:\\jarvis"'])
def test_paths_on_d(path):
    assert budget.is_on_d(path)


@pytest.mark.parametrize("path", ["C:\\Users\\x\\AppData\\Local\\Temp", "E:\\jarvis", "\\\\srv\\share", "jarvis\\tmp", "./x", "", None, "D:jarvis",
                                  "C:\\x\\D:\\jarvis", "C:\\..\\D:\\jarvis"])
def test_paths_not_on_d(path):
    assert not budget.is_on_d(path)


def test_storage_env_covers_the_required_variables_and_all_sit_on_d():
    env = budget.storage_env()
    for name in ("TEMP", "TMP", "TMPDIR", "HF_HOME", "HF_HUB_CACHE", "XDG_CACHE_HOME", "PIP_CACHE_DIR", "UV_CACHE_DIR", "npm_config_cache",
                 "PNPM_HOME", "PLAYWRIGHT_BROWSERS_PATH", "OLLAMA_MODELS", "LLAMA_CACHE", "APP_USER_DATA_PATH"):
        assert name in env, name
    assert any(k in env for k in ("npm_config_store_dir", "PNPM_STORE_DIR"))
    assert all(budget.is_on_d(v) for v in env.values())
    assert budget.storage_problems(env) == []
    assert budget.face_args() == ["--user-data-dir=D:\\jarvis\\data\\face\\user-data"]
    assert budget.storage_env("E:\\x")["TEMP"] == "E:\\x\\tmp"          # the root is a parameter (and then the check fails on purpose)
    assert budget.storage_problems(budget.storage_env("E:\\x"))


def test_storage_check_names_the_bad_variable_in_one_line():
    bad = {"TEMP": "C:\\Users\\me\\AppData\\Local\\Temp", "HF_HOME": "D:\\jarvis\\cache\\hf", "JARVIS_DATA_DIR": "C:\\data", "PATH": "C:\\Windows"}
    assert budget.storage_problems(bad) == ["TEMP=C:\\Users\\me\\AppData\\Local\\Temp", "JARVIS_DATA_DIR=C:\\data"]
    with pytest.raises(budget.StorageError) as e:
        budget.require_d_drive(bad, "face-tools")
    msg = str(e.value)
    assert "\n" not in msg and msg.startswith("face-tools: refusing to start, not on the D: drive") and "TEMP=C:" in msg
    budget.require_d_drive({"TEMP": "D:\\jarvis\\tmp", "EMPTY": ""}, "x")      # no error
    budget.require_d_drive({"TEMP": ""}, "x")                                   # an empty value is "not set"


def ps1_env(path: Path) -> dict[str, str]:
    return dict(re.findall(r"^\$env:(\w+) = '([^']+)'$", path.read_text(encoding="utf-8-sig"), re.M))


def test_d_env_ps1_mirrors_storage_env():
    """d-env.ps1 (dot-sourced by jarvis-start.ps1, the installer and every start script) must set exactly what budget.storage_env() returns."""
    assert ps1_env(JARVIS / "scripts" / "d-env.ps1") == budget.storage_env()


def test_start_scripts_use_d_env_and_check_d():
    start = (JARVIS / "scripts" / "jarvis-start.ps1").read_text(encoding="utf-8-sig")
    assert 'd-env.ps1' in start and "refusing to start" in start and "--user-data-dir=" in start and "tools\\governor.py" in start
    inst = (JARVIS / "scripts" / "Install-JARVIS.ps1").read_text(encoding="utf-8-sig")
    assert "d-env.ps1" in inst and "is not on the D: drive" in inst and "PLAYWRIGHT_BROWSERS_PATH" in inst
    for name in ("local-models.ps1", "start-node.ps1", "start-console.ps1", "start-verdict.ps1"):
        assert "d-env.ps1" in (JARVIS / "scripts" / name).read_text(encoding="utf-8-sig"), name


def test_laptop_manifest_carries_d_only_env():
    comps = manifest.load(HERE / "manifests" / "laptop.json")
    want = budget.storage_env()
    assert comps
    for c in comps:
        for k in ("TEMP", "TMP", "HF_HOME", "XDG_CACHE_HOME", "PIP_CACHE_DIR", "UV_CACHE_DIR", "npm_config_cache"):
            assert k in c.env, (c.name, k)
        for k, v in c.env.items():
            if k in want:
                assert v.replace("/", "\\") == want[k], (c.name, k)
        assert budget.storage_problems(c.env) == []


def test_manifest_refuses_a_cache_path_on_c():
    base = {"name": "a", "argv": SLEEP}
    with pytest.raises(manifest.ManifestError) as e:
        manifest.parse({"components": [{**base, "env": {"HF_HOME": "C:/Users/me/.cache/huggingface"}}]})
    assert "not on the D: drive" in str(e.value) and "HF_HOME" in str(e.value)
    with pytest.raises(manifest.ManifestError):
        manifest.parse({"components": [{**base, "env": {"JARVIS_DATA_DIR": "relative/data"}}]})
    assert manifest.parse({"components": [{**base, "env": {"JARVIS_DATA_DIR": "D:/jarvis/data", "OTHER": "C:/anything"}}]})


def test_supervisor_refuses_to_start_a_service_whose_paths_are_not_on_d(tmp_path):
    comp = manifest.Component(name="svc", argv=SLEEP, env={"TEMP": "C:\\Temp"})
    sup = supervisor.Supervisor([comp], tmp_path)
    with pytest.raises(budget.StorageError, match="svc: refusing to start, not on the D: drive"):
        sup.start()
    assert sup.procs == {}                                  # nothing was started
    sup.stop()


def test_supervisor_injects_the_storage_environment_when_asked(tmp_path):
    comp = manifest.Component(name="svc", argv=[PY, "-c", "import os,sys; sys.exit(0 if os.environ['HF_HOME']=='D:\\\\jarvis\\\\cache\\\\hf' else 3)"])
    sup = supervisor.Supervisor([comp], tmp_path, storage_env=budget.storage_env())
    try:
        sup.start()
        assert sup.procs["svc"].wait(timeout=20) == 0
    finally:
        sup.stop()


def test_cpu_rate_is_in_hundredths_of_a_percent_of_the_whole_machine():
    assert jobobject.cpu_rate_value(20) == 2000
    assert jobobject.cpu_rate_value(18.75) == 1875
    assert jobobject.cpu_rate_value(0) == 1 and jobobject.cpu_rate_value(250) == 10000


def test_win32_structure_layouts():
    assert ctypes.sizeof(jobobject.CPURATE) == 8        # ControlFlags + CpuRate (JOBOBJECT_CPU_RATE_CONTROL_INFORMATION)
    assert ctypes.sizeof(jobobject.POWER) == 12         # PROCESS_POWER_THROTTLING_STATE
    assert jobobject.JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | jobobject.JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP == 0x5
    assert jobobject.JobObjectCpuRateControlInformation == 15 and jobobject.ProcessPowerThrottling == 4
    assert jobobject.BELOW_NORMAL_PRIORITY_CLASS == 0x4000


@pytest.mark.skipif(jobobject.IS_WINDOWS, reason="signals")
def test_suspend_and_resume_a_process_off_windows():
    import subprocess
    p = subprocess.Popen(SLEEP)
    try:
        assert jobobject.suspend_pid(p.pid) and jobobject.resume_pid(p.pid)
        assert jobobject.throttle_pid(p.pid) == {"priority": False, "eco": False, "error": None}      # Windows only: honest False
    finally:
        p.kill()
        p.wait()


def test_process_tree_without_limits_is_unchanged():
    with jobobject.ProcessTree() as t:
        p = t.start(SLEEP)
        assert p.poll() is None and t.problems == [] and t.limits is None
