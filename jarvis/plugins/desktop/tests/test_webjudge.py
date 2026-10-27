"""D77, the web judge: practice problems on a web page, in the owner's Chrome, through the REAL kernel policy and node code and the D64 fake Chrome (chromeharness.py).

Every test here ends with the same check (the `rig` fixture's teardown): Submit was NEVER clicked. Not one click on a Submit-like control reached the page or the kernel, no
Enter key was pressed, and the fake judge never recorded a grade. That holds whatever the test is about."""
import json
import re
import time

import pytest

from jarvis.plugins._shared import code_runner as cr, screen_text
from jarvis.plugins.desktop import browser_agent as BA
from jarvis.plugins.desktop import browser_snapshot as S
from jarvis.plugins.desktop import core, decide as D, problemset as P, problemset_text as PT, webjudge as W
from jarvis.plugins.desktop.browser_backend import GrantsClient, KernelBackend
from jarvis.plugins.desktop.browser_planner import LocalPlanner
from jarvis.plugins.desktop.tests.chromeharness import Harness

from .test_problemset import GOOD, Model, reply

PY = cr.LocalRunner()
needs_python = pytest.mark.skipif(not PY.tool_status("python")[0], reason="no python in the allowed places")
SET_URL = "https://judge.test/problems/1"

FACT = GOOD["Factorial"]
SUM = GOOD["Sum"]
LARGEST = "input()\nprint(max(map(int, input().split())))"
REVERSE = GOOD["Reverse"]
SQUARE = "n = int(input())\nprint(n * n)"
ANSWERS = {"Factorial": FACT, "Sum of two numbers": SUM, "Largest number": LARGEST, "Reverse a word": REVERSE, "Square": SQUARE, "Cube": "n = int(input())\nprint(n ** 3)"}


class JudgeModel(Model):
    """Answers by the problem's title, like the folder tests' fake model. `wrong={"Factorial": 2}`: the first two answers for that problem are wrong."""

    def __init__(self, answers=None, wrong=None):
        super().__init__()
        self.table, self.wrong, self.seen = dict(answers or ANSWERS), dict(wrong or {}), {}

    def __call__(self, messages, max_tokens):
        self.calls.append((messages, max_tokens))
        body = messages[1]["content"]
        if "Teach the concept" in body:
            return "# Concept\nAdding numbers.\nExample on different numbers: 10 and 20 give 30.", "fake"
        for title, code in self.table.items():
            if f": {title}".lower() in body.lower():
                n = self.seen[title] = self.seen.get(title, 0) + 1
                return reply(title, "print(0)" if n <= self.wrong.get(title, 0) else code), "fake"
        return "I cannot do that.", "fake"


