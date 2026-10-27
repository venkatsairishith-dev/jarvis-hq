"""The persistent UI Automation worker's life (D68), against REAL child processes (tests/uia_fake_child.py speaks the protocol over a fake desktop and can crash,
hang, flood, garble and go deaf on request): start once, serve many, time out, restart with a back-off, fall back, shut down, and never repeat a changing job."""
import ast
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

from jarvis_node import runtime, uia  # noqa: E402

CHILD = os.path.join(HERE, "uia_fake_child.py")


def _win_alive(pid):
    """Windows: os.kill(pid, 0) is CTRL_C_EVENT there (and raises SystemError), so ask the process handle."""
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259          # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def pid_alive(pid):
    if sys.platform == "win32":
        return _win_alive(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        return open(f"/proc/{pid}/stat").read().split()[2] != "Z"           # a zombie that nobody waited for is dead
    except OSError:
        return True


def wait_dead(pid, s=5.0):
    end = time.time() + s
    while time.time() < end:
        if not pid_alive(pid):
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def mk(tmp_path):
    made = []

    def make(mode="ok", env=None, **kw):
        e = {**os.environ, "FAKE_UIA_START": mode, "FAKE_UIA_STARTS": str(tmp_path / "starts.txt"), "FAKE_UIA_GRACE": "0.3", **(env or {})}
        kw.setdefault("request_timeout", 5.0)
        kw.setdefault("ready_timeout", 5.0)
        kw.setdefault("backoff", (0.15, 0.3, 0.6))
        kw.setdefault("ping_after_idle_s", 3600)
        c = uia.WorkerClient([sys.executable, CHILD], env=e, **kw)
        made.append(c)
        return c
    make.starts = lambda: (tmp_path / "starts.txt").read_text().split() if (tmp_path / "starts.txt").exists() else []
    yield make
    for c in made:
        c.shutdown()
    uia.reset()


# ------------------------------------------------------------------------------------------------------------------------------ normal life
def test_one_process_serves_many_requests_and_says_which_library_it_is(mk):
    c = mk()
    pids = {c.request("pid")["pid"] for _ in range(25)}
    assert len(pids) == 1 and len(mk.starts()) == 1
    assert c.lib == "fake 1.0" and c.served == 25 and c.fails == 0 and c.status()["alive"]
    assert c.request("windows", {"proc": "calc"})["windows"][0]["name"] == "calc"


def test_a_request_is_much_faster_than_a_powershell_start(mk):
    c = mk()
    c.request("ping")
    t0 = time.perf_counter()
    for _ in range(50):
        c.request("ping")
    each = (time.perf_counter() - t0) / 50
    assert each < 0.05, f"a request took {each * 1000:.1f} ms here (the PowerShell it replaces needs 235 to 343 ms just to start)"


def test_the_workers_stdout_is_the_protocol_and_nothing_else(mk):
    c = mk()
    assert c.request("noise") == {"noisy": True}                       # a library that prints to stdout and to fd 1 cannot break the channel
    assert c.request("ping") == {"pong": True} and c.fails == 0


def test_the_real_main_function_protects_stdout_and_reports_a_missing_library(mk):
    c = mk(env={"FAKE_UIA_VIA_MAIN": "1"})
    assert c.request("noise") == {"noisy": True} and c.request("ping") == {"pong": True}
    c2 = mk("missing")
    with pytest.raises(uia.NotSent) as e:
        c2.request("ping")
    assert "library missing" in str(e.value) and c2.disabled


def test_a_missing_library_is_found_out_once_and_the_worker_stays_off(mk):
    c = mk("missing")
    for _ in range(3):
        with pytest.raises(uia.NotSent):
            c.request("ping")
    assert len(mk.starts()) == 1, "no new process per call: the library does not install itself while the node runs"
    assert not c.usable()


# ------------------------------------------------------------------------------------------------------------------------------ faults
def test_a_crash_in_the_middle_of_a_request_is_lost_and_the_next_start_waits_for_the_backoff(mk):
    c = mk()
    first = c.request("pid")["pid"]
    with pytest.raises(uia.Lost) as e:
        c.request("crash")
    assert e.value.why == "crash" and c.fails == 1 and wait_dead(first)
    with pytest.raises(uia.NotSent):                                   # inside the back-off: nobody is started, the caller uses PowerShell
        c.request("ping")
    assert len(mk.starts()) == 1
    time.sleep(0.2)
    assert c.request("pid")["pid"] != first and c.fails == 0 and len(mk.starts()) == 2, "restarted after the wait, and a good answer clears the count"


def test_the_backoff_grows_with_each_failure_in_a_row(mk):
    c = mk(backoff=(0.1, 0.4, 0.8))
    waits = []
    for _ in range(3):
        deadline = time.time() + 3
        while True:
            try:
                c.request("crash")
            except uia.Lost:
                break
            except uia.NotSent:
                if time.time() > deadline:
                    pytest.fail("never restarted")
                time.sleep(0.02)
        waits.append(round(c.next_try - c.clock(), 1))
    assert waits[0] <= 0.1 + 0.05 and waits[1] > waits[0] and waits[2] > waits[1]


def test_a_hung_request_times_out_the_worker_is_killed_and_replaced_later(mk):
    c = mk()
    pid = c.request("pid")["pid"]
    t0 = time.time()
    with pytest.raises(uia.Lost) as e:
        c.request("hang", timeout=0.4)
    assert e.value.why == "timeout" and 0.35 < time.time() - t0 < 3
    assert wait_dead(pid), "a hung worker is killed, not left behind"
    time.sleep(0.2)
    assert c.request("pid")["pid"] != pid


def test_a_line_that_is_too_long_kills_the_worker(mk):
    c = mk(max_line=64 * 1024)
    pid = c.request("pid")["pid"]
    with pytest.raises(uia.Lost) as e:
        c.request("flood")
    assert e.value.why == "oversize" and wait_dead(pid)


def test_a_line_that_is_not_json_kills_the_worker(mk):
    c = mk()
    pid = c.request("pid")["pid"]
    with pytest.raises(uia.Lost) as e:
        c.request("garble")
    assert e.value.why == "garbled" and wait_dead(pid)


def test_an_answer_over_the_workers_own_cap_is_a_clean_refusal_and_the_worker_lives(mk):
    c = mk()
    pid = c.request("pid")["pid"]
    with pytest.raises(uia.Refused) as e:
        c.request("big", {"n": 2 * 1024 * 1024})
    assert e.value.code == "oversize" and c.request("pid")["pid"] == pid


def test_a_worker_that_never_says_ready_is_given_up_on(mk):
    c = mk("silent", ready_timeout=0.4)
    t0 = time.time()
    with pytest.raises(uia.NotSent):
        c.request("ping")
    assert time.time() - t0 < 3 and c.fails == 1 and not c.alive()


def test_a_worker_that_dies_before_it_is_ready_and_a_protocol_mismatch(mk):
    c = mk("crash")
    with pytest.raises(uia.NotSent):
        c.request("ping")
    assert c.fails == 1
    c2 = mk("badproto")
    with pytest.raises(uia.NotSent):
        c2.request("ping")
    assert c2.disabled and "protocol" in c2.disabled


def test_a_program_that_cannot_be_started_is_not_sent_to(tmp_path):
    c = uia.WorkerClient([str(tmp_path / "no-such-python")], backoff=(0.1,))
    with pytest.raises(uia.NotSent):
        c.request("ping")
    assert c.fails == 1
    c.argv = None
    c.next_try = 0
    with pytest.raises(uia.NotSent):
        c.request("ping")


def test_a_worker_that_stops_answering_pings_after_a_quiet_spell_is_replaced_before_the_request(mk):
    c = mk(ping_after_idle_s=0.05)
    pid = c.request("pid")["pid"]
    c.request("deaf")                                                  # from now on its ping never comes back
    time.sleep(0.1)
    t0 = time.time()
    got = c.request("pid")["pid"]
    assert got != pid and len(mk.starts()) == 2 and time.time() - t0 < 6, "it did not wait for the whole request timeout"
    assert wait_dead(pid)


def test_a_worker_that_died_while_idle_is_replaced_at_once_without_losing_the_request(mk):
    c = mk()
    pid = c.request("pid")["pid"]
    os.kill(pid, 9)
    end = time.time() + 5
    while c.proc.poll() is None and time.time() < end:                 # until the node itself can see it is gone (a zombie is visible a moment before wait() reaps it)
        time.sleep(0.005)
    got = c.request("pid")["pid"]                                      # nobody was asking when it died: nothing was lost, one start at once, counted as a failure
    assert got != pid and len(mk.starts()) == 2 and c.fails == 0, "and a good answer clears the count"


def test_after_the_set_number_of_requests_a_fresh_process_takes_over_without_a_failure(mk):
    c = mk(recycle_after=3)
    p1 = c.request("pid")["pid"]
    c.request("ping")
    c.request("ping")                                                  # the third answer ends this process
    assert wait_dead(p1) and c.fails == 0
    assert c.request("pid")["pid"] != p1 and len(mk.starts()) == 2


# ------------------------------------------------------------------------------------------------------------------------------ shutdown and idle
def test_shutdown_asks_politely_and_leaves_no_process(mk):
    c = mk()
    pid = c.request("pid")["pid"]
    c.shutdown()
    assert wait_dead(pid) and not c.alive()
    c.shutdown()                                                       # twice is harmless


def test_shutdown_kills_a_worker_that_is_stuck_and_cannot_hear_the_quit(mk):
    c = mk()
    pid = c.request("pid")["pid"]
    with pytest.raises(uia.Lost):
        c.request("hang", timeout=0.2)                                 # (killed by the timeout already)
    assert wait_dead(pid)
    c2 = mk()
    pid2 = c2.request("pid")["pid"]
    c2.proc.stdin.write(b'{"id":99,"op":"hang","args":{}}\n')          # busy inside an application when the node shuts down
    c2.proc.stdin.flush()
    time.sleep(0.1)
    t0 = time.time()
    c2.shutdown()
    assert wait_dead(pid2) and time.time() - t0 < 6


def test_when_the_node_dies_the_pipe_closes_and_even_a_stuck_worker_exits(mk):
    c = mk()
    pid = c.request("pid")["pid"]
    c.proc.stdin.write(b'{"id":99,"op":"hang","args":{}}\n')
    c.proc.stdin.flush()
    time.sleep(0.1)
    c.proc.stdin.close()                                               # what the operating system does when the node process ends
    assert wait_dead(pid, 5), "end of input must end the worker even while the main loop is inside a hung application (grace 0.3 s here)"


def test_an_idle_worker_is_stopped_by_the_reaper_and_started_again_by_the_next_request(mk):
    c = mk(idle_exit_s=0.1)
    p1 = c.request("pid")["pid"]
    assert c.reap() is False, "just used"
    time.sleep(0.2)
    assert c.reap() is True and wait_dead(p1) and not c.alive()
    assert c.fails == 0 and c.request("pid")["pid"] != p1, "an idle stop is no failure and has no back-off"


def test_the_reaper_never_stops_a_worker_that_is_busy(mk):
    c = mk(idle_exit_s=0.0)
    c.request("ping")
    import threading
    t = threading.Thread(target=lambda: c.request("slow", {"s": 0.6}))
    t.start()
    time.sleep(0.2)
    assert c.reap() is False
    t.join()


# ------------------------------------------------------------------------------------------------------------------------------ how it is started
def test_it_is_started_without_a_shell_at_the_nodes_own_priority_with_stdout_as_the_channel():
    seen = {}

    class P:
        pid = 1
        stdin = stdout = None

        def poll(self):
            return 1

    def fake_popen(argv, **kw):
        seen.update(kw, argv=argv)
        raise OSError("not really")
    c = uia.WorkerClient(["py", "-I", "uia_worker.py"], popen=fake_popen)
    with pytest.raises(uia.NotSent):
        c.request("ping")
    assert isinstance(seen["argv"], list) and "shell" not in seen and seen["stdout"] == subprocess.PIPE and seen["stdin"] == subprocess.PIPE
    assert seen["stderr"] == subprocess.DEVNULL
    assert seen["creationflags"] & (0x4000 | 0x40 | 0x80 | 0x100 | 0x8000 | 0x20) == 0, "no priority class: it runs at the node's normal priority"


def test_the_default_command_is_the_uia_venv_python_with_the_workers_script_in_isolated_mode(monkeypatch, tmp_path):
    py = tmp_path / ("python.exe" if os.name == "nt" else "python")
    py.write_text("")
    monkeypatch.setenv("JARVIS_UIA_PYTHON", str(py))
    assert uia._argv() == [str(py), "-I", uia.worker_script()] and os.path.isfile(uia.worker_script())
    monkeypatch.setenv("JARVIS_UIA_PYTHON", str(tmp_path / "missing"))
    assert uia._argv() is None


def test_the_worker_and_its_client_have_no_network_and_no_shell():
    banned = {"socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "xmlrpc", "asyncio"}
    for name in ("uia_worker.py", "uia_lib_uiautomation.py", "uia.py"):
        src = open(os.path.join(HERE, "..", "jarvis_node", name), encoding="utf-8").read()
        tree = ast.parse(src)
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        assert not (mods & banned), f"{name} imports {mods & banned}"
        assert "shell=True" not in src and "os.system" not in src and "Popen(" not in src.replace("popen=subprocess.Popen", "").replace("self._popen(", "")
        if name != "uia.py":
            assert "subprocess" not in mods, f"{name} starts no program"


# ------------------------------------------------------------------------------------------------------------------------------ the node-side rules (call())
@pytest.fixture
def node(mk):
    c = mk()
    uia.set_client(c)
    yield c
    uia.set_client(None)


def test_a_refusal_becomes_the_fixed_reason_code_of_the_powershell_path(node):
    with pytest.raises(RuntimeError, match=r"^ui-action-failed:no element$"):
        uia.call("click", {"proc": "calc", "elem": "Minus"})
    with pytest.raises(RuntimeError, match=r"^ui-action-failed:no window$"):
        uia.call("click", {"proc": "nothere", "elem": "x"})


def test_a_changing_job_is_never_repeated_through_powershell_after_a_crash_or_a_hang(node):
    with pytest.raises(RuntimeError, match=r"^ui-action-failed:other$"):
        uia.call("crash", {})
    time.sleep(0.2)
    with pytest.raises(RuntimeError, match=r"^ui-action-failed:other$"):
        uia.call("hang", {}, timeout=0.3)


def test_a_read_that_met_a_crash_is_asked_again_the_old_way_but_a_hung_read_is_not(node, monkeypatch):
    monkeypatch.setattr(uia, "READS", uia.READS | {"crash", "hang"})
    assert uia.call("crash", {}) is None                                # None = "use the PowerShell script"
    time.sleep(0.2)
    with pytest.raises(RuntimeError):
        uia.call("hang", {}, timeout=0.3)                                # a hung application would hang PowerShell too


def test_during_the_backoff_every_call_says_use_powershell(node):
    with pytest.raises(RuntimeError):
        uia.call("crash", {})
    assert uia.call("windows", {"proc": "calc"}) is None and uia.call("click", {"proc": "calc", "elem": "num7Button"}) is None
    assert not node.usable()


def test_the_switch_turns_the_worker_off_even_when_one_is_installed(node, monkeypatch):
    assert uia.enabled()
    monkeypatch.setenv("JARVIS_UIA_WORKER", "0")
    assert not uia.enabled() and uia.client() is None and uia.call("ping", {}) is None
    assert uia.windows_of("calc") is None


def test_a_unit_test_never_starts_the_real_worker_on_its_own(monkeypatch):
    uia.reset()
    monkeypatch.delenv("JARVIS_UIA_WORKER", raising=False)
    assert not uia.enabled() and uia.client() is None


def test_a_cancel_kills_the_worker_the_job_is_over_and_no_failure_is_counted(mk):
    seen = []
    c = mk(adopt=lambda p: seen.append(p), release=lambda p: seen.append("released"), cancelled=lambda: True)
    pid = c.request("pid")["pid"]
    import threading
    threading.Timer(0.2, lambda: c.proc.kill()).start()               # what runtime.cancel does to the registered process
    with pytest.raises(uia.Lost) as e:
        c.request("hang", timeout=5)
    assert e.value.why == "cancelled" and c.fails == 0 and wait_dead(pid)
    assert seen[0].pid and "released" in seen, "the process was registered for the cancel while the request waited, and let go after"


def test_runtime_registers_the_worker_for_one_request_so_the_jobs_cancel_reaches_it(mk, monkeypatch):
    c = mk(adopt=runtime.adopt, release=runtime.release, cancelled=runtime.cancelled)
    runtime.begin("job-1")
    try:
        c.request("ping")
        assert runtime._current["procs"] == [], "released after the request: a later job's cancel never touches an idle worker"
        import threading
        threading.Timer(0.2, lambda: runtime.cancel("job-1")).start()
        with pytest.raises(uia.Lost) as e:
            c.request("hang", timeout=5)
        assert e.value.why == "cancelled" and c.fails == 0
        with pytest.raises(runtime.Cancelled):
            uia.set_client(c)
            try:
                uia.call("ping", {})
            finally:
                uia.set_client(None)
    finally:
        runtime.end()


# ------------------------------------------------------------------------------------------------------------------------------ ids
def test_an_answer_for_another_request_id_is_ignored_and_a_wrong_shape_is_a_refusal():
    import io

    class FakeProc:
        pid = 4242
        returncode = None

        def __init__(self, lines):
            self.stdin, self.stdout = io.BytesIO(), io.BytesIO(lines)

        def poll(self):
            return None

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode
    hello = b'{"ready":true,"proto":1,"lib":"x","pid":1}\n'
    stale = b'{"id":99,"ok":true,"result":{"from":"an older request"}}\n'
    good = b'{"id":1,"ok":true,"result":{"mine":true}}\n'
    c = uia.WorkerClient(["x"], popen=lambda argv, **kw: FakeProc(hello + stale + good), request_timeout=2)
    assert c.request("ping") == {"mine": True}
    assert c.proc.stdin.getvalue() == b'{"id":1,"op":"ping","args":{}}\n'
    c2 = uia.WorkerClient(["x"], popen=lambda argv, **kw: FakeProc(hello + b'{"id":1,"ok":true,"result":"not a dict"}\n'), request_timeout=2)
    with pytest.raises(uia.Refused) as e:
        c2.request("ping")
    assert e.value.code == "other"
