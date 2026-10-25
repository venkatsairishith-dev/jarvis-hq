"""Process-tree ownership (plan P1 step 4). On Windows a Job Object with KILL_ON_JOB_CLOSE: when the supervisor dies for any reason,
Windows closes the job handle and kills every process in it, so no orphan survives. Elsewhere: a new session per child and killpg on stop.

Footprint budget (D50, jarvis/supervisor/budget.py): the same job can also carry a CPU rate HARD CAP (JOBOBJECT_CPU_RATE_CONTROL_INFORMATION,
CpuRate in 1/100 of a percent of the WHOLE machine) and a job memory ceiling (committed bytes) as a backstop, and every child can be set to
BELOW_NORMAL priority and EcoQoS (PROCESS_POWER_THROTTLING). The helpers at the bottom (throttle_pid, adopt_pid, suspend_pid, ...) are also used
by jarvis/tools/governor.py for processes that jarvis-start.ps1 started. Nothing here changes behaviour unless `limits` is passed."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"
BELOW_NORMAL_PRIORITY_CLASS = 0x4000
JobObjectExtendedLimitInformation = 9
JobObjectCpuRateControlInformation = 15
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4
ProcessPowerThrottling = 4
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_SET_INFORMATION = 0x0200
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True)
class JobLimits:
    """What a job enforces besides ownership. cpu_percent is of the whole machine (all logical processors)."""
    cpu_percent: float | None = None
    job_memory_bytes: int | None = None
    kill_on_close: bool = True          # the supervisor's tree: True. The governor's adoption job: False (closing it must not kill JARVIS)
    throttle: bool = True               # BELOW_NORMAL + EcoQoS for every process put in the job


def cpu_rate_value(percent: float) -> int:
    """JOBOBJECT_CPU_RATE_CONTROL_INFORMATION.CpuRate: 1/100 of a percent of the whole machine, 1..10000 (20 percent is 2000)."""
    return max(1, min(10000, int(round(float(percent) * 100))))


class CPURATE(ctypes.Structure):      # JOBOBJECT_CPU_RATE_CONTROL_INFORMATION (the union is one DWORD here: CpuRate); 8 bytes
    _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]


class POWER(ctypes.Structure):        # PROCESS_POWER_THROTTLING_STATE; 12 bytes
    _fields_ = [("Version", ctypes.c_uint32), ("ControlMask", ctypes.c_uint32), ("StateMask", ctypes.c_uint32)]


if IS_WINDOWS:
    from ctypes import wintypes

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                   "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]


def _apply_limits(job, limits: JobLimits) -> list[str]:
    """Set kill-on-close, the memory ceiling and the CPU hard cap on `job`. Returns what Windows refused, as short strings (empty = all set)."""
    problems: list[str] = []
    info = _EXT()
    flags = 0
    if limits.kill_on_close:
        flags |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if limits.job_memory_bytes:
        flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
        info.JobMemoryLimit = int(limits.job_memory_bytes)
    info.BasicLimitInformation.LimitFlags = flags
    if not _k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
        problems.append(f"job limits (kill/memory) error {ctypes.get_last_error()}")
    if limits.cpu_percent:
        rate = CPURATE(JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP, cpu_rate_value(limits.cpu_percent))
        if not _k32.SetInformationJobObject(job, JobObjectCpuRateControlInformation, ctypes.byref(rate), ctypes.sizeof(rate)):
            problems.append(f"cpu hard cap error {ctypes.get_last_error()}")
    return problems


def create_job(limits: JobLimits, name: str | None = None):
    """A Job Object with `limits` (Windows only). Returns (handle, problems). The caller keeps the handle open for as long as the
    limits must hold. A job made with kill_on_close=False survives its creator: the processes keep running and the caps stay."""
    job = _k32.CreateJobObjectW(None, name)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    return job, _apply_limits(job, limits)


def adopt_pid(job, pid: int) -> tuple[bool, int]:
    """Put a running process into `job`. (True, 0) or (False, win32 error). 5 (access denied) is the usual answer for a process that
    already sits in a job that cannot be nested with this one; the caller then relies on the soft governor for that process."""
    h = _k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
    if not h:
        return False, ctypes.get_last_error()
    try:
        if _k32.AssignProcessToJobObject(job, h):
            return True, 0
        return False, ctypes.get_last_error()
    finally:
        _k32.CloseHandle(h)


def in_job(job, pid: int) -> bool:
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        res = wintypes.BOOL(0)
        return bool(_k32.IsProcessInJob(h, job, ctypes.byref(res))) and bool(res.value)
    finally:
        _k32.CloseHandle(h)


def throttle_pid(pid: int) -> dict:
    """BELOW_NORMAL priority class and EcoQoS (power throttling, Windows 10 1709+) for one process, so JARVIS yields to the owner's apps.
    Returns {"priority": bool, "eco": bool, "error": int|None}. Off Windows both are False."""
    out = {"priority": False, "eco": False, "error": None}
    if not IS_WINDOWS:
        return out
    h = _k32.OpenProcess(PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        out["error"] = ctypes.get_last_error()
        return out
    try:
        out["priority"] = bool(_k32.SetPriorityClass(h, BELOW_NORMAL_PRIORITY_CLASS))
        st = POWER(1, PROCESS_POWER_THROTTLING_EXECUTION_SPEED, PROCESS_POWER_THROTTLING_EXECUTION_SPEED)
        out["eco"] = bool(_k32.SetProcessInformation(h, ProcessPowerThrottling, ctypes.byref(st), ctypes.sizeof(st)))
        if not (out["priority"] and out["eco"]):
            out["error"] = ctypes.get_last_error()
    finally:
        _k32.CloseHandle(h)
    return out


def _signal_pid(pid: int, name: str) -> bool:
    import signal
    try:
        os.kill(pid, getattr(signal, name))
        return True
    except OSError:
        return False


def suspend_pid(pid: int) -> bool:
    """Freeze every thread of a process (NtSuspendProcess; SIGSTOP off Windows). Always pair with resume_pid in a finally."""
    if not IS_WINDOWS:
        return _signal_pid(pid, "SIGSTOP")
    h = _k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not h:
        return False
    try:
        return _ntdll.NtSuspendProcess(h) == 0
    finally:
        _k32.CloseHandle(h)


def resume_pid(pid: int) -> bool:
    if not IS_WINDOWS:
        return _signal_pid(pid, "SIGCONT")
    h = _k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not h:
        return False
    try:
        return _ntdll.NtResumeProcess(h) == 0
    finally:
        _k32.CloseHandle(h)


class ProcessTree:
    """Owns every child it starts. Use as a context manager or call close().

    With `limits` (Windows) the job also enforces the CPU hard cap and the memory ceiling, and each child starts BELOW_NORMAL and is set to
    EcoQoS. `problems` lists what Windows refused (empty when everything was set)."""

    def __init__(self, limits: JobLimits | None = None) -> None:
        self.children: list[subprocess.Popen] = []
        self.limits = limits
        self.problems: list[str] = []
        self._job = None
        if IS_WINDOWS:
            job = _k32.CreateJobObjectW(None, None)
            if not job:
                raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
            info = _EXT()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
            self._job = job
            if limits:
                self.problems = _apply_limits(job, JobLimits(limits.cpu_percent, limits.job_memory_bytes, True, limits.throttle))

    def start(self, argv: list[str], cwd: str | None = None, env: dict[str, str] | None = None, **kw) -> subprocess.Popen:
        full_env = {**os.environ, **(env or {})}
        if IS_WINDOWS:
            if self.limits and self.limits.throttle:
                kw["creationflags"] = kw.get("creationflags", 0) | BELOW_NORMAL_PRIORITY_CLASS
            p = subprocess.Popen(argv, cwd=cwd, env=full_env, **kw)
            h = _k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, p.pid)
            try:
                if not h or not _k32.AssignProcessToJobObject(self._job, h):
                    p.kill()
                    raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
            finally:
                if h:
                    _k32.CloseHandle(h)
            if self.limits and self.limits.throttle:
                t = throttle_pid(p.pid)
                if not t["eco"]:
                    self.problems.append(f"pid {p.pid}: EcoQoS not set (error {t['error']})")
        else:
            p = subprocess.Popen(argv, cwd=cwd, env=full_env, start_new_session=True, **kw)
        self.children.append(p)
        return p

    def close(self) -> None:
        if IS_WINDOWS:
            if self._job:
                _k32.TerminateJobObject(self._job, 1)
                _k32.CloseHandle(self._job)
                self._job = None
        else:
            import signal
            for p in self.children:
                if p.poll() is None:
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except OSError:
                        pass
        for p in self.children:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    def __enter__(self) -> "ProcessTree":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
