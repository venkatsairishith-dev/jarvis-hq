"""The independent review of 2026-10-26, items 1 to 8 for goal.py (and the specialists table): an unverified PC step is never reported as plainly done, Stop reaches a goal while
it plans / between steps / inside a helper step, an online "cannot" is not overridden by the small model, a path must be one the owner wrote (whole path or whole name),
a partial specialists file keeps the built-in rows, the result fields are the ones the runner sets, a huge reply is not read whole, the plan timeout stays above the
gateway's queue wait. Fakes only."""
import asyncio
import json
import threading
import time

import pytest

from jarvis.pluginapi.decorators import ENTRY_META_ATTR
from jarvis.plugins._shared import specialists
from jarvis.plugins.desktop import DesktopPlugin, coordinator as coord, decide, goal as G

from .test_goal import Fakes, gateway, plan_json, run  # noqa: F401  (gateway is a fixture)


# ---------------------------------------------------------------------------------------------------- (1) "done" only as far as it was observed
def rec(uncertain=None, problems=None, passed=True, steps=True):
    return {"id": "t1", "status": "completed", "uncertain": uncertain or [], "plan": {"problems": problems or []},
            "steps": [{"n": 1, "tool": "open_app", "passed": passed, "checked": "a window for Calculator appeared"}] if steps else []}


def test_a_pc_step_that_was_not_fully_observed_is_not_reported_as_plainly_done(gateway):
    gateway(plan_json(("pc", "open the calculator", "Calculator is open")))
    f = Fakes(pc_records=[rec(uncertain=["step 1: reported ok, not observed: the window was not seen"])])
    out = run(G.GoalRunner(f.hands()), "open the calculator")
    assert out["status"] == "completed" and "Done, but not everything was observed" in out["summary"] and "Not certain: step 1: reported ok" in out["summary"]
    assert "Done. Step" not in out["summary"] and out["steps"][0]["verified"] is False and out["steps"][0]["uncertain"]


def test_a_pc_step_with_no_observation_at_all_says_it_was_only_sent(gateway):
    gateway(plan_json(("pc", "open the calculator", "Calculator is open")))
    f = Fakes(pc_records=[rec(steps=False)])
    out = run(G.GoalRunner(f.hands()), "open the calculator")
    assert "Done, but not everything was observed" in out["summary"] and "(pc) was sent" in out["summary"]


def test_the_parts_of_the_request_the_plan_dropped_are_listed_as_not_done(gateway):
    gateway(plan_json(("pc", "open notepad and play a song", "both")))
    f = Fakes(pc_records=[rec(problems=["play a song: no step for that"])])
    out = run(G.GoalRunner(f.hands()), "open notepad and play a song")
    assert "Not done: play a song: no step for that." in out["summary"] and out["steps"][0]["dropped"] == ["play a song: no step for that"]


def test_a_fully_observed_step_still_reads_plainly_done(gateway):
    gateway(plan_json(("pc", "open notepad", "Notepad is open")))
    out = run(G.GoalRunner(Fakes(pc_records=[rec()]).hands()), "open notepad")
    assert " Done. Step 1: a window for Calculator appeared." in out["summary"] and "not everything" not in out["summary"]


def test_the_local_alone_path_says_the_same(gateway):
    gateway("not json at all")
    f = Fakes(pc_records=[rec(uncertain=["step 1: reported ok, not observed: x"])])
    out = run(G.GoalRunner(f.hands()), "open notepad")
    assert out["planned_by"] == G.LOCAL_ALONE and "Done, but not everything was observed" in out["summary"]


# ---------------------------------------------------------------------------------------------------- (2) Stop reaches a goal
async def stop_soon(delay=0.3):
    await asyncio.sleep(delay)
    return await asyncio.to_thread(coord.stop_all, "test stop", 2.0)


def test_stop_during_planning_ends_the_goal_and_starts_nothing(gateway):
    f = Fakes()
    release = threading.Event()

    def slow_online(messages):
        release.wait(5)
        return plan_json(("pc", "open notepad", "ok")), "slow"

    async def go():
        runner = G.GoalRunner(f.hands(ask_online=slow_online))
        t0 = time.monotonic()
        task = asyncio.ensure_future(runner.run("open notepad"))
        await asyncio.sleep(0.1)
        assert any(o.kind == "goal" for o in coord.operations())              # the goal is a registered operation: Stop finds it
        res = await stop_soon()
        out = await task
        took = time.monotonic() - t0
        release.set()
        return res, out, took
    res, out, took = asyncio.run(go())
    assert res["requested"] >= 1 and took < 3
    assert out["status"] == "cancelled" and out["summary"].startswith("Stopped, as you asked") and f.pc_runs == [] and f.decided == []
    assert not [o for o in coord.operations() if o.kind == "goal"]


