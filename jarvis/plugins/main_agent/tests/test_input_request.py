"""Pending-input contract (2026-10-20): a step asks, the run is held on its checkpoint, the owner answers (chat box or voice,
one path), the answer is validated and single-use, the step re-runs with it, secrets never touch disk or the chat."""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from jarvis.plugins.main_agent import input_request as ir
from jarvis.plugins.main_agent import planner
from test_run_control import Err, Ok, make, settle  # noqa: E402 - the harness of the sibling test file

PC = {"summary": "On the PC", "steps": [{"id": "s1", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "open that",
                                         "args": {"request": "open that"}},
                                        {"id": "s2", "agent": "think", "goal": "report", "depends_on": ["s1"]}]}
FAKE_KEY = "gk-" + "Zq9" * 12                      # built at run time: no key-shaped literal in the source


def text_request():
    return {"title": "Clarification", "prompt": "Which app should I open?", "answer_arg": "answers",
            "fields": [{"name": "clarification", "kind": "text", "label": "Your answer", "max_len": 300}]}


def secret_request():
    return {"title": "Gemini API key", "prompt": "Paste the Gemini API key to test it.",
            "fields": [{"name": "api_key", "kind": "secret", "label": "API key"},
                       {"name": "role", "kind": "choice", "label": "Use it as",
                        "options": [{"value": "primary", "label": "Primary"}, {"value": "fallback", "label": "Fallback"}]}]}


def asking_entry(request_factory, done=None):
    seen = []

    async def call_entry(ref, params=None, timeout=10.0):
        seen.append(dict(params or {}))
        if "answers" in (params or {}):
            if done is not None:
                done.append(params["answers"])
            return Ok({"status": "completed", "task": "t", "steps": [{"n": 1, "passed": True}]})
        return Ok({"status": "needs_clarification", "question": "?", "input_request": request_factory()})
    call_entry.seen = seen
    return call_entry


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- the contract itself
def test_normalize_cleans_and_refuses_what_cannot_be_answered_safely():
    req = ir.normalize({"prompt": "Pick <script>x</script> one‮", "fields": [
        {"name": "role", "kind": "choice", "label": "Role", "options": [{"value": "primary", "label": "Primary"}]}],
        "id": "q-attacker-chosen"}, step_id="s1", ref="x:y")
    assert req["id"].startswith("q-") and req["id"] != "q-attacker-chosen" and req["status"] == "open"
    assert "<script" not in req["prompt"] and "‮" not in req["prompt"]
    bad = [{}, {"prompt": "p", "fields": []}, {"prompt": "p", "fields": [{"name": "A B", "kind": "text"}]},
           {"prompt": "p", "fields": [{"name": "x", "kind": "shell"}]},
           {"prompt": "p", "fields": [{"name": "x", "kind": "choice", "options": []}]},
           {"prompt": "", "fields": [{"name": "x", "kind": "text"}]},
           {"prompt": "p", "fields": [{"name": f"f{i}", "kind": "text"} for i in range(7)]},
           {"prompt": "p", "answer_arg": "__class__", "fields": [{"name": "x", "kind": "text"}]}]
    for raw in bad:
        with pytest.raises(ir.RequestError):
            ir.normalize(raw, step_id="s1", ref="x:y")


def test_validate_types_required_unknown_and_secret_split():
    req = ir.normalize(secret_request(), step_id="s1", ref="x:y")
    pub, sec = ir.validate(req, {"api_key": FAKE_KEY, "role": "fallback"})
    assert pub == {"role": "fallback"} and sec == {"api_key": FAKE_KEY}
    assert ir.summary(req, pub, sec) == {"values": {"role": "fallback"}, "secret_fields": ["api_key"]}
    for values, needle in (({"role": "fallback"}, "required"), ({"api_key": FAKE_KEY, "role": "admin"}, "offered"),
                           ({"api_key": FAKE_KEY, "role": "primary", "extra": 1}, "no field named"),
                           ("text", "named values"), ({"api_key": "x\x00y", "role": "primary"}, "control")):
        with pytest.raises(ir.AnswerError, match=needle) as e:
            ir.validate(req, values)
        assert FAKE_KEY not in str(e.value)


