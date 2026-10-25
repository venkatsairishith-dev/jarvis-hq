"""Tests for jarvis/tools/footprint.py with fake process lists (no Windows needed): process matching, CPU/RAM/VRAM math, verdicts,
the averaging window, the outside-D: scan, the Windows parsers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.supervisor import budget as B  # noqa: E402
from jarvis.tools import footprint as F  # noqa: E402

MB = F.MB
FACE = r"D:\APPS\jarvis\jarvis.exe"
VENV = r"D:\jarvis\tools\agent-venv\Scripts\python.exe"


def P(pid, ppid, name, exe="", cmd="", ws=0, priv=0, cpu=0.0, w=0, gpu=None):
    return F.Proc(pid, ppid, name, exe, cmd, ws * MB, priv * MB, cpu, w * MB, None if gpu is None else gpu * MB)


def machine():
    """A laptop mid-day: the owner's apps, JARVIS's processes, and a few look-alikes that must NOT be counted."""
    return [
        P(4, 0, "System"), P(100, 4, "explorer.exe", r"C:\Windows\explorer.exe", "explorer.exe"),
        P(200, 100, "chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome.exe --profile-directory=Default", ws=900),
        P(210, 100, "Code.exe", r"C:\Users\me\AppData\Local\Programs\Microsoft VS Code\Code.exe", r'Code.exe D:\jarvis\hq', ws=400),      # opens the repo: not JARVIS
        P(220, 100, "powershell.exe", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "powershell.exe", ws=80),            # owner's shell
        P(300, 100, "jarvis.exe", FACE, FACE, ws=250),                                                                                    # the face
        P(301, 300, "jarvis.exe", FACE, FACE + " --type=renderer", ws=300),
        P(302, 300, "jarvis.exe", FACE, FACE + " --type=gpu-process", ws=200, gpu=300),
        P(400, 100, "python.exe", VENV, VENV + r" -m jarvis.bridge.face_tools --data D:\jarvis\data\muse", ws=500),
        P(410, 100, "python.exe", r"D:\jarvis\tools\privacy-venv\Scripts\python.exe", r"D:\jarvis\tools\privacy-venv\Scripts\python.exe D:\jarvis\hq\jarvis\privacy\gateway.py --port 48950", ws=900),
        P(420, 100, "python.exe", r"D:\jarvis\tools\jarvis-venv\Scripts\python.exe", r"python.exe D:\jarvis\hq\jarvis\scripts\local_speech_server.py --port 48930", ws=700),
        P(430, 100, "llama-swap.exe", r"D:\jarvis\tools\llama-swap\v261\llama-swap.exe", r"llama-swap.exe --config D:\jarvis\hq\jarvis\local_models\llama-swap.yaml", ws=40),
        P(431, 430, "llama-server.exe", r"D:\jarvis\tools\llama.cpp\b11669\vulkan\llama-server.exe", "llama-server.exe -m x.gguf -t 3", ws=3000, gpu=1200),
        P(500, 100, "node.exe", r"C:\Program Files\nodejs\node.exe", r"node.exe C:\Users\me\AppData\npm\node_modules\@playwright\mcp\cli.js --extension", ws=150),
        P(501, 500, "conhost.exe", r"C:\Windows\System32\conhost.exe", "conhost.exe", ws=10),
        P(600, 100, "cua-driver.exe", r"D:\jarvis\tools\cua\cua-driver.exe", "cua-driver.exe serve", ws=60),
        P(700, 100, "python.exe", r"D:\jarvis\tools\agent-venv\Scripts\python.exe", r"python.exe D:\jarvis\hq\jarvis\supervisor\core.py D:\x\laptop.json", ws=50),
        P(710, 100, "python.exe", VENV, VENV + r" D:\jarvis\hq\jarvis\tools\governor.py", ws=40),
        P(720, 100, "python.exe", VENV, VENV + r" D:\jarvis\hq\jarvis\tools\footprint.py --json", ws=40),      # this program
        P(721, 720, "powershell.exe", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "powershell.exe -NoProfile -Command Get-CimInstance", ws=60),
        P(800, 100, "python.exe", r"C:\Python311\python.exe", r"python.exe D:\jarvis\hq\jarvis\muse\runner_tool.py", ws=30),            # an interpreter running a JARVIS script
        P(900, 100, "python.exe", r"C:\Python311\python.exe", r"python.exe C:\work\other.py", ws=30),
    ]


def test_classify_finds_each_jarvis_component():
    got = F.jarvis_processes(machine(), own_pid=720)
    names = {pid: c for pid, c in got.items()}
    assert names[300] == names[301] == names[302] == "face"
    assert names[400] == "face-tools" and names[410] == "privacy-gateway" and names[420] == "local-speech"
    assert names[430] == "llama-swap" and names[431] == "llama-server"
    assert names[500] == "playwright-mcp" and names[501] == "playwright-mcp"          # a child inherits its parent's component
    assert names[600] == "cua-driver" and names[700] == "supervisor" and names[710] == "governor"
    assert names[800] == "jarvis-other"


def test_the_owners_apps_and_this_program_are_not_counted():
    got = F.jarvis_processes(machine(), own_pid=720)
    for pid in (4, 100, 200, 210, 220, 720, 721, 900):
        assert pid not in got, pid


def test_protected_components_are_the_face_kernel_and_supervisor():
    assert {"face", "kernel", "supervisor", "governor", "console", "node-agent"} <= F.PROTECTED
    assert "llama-server" not in F.PROTECTED and "llama-swap" not in F.PROTECTED


def test_kernel_console_and_node_are_recognised():
    ps = [P(1, 0, "python.exe", VENV, VENV + " -m jarvis_kernel serve"), P(2, 0, "pythonw.exe", r"D:\jarvis\tools\jarvis-venv\Scripts\pythonw.exe", "pythonw.exe -m jarvis_console"),
          P(3, 0, "python.exe", VENV, VENV + r" -m jarvis_node.loop"), P(40, 0, "python.exe", r"D:\jarvis-apps\openjev\.venv\Scripts\python.exe", "python.exe -m openjev")]
    assert F.jarvis_processes(ps, 99) == {1: "kernel", 2: "console", 3: "node-agent", 40: "verdict"}


def snap(t, procs, gpu=None):
    comp = F.jarvis_processes(procs, own_pid=720)
    return F.Snapshot(t, {p.pid: p for p in procs if p.pid in comp}, comp, gpu)


def test_cpu_percent_is_of_the_whole_machine():
    a = machine()
    b = [F.Proc(**{**p.__dict__}) for p in machine()]
    for p in b:
        if p.pid == 431:
            p.cpu_s += 24.0           # llama-server burned 24 CPU-seconds in 10 s: 2.4 threads
        if p.pid == 300:
            p.cpu_s += 4.0
    d = F.diff(snap(0.0, a), snap(10.0, b), threads=16)
    by = {r["name"]: r for r in d["components"]}
    assert by["llama-server"]["cpu_pct"] == pytest.approx(100 * 24 / (10 * 16))      # 15.0
    assert by["face"]["cpu_pct"] == pytest.approx(2.5)
    assert F.totals(d["components"])["cpu_pct"] == pytest.approx(17.5)
    assert by["face"]["procs"] == 3 and by["face"]["ws_mb"] == 750 and by["face"]["vram_mb"] == 300
    assert d["components"][0]["name"] == "llama-server"                              # sorted by CPU


def test_a_process_that_started_inside_the_interval_counts_from_zero():
    a = [P(1, 0, "llama-swap.exe", r"D:\jarvis\tools\llama-swap\v261\llama-swap.exe", "", cpu=1.0)]
    b = a + [P(2, 1, "llama-server.exe", r"D:\jarvis\tools\llama.cpp\x\llama-server.exe", "", cpu=8.0)]
    rows = {r["name"]: r for r in F.diff(snap(0, a), snap(4, b), threads=8)["components"]}
    assert rows["llama-server"]["cpu_pct"] == pytest.approx(100 * 8 / (4 * 8))


def test_disk_write_rate_and_vram_unreadable():
    a = [P(1, 0, "node.exe", r"D:\jarvis\tools\x\node.exe", r"node.exe D:\jarvis\hq\a.js", w=10)]
    b = [P(1, 0, "node.exe", r"D:\jarvis\tools\x\node.exe", r"node.exe D:\jarvis\hq\a.js", w=30)]
    d = F.diff(snap(0, a), snap(10, b), 16)
    assert d["components"][0]["write_mb_s"] == pytest.approx(2.0) and d["components"][0]["vram_mb"] is None
    assert F.totals(d["components"])["vram_mb"] is None


def rows_total(cpu, ws, vram, write=0.0):
    rows = [{"name": "llama-server", "procs": 1, "pids": [1], "protected": False, "cpu_pct": cpu, "cpu_s": 0, "ws_mb": ws, "private_mb": ws, "vram_mb": vram, "write_mb_s": write}]
    return rows, F.totals(rows)


def test_verdict_ok_and_over_against_the_owners_numbers():
    bud = B.Budget()
    _, t = rows_total(19.9, 6554, 2048)
    vs = {v["metric"]: v for v in F.verdicts(t, 19.9, 60, bud, None, True)}
    assert {k: v["status"] for k, v in vs.items()} == {"cpu": "OK", "ram": "OK", "vram": "OK", "disk_write": "OK"}      # at the limit is OK
    _, t = rows_total(20.1, 6555, 2049, 5.5)
    vs = {v["metric"]: v for v in F.verdicts(t, 20.1, 60, bud, None, True)}
    assert all(v["status"] == "OVER" for v in vs.values())
    assert F.overall(list(vs.values())) == "OVER"


def test_vram_not_readable_is_na_not_ok():
    _, t = rows_total(1, 1, None)
    vs = {v["metric"]: v for v in F.verdicts(t, 1, 5, B.Budget(), None, False)}
    assert vs["vram"]["status"] == "N/A" and "not readable" in vs["vram"]["note"]
    assert F.overall(list(vs.values())) == "OK"


def test_outside_d_verdict_levels():
    _, t = rows_total(1, 1, 1)
    none = {"jarvis_items": 0, "jarvis_mb": 0, "suspect_items": 0, "suspect_mb": 0}
    caches = {"jarvis_items": 0, "jarvis_mb": 0, "suspect_items": 2, "suspect_mb": 1200.0}
    mine = {"jarvis_items": 1, "jarvis_mb": 55.0, "suspect_items": 0, "suspect_mb": 0}
    st = lambda o: [v for v in F.verdicts(t, 1, 5, B.Budget(), o, True) if v["metric"] == "outside_d"][0]["status"]  # noqa: E731
    assert (st(none), st(caches), st(mine)) == ("OK", "CHECK", "OVER")


def test_top_offenders_name_the_biggest_over_budget_components():
    rows = [{"name": "face", "protected": True, "cpu_pct": 12.0, "ws_mb": 1000, "vram_mb": 300, "write_mb_s": 0.1},
            {"name": "llama-server", "protected": False, "cpu_pct": 15.0, "ws_mb": 6000, "vram_mb": 2500, "write_mb_s": 0.0},
            {"name": "node", "protected": False, "cpu_pct": 1.0, "ws_mb": 100, "vram_mb": 0, "write_mb_s": 0.0}]
    vs = [{"metric": "cpu", "status": "OVER"}, {"metric": "vram", "status": "OVER"}, {"metric": "ram", "status": "OK"}]
    off = F.offenders(rows, vs)
    assert off["cpu"][:2] == ["llama-server 15.0", "face 12.0*"] and off["vram"][0] == "llama-server 2500" and "ram" not in off


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def fake_sampler(clock, script):
    """`script(t)` returns the process list at time t."""
    return F.Sampler(procs=lambda: script(clock.t), gpu=lambda: None, now=clock.now, sleep=clock.sleep, threads=lambda: 16, ram_mb=lambda: 32768, own_pid=720)


def test_the_meter_averages_cpu_over_the_window():
    clock = FakeClock()
    bud = B.Budget(window_s=30)

    def procs(t):
        # a burst: 40 % of the machine (6.4 threads) between t=5 and t=10, nothing else
        busy = min(max(t - 5, 0), 5) * 6.4
        return [P(1, 0, "llama-server.exe", r"D:\jarvis\tools\llama.cpp\x\llama-server.exe", "", ws=2000, cpu=busy)]

    m = F.Meter(fake_sampler(clock, procs), bud)
    r1 = m.sample(5.0)       # t 0..5: idle
    r2 = m.sample(5.0)       # t 5..10: the burst
    assert r1["total"]["cpu_pct"] == 0 and r2["total"]["cpu_pct"] == pytest.approx(40.0)
    cpu = [v for v in r2["verdicts"] if v["metric"] == "cpu"][0]
    assert cpu["value"] == pytest.approx(20.0) and cpu["status"] == "OK"          # (0 + 40) / 2 over 10 s
    r3 = m.sample(5.0)       # t 10..15: idle again
    assert [v for v in r3["verdicts"] if v["metric"] == "cpu"][0]["value"] == pytest.approx(40 * 5 / 15, abs=0.01)
    for _ in range(6):
        r = m.sample(5.0)
    assert r["window_s"] <= 35 and [v for v in r["verdicts"] if v["metric"] == "cpu"][0]["value"] == pytest.approx(0.0)


def test_report_is_json_and_text_fits_one_screen():
    clock = FakeClock()
    rep = F.report_once(2.0, scan=False, sampler=fake_sampler(clock, lambda t: [p for p in machine()]), bud=B.Budget())
    json.dumps(rep)
    text = F.render(rep)
    assert len(text.splitlines()) <= 40 and "OVERALL" in text and "face*" in text and "llama-server" in text
    assert {"time", "components", "total", "verdicts", "overall", "budget"} <= set(rep)


def test_notes_warn_when_the_machine_is_not_the_budgeted_one():
    clock = FakeClock()
    s = fake_sampler(clock, lambda t: [])
    s.threads = lambda: 8
    rep = F.Meter(s, B.Budget()).sample(1.0)
    assert any("8 logical processors" in n for n in rep["notes"])


def test_windows_json_parsers():
    ps = json.dumps([{"ProcessId": 5, "ParentProcessId": 1, "Name": "node.exe", "ExecutablePath": None, "CommandLine": "node x",
                      "WorkingSetSize": 2 * MB, "PrivatePageCount": MB, "KernelModeTime": 10_000_000, "UserModeTime": 20_000_000, "WriteTransferCount": 7}])
    p = F.parse_ps_procs(ps)[0]
    assert (p.pid, p.ppid, p.exe, p.ws, p.private, p.cpu_s, p.io_write) == (5, 1, "", 2 * MB, MB, 3.0, 7)
    assert len(F.parse_ps_procs(json.dumps({"ProcessId": 9, "Name": "a"}))) == 1          # a single row comes back as an object
    assert F.parse_ps_procs("") == []
    g = json.dumps([{"Name": "pid_431_luid_0x0_0x1_phys_0", "DedicatedUsage": 100 * MB}, {"Name": "pid_431_luid_0x0_0x2_phys_0", "DedicatedUsage": 50 * MB},
                    {"Name": "pid_7_luid_0x0_0x1_phys_0", "DedicatedUsage": 1}, {"Name": "garbage", "DedicatedUsage": 5}])
    assert F.parse_gpu(g) == {431: 150 * MB, 7: 1}


def test_gpu_memory_is_attached_to_the_matched_processes():
    clock = FakeClock()
    s = fake_sampler(clock, lambda t: machine())
    s.gpu = lambda: {431: 1500 * MB, 302: 100 * MB, 900: 999 * MB}
    rep = F.Meter(s, B.Budget()).sample(1.0)
    assert rep["total"]["vram_mb"] == 1600 and [v for v in rep["verdicts"] if v["metric"] == "vram"][0]["status"] == "OK"


# ---- outside D: ------------------------------------------------------------------------------------------------------------------------

def test_scan_lists_jarvis_owned_folders_outside_d_by_name_and_size(tmp_path):
    appdata, local, home, temp = (tmp_path / n for n in ("Roaming", "Local", "home", "Temp"))
    (appdata / "JARVIS" / "Cache").mkdir(parents=True)
    (appdata / "JARVIS" / "Cache" / "data_0").write_bytes(b"x" * 3 * MB)
    (appdata / "JARVIS" / "token.json").write_text("nope")
    (appdata / "JARVIS" / "auth.json").write_text("secret-that-must-not-be-counted")
    (appdata / "Slack").mkdir(parents=True)                                            # not JARVIS
    (local / "pnpm").mkdir(parents=True)
    (local / "pnpm" / "store").write_bytes(b"y" * MB)
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes" / "memory.db").write_bytes(b"z" * 2 * MB)
    (home / ".hermes" / ".env").write_text("KEY=abc")
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("private")
    (temp / "jarvis-abc").mkdir(parents=True)
    (temp / "jarvis-abc" / "f").write_bytes(b"q" * MB)
    (temp / "other").mkdir()
    env = {"APPDATA": str(appdata), "LOCALAPPDATA": str(local), "TEMP": str(temp), "TMP": str(temp), "HF_HOME": "C:\\Users\\me\\.cache\\huggingface"}
    r = F.scan_outside_d(env, str(home))
    by = {i["path"]: i for i in r["items"]}
    assert set(by) == {str(appdata / "JARVIS"), str(local / "pnpm"), str(home / ".hermes"), str(temp / "jarvis-abc")}
    assert by[str(appdata / "JARVIS")]["mb"] == 3.0 and by[str(appdata / "JARVIS")]["files"] == 1       # token.json and auth.json skipped
    assert by[str(home / ".hermes")]["mb"] == 2.0 and by[str(local / "pnpm")]["kind"] == "tool-cache"
    assert (r["jarvis_items"], r["jarvis_mb"], r["suspect_items"]) == (3, 6.0, 1)
    assert [p.split("=")[0] for p in r["env_problems"]] == ["TEMP", "TMP", "HF_HOME"]
    assert ".ssh" not in json.dumps(r)                                                  # the owner's keys folder is never visited or named


def test_scan_ignores_folders_that_are_on_d(tmp_path):
    (tmp_path / "x").mkdir()
    places = (("on D", "home", "x", "jarvis"),)
    r = F.scan_outside_d({}, "D:\\jarvis\\home", places=places)
    assert r["items"] == []


def test_dir_size_never_counts_secret_looking_files_or_follows_links(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"1" * 1000)
    for n in (".env", ".env.local", "server.key", "cert.pem", "owner.token", "id_rsa", "credentials.json", "secrets.txt"):
        (tmp_path / n).write_text("s" * 5000)
    link = tmp_path / "link"
    try:
        link.symlink_to(tmp_path.parent)
    except OSError:
        pass
    size, files, complete = F.dir_size(tmp_path)
    assert (size, files, complete) == (1000, 1, True)
    assert F.dir_size(tmp_path, max_files=0)[2] is False


def test_cli_scan_only_and_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nolocal"))
    monkeypatch.setenv("TEMP", str(tmp_path / "notemp"))
    monkeypatch.setenv("TMP", str(tmp_path / "notemp"))
    (tmp_path / "JARVIS").mkdir()
    (tmp_path / "JARVIS" / "f").write_bytes(b"x" * 10)
    assert F.main(["--scan-only", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["jarvis_items"] >= 1