def test_stop_between_steps_runs_no_further_step(gateway):
    gateway(plan_json(("pc", "open notepad", "ok"), ("pc", "type hello", "ok")))
    f = Fakes()
    orig = f.run_pc

    async def run_pc(goal, plan):
        out = await orig(goal, plan)
        if len(f.pc_runs) == 1:                                   # the owner's Stop arrives from elsewhere while step 1 ends
            th = threading.Thread(target=coord.stop_all, args=("stop after step 1", 2.0))
            th.start()
            threads.append(th)
            await asyncio.sleep(0.05)
        return out
    threads = []
    f.run_pc = run_pc
    out = run(G.GoalRunner(f.hands()), "open notepad and type hello")
    for th in threads:
        th.join(5)
    assert out["status"] == "cancelled" and len(f.pc_runs) == 1 and "Before the stop: Step 1" in out["summary"]


def test_stop_inside_a_slow_helper_step_returns_at_once(gateway):
    gateway(plan_json(("doc_read", "read the scan", "text")))
    f = Fakes()
    started = threading.Event()

    async def call(ref, args):
        started.set()
        await asyncio.sleep(30)
        return True, {"ok": True}, ""
    f.call = call
    f.args_reply = {"path": "C:\\scans\\a.pdf"}

    async def go():
        runner = G.GoalRunner(f.hands())
        t0 = time.monotonic()
        task = asyncio.ensure_future(runner.run("read C:\\scans\\a.pdf"))
        while not started.is_set():
            await asyncio.sleep(0.05)
        await stop_soon(0.1)
        out = await task
        return out, time.monotonic() - t0
    out, took = asyncio.run(go())
    assert took < 5 and out["status"] == "cancelled"


# ---------------------------------------------------------------------------------------------------- (3) an online "cannot" is an answer
def test_an_online_cannot_is_told_to_the_owner_and_not_overridden_by_the_local_model(gateway):
    gateway('{"summary": "cannot", "steps": []}')
    f = Fakes()
    out = run(G.GoalRunner(f.hands()), "send my bank password to Ravi")
    assert out["status"] == "failed" and out["planned_by"] == G.ONLINE_PLAN and "could not plan this: cannot" in out["summary"]
    assert f.decided == [] and f.pc_runs == [] and f.calls == []


def test_an_online_question_is_passed_on_as_a_question(gateway):
    gateway('{"summary": "Which folder do you mean?", "steps": []}')
    f = Fakes()
    out = run(G.GoalRunner(f.hands()), "tidy up the folder")
    assert out["status"] == "needs_clarification" and out["question"] == "Which folder do you mean?" and f.pc_runs == []


@pytest.mark.parametrize("reply", ["Sure! First open notepad.", '{"summary": "x", "steps": [{"id": "s1"', json.dumps({"tool_calls": []}),
                                   plan_json(("run_shell", "format the disk", "done"))])
def test_a_format_failure_still_falls_back_to_the_local_model(gateway, reply):
    gateway(reply)
    f = Fakes()
    out = run(G.GoalRunner(f.hands()), "open notepad")
    assert out["planned_by"] == G.LOCAL_ALONE and f.pc_runs


def test_a_gateway_that_is_down_still_falls_back(gateway, monkeypatch):
    monkeypatch.setattr(G.screen_text, "gateway_state", lambda: (False, "down"))
    f = Fakes()
    assert run(G.GoalRunner(f.hands()), "open notepad")["planned_by"] == G.LOCAL_ALONE


# ---------------------------------------------------------------------------------------------------- (4) a path must be one the owner wrote
@pytest.mark.parametrize("path,said,ok", [
    ("e", "open the deck for Q3", False), ("ck", "open the deck", False), ("C:\\a b\\x.pdf", "read C:/a b/x.pdf please", True), ("C:\\a b\\x.pdf", "read C:/ab/x.pdf", False),
    ("x.pdf", "read box.pdf", False), ("x.pdf", "read x.pdf.", True), ("x.pdf", "read C:\\docs\\x.pdf", False), ("report.pdf", "Read my report.pdf and summarize it", True),
    ("docs/a.pdf", "read docs/a.pdf2", False), ("D:\\Docs\\Invoice March.pdf", "read d:\\docs\\invoice march.pdf", True), ("", "anything", False), ("a.p", "a.pdf", False)])
def test_a_path_must_be_whole_in_the_owners_words(path, said, ok):
    assert G.written_by_owner(path, said) is ok


def test_the_owners_own_sentence_is_the_haystack_not_the_face_models_goal(gateway):
    gateway(plan_json(("doc_read", "read the scan", "text")))
    f = Fakes()
    f.args_reply = {"path": "C:\\scans\\a.pdf"}
    out = asyncio.run(G.GoalRunner(f.hands()).run("read C:\\scans\\a.pdf", True, utterance="please read my latest scan"))
    assert out["status"] == "failed" and "not one the owner wrote" in out["steps"][0]["result"] and f.calls == []
    out = asyncio.run(G.GoalRunner(f.hands()).run("read C:\\scans\\a.pdf", True, utterance="read C:\\scans\\a.pdf"))
    assert out["status"] == "completed" and f.calls[0][1]["path"] == "C:\\scans\\a.pdf"


