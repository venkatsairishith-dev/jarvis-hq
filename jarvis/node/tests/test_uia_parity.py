"""The node's UI jobs must return the SAME shapes whether the persistent worker (a real child process over a fake desktop) or the old PowerShell scripts (a fake
PowerShell double over an identical fake desktop) did the work, and must fall back, or refuse to, exactly as D68 says. The kernel's policy and the job kinds are not
under test here: the jobs are called the way the node calls them after the kernel has said yes.

The PowerShell double plays each old script on the fake desktop and answers in PowerShell's terms (return code, stdout, stderr). What it proves is the NODE's side of
both paths: the glue in winbackend / windowpick / ui / diag, the reason codes, the result dicts. That the real scripts and the real library behave alike on a real
Windows desktop is what only the laptop can show (D68 lists it)."""
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))

import fakeuia  # noqa: E402
import uia_worker as W  # noqa: E402
from jarvis_node import diag, jobs, runtime, ui, uia, windowpick, winbackend as wb  # noqa: E402

CHILD = os.path.join(HERE, "uia_fake_child.py")


class FakePowerShell:
    """The old scripts, played on a fake desktop: what the node sees of PowerShell is only (return code, stdout, stderr), and these are those, per script."""

    def __init__(self, backend):
        self.b = backend
        self.ops = W.Ops(backend, sleep=lambda s: None, env={})
        self.runs = []

    @staticmethod
    def done(argv, rc=0, out="", err=""):
        return subprocess.CompletedProcess(argv, rc, out, err)

    def run(self, argv, timeout=30, env=None, cwd=None, input_text=None):
        script, e = argv[-1], env or {}
        self.runs.append(script)
        a = {"proc": e.get("JARVIS_UI_PROC", ""), "elem": e.get("JARVIS_UI_ELEM", "")}
        if e.get("JARVIS_UI_HWND"):
            a.update(hwnd=int(e["JARVIS_UI_HWND"]), pid=int(e["JARVIS_UI_PID"]))
        try:
            if script is windowpick.LIST:
                wins, fg = self.b.list_windows(a["proc"])
                return self.done(argv, out=json.dumps({"fg": fg, "windows": wins}))
            if script is wb.FOCUS:
                self.ops.focus(a)
            elif script is wb.CLICK:
                self.ops.click(a)
            elif script is wb.TYPE:                                  # the TYPE script alone: set the value, or type with keys
                el = self.ops._find(self.ops._root(a["hwnd"]), a["elem"])
                text = e["JARVIS_UI_TEXT"]
                if self.b.has_value(el) and not el.password:
                    self.b.set_value(el, text)
                else:
                    if el.password:
                        raise W.OpError("password field")
                    self.b.set_focus(el)
                    if self.b.foreground_pid() != a["pid"]:
                        raise W.OpError("target is not the foreground window")
                    self.b.send_text(text)
            elif script is wb.READBACK:
                el = self.ops._find(self.ops._root(a["hwnd"]), a["elem"])
                have = self.b.get_value(el) if self.b.has_value(el) else None
                if not have and el.hwnd:
                    have = self.b.wm_gettext(el.hwnd)
                if not have or e["JARVIS_UI_TEXT"] not in have:
                    raise W.OpError("text not in field")
            elif script is wb.WINDOW_STATE:
                self.ops.window_state({**a, "show": int(e["JARVIS_UI_SHOW"]), "want": e["JARVIS_UI_WANT"]})
            elif script is wb.TYPE_FOCUSED:
                r = self.ops.paste({**a, "text": e["JARVIS_UI_TEXT"]})
                return self.done(argv, out="JARVIS_TYPED:" + r["typed"] + "\n")
            else:
                raise AssertionError("a script this double does not know")
            return self.done(argv)
        except W.OpError as err:
            return self.done(argv, rc=1, err=err.code)

    def snapshot(self, argv, **kw):                                  # ui.py: subprocess.run(["powershell", ..., script])
        m = re.search(r"\$procName = '([^']*)'; \$maxN = (\d+); \$maxD = (\d+); \$wantValues = \$(true|false)", argv[-1])
        env = kw.get("env") or {}
        a = {"proc": m.group(1), "max": int(m.group(2)), "depth": int(m.group(3)), "values": m.group(4) == "true"}
        if env.get("JARVIS_UI_HWND"):
            a.update(hwnd=int(env["JARVIS_UI_HWND"]), pid=int(env["JARVIS_UI_PID"]))
        return self.done(argv, out=json.dumps(self.ops.snapshot(a)))

    def diag_runner(self, script, env, timeout):
        return self.done(["powershell"], out=json.dumps(self.ops.window_text({"name": env["JARVIS_DIAG_NAME"]})["rows"]))


