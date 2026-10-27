"""WindowsEnv's PowerShell plumbing (fake process runner), the self-test, the catalogue adapter and the command line."""
import json
import subprocess
import types

import pytest

from jarvis.evals.reliability import catalogue, env as E, model, runner, selftest
from jarvis.evals.reliability.fakes import FakeEnv


class FakePS:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        verb = cmd[6]
        out = self.answers.get(verb, {"ok": True})
        if isinstance(out, Exception):
            raise out
        if isinstance(out, int):
            return subprocess.CompletedProcess(cmd, out, stdout="", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="startup noise\n" + json.dumps(out) + "\n", stderr="")


def test_windows_env_asks_powershell_with_the_right_words():
    ps = FakePS({"windows": [{"hwnd": 70000, "pid": 12, "process": "Notepad", "title": "a.txt - Notepad"}], "window-text": {"text": "milk eggs"},
                 "bin-list": [{"name": "del-1.txt", "from": "C:\\x"}], "bin-purge": {"removed": 1}})
    e = E.WindowsEnv(script="envprobe.ps1", runner=ps)
    w = e.windows()[0]
    assert (w.hwnd, w.process, w.title) == (70000, "Notepad", "a.txt - Notepad")
    assert e.window_text(w) == "milk eggs" and e.recycle_names() == ["del-1.txt <C:\\x>"] and e.recycle_purge("r3f9a2") == 1
    e.close_window(w)
    e.recycle_file("D:\\w\\a.txt")
    e.open_app("calc")
    e.message_box("T", "Disk almost full")
    cmds = [c[6:] for c in ps.calls]
    assert ["windows"] in cmds and ["window-text", "70000"] in cmds and ["close", "70000"] in cmds and ["bin-recycle", "D:\\w\\a.txt"] in cmds
    assert ["bin-purge", "r3f9a2"] in cmds and ["start", "calc"] in cmds and ["msgbox", "T", "Disk almost full"] in cmds
    assert ps.calls[0][:6] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "envprobe.ps1"]


def test_windows_env_errors_are_plain():
    with pytest.raises(E.EnvError, match="failed: boom"):
        E.WindowsEnv(runner=FakePS({"windows": 1})).windows()
    with pytest.raises(E.EnvError, match="timed out"):
        E.WindowsEnv(runner=FakePS({"windows": subprocess.TimeoutExpired("x", 1)})).windows()
    bad = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="not json at all", stderr="")   # noqa: E731
    with pytest.raises(E.EnvError, match="not JSON"):
        E.WindowsEnv(runner=bad).windows()


def test_the_powershell_script_never_empties_the_bin_and_is_shipped():
    text = E.PS_SCRIPT.read_text(encoding="utf-8")
    assert "bin-purge needs at least 4 characters" in text and "Clear-RecycleBin" not in text
    assert "-InputObject" in text                         # a one-item list must stay a list in the JSON
    assert " $pid" not in text.replace("$procId", "")      # $PID is read-only in PowerShell: the script uses $procId


def test_selftest_with_a_pretend_pc_reports_each_primitive(tmp_path):
    class Smart(FakeEnv):
        """Behaves like the real one enough for the self-test: new windows show up where the real ones would."""
        def windows(self):
            return list(self._wins) or [E.Win(1, 1, "explorer", "Home")]

    class Drv:
        name = "mcp"
    args = types.SimpleNamespace(work_root=str(tmp_path), mcp_url="http://127.0.0.1:1/mcp")
    lines = []
    code = selftest.run(Smart(), Drv(), args, sleep=lambda s: None, log=lines.append)
    text = "\n".join(lines)
    assert "PASS  files" in text and "FAIL  tool server" in text                 # no tool server here: named, not hidden
    assert "recycle bin round trip" in text and code == 1 and "FIX FIRST: tool server" in text


def test_selftest_skips_windows_primitives_on_a_plain_environment(tmp_path):
    class Drv:
        name = "mcp"
    lines = []
    selftest.run(E.LocalEnv(), Drv(), types.SimpleNamespace(work_root=str(tmp_path), mcp_url="http://127.0.0.1:1/mcp"), sleep=lambda s: None, log=lines.append)
    assert any(l.startswith("SKIP  notepad") for l in lines) and any(l.startswith("PASS  files") for l in lines)


def test_catalogue_entries_become_goals_and_need_a_way_to_verify(tmp_path):
    cat = model.load()
    before = len(cat.capabilities["simple_tasks"].goals)
    f = tmp_path / "cat.json"
    f.write_text(json.dumps([{"id": "open-calc", "phrases": ["open calculator", "calculator kholo"], "verify": [{"check": "window_appeared", "title_contains": "Calculator"}]}]))
    assert catalogue.merge(cat, f) == 2 and len(cat.capabilities["simple_tasks"].goals) == before + 2
    assert catalogue.merge(cat, f) == 0                                      # no duplicates the second time
    assert model.validate(cat) == []
    f.write_text(json.dumps([{"id": "x", "phrases": ["do x"]}]))
    with pytest.raises(catalogue.CatalogueError, match="no verify"):
        catalogue.merge(model.load(), f)


def test_command_line_list_and_validate(capsys):
    assert runner.main(["--validate"]) == 0
    assert "293 goals in 18 capabilities; sound" in capsys.readouterr().out
    assert runner.main(["--list", "--repeats", "10"]) == 0
    out = capsys.readouterr().out
    assert "open_type_save_click" in out and "2930 runs" in out and "hours" in out


def test_command_line_refuses_an_unknown_capability(capsys, tmp_path):
    assert runner.main(["--capability", "nonsense", "--out-dir", str(tmp_path), "--driver", "mcp"]) == 2
    assert "unknown capability" in capsys.readouterr().out


def test_wrapper_and_node_scripts_are_present():
    here = runner.HERE
    for f in ("run-reliability.ps1", "ps/envprobe.ps1", "drive/reliability_drive.mjs", "drive/probe_ui.mjs", "drive/probe_startup.mjs", "goals.json"):
        assert (here / f).is_file(), f
    ps = (here / "run-reliability.ps1").read_text(encoding="utf-8")
    for mode in ("selftest", "quick", "gate", "report", "latency"):
        assert f"'{mode}'" in ps
