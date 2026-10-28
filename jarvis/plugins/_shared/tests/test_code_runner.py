"""D77: the restricted runner for model-written code (plugins/_shared/code_runner.py). Real processes, no network, no model.

Isolation tests that depend on the OS (network namespace, Landlock, process limits) assert the behaviour where the result says the OS gave it, and say so (skip) where
it did not: the runner reports honestly what was in force (`isolation`). On the owner's Windows laptop they cannot be shown here (see D77: what only the laptop can verify).
"""
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from jarvis.plugins._shared import code_runner as C

R = C.LocalRunner()
FAST = C.Limits(cpu_s=2, wall_s=4, mem_mb=256, out_bytes=2000, max_procs=16)
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX isolation")


def have(lang):
    ok, _ = R.tool_status(lang)
    return pytest.mark.skipif(not ok, reason=f"no {lang} toolchain in the allowed places")


def one(lang, src, stdin="", lim=FAST):
    return R.execute(lang, src, [stdin], lim)[0]


def iso_ok(res, key, word):
    if word not in str(res.isolation.get(key, "")):
        pytest.skip(f"this OS did not give {key}: {res.isolation.get(key)}")


# ----------------------------------------------------------------------------- the five languages, correct answers
@have("python")
def test_python_correct_and_stdin():
    r = one("python", "n=int(input())\nprint(n*2)", "21\n")
    assert (r.status, r.stdout.strip(), r.exit_code) == ("ok", "42", 0)


@have("c")
def test_c_correct():
    r = R.execute("c", '#include <stdio.h>\nint main(){int n;scanf("%d",&n);printf("%d\\n",n*2);return 0;}', ["21", "5"], FAST)
    assert [x.stdout.strip() for x in r] == ["42", "10"] and all(x.status == "ok" for x in r)


@have("cpp")
def test_cpp_correct():
    r = one("cpp", '#include <iostream>\nint main(){int n;std::cin>>n;std::cout<<n*2<<"\\n";}', "21")
    assert (r.status, r.stdout.strip()) == ("ok", "42")


@have("java")
def test_java_correct_with_any_public_class_name():
    src = "import java.util.*;\npublic class Solver{public static void main(String[] a){Scanner s=new Scanner(System.in);System.out.println(s.nextInt()*2);}}"
    r = one("java", src, "21", C.Limits(cpu_s=20, wall_s=40, mem_mb=512, out_bytes=2000))
    assert (r.status, r.stdout.strip()) == ("ok", "42"), r


@have("javascript")
def test_javascript_correct():
    r = one("javascript", "console.log(Number(require('fs').readFileSync(0,'utf8'))*2)", "21")
    assert (r.status, r.stdout.strip()) == ("ok", "42")


@have("python")
def test_wrong_answer_is_ok_status_with_the_actual_output():
    r = one("python", "print(41)")
    assert r.status == "ok" and r.stdout.strip() == "41"          # the comparison with the expected output is the mission's job


@have("python")
def test_runtime_error_and_exit_code():
    r = one("python", "raise ValueError('boom')")
    assert r.status == "runtime_error" and r.exit_code == 1 and "boom" in r.stderr


@have("c")
def test_compile_error_is_reported_with_the_message():
    r = one("c", "int main(){ this is not C }")
    assert r.status == "compile_error" and r.stderr and "compile" in r.note


# ----------------------------------------------------------------------------- limits
@have("python")
def test_infinite_loop_is_killed_by_cpu_time():
    t = time.time()
    r = one("python", "while True: pass", lim=C.Limits(cpu_s=1, wall_s=8, mem_mb=256))
    assert r.status == "timeout" and time.time() - t < 6.5, r


@have("python")
def test_sleeping_forever_is_killed_by_wall_time():
    t = time.time()
    r = one("python", "import time\nwhile True: time.sleep(1)", lim=C.Limits(cpu_s=5, wall_s=1.5, mem_mb=256))
    assert r.status == "timeout" and "wall" in r.note and time.time() - t < 5