class Rig:
    """Two identical fake desktops: one behind the real worker child, one behind the PowerShell double. The sequence of jobs is the same on both."""

    def __init__(self, tmp_path):
        os.environ["JARVIS_RUN_DIR"] = str(tmp_path / "run")
        os.environ["JARVIS_OUTPUTS"] = str(tmp_path / "out")
        self.ps_backend = fakeuia.FakeBackend()
        self.ps = FakePowerShell(self.ps_backend)
        self.client = uia.WorkerClient([sys.executable, CHILD], env={**os.environ, "FAKE_UIA_GRACE": "0.3"}, request_timeout=10, ready_timeout=10)

    def via_worker(self, fn):
        uia.set_client(self.client)
        try:
            return fn()
        finally:
            uia.set_client(None)

    def via_powershell(self, fn):
        uia.set_client(None)
        saved = runtime.run, ui.subprocess.run
        runtime.run, ui.subprocess.run = self.ps.run, self.ps.snapshot
        os.environ["JARVIS_UIA_WORKER"] = "0"
        try:
            return fn()
        finally:
            runtime.run, ui.subprocess.run = saved
            os.environ.pop("JARVIS_UIA_WORKER", None)

    def setup(self, name, value=None):
        fakeuia.apply_setup(self.ps_backend.d, name, value)
        self.client.request("setup", {"name": name, "value": value})

    def both(self, fn):
        a, b = self.via_worker(fn), self.via_powershell(fn)
        self.same_desktop()
        return a, b

    def same_desktop(self):
        mine = json.loads(json.dumps(fakeuia.desktop_state(self.ps_backend.d)))
        assert self.client.request("state")["state"] == mine, "the two desktops must end up identical"

    def close(self):
        self.client.shutdown()


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    old = {k: os.environ.get(k) for k in ("JARVIS_RUN_DIR", "JARVIS_OUTPUTS")}
    r = Rig(tmp_path_factory.mktemp("parity"))
    yield r
    r.close()
    uia.set_client(None)
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def act(op, target, text=""):
    return lambda: jobs.ui_action({"op": op, "target": target, "text": text}, lister=lambda: [])


# ------------------------------------------------------------------------------------------------------------------------------ the changing jobs
def test_a_click_answers_the_same_and_changes_the_same(rig):
    a, b = rig.both(act("click", "calc|num7Button"))
    assert a == b == {"ok": True, "done": "click"}
    assert wb.CLICK in rig.ps.runs and ("invoke", "Seven") in rig.ps_backend.d.log


def test_a_click_on_a_missing_control_gives_the_same_fixed_reason(rig):
    a, b = rig.both(act("click", "calc|Minus"))
    assert a == b == {"ok": False, "error": "RuntimeError", "reason": "no element"}
    a, b = rig.both(act("click", "nothere|x"))
    assert a == b == {"ok": False, "error": "RuntimeError", "reason": "no window"}


def test_a_classic_button_without_invoke_is_clicked_by_its_own_message_in_both(rig):
    a, b = rig.both(act("click", "oldtool|CommandButton_6"))
    assert a == b == {"ok": True, "done": "click"}


def test_typing_answers_the_same_and_the_text_is_in_the_same_place(rig):
    a, b = rig.both(act("type", "notepad|15", "hello from the test"))
    assert a == b == {"ok": True, "done": "type", "observed": "text read back from the field"}
    a, b = rig.both(act("type", "oldtool|1001", "keys path"))                        # a classic Edit with no patterns: typed with keys
    assert a == b and a["ok"] and a["observed"] == "text read back from the field"


def test_a_password_field_is_refused_the_same_way_and_nothing_is_typed(rig):
    a, b = rig.both(act("type", "loginapp|pw", "typed-secret-xyz"))
    assert a == b == {"ok": False, "error": "RuntimeError", "reason": "password field"}
    assert "typed-secret-xyz" not in json.dumps(fakeuia.desktop_state(rig.ps_backend.d))


def test_focus_answers_the_same_and_a_foreground_lock_is_the_same_reason(rig):
    a, b = rig.both(act("focus", "calc|"))
    assert a == b == {"ok": True, "done": "focus", "observed": "the target is the foreground window", "foreground": True}
    rig.setup("refuse_foreground", True)
    try:
        a, b = rig.both(act("focus", "notepad|"))
        assert a == b == {"ok": False, "error": "RuntimeError", "reason": "target is not the foreground window"}
    finally:
        rig.setup("refuse_foreground", False)
    rig.both(act("focus", "notepad|"))