def test_voice_matching_is_conservative():
    two = ir.normalize({"prompt": "Which providers?", "fields": [
        {"name": "primary", "kind": "choice", "label": "Primary", "options": [{"value": o, "label": o.title()} for o in ("gemini", "groq", "openrouter")]},
        {"name": "fallback", "kind": "choice", "label": "Fallback", "options": [{"value": o, "label": o.title()} for o in ("gemini", "groq", "openrouter")]}]},
        step_id="s1", ref="x:y")
    assert ir.from_utterance(two, "Use Gemini as primary and Groq as fallback") == ("answer", {"primary": "gemini", "fallback": "groq"}, "")
    assert ir.from_utterance(two, "Use Gemini")[0] == "no"                       # one name for two fields: not guessed
    one = ir.normalize(text_request(), step_id="s1", ref="x:y")
    assert ir.from_utterance(one, "open the calculator")[0] == "no"               # a new command is not taken as an answer
    assert ir.from_utterance(one, "the answer is notepad") == ("answer", {"clarification": "notepad"}, "")
    assert ir.from_utterance(one, "never mind")[0] == "cancel"
    yes = ir.normalize({"prompt": "Run the test?", "fields": [{"name": "go", "kind": "bool"}]}, step_id="s1", ref="x:y")
    assert ir.from_utterance(yes, "yes")[1] == {"go": True} and ir.from_utterance(yes, "maybe later")[0] == "no"
    sec = ir.normalize(secret_request(), step_id="s1", ref="x:y")
    verdict, _, why = ir.from_utterance(sec, f"the answer is {FAKE_KEY}")
    assert verdict == "no" and "never taken by voice" in why


def test_expiry():
    req = ir.normalize({**text_request(), "expires_s": 120}, step_id="s1", ref="x:y", now=1000.0)
    assert ir.is_open(req, now=1100.0) and not ir.is_open(req, now=1121.0)


# ---------------------------------------------------------------- the whole loop through the plugin
def start_held(monkeypatch, tmp_path, request_factory=text_request, done=None):
    entry = asking_entry(request_factory, done)
    p = make(monkeypatch, tmp_path, plan=PC, call_entry=entry)

    async def go():
        res = await p.run_task(task="open that", source="chat")
        await settle(p)
        return res
    run(go())
    rid = next(iter(p._runs))
    return p, entry, rid


def test_ask_hold_answer_resume_complete(monkeypatch, tmp_path):
    done = []
    p, entry, rid = start_held(monkeypatch, tmp_path, done=done)
    rec = p._runs[rid]
    assert rec["state"] == "held" and rec["pending_input"]["status"] == "open"
    note = [m for m in p.pushed if (m.get("metadata") or {}).get("kind") == "jarvis_input_request"]
    assert note and note[0]["ai_behavior"] == "blind" and "Which app should I open?" in note[0]["parts"][0]["text"]
    listed = run(p.pending_inputs()).value
    assert listed["count"] == 1 and listed["requests"][0]["request"]["fields"][0]["name"] == "clarification"
    on_disk = json.loads((p._store.dir / f"{rid}.json").read_text())
    assert on_disk["pending_input"]["id"] == rec["pending_input"]["id"]          # survives a restart (checkpoint)
    qid = rec["pending_input"]["id"]

    async def answer():
        res = await p.answer_input(run_id=rid, request_id=qid, values={"clarification": "notepad"})
        await settle(p)
        return res
    res = run(answer())
    assert res.value["state"] == "answered"
    assert p._runs[rid]["state"] == "done" and done == [{"clarification": "notepad"}]
    assert entry.seen[0] == {"request": "open that"} and entry.seen[1]["answers"] == {"clarification": "notepad"}
    again = run(p.answer_input(run_id=rid, request_id=qid, values={"clarification": "calc"}))
    assert isinstance(again, Err) and "not waiting" in str(again.error) or "already" in str(again.error)
    assert len(done) == 1                                                       # the duplicate did nothing


def test_secret_reaches_only_the_step_and_is_never_stored(monkeypatch, tmp_path):
    done = []
    p, entry, rid = start_held(monkeypatch, tmp_path, secret_request, done)
    qid = p._runs[rid]["pending_input"]["id"]

    async def answer():
        res = await p.answer_input(run_id=rid, request_id=qid, values={"api_key": FAKE_KEY, "role": "primary"})
        await settle(p)
        return res
    res = run(answer())
    assert res.value["state"] == "answered" and done == [{"role": "primary", "api_key": FAKE_KEY}]
    stored = (p._store.dir / f"{rid}.json").read_text()
    assert FAKE_KEY not in stored and '"secret_fields": ["api_key"]' in stored
    assert FAKE_KEY not in json.dumps(p.pushed, default=str) and FAKE_KEY not in json.dumps(res.value)
    assert p._secrets() == {}                                                   # used once, dropped after its step


