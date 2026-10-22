"""Process-tree ownership (plan P1 step 4). On Windows a Job Object with KILL_ON_JOB_CLOSE: when the supervisor dies for any reason,
Windows closes the job handle and kills every process in it, so no orphan survives. Elsewhere: a new session per child and killpg on stop."""
from __future__ import annotations

import os
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001

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


class ProcessTree:
    """Owns every child it starts. Use as a context manager or call close()."""

    def __init__(self) -> None:
        self.children: list[subprocess.Popen] = []
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

    def start(self, argv: list[str], cwd: str | None = None, env: dict[str, str] | None = None, **kw) -> subprocess.Popen:
        full_env = {**os.environ, **(env or {})}
        if IS_WINDOWS:
            p = subprocess.Popen(argv, cwd=cwd, env=full_env, **kw)
            h = _k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, p.pid)
            try:
                if not h or not _k32.AssignProcessToJobObject(self._job, h):
                    p.kill()
                    raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
            finally:
                if h:
                    _k32.CloseHandle(h)
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