@pytest.mark.parametrize("op,state", [("minimize", "minimized"), ("maximize", "maximized"), ("restore", "normal")])
def test_window_state_answers_the_same(rig, op, state):
    a, b = rig.both(act(op, "calc|"))
    assert a == b == {"ok": True, "done": op, "observed": "window state read back", "window_state": state}


def test_type_focused_answers_the_same_and_a_paste_that_does_not_land_is_the_same_reason(rig):
    rig.setup("focus_document", 1001)
    a, b = rig.both(act("type_focused", "notepad|", "pasted\ntext"))
    assert a == b == {"ok": True, "done": "type_focused", "observed": "text read back from the editor"}
    rig.setup("paste_lands", False)
    try:
        a, b = rig.both(act("type_focused", "notepad|", "never arrives"))
        assert a == b == {"ok": False, "error": "RuntimeError", "reason": "text not in field"}
    finally:
        rig.setup("paste_lands", True)


def test_two_windows_of_one_app_and_a_change_is_refused_by_the_node_in_both_paths_and_a_read_takes_the_front_one(rig):
    rig.both(act("focus", "notepad|"))                                              # Notepad is in front before the owner's second one appears
    rig.setup("second_notepad")
    try:
        a, b = rig.both(act("type", "notepad|15", "must not be typed"))
        assert a == b == {"ok": False, "error": "SeveralWindowsSayWhich"}
        a, b = rig.both(lambda: jobs.ui_snapshot({"target": "notepad"}))
        assert a == b and a["ok"] and a["window"] == "Untitled - Notepad", "a read: the one in front"
    finally:
        rig.setup("remove_window", 1101)
    rig.same_desktop()


# ------------------------------------------------------------------------------------------------------------------------------ the reads
def test_the_window_list_of_one_process_is_the_same(rig):
    a, b = rig.both(lambda: windowpick.list_windows("notepad"))
    assert a == b and a[0][0]["name"] == "notepad" and a[1] == rig.ps_backend.d.foreground
    a, b = rig.both(lambda: windowpick.list_windows(""))
    assert a == b and len(a[0]) >= 4


def test_a_bad_process_name_is_refused_before_either_path_looks():
    with pytest.raises(ValueError):
        windowpick.list_windows("notepad; calc")


def test_the_snapshot_job_has_the_same_shape(rig):
    a, b = rig.both(lambda: jobs.ui_snapshot({"target": "calc"}))
    assert a == b and a["ok"] and a["process"] == "calc" and set(a["elements"][0]) == {"depth", "type", "name", "id", "cls", "enabled"}
    a, b = rig.both(lambda: jobs.ui_snapshot({"target": ""}))                        # the foreground window
    assert a == b and a["ok"]
    a, b = rig.both(lambda: jobs.ui_snapshot({"target": "nothere"}))
    assert a == b == {"ok": False, "error": "no window"}


def test_the_snapshot_with_values_is_scrubbed_the_same_in_both_paths_and_never_reads_a_password_box(rig):
    rig.setup("set_value", [3001, 0, "token ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789ab"])
    rig.setup("set_value", [3001, 1, "hunter2"])
    a = rig.via_worker(lambda: ui.snapshot("loginapp", 60, 4, True))
    b = rig.via_powershell(lambda: ui.snapshot("loginapp", 60, 4, True))
    assert a == b and set(a) == {"window", "process", "class", "elements", "truncated"}
    assert a["elements"][1]["value"] is None and "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789ab" not in json.dumps(a), "the password box is never read; the token is scrubbed"


def test_window_text_gives_the_same_summary_and_the_same_local_file(rig):
    def go(runner):
        out = diag.run({"op": "window_text", "target": "oldtool"}, runner=runner, lister=lambda: [])
        path = out.pop("local_result")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        os.remove(path)
        return out, data
    a = rig.via_worker(lambda: go(None))
    b = rig.via_powershell(lambda: go(rig.ps.diag_runner))
    assert a == b and a[0]["summary"] == {"windows": 2, "dialogs": 1}
    assert a[1][1]["owner"] == "Old tool" and a[1][1]["buttons"] == ["Save", "Don't save"]


def test_a_bank_window_is_left_out_of_window_text_whichever_path_read_it(rig):
    rig.setup("add_bank")
    try:
        def go(runner):
            return diag.run({"op": "window_text", "target": "chrome"}, runner=runner, lister=lambda: [])
        a = rig.via_worker(lambda: go(None))
        b = rig.via_powershell(lambda: go(rig.ps.diag_runner))
        assert a["ok"] and a["summary"] == b["summary"] == {"windows": 0, "dialogs": 0}
    finally:
        rig.setup("remove_window", 6001)