class Rig(Harness):
    def __init__(self, tmp_path, monkeypatch):
        super().__init__(tmp_path, monkeypatch)
        self.web.signed_in.add("judge.test")
        self.guards = []
        self.state = tmp_path / "state"

    def make_agent(self, hosts):
        backend = KernelBackend(self.pc_run(), "default", take_local=core.take_local)
        guard = W.SubmitGuard(backend)
        self.guards.append(guard)
        grants = GrantsClient(owner=self.call, ttl=0.0)

        def ask(spec):
            self.asks.append(spec)
            return {"answered": True, "button": "stop"}
        ag = BA.Agent(guard, goal="solve the practice problems", hosts=hosts, planner=LocalPlanner(use_model=False), ask=ask, journal=self.journal.append,
                      stop=lambda: self.stop_flag, budget=BA.Budget(max_seconds=600, patience_s=2.0), banners={"reject": ["Reject all"], "accept": []}, grants=grants,
                      sleep=lambda s: None, human=False)
        return ag, guard

    def job(self, url=SET_URL, text=None, **params):
        text = text or f"solve all the problems at {url}"
        prep = P.prepare(text, base=self.state)
        assert prep.status == "ready", prep
        pj = P.job_dir(prep.job, self.state) / "params.json"
        data = json.loads(pj.read_text(encoding="utf-8"))
        data.update(params)
        pj.write_text(json.dumps(data), encoding="utf-8")
        return prep.job

    def run(self, url=SET_URL, model=None, ask_owner=None, cfg=None, stop=None, job=None, **params):
        job = job or self.job(url, **params)
        deps = P.Deps(ask_model=model or JudgeModel(), release=lambda t: screen_text.Released(t, True, "gateway", "cleared"), runner=PY, runner_note="on this PC", base=self.state,
                      cfg={**PT.load_config(), "limits": {"cpu_s": 2, "wall_s": 4, "mem_mb": 256}, **(cfg or {})}, ask_owner=ask_owner, stop=stop or (lambda: False),
                      model_thread_poll_s=0.01)
        hands = W.WebHands(self.make_agent, run_wait_s=1.5, sleep=lambda s: time.sleep(0.01))
        self.last_deps, self.last_job = deps, job
        return W.run_web(job, deps, hands)

    def assert_never_submitted(self):
        assert self.world.graded == [], f"the fake judge recorded a grade: {self.world.graded}"
        bad = [c for c in self.world.click_log if W.FINALIZE.search(c)]
        assert not bad, f"a Submit-like control was clicked: {bad}"
        for body in self.calls:
            if body.get("op") == "browser":
                assert not (body.get("action") in ("click", "xy_click", "check", "drag") and W.FINALIZE.search(str(body.get("target", "")))), body
                assert not (body.get("action") == "press" and W.ENTER_KEY.search(str(body.get("text", "")))), body
        for g in self.guards:
            assert not [1 for k, label in g.sent if k in ("click", "check", "xy_click", "drag") and W.finalizing(label)], g.sent


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    yield r
    try:
        r.assert_never_submitted()                       # ABSOLUTE RULE, checked at the end of EVERY test in this file
    finally:
        r.close()


# ----------------------------------------------------------------------------- the whole set
@needs_python
def test_the_whole_set_is_solved_typed_run_and_read_back_then_it_stops(rig):
    model = JudgeModel()
    res = rig.run(model=model)
    assert [r["n"] for r in res["table"]] == [1, 2, 3, 4], res["table"]
    st = {r["n"]: r["status"] for r in res["table"]}
    assert st == {1: "passed", 2: "passed", 3: "untested", 4: "passed"}, res["table"]
    assert res["status"] == "completed" and res["submit_clicked"] == 0
    assert "end of the set" in res["summary"] or "Next" in res["summary"]
    runs = rig.world.judge_runs
    assert [v for _u, _c, v in runs] == ["Passed 2/2 sample tests", "Passed 2/2 sample tests", "Passed 2/2 sample tests"] or len(runs) == 3
    assert len(model.calls) == 4


def codes(rig):
    """(field name, text) of everything typed into the fake page, in order."""
    return list(rig.world.typed)


def answer_with(choice, button="ok"):
    return lambda spec, t: {"answered": True, "button": button, "values": {"choice": choice}}


# ----------------------------------------------------------------------------- the editor, found by role and name
@needs_python
def test_the_code_is_typed_into_the_real_editor_found_by_role_and_name_not_the_custom_input_box(rig):
    rig.run()
    fields = [f for f, _ in codes(rig)]
    assert set(fields) == {"Code editor", "Editor content"}, fields               # the textarea on pages 1 and 4, the contenteditable text box on page 2; never "Custom input"
    assert "Custom input" not in fields
    assert "math.factorial" in [v for f, v in codes(rig) if f == "Code editor"][0]
    assert "a, b" in [v for f, v in codes(rig) if f == "Editor content"][0] or "a,b" in [v for f, v in codes(rig) if f == "Editor content"][0]


@needs_python
def test_the_whole_code_with_its_line_breaks_arrives_in_the_editor_in_one_job(rig):
    code = "n = int(input())\nif n < 0:\n    n = -n\nprint(n * n)\n"
    res = rig.run(url="https://judge.test/problems/one", model=JudgeModel({"Square": code}))
    assert res["counts"] == {"passed": 1}
    editor_values = [v for f, v in codes(rig) if f == "Code editor"]
    assert len(editor_values) == 1 and editor_values[0].startswith("n = int(input())\nif n < 0:\n    n = -n")
    assert any(c.get("action") == "type" and "\n" in c.get("text", "") for c in rig.calls if c.get("op") == "browser")      # ONE type job with the line breaks
    assert not any(c.get("action") == "press" for c in rig.calls if c.get("op") == "browser")                                 # no keys were pressed to "paste" or "run"