def test_stale_wrong_and_expired_answers_are_refused(monkeypatch, tmp_path):
    p, entry, rid = start_held(monkeypatch, tmp_path)
    qid = p._runs[rid]["pending_input"]["id"]
    assert isinstance(run(p.answer_input(run_id=rid, request_id="q-000000000000", values={"clarification": "x"})), Err)
    assert isinstance(run(p.answer_input(run_id="other-run", request_id=qid, values={"clarification": "x"})), Err)
    bad = run(p.answer_input(run_id=rid, request_id=qid, values={"clarification": 5}))
    assert isinstance(bad, Err) and p._runs[rid]["pending_input"]["status"] == "open"     # an invalid answer keeps it open
    p._runs[rid]["pending_input"]["expires"] = time.time() - 1
    exp = run(p.answer_input(run_id=rid, request_id=qid, values={"clarification": "x"}))
    assert isinstance(exp, Err) and "expired" in str(exp.error) and p._runs[rid]["pending_input"]["status"] == "expired"
    assert len(entry.seen) == 1                                                 # nothing re-ran


def test_answer_while_another_task_runs_is_not_used(monkeypatch, tmp_path):
    p, entry, rid = start_held(monkeypatch, tmp_path)
    qid = p._runs[rid]["pending_input"]["id"]
    p._active = "someone-else"
    res = run(p.answer_input(run_id=rid, request_id=qid, values={"clarification": "notepad"}))
    assert isinstance(res, Err) and "another task" in str(res.error)
    assert p._runs[rid]["pending_input"]["status"] == "open" and "answers" not in p._runs[rid] or not p._runs[rid].get("answers")


def test_cancel_ends_the_held_task(monkeypatch, tmp_path):
    p, entry, rid = start_held(monkeypatch, tmp_path)
    qid = p._runs[rid]["pending_input"]["id"]
    res = run(p.cancel_input(run_id=rid, request_id=qid))
    assert res.value["state"] == "cancelled" and p._runs[rid]["state"] == "cancelled"
    assert run(p.pending_inputs()).value["count"] == 0
    assert isinstance(run(p.answer_input(run_id=rid, request_id=qid, values={"clarification": "x"})), Err)


def test_voice_and_widget_text_use_the_same_answer_path(monkeypatch, tmp_path):
    done = []
    p, entry, rid = start_held(monkeypatch, tmp_path, done=done)

    async def say(text):
        res = await p.run_task(task=text, source="widget")
        await settle(p)
        return res
    first = run(say("the answer is notepad"))
    assert first.value["state"] == "answered" and first.value["source"] == "voice"
    assert done == [{"clarification": "notepad"}] and p._runs[rid]["state"] == "done"


def test_an_unrelated_command_while_a_question_waits_starts_a_new_task(monkeypatch, tmp_path):
    p, entry, rid = start_held(monkeypatch, tmp_path)

    async def say(text):
        res = await p.run_task(task=text, source="widget")
        await settle(p)
        return res
    res = run(say("play some music"))
    assert "run_id" in res.value and res.value["run_id"] != rid
    assert p._runs[rid]["pending_input"]["status"] == "open"                   # the question still waits


def test_a_secret_question_is_never_answered_by_voice(monkeypatch, tmp_path):
    p, entry, rid = start_held(monkeypatch, tmp_path, secret_request)
    res = run(p.run_task(task=f"the answer is {FAKE_KEY}", source="widget"))
    assert res.value["state"] == "needs_input" and "type it" in res.value["message"]
    assert FAKE_KEY not in json.dumps(res.value) and len(entry.seen) == 1
    assert FAKE_KEY not in (p._store.dir / f"{rid}.json").read_text()


def test_a_malformed_question_fails_the_step_instead_of_waiting(monkeypatch, tmp_path):
    p, entry, rid = start_held(monkeypatch, tmp_path, lambda: {"prompt": "x", "fields": [{"name": "x", "kind": "shell"}]})
    assert p._runs[rid]["state"] != "held" and "pending_input" not in p._runs[rid]
    assert "cannot be shown safely" in json.dumps(p._runs[rid]["results"])


def test_the_planner_can_never_answer_or_cancel_a_question():
    listing = [{"id": "main_agent", "status": "running", "entries": [{"id": e, "description": "d"} for e in
                                                                     ("answer_input", "cancel_input", "pending_inputs")]}]
    caps = planner.build_catalogue(listing, browser_ready=False, jarvis_ready=False, computer_ready=False,
                                   allow_computer=False, mode="work")
    assert not [c for c in caps if c.ref.startswith("main_agent:")]