@have("python")
def test_memory_bomb_is_killed():
    r = one("python", "x = []\nwhile True:\n    x.append(bytearray(50*1024*1024))", lim=C.Limits(cpu_s=5, wall_s=8, mem_mb=200))
    assert r.status == "memory", r
    iso_ok(r, "memory", "limited")


@have("python")
def test_one_huge_allocation_is_refused():
    r = one("python", "x = bytearray(8*1024*1024*1024)\nprint(len(x))", lim=C.Limits(cpu_s=5, wall_s=8, mem_mb=200))
    assert r.status in ("memory", "runtime_error") and "8589934592" not in r.stdout, r
    assert r.status == "memory" or "MemoryError" in r.stderr or r.exit_code != 0


@have("python")
def test_huge_output_is_capped_and_the_run_killed():
    r = one("python", "import sys\nwhile True: sys.stdout.write('x'*10000)", lim=C.Limits(cpu_s=5, wall_s=8, mem_mb=256, out_bytes=3000))
    assert r.status == "output_limit" and len(r.stdout) <= 3000 and r.truncated, (r.status, len(r.stdout))


@have("python")
def test_big_but_finite_output_is_truncated_not_failed():
    r = one("python", "print('y'*20000)", lim=C.Limits(cpu_s=5, wall_s=8, mem_mb=256, out_bytes=5000))
    assert r.status == "ok" and len(r.stdout) == 5000 and r.truncated


@posix_only
@have("python")
def test_fork_bomb_attempt_is_contained():
    src = "import os\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        pass\n"
    t = time.time()
    r = one("python", src, lim=C.Limits(cpu_s=3, wall_s=6, mem_mb=256, max_procs=16))
    assert r.status in ("process_limit", "timeout", "runtime_error"), r
    assert time.time() - t < 12
    time.sleep(0.5)
    # nothing of the bomb is still running: no process of this user has python -c / jarvis-ps in its command line
    stragglers = []
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "replace")
            except OSError:
                continue
            if "jarvis-ps-" in cmd:
                stragglers.append(pid)
    assert not stragglers, stragglers


@posix_only
@have("python")
def test_a_background_process_does_not_outlive_the_run():
    src = ("import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', 'import time\\nfor _ in range(600): time.sleep(1)'])\nprint('parent done')\n")
    r = one("python", src)
    assert r.status == "ok" and "parent done" in r.stdout
    time.sleep(0.4)
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "replace")
            except OSError:
                continue
            assert "range(600)" not in cmd


# ----------------------------------------------------------------------------- isolation
@posix_only
@have("python")
def test_network_attempt_fails():
    src = ("import socket\ns=socket.socket(); s.settimeout(2)\n"
           "try:\n    s.connect(('1.1.1.1',80)); print('CONNECTED')\nexcept Exception as e:\n    print('FAILED', type(e).__name__)\n")
    r = one("python", src)
    assert r.status == "ok" and "CONNECTED" not in r.stdout, r
    iso_ok(r, "network", "unshared")
    assert "FAILED" in r.stdout


@posix_only
@have("python")
def test_loopback_server_is_not_reachable_either():
    src = ("import socket\nsrv=socket.socket(); srv.bind(('127.0.0.1',0)); srv.listen(1); port=srv.getsockname()[1]\n"
           "c=socket.socket(); c.settimeout(1)\ntry:\n    c.connect(('127.0.0.1',port)); print('LOOPBACK')\nexcept Exception as e:\n    print('NOLOOP')\n")
    r = one("python", src)
    iso_ok(r, "network", "unshared")
    assert "LOOPBACK" not in r.stdout


@posix_only
@have("python")
def test_writing_outside_the_temp_folder_is_refused(tmp_path):
    target = tmp_path / "owner-file.txt"
    target.write_text("original")
    src = f"open({str(target)!r}, 'w').write('HACKED')\nprint('wrote')\n"
    r = one("python", src)
    iso_ok(r, "fs", "restricted")
    assert r.status == "runtime_error" and "wrote" not in r.stdout
    assert target.read_text() == "original"
    victim = tmp_path / "new.txt"
    r2 = one("python", f"open({str(victim)!r}, 'w').write('x')\n")
    assert r2.status == "runtime_error" and not victim.exists()