@needs_python
def test_code_with_braces_and_risky_looking_words_is_typed_unchanged_without_a_card(rig):
    code = '# delete the password, pay later, submit nothing\nn = int(input())\nprint(f"{n * n}")\n'
    res = rig.run(url="https://judge.test/problems/one", model=JudgeModel({"Square": code}))
    assert res["counts"] == {"passed": 1}, res["table"]
    typed = [v for f, v in codes(rig) if f == "Code editor"][0]
    assert typed.startswith("# delete the password") and 'print(f"{n * n}")' in typed
    assert not rig.pending, "typing code needs no PIN card"


# ----------------------------------------------------------------------------- Run, and the verdict read from the page
@needs_python
def test_run_is_clicked_and_the_verdict_comes_from_the_page_not_the_model(rig):
    res = rig.run(url="https://judge.test/problems/one", model=JudgeModel({"Square": "n = int(input())\nprint(n * n)"}))
    row = res["table"][0]
    assert row["status"] == "passed" and row["tests"] == "2/2"
    assert [v for _u, _c, v in rig.world.judge_runs] == ["Passed 2/2 sample tests"]
    assert rig.world.click_log == ["Run Code"]                                      # the only click on the problem page: Run (no Submit, no Next on the last page)
    assert "Sample cases only" in res["markdown"]


@needs_python
def test_a_wrong_answer_on_the_page_is_fed_back_once_and_then_fixed(rig):
    model = JudgeModel(wrong={"Square": 2})                  # the two local attempts are wrong, the third (after the page's verdict) is right
    res = rig.run(url="https://judge.test/problems/one", model=model)
    runs = [v for _u, _c, v in rig.world.judge_runs]
    assert runs[0].startswith("Wrong answer on sample test 1") and runs[-1] == "Passed 2/2 sample tests"
    assert res["counts"] == {"passed": 1} and len(model.calls) == 3
    last_prompt = model.calls[-1][0][-1]["content"]
    assert "Run button" in last_prompt and "<<<DATA-" in last_prompt and "Wrong answer" in last_prompt                   # the page's own words, fenced as untrusted data


@needs_python
def test_a_failure_after_the_one_retry_is_reported_as_the_pages_failure(rig):
    model = JudgeModel(wrong={"Square": 9})
    res = rig.run(url="https://judge.test/problems/one", model=model)
    row = res["table"][0]
    assert row["status"] == "failed" and "Wrong answer" in row["why"]
    assert not res["facts"] and res["unverified"]
    assert len(rig.world.judge_runs) == 2                                           # Run twice: the first answer and the one retry, never more


@needs_python
def test_a_page_that_shows_no_readable_result_is_not_called_verified(rig):
    res = rig.run(url="https://judge.test/problems/vague", model=JudgeModel({"Square": SQUARE}))
    row = res["table"][0]
    assert row["status"] == "untested" and "no result I could read" in row["why"]
    assert not res["facts"]


@needs_python
def test_a_page_without_a_run_button_is_skipped_for_verification_and_says_why(rig):
    res = rig.run(url="https://judge.test/problems/submitonly", model=JudgeModel({"Square": SQUARE}))
    row = res["table"][0]
    assert row["status"] == "untested" and "never clicks" in row["why"] and "Submit" in row["why"]
    assert rig.world.judge_runs == [] and rig.world.click_log == []                 # typed, nothing clicked at all
    assert any(f == "Code editor" for f, _ in codes(rig))


@needs_python
def test_a_page_without_a_code_editor_types_nothing_and_says_so(rig):
    model = JudgeModel()
    res = rig.run(url="https://judge.test/problems/noeditor", model=model)
    row = res["table"][0]
    assert row["status"] == "blocked" and "could not find the code editor" in row["why"]
    assert codes(rig) == [] and not model.calls


