"""The desktop baseline runner's logic (jarvis/evals/neko95/task_baseline.py) with a fake machine: what counts as pass, what is blocked, that it
closes only the windows it opened, and that a claim of success is never believed without the screen. The real run needs the laptop.
Run: python -m pytest jarvis/evals/neko95/tests/test_task_baseline.py -q"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
spec = importlib.util.spec_from_file_location("task_baseline_t", HERE / "task_baseline.py")
TB = importlib.util.module_from_spec(spec)
sys.modules["task_baseline_t"] = TB
spec.loader.exec_module(TB)

NOTEPAD = "notepad|111|Untitled - Notepad"
TYPED = "notepad|111|*Untitled - Notepad"
CALC = "ApplicationFrameHost|222|Calculator"


def run_record(state="done", text="", error="", pending=None, steps=None):
    steps = steps or [{"id": "s1", "depends_on": []}]
    return {"state": state, "plan": {"steps": steps}, "results": {steps[-1]["id"]: {"text": text, "error": error}}, "pending_input": pending}


class Fake:
    """A machine: window listings per phase, canned run records, a record of every PowerShell command and every POST."""

    def __init__(self, windows_before="", windows_after="", run=None, accept=True, folder=False):
        self.before, self.after, self.run, self.accept, self.folder = windows_before, windows_after, run, accept, folder
        self.ps_calls, self.posts, self.phase, self.t, self.said = [], [], 0, 0.0, []

    def ps(self, command, timeout=20.0):
        self.ps_calls.append(command)
        if "Stop-Process" in command or "Quit()" in command:
            return ""
        if command.startswith("Get-Process"):
            self.phase += 1
            return self.before if self.phase == 1 else self.after
        return ""

    def post(self, path, body):
        self.posts.append((path, body))
        if path == "/api/task":
            return {"ok": self.accept, "run_id": "r1", "state": "planning"} if self.accept else {"ok": False, "error": "busy"}
        return {"ok": True, "stopped": True}

    def env(self):
        env = TB.Env(post=self.post, read_run=lambda rid: self.run, ps=self.ps, sleep=self._sleep, now=self._now,
                     folder_exists=lambda p: self.folder, say=self.said.append)
        return env

    def _sleep(self, s):
        self.t += s

    def _now(self):
        return self.t


def one(task_id, fake, timeout=300):
    return TB.run_task(TB.BY_ID[task_id], fake.env(), timeout)


def test_an_open_window_on_the_screen_is_a_pass_and_only_that_new_window_is_closed():
    f = Fake(windows_before="chrome|9|My notes - Chrome", windows_after="chrome|9|My notes - Chrome\n" + NOTEPAD, run=run_record())
    r = one("T01", f)
    assert r["result"] == "pass" and "Untitled - Notepad" in r["reason"]
    closes = [c for c in f.ps_calls if "Stop-Process" in c]
    assert closes == ["Stop-Process -Id 111 -Force -ErrorAction SilentlyContinue"]              # not Chrome, no name-wide kill


def test_the_agent_saying_done_without_a_window_is_a_fail():
    f = Fake(windows_after="", run=run_record(text="Notepad is open and I typed hello."))
    r = one("T01", f)
    assert r["result"] == "fail" and "no window" in r["reason"]


def test_typed_text_needs_the_modified_marker_in_the_title():
    assert one("T02", Fake(windows_after=NOTEPAD, run=run_record()))["result"] == "fail"
    assert one("T02", Fake(windows_after=TYPED, run=run_record()))["result"] == "pass"


def test_calculator_and_folder_checks():
    assert one("T03", Fake(windows_after=CALC, run=run_record()))["result"] == "pass"
    f = Fake(windows_after="explorer|7|Documents", run=run_record())
    assert one("T04", f)["result"] == "pass" and any("Quit()" in c and "Documents" in c for c in f.ps_calls)


def test_a_run_that_ended_failed_is_a_fail_even_if_a_window_exists():
    r = one("T01", Fake(windows_after=NOTEPAD, run=run_record(state="failed", error="timed out")))
    assert r["result"] == "fail" and "failed" in r["reason"]


def test_no_run_file_means_the_planner_never_made_a_plan():
    f = Fake(windows_after=NOTEPAD, run=None)
    r = one("T01", f, timeout=10)
    assert r["result"] == "fail" and "no run was created" in r["reason"]


def test_a_task_the_widget_refuses_is_a_fail_with_that_reason():
    r = one("T05", Fake(accept=False))
    assert r["result"] == "fail" and "did not accept" in r["reason"]


def test_answer_tasks_judge_the_final_answer_only():
    steps = [{"id": "s1", "depends_on": []}, {"id": "s2", "depends_on": ["s1"]}]
    ok = run_record(text="It is 14:32 now.", steps=steps)
    assert one("T05", Fake(run=ok))["result"] == "pass"
    assert one("T05", Fake(run=run_record(text="I do not know.", steps=steps)))["result"] == "fail"
    bullets = run_record(text="- heat\n- panels\n- inverter", steps=steps)
    assert one("T06", Fake(run=bullets))["result"] == "pass"
    assert one("T06", Fake(run=run_record(text="- one only", steps=steps)))["result"] == "fail"


def test_a_vague_request_must_ask_or_decline_never_claim_success():
    assert one("T08", Fake(run=run_record(state="held", pending={"status": "open", "id": "q-1"})))["result"] == "pass"
    assert one("T08", Fake(run=run_record(state="failed", error="no single file matches")))["result"] == "pass"
    bad = one("T08", Fake(run=run_record(state="done", text="Opened your report.")))
    assert bad["result"] == "fail" and "reported done" in bad["reason"]


def test_creating_a_folder_must_be_refused_in_words_and_create_nothing():
    said = run_record(state="failed", error="I cannot create folders or files yet (the PC agent can open, type and read)")
    assert one("T09", Fake(run=said))["result"] == "pass"
    assert one("T09", Fake(run=run_record(state="failed", error="no idea")))["result"] == "fail"
    assert one("T09", Fake(run=said, folder=True))["result"] == "fail"                           # a folder appeared: the worst outcome


def test_stop_must_end_the_run_quickly():
    f = Fake(run=run_record(state="cancelled"))
    r = one("T10", f)
    assert r["result"] == "pass" and ("/api/action", {"action": "stop"}) in f.posts


def test_existing_notepad_or_calculator_windows_block_the_window_tasks_but_not_the_others():
    f = Fake(windows_before="notepad|5|notes.txt - Notepad", windows_after="notepad|5|notes.txt - Notepad",
             run=run_record(text="It is 9:05."))
    report = TB.run_all(f.env(), only={"T01", "T05"})
    by = {r["id"]: r for r in report["tasks"]}
    assert by["T01"]["result"] == "blocked" and "close your open Notepad" in by["T01"]["reason"]
    assert by["T05"]["result"] == "pass"
    assert report["blocked"] == 1 and report["run"] == 1 and report["pass_rate"] == 1.0
    assert not any("Stop-Process" in c for c in f.ps_calls)                                      # the existing window was never touched


def test_the_task_list_is_ten_and_the_report_is_json():
    assert len(TB.TASKS) == 10 and len({t.id for t in TB.TASKS}) == 10
    f = Fake(run=run_record(text="It is 9:05."))
    report = TB.run_all(f.env(), only={"T05"})
    json.dumps(report)
    assert report["tasks"][0]["seconds"] >= 0


def test_closing_commands_are_built_safely():
    assert TB.close_command("notepad", "111", "x") == "Stop-Process -Id 111 -Force -ErrorAction SilentlyContinue"
    assert "-eq 'O''Brien'" in TB.close_command("explorer", "7", "O'Brien")
    try:
        TB.close_command("notepad", "1; calc", "x")
    except ValueError:
        pass
    else:
        raise AssertionError("a pid with text in it must be refused")


def test_the_person_watching_sees_each_task_announced_and_the_result_stays_on_screen_before_it_is_closed():
    f = Fake(windows_after=NOTEPAD, run=run_record())
    env = f.env()
    env.linger = 6.0
    t_before = f.t
    TB.run_task(TB.BY_ID["T01"], env)
    text = "\n".join(f.said)
    assert '>>> T01: saying "open Notepad"' in text and "PASS" in text and "leaving it on screen for 6 s" in text
    assert f.t - t_before >= 6.0                                                                  # the sleep happened before the close
    assert text.index("PASS") < text.index("leaving it")
    assert any("Stop-Process" in c for c in f.ps_calls)


def test_a_failed_task_is_not_lingered_and_the_banner_says_what_to_expect():
    f = Fake(windows_after="", run=run_record(state="failed", error="timed out"))
    env = f.env()
    report = TB.run_all(env, only={"T01"})
    said = "\n".join(f.said)
    assert "Watch the screen" in said and "FAIL" in said and "leaving it on screen" not in said
    assert report["passed"] == 0