# ------------------------------------------------------------------------------------------------------------------------------ fallbacks
class Stub:
    """A client that answers `windows` with nothing and anything else with the exception it was given."""

    def __init__(self, exc=None, result=None):
        self.exc, self.result, self.asked = exc, result, []

    def usable(self):
        return True

    def request(self, op, args=None, timeout=None):
        self.asked.append(op)
        if op == "windows":
            return {"fg": 0, "windows": []}
        if self.exc:
            raise self.exc
        return self.result or {}


@pytest.fixture
def ps_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(wb, "_run", lambda script, env, *a, **k: calls.append(script) or "")
    yield calls
    uia.set_client(None)


@pytest.mark.parametrize("op,target,text", [("click", "calc|num7Button", ""), ("type", "notepad|15", "hi"), ("focus", "calc|", ""), ("minimize", "calc|", ""),
                                            ("type_focused", "notepad|", "hi")])
def test_a_worker_that_is_not_there_hands_every_job_to_the_powershell_script(ps_calls, op, target, text):
    uia.set_client(Stub(exc=uia.NotSent("backing off")))
    out = jobs.ui_action({"op": op, "target": target, "text": text}, lister=lambda: [])
    assert out["ok"] is True and ps_calls, "the PowerShell script ran"


def test_with_the_switch_off_the_worker_is_never_asked(ps_calls, monkeypatch):
    s = Stub(result={})
    uia.set_client(s)
    monkeypatch.setenv("JARVIS_UIA_WORKER", "0")
    assert jobs.ui_action({"op": "click", "target": "calc|num7Button", "text": ""}, lister=lambda: [])["ok"]
    assert s.asked == [] and ps_calls


@pytest.mark.parametrize("exc", [uia.Lost("crash"), uia.Lost("timeout"), uia.Lost("oversize"), uia.Refused("other")])
def test_a_changing_job_that_was_sent_is_never_run_again_through_powershell(ps_calls, exc):
    uia.set_client(Stub(exc=exc))
    out = jobs.ui_action({"op": "click", "target": "calc|num7Button", "text": ""}, lister=lambda: [])
    assert out == {"ok": False, "error": "RuntimeError", "reason": "other"} and ps_calls == [], "the click may have happened: nothing is repeated"


def test_a_refusal_from_the_worker_is_final_it_is_not_tried_again_the_old_way(ps_calls):
    uia.set_client(Stub(exc=uia.Refused("no element")))
    out = jobs.ui_action({"op": "click", "target": "calc|Nope", "text": ""}, lister=lambda: [])
    assert out == {"ok": False, "error": "RuntimeError", "reason": "no element"} and ps_calls == []


def test_a_primitive_the_adapter_lacks_hands_the_job_to_powershell(ps_calls):
    uia.set_client(Stub(exc=uia.Refused("unsupported")))
    assert jobs.ui_action({"op": "click", "target": "calc|num7Button", "text": ""}, lister=lambda: [])["ok"] and ps_calls


def test_several_windows_and_a_change_is_refused_before_the_worker_is_asked_to_change_anything(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))

    class Two(Stub):
        def request(self, op, args=None, timeout=None):
            self.asked.append(op)
            if op == "windows":
                return {"fg": 0, "windows": [{"hwnd": 1, "pid": 5, "start": 1, "name": "notepad"}, {"hwnd": 2, "pid": 6, "start": 2, "name": "notepad"}]}
            raise AssertionError("a change was sent although the window is ambiguous")
    s = Two()
    uia.set_client(s)
    try:
        assert jobs.ui_action({"op": "type", "target": "notepad|15", "text": "x"}, lister=lambda: []) == {"ok": False, "error": "SeveralWindowsSayWhich"}
    finally:
        uia.set_client(None)
    assert s.asked == ["windows"]


def test_a_read_that_met_a_crash_is_asked_again_with_powershell(monkeypatch):
    uia.set_client(Stub(exc=uia.Lost("crash")))
    ran = []
    reply = json.dumps({"window": "W", "process": "p", "class": "c", "elements": [], "truncated": False})
    monkeypatch.setattr(ui.subprocess, "run", lambda *a, **k: ran.append(1) or subprocess.CompletedProcess(a, 0, reply, ""))
    try:
        out = ui.snapshot("", 60, 4, False)
    finally:
        uia.set_client(None)
    assert ran == [1] and out["window"] == "W"


def test_diag_with_a_test_runner_never_goes_to_the_worker():
    s = Stub(result={"rows": []})
    uia.set_client(s)
    try:
        diag.run({"op": "window_text", "target": "oldtool"}, runner=lambda *a: subprocess.CompletedProcess(a, 0, "[]", ""), lister=lambda: [])
    finally:
        uia.set_client(None)
    assert s.asked == []