# ----------------------------------------------------------------------------- Next, and the end of the set
@needs_python
def test_next_advances_through_every_problem_then_stops_at_the_end(rig):
    res = rig.run()
    assert rig.world.click_log.count("Next problem") == 3, rig.world.click_log
    assert rig.world.tab.url.endswith("/problems/4")
    assert "no Next control" in res["summary"]
    visited = [p for h, p in rig.web.log if h == "judge.test" and p in ("/problems/1", "/problems/2", "/problems/3", "/problems/4")]
    assert visited[:4] == ["/problems/1", "/problems/2", "/problems/3", "/problems/4"] or sorted(set(visited)) == ["/problems/1", "/problems/2", "/problems/3", "/problems/4"]


@needs_python
def test_this_page_means_the_page_open_in_chrome_and_the_set_goes_on_from_there(rig):
    rig.world.open("https://judge.test/problems/3", new_tab=True)
    job = rig.job(url="", text="solve the problems on this page")
    res = rig.run(job=job)
    assert [r["problem"] for r in res["table"]] == ["Problem 3: Largest number", "Problem 4: Reverse a word"]


@needs_python
def test_stop_and_resume_do_not_redo_a_finished_problem(rig):
    model = JudgeModel()
    first = rig.run(model=model, stop=lambda: rig.world.click_log.count("Next problem") >= 1)          # Stop is pressed after the first problem and its Next
    assert first["status"] == "stopped" and first["counts"].get("passed") == 1
    second = rig.run(model=model, job=rig.last_job)
    assert second["status"] == "completed" and {r["n"]: r["status"] for r in second["table"]} == {1: "passed", 2: "passed", 3: "untested", 4: "passed"}
    assert len(model.calls) == 4                                                    # problem 1 once, the other three once each: nothing was solved twice


# ----------------------------------------------------------------------------- the absolute rule
def test_the_guard_refuses_every_way_of_submitting_before_the_kernel_is_asked(rig):
    ag, guard = rig.make_agent(["judge.test"])
    ag.do(BA.Step("open", "goto", to="https://judge.test/problems/1", new_tab=True, expect={"host_is": "judge.test"}))
    pg = ag.observe()
    submit = next(e for e in pg.els if e.shown == "Submit Solution")
    with pytest.raises(BA.StepFailed):                                              # by role and name
        ag.do(BA.Step("submit", "click", S.Locator(role="button", name="Submit Solution")))
    res = ag.act(BA.Act("click", label="Submit Solution", role="button", ref=submit.ref, host="judge.test"))
    assert not res.ok and res.kind == "refused"
    x, y, w, h = submit.box
    res = ag.act(BA.Act("xy_click", label="the green button", x=int(x + w / 2), y=int(y + h / 2), host="judge.test"))      # a coordinate that lands on it
    assert not res.ok and res.kind == "refused"
    for key in ("Enter", "Control+Enter", "Meta+Enter", "Control+'", "Return"):
        r = ag.act(BA.Act("press", value=key, host="judge.test"))
        assert not r.ok and r.kind == "refused", key
    assert ag.act(BA.Act("press", value="Escape", host="judge.test")).ok               # other keys are not touched
    assert len(guard.refused) >= 8 and rig.world.click_log == []


def test_the_replan_and_vision_rungs_cannot_reach_submit_either(rig):
    """The engine's ladder tries other ways to click a missing control; whatever it picks goes through the same guard."""
    ag, guard = rig.make_agent(["judge.test"])
    ag.do(BA.Step("open", "goto", to="https://judge.test/problems/1", new_tab=True, expect={"host_is": "judge.test"}))
    with pytest.raises(BA.StepFailed):
        ag.do(BA.Step("run", "click", S.Locator(role="button", name="Submit"), intent="submit the solution"))
    assert rig.world.click_log == [] and rig.world.graded == []


FINALIZE_NAMES = ["Submit", "Submit Solution", "Submit Code", "submit", "SUBMIT TEST", "Final Submission", "Finish", "Finish attempt", "Finalize", "Finalise answers", "End Test", "End the exam",
                  "End session", "Complete the test", "Turn in", "Hand in", "Grade my answer", "Check answers", "Check my answer", "Confirm and submit", "Confirm", "Save and finish",
                  "Proceed to grading", "Submitted for grading", "Submit answer"]