@posix_only
@have("python")
def test_writing_inside_its_own_temp_folder_works():
    r = one("python", "open('a.txt','w').write('hello')\nprint(open('a.txt').read())\nimport os\nopen(os.environ['TMPDIR']+'/b','w').write('1')\nprint('ok')\n")
    assert r.status == "ok" and r.stdout.split() == ["hello", "ok"], r


@posix_only
@have("python")
def test_the_owners_files_cannot_be_read(tmp_path):
    secret = tmp_path / "secret-notes.txt"
    secret.write_text("TOPSECRET")
    r = one("python", f"print(open({str(secret)!r}).read())\n")
    iso_ok(r, "fs", "restricted")
    assert "TOPSECRET" not in r.stdout and r.status == "runtime_error"


@posix_only
@have("python")
def test_the_environment_is_minimal_and_holds_no_jarvis_or_secret_variable(monkeypatch):
    monkeypatch.setenv("JARVIS_FAKE_SECRET", "s3cret-value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    r = one("python", "import os\nprint(sorted(os.environ))\n")
    assert "JARVIS_FAKE_SECRET" not in r.stdout and "OPENAI_API_KEY" not in r.stdout and "s3cret" not in r.stdout
    assert "HOME" in r.stdout


@have("python")
def test_the_temp_folder_is_removed_afterwards(tmp_path):
    runner = C.LocalRunner(work_base=tmp_path)
    runner.execute("python", "open('f.txt','w').write('x')", [""], FAST)
    assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------------------------------- tools
def test_a_missing_compiler_is_reported_plainly_with_how_to_install(tmp_path):
    runner = C.LocalRunner(tool_paths={"gcc": [str(tmp_path / "nope" / "gcc")], "g++": [], "python": [], "javac": [], "java": [], "node": []})
    r = runner.execute("c", "int main(){}", ["a", "b"], FAST)
    assert len(r) == 2 and all(x.status == "missing_tool" for x in r)
    assert "Install" in r[0].note and "your step" in r[0].note
    ok, why = runner.tool_status("java")
    assert not ok and "JDK" in why


def test_tools_come_only_from_the_allowlist_never_from_path(tmp_path, monkeypatch):
    fake = tmp_path / "python3"
    fake.write_text("#!/bin/sh\necho EVIL\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    runner = C.LocalRunner(tool_paths={**C.tool_paths_from(None), "python": []})
    assert runner.execute("python", "print(1)", [""], FAST)[0].status == "missing_tool"
    assert C.find_tool("python", {"python": ["relative/python3"]}) is None


def test_config_cannot_add_a_relative_path_and_unknown_tools_are_ignored():
    table = C.tool_paths_from({"tool_paths": {"python": ["python3", "/usr/bin/python3"], "rm": ["/bin/rm"], "gcc": "no"}})
    assert table["python"] == ["/usr/bin/python3"] and "rm" not in table and table["gcc"]


def test_unsupported_language():
    r = R.execute("brainf", "+", ["x"], FAST)[0]
    assert r.status == "unsupported_language"
    assert C.norm_language("C++") == "cpp" and C.norm_language("Python3") == "python" and C.norm_language("js") == "javascript" and C.norm_language("rust") == ""


def test_limits_from_ignores_bad_values():
    lim = C.limits_from({"cpu_s": 3, "wall_s": "x", "mem_mb": -5, "evil": 9, "max_procs": True})
    assert lim.cpu_s == 3 and lim.wall_s == C.Limits().wall_s and lim.mem_mb == C.Limits().mem_mb and lim.max_procs == C.Limits().max_procs


# ----------------------------------------------------------------------------- the VM2 target (a fake transport; nothing connects anywhere)
def test_vm2_is_off_by_default_and_preferred_when_configured():
    calls = []

    def transport(req):
        calls.append(req)
        return {"ok": True, "results": [{"status": "ok", "stdout": "42\n", "exit_code": 0} for _ in req["stdins"]]}
    assert C.choose_runner({}, transport).runner.name == "local"
    assert C.choose_runner({"run_on_vm2": False}, transport).runner.name == "local"
    assert calls == []
    chosen = C.choose_runner({"run_on_vm2": True}, transport)
    assert chosen.runner.name == "vm2" and "VM2" in chosen.why
    out = chosen.runner.execute("python", "print(42)", ["", ""])
    assert [x.status for x in out] == ["ok", "ok"] and calls[0]["language"] == "python" and calls[0]["op"] == "run_code" and calls[0]["stdins"] == ["", ""]


def test_vm2_unreachable_or_not_set_up_falls_back_to_local_and_says_so():
    c1 = C.choose_runner({"run_on_vm2": True}, None)
    assert c1.runner.name == "local" and "no connection" in c1.why
    c2 = C.choose_runner({"run_on_vm2": True}, lambda r: {"ok": True}, vm2_probe=lambda: False)
    assert c2.runner.name == "local" and "did not answer" in c2.why
    c3 = C.choose_runner({"run_on_vm2": True}, lambda r: {"ok": True}, vm2_probe=lambda: 1 / 0)
    assert c3.runner.name == "local"


def test_a_vm2_transport_that_fails_is_an_error_result_not_a_crash():
    def boom(req):
        raise RuntimeError("connection refused")
    out = C.RemoteRunner(boom).execute("python", "print(1)", ["a"])
    assert out[0].status == "error" and "VM2" in out[0].note
    bad = C.RemoteRunner(lambda r: {"ok": True, "results": []}).execute("python", "print(1)", ["a"])
    assert bad[0].status == "error"


def test_the_module_never_imports_the_lab_or_touches_vm2():
    src = Path(C.__file__).read_text(encoding="utf-8")
    assert "jarvis.lab" not in src and "import lab" not in src and "paramiko" not in src and "ssh " not in src.lower().replace("ssh)", "")


@have("python")
def test_a_fraction_of_a_second_of_cpu_time_is_rounded_up_not_to_zero():
    r = one("python", "print(1+1)", lim=C.Limits(cpu_s=0.5, wall_s=4, mem_mb=256))
    assert r.status == "ok" and r.stdout.strip() == "2"


@have("python")
def test_runs_are_made_under_the_configured_work_dir(tmp_path):
    runner = C.LocalRunner(cfg={"work_dir": str(tmp_path / "runs")})
    r = runner.execute("python", "import os\nprint(os.getcwd())", [""], FAST)[0]
    assert r.status == "ok" and r.stdout.strip().startswith(str((tmp_path / "runs").resolve())) and list((tmp_path / "runs").iterdir()) == []


def test_windows_allowlist_covers_the_python_install_manager_layout(tmp_path, monkeypatch):
    """The laptop's Python is %LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe; the Windows table did not list that layout, so 'solve all the problems in <folder>'
    saved every solution as -unverified with 'Python 3 is not installed' (laptop 2026-10-28)."""
    table = C.DEFAULT_TOOL_PATHS["windows"]
    home = tmp_path / "Local"
    exe = home / "Python" / "pythoncore-3.14-64" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    monkeypatch.setenv("LOCALAPPDATA", str(home))
    exe.chmod(0o755)
    found = C.find_tool("python", {"python": table["python"]})
    assert found and found.lower().endswith(os.path.join("pythoncore-3.14-64", "python.exe").lower())


def test_the_windows_table_lists_the_laptops_own_mingw_folder():
    t = C.DEFAULT_TOOL_PATHS["windows"]
    bs = chr(92)
    assert f"C:{bs}mingw64{bs}bin{bs}gcc.exe" in t["gcc"] and f"C:{bs}mingw64{bs}bin{bs}g++.exe" in t["g++"]       # this laptop's gcc/g++ were not found before (2026-10-28): C and C++ solutions were saved "-unverified"