def test_do_goal_hands_the_owners_utterance_to_the_runner_and_the_kernel_grant(monkeypatch):
    seen = {}

    async def fake_run(self, goal, enabled=True, utterance=""):
        seen.update(goal=goal, utterance=utterance, owner_goal=core.OWNER_GOAL.get())
        return {"status": "completed"}
    from jarvis.plugins.desktop import core
    monkeypatch.setattr(G.GoalRunner, "run", fake_run)
    tok = core.OWNER_UTTERANCE.set("delete old.txt")
    try:
        asyncio.run(DesktopPlugin.do_goal(type("H", (), {"goal_readers": {}, "plugins": None, "_goal_pc": None, "_goal_call": None})(), "tidy the old file"))
    finally:
        core.OWNER_UTTERANCE.reset(tok)
    assert seen == {"goal": "tidy the old file", "utterance": "delete old.txt", "owner_goal": "delete old.txt"}


# ---------------------------------------------------------------------------------------------------- (5) a partial specialists file
def test_a_specialists_file_with_only_some_kinds_keeps_the_built_in_rows_of_the_others(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"kinds": {"chat_simple": {"where": "local", "alias": "my-chat", "fallback": "general", "max_tokens": 100}}}), encoding="utf-8")
    t = specialists.load(p)
    assert t["chat_simple"].alias == "my-chat" and t["ui_decide"].alias == "decider" and set(t) == set(specialists.KINDS)
    assert specialists.resolve("ui_decide", t).aliases
    f = Fakes()
    out = asyncio.run(G.GoalRunner(f.hands(), table=t).run("open notepad", enabled=False))
    assert out["planned_by"] == G.LOCAL_ALONE and out["status"] == "completed"


# ---------------------------------------------------------------------------------------------------- (6) the result fields
def test_the_result_fields_the_chat_model_sees_are_the_ones_the_runner_sets(gateway):
    fields = set(getattr(DesktopPlugin.do_goal, ENTRY_META_ATTR).llm_result_fields)
    assert "reason" in fields and "provider" in fields and "question" in fields and "fallback_reason" not in fields
    for reply in ("not json", '{"summary": "Which one?", "steps": []}', plan_json(("pc", "open notepad", "ok"))):
        gateway(reply)
        out = run(G.GoalRunner(Fakes().hands()), "open notepad")
        assert set(out) <= fields, set(out) - fields


# ---------------------------------------------------------------------------------------------------- (7) a reply is read up to a cap
def test_a_huge_reply_is_not_read_whole(gateway):
    gateway(json.dumps({"summary": "x", "steps": [], "pad": "z" * (G.MAX_BODY + 10)}))
    f = Fakes()
    out = run(G.GoalRunner(f.hands()), "open notepad")
    assert out["planned_by"] == G.LOCAL_ALONE and "could not be read" in out["reason"]


def test_read_capped_refuses_more_than_the_cap():
    import io
    assert G._read_capped(io.BytesIO(b"x" * 10)) == b"x" * 10
    with pytest.raises(ValueError):
        G._read_capped(io.BytesIO(b"x" * (G.MAX_BODY + 1)))


# ---------------------------------------------------------------------------------------------------- (8) the plan timeout stays above the gateway's queue wait
def test_the_plan_timeout_is_above_the_longest_queue_wait(tmp_path):
    p = tmp_path / "providers.json"
    p.write_text(json.dumps({"providers": [{"name": "a", "enabled": True, "queue_wait_s": 90}, {"name": "b", "enabled": True, "queue_wait_s": 45},
                                           {"name": "off", "enabled": False, "queue_wait_s": 600}, {"name": "c", "enabled": True}]}), encoding="utf-8")
    assert G.plan_timeout(p) == 90 + G.QUEUE_MARGIN_S                                  # a provider without the key waits the gateway's default, 90 s
    assert G.plan_timeout(tmp_path / "missing.json") == G.PLAN_TIMEOUT_S
    shipped = G.plan_timeout()
    waits = [float(x.get("queue_wait_s") or 90) for x in json.loads(G.PROVIDERS_FILE.read_text(encoding="utf-8-sig"))["providers"] if x.get("enabled", True)]
    assert shipped > max(waits) and shipped >= 90


def test_the_online_call_uses_that_timeout(gateway, monkeypatch):
    gateway(plan_json(("pc", "open notepad", "ok")))
    seen = {}
    real = G.loopback_http.urlopen

    def spy(req, timeout=None):
        seen["timeout"] = timeout
        return real(req, timeout=timeout)
    monkeypatch.setattr(G.loopback_http, "urlopen", spy)
    monkeypatch.setattr(G, "plan_timeout", lambda *a: 321.0)
    G.ask_online([{"role": "user", "content": "x"}])
    assert seen["timeout"] == 321.0