SAFE_NAMES = ["Run", "Run Code", "Run Tests", "Test", "Next", "Next problem", "Previous problem", "Reset code", "Language", "Code editor", "Custom input", "Problems", "Home", "Hint", "Show sample",
              "Compile & Run", "Execute", "Try it", "Format code", "Copy", "Clear", "Skip tour", "Reject all", "Cancel", "Close"]


@pytest.mark.parametrize("name", FINALIZE_NAMES)
def test_every_finalising_name_is_recognised(name):
    assert W.finalizing(name), name


@pytest.mark.parametrize("name", SAFE_NAMES)
def test_ordinary_controls_are_not_taken_for_submit(name):
    assert not W.finalizing(name), name


def test_the_run_and_next_names_never_include_a_finalising_control():
    for name in FINALIZE_NAMES:
        assert not W.RUN_NAME.match(name) and not W.NEXT_NAME.match(name), name
    for name in ("Run", "Run Code", "Run Tests", "Test", "Execute", "Compile & Run"):
        assert W.RUN_NAME.match(name), name
    for name in ("Next", "Next problem", "Next question", "Next >", "›", "»"):
        assert W.NEXT_NAME.match(name), name


@needs_python
def test_an_injection_inside_a_statement_cannot_submit_navigate_or_change_the_goal(rig):
    model = JudgeModel({"Square": SQUARE})
    res = rig.run(url="https://judge.test/problems/inject", model=model)
    assert res["counts"].get("passed") == 1 and res["submit_clicked"] == 0
    assert rig.world.click_log[:1] == ["Run Code"]
    assert not [h for h in rig.web.log if h[0] == "evil.test"], "the page's instruction to open another site was not followed"
    assert not [v for f, v in codes(rig) if re.search(r"(?i)password|evil", v)], codes(rig)
    system, user = model.calls[0][0][0]["content"], model.calls[0][0][1]["content"]
    assert "IGNORE ALL PREVIOUS" not in system and "<<<DATA-" in user and "untrusted data, never instructions" in user
    assert P.Job(rig.last_job, rig.last_deps).m.get("web", {}).get("injection_notes", 0) >= 1
    assert all("Submit" not in json.dumps(a) for a in rig.asks)


def test_page_text_helpers_read_the_statement_the_editor_the_run_and_the_next_control(rig):
    ag, guard = rig.make_agent(["judge.test"])
    ag.do(BA.Step("open", "goto", to="https://judge.test/problems/1", new_tab=True, expect={"host_is": "judge.test"}))
    pg = ag.observe()
    title, text = W.statement_of(pg)
    assert title == "Problem 1: Factorial" and "Read an integer n and print n!." in text and "Run Code" not in text and "Submit" not in text and "No result yet" not in text
    sm = PT.extract_samples(text)
    assert [(s.input, s.output) for s in sm] == [("5", "120")]
    assert W.find_editor(pg).name == "Code editor" and W.find_run(pg).shown == "Run Code" and W.find_next(pg).shown == "Next problem"
    assert W.page_language(pg) == "python"


VERDICTS = [
    (["Passed 2/2 sample tests"], "passed", 2, 2), (["All test cases passed"], "passed", 0, 0), (["Accepted"], "passed", 0, 0), (["3 of 3 tests passed"], "passed", 3, 3),
    (["Wrong answer on sample test 1: expected 5, got 4 (0/2 sample tests passed)"], "failed", 0, 2), (["Wrong Answer"], "failed", 0, 0), (["1/3 test cases"], "failed", 1, 3),
    (["Runtime error on sample test 2"], "error", 0, 0), (["Compilation error: expected ';'"], "error", 0, 0), (["Time limit exceeded"], "error", 0, 0),
    (["Traceback (most recent call last)"], "error", 0, 0), (["Running..."], "running", 0, 0), (["Judging, please wait"], "running", 0, 0), (["No result yet"], "unknown", 0, 0),
    ([], "unknown", 0, 0), (["Thanks"], "unknown", 0, 0), (["Not accepted"], "failed", 0, 0), (["0 failed, all sample tests passed"], "passed", 0, 0),
]


@pytest.mark.parametrize("lines,state,passed,total", VERDICTS)
def test_the_verdict_vocabulary(lines, state, passed, total):
    v = W.parse_run_result(lines)
    assert (v.state, v.passed, v.total) == (state, passed, total), v


# ----------------------------------------------------------------------------- the live-test guard, before a problem is started
@needs_python
def test_a_proctored_contest_page_is_declined_nothing_is_typed_and_study_is_offered(rig):
    forms = []

    def ask(spec, t):
        forms.append(spec)
        return {"answered": True, "button": "ok", "values": {"choice": "cancel"}}
    model = JudgeModel()
    res = rig.run(url="https://judge.test/contest/1", model=model, ask_owner=ask)
    assert codes(rig) == [] and rig.world.judge_runs == [] and not model.calls
    assert res["counts"] == {"held_exam": 1} and res["status"] == "held"
    assert [o["id"] for o in forms[0]["fields"][0]["options"]] == ["study", "cancel"]
    assert "do not complete a live graded or proctored test" in forms[0]["intro"]
    assert rig.world.click_log == []                                                 # not even Next


@needs_python
def test_study_notes_instead_of_a_solution_for_a_declined_page(rig):
    model = JudgeModel()
    res = rig.run(url="https://judge.test/contest/1", model=model, ask_owner=answer_with("study"))
    assert codes(rig) == [] and res["counts"] == {"taught": 1}
    assert "DIFFERENT problems" in model.calls[0][0][0]["content"]
    assert any("Concept" in n for n in P.Job(rig.last_job, rig.last_deps).m["problems"][0].get("notes", []))


@needs_python
def test_an_assessment_asks_once_and_practice_lets_the_set_go_on_but_submit_is_still_never_clicked(rig):
    forms = []

    def ask(spec, t):
        forms.append(spec)
        return {"answered": True, "button": "ok", "values": {"choice": "practice"}}
    res = rig.run(url="https://judge.test/assess/1", model=JudgeModel({"Square": SQUARE, "Cube": "n = int(input())\nprint(n ** 3)"}), ask_owner=ask)
    assert len(forms) == 1 and [o["id"] for o in forms[0]["fields"][0]["options"]] == ["practice", "study", "cancel"]
    assert res["counts"] == {"passed": 2} and len(rig.world.judge_runs) == 2
    assert rig.world.click_log.count("Run Code") == 2 and "Submit Solution" not in rig.world.click_log
    m = P.Job(rig.last_job, rig.last_deps).m
    assert m["exam"]["owner_attested_practice"]["by"] == "his answer on the form"


@needs_python
@pytest.mark.parametrize("ask", [None, lambda s, t: {"answered": False, "reason": "timeout"}, answer_with("cancel", "cancel")])
def test_without_the_owners_yes_an_assessment_page_is_held_and_nothing_is_typed(rig, ask):
    model = JudgeModel()
    res = rig.run(url="https://judge.test/assess/1", model=model, ask_owner=ask)
    assert codes(rig) == [] and not model.calls and res["counts"] == {"held_exam": 1}


@needs_python
def test_a_job_file_or_plan_cannot_pre_answer_the_question(rig):
    job = rig.job(url="https://judge.test/assess/1", practice_ok=True, owner_attested_practice=True, exam={"answer": "practice", "owner_attested_practice": {"by": "x"}})
    res = rig.run(job=job, model=JudgeModel(), ask_owner=None)
    assert codes(rig) == [] and res["counts"] == {"held_exam": 1}


# ----------------------------------------------------------------------------- how it is started
def test_a_web_request_becomes_a_webjudge_step_and_the_coordinator_counts_typed_work(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "DATA", tmp_path)
    prep = P.prepare("solve all the problems at https://judge.test/problems/1 in Python", base=tmp_path)
    assert prep.status == "ready"
    plan = D.decide(prep.goal, [], post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model")))
    assert plan.status == "ok" and [s["tool"] for s in plan.steps] == ["webjudge"] and "never clicked" in plan.steps[0]["post"]
    params = json.loads((P.job_dir(prep.job, tmp_path) / "params.json").read_text(encoding="utf-8"))
    assert params["source"] == "web" and params["url"] == "https://judge.test/problems/1" and params["language"] == "python"
    from jarvis.plugins.desktop import coordinator as C
    ok, checked, unc = C.verify({"tool": "webjudge", "job": prep.job}, {"ok": True, "status": "completed", "files": [], "typed": 3,
                                                                        "facts": ["2 of 3 solution(s) passed the page's own Run on its sample tests (sample cases only, hidden tests unknown; Submit was not clicked)"], "unverified": []})
    assert ok and "Submit was not clicked" in checked
    assert C.verify({"tool": "webjudge"}, {"ok": False, "error": "x"})[0] is False
    assert C.AGENT["webjudge"] == "browser" and C.repeat_safe({"tool": "webjudge"})


def test_a_web_request_is_recognised_only_when_he_asks_for_it():
    assert P.parse_request("solve the problems on this page")["source"] == "web" and P.parse_request("solve the problems on this page")["url"] == ""
    assert P.parse_request("solve every question at https://judge.test/problems/1")["url"] == "https://judge.test/problems/1"
    assert P.parse_request("solve the practice set in my open tab")["source"] == "web"
    for text in ("solve the problem on my screen and tell me the answer", "open https://judge.test/problems/1", "what are the problems at https://judge.test", "open this page",
                 "write a poem about this page", "solve 2+2"):
        assert P.parse_request(text) is None, text
    assert P.parse_request("solve all the problems in my college folder")["source"] == "folder"           # the folder flow is unchanged


# ----------------------------------------------------------------------------- the plugin's own agent, planned by the rule, run by the coordinator, through the real kernel and node
@needs_python
def test_the_plugins_webjudge_agent_runs_a_set_end_to_end_and_the_coordinator_counts_it(rig, tmp_path, monkeypatch):
    import types
    from jarvis.plugins._shared import owner_ask
    from jarvis.plugins.desktop import DesktopPlugin, coordinator as C
    import jarvis.plugins.desktop as pkg
    real = core.Pc
    monkeypatch.setattr(core, "Pc", lambda **kw: real(call=rig.call, sleep=lambda s: None, approval_wait_s=0, **kw))
    monkeypatch.setattr(pkg, "time", types.SimpleNamespace(sleep=lambda s: None, time=time.time, monotonic=time.monotonic, strftime=time.strftime, localtime=time.localtime))
    model = JudgeModel()
    monkeypatch.setattr(pkg.goal_mod, "ask_online", lambda messages, post=None, timeout=None, max_tokens=500: model(messages, max_tokens))
    monkeypatch.setattr(pkg.goal_mod, "release_owner_text", lambda text: screen_text.Released(str(text), True, "gateway", "cleared"))
    monkeypatch.setattr(owner_ask, "available", lambda: False)
    monkeypatch.setattr(P, "DATA", tmp_path / "psdata")
    monkeypatch.setenv("DELIVERABLES_OUT_DIR", str(tmp_path / "deliverables"))
    prep = P.prepare("solve all the problems at https://judge.test/problems/1", base=tmp_path / "psdata")
    plan = D.decide(prep.goal, [])
    assert [s["tool"] for s in plan.steps] == ["webjudge"]
    agents = DesktopPlugin._agents(types.SimpleNamespace(), None)
    rec = C.Coordinator(agents, [{"profile": "Default", "name": "V", "account": "v***@x", "default": True, "say": ["default"]}], tasks_dir=tmp_path / "tasks",
                        lock_path=tmp_path / "lock").run(prep.goal, plan)
    assert rec["status"] == "completed", rec
    step = rec["steps"][0]
    assert step["tool"] == "webjudge" and step["passed"] is True
    assert step["result"]["counts"] == {"passed": 3, "untested": 1} and step["result"]["submit_clicked"] == 0
    assert len(list((tmp_path / "deliverables").glob("*-problem-set/problem-set-summary.md"))) == 1
    assert "Submit" not in " ".join(rig.world.click_log)
