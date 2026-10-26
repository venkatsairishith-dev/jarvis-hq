"""One invariant per LAPTOP-side entry point (D58): a Telugu, Hindi or Tenglish owner message reaches the model call as English, English costs nothing, and a
translator that is missing or unsure never blocks (the original goes on and the owner is told). Fake translator (translate.FakeBackend) + fake model calls; the
gateway, the console, the speech server and the kernel's entry points have their own files (privacy/test_gateway_translate.py, console/tests/test_lang_hook.py,
kernel/tests/test_lang.py). The table of entry points is in docs/03_DECISIONS.md D58.
"""
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from jarvis.plugins._shared import translate as T

NATIVE = re.compile("[ऀ-ॿఀ-౿]")
TABLE = {"ఓపెన్": "open", "నోట్‌ప్యాడ్": "notepad", "చేయి": "do", "नोटपैड": "notepad", "खोलो": "open", "chesi": "and", "rayi": "write", "ఈ": "this"}
CASES = [("ఓపెన్ నోట్‌ప్యాడ్ చేయి", "open notepad do"), ("नोटपैड खोलो", "notepad open"), ("notepad chesi rayi", "notepad and write")]


@pytest.fixture(autouse=True)
def layer(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_LANGUAGE_FILE", str(tmp_path / "language.json"))
    monkeypatch.setenv("JARVIS_GLOSSARY", str(tmp_path / "glossary.json"))
    be = T.FakeBackend(table=TABLE)
    T.set_translator(T.Translator([be], {"keep_original": False}, glossary=[]))
    yield be
    T.set_translator(None)


# ------------------------------------------------------------------------------------------------------------ the desktop plugin

@pytest.mark.parametrize("text,english", CASES)
def test_decide_hands_the_decision_model_english(text, english, monkeypatch):
    from jarvis.plugins.desktop import decide
    monkeypatch.setattr(decide, "FAST_PATH", False)
    seen = []
    decide.decide(text, [], post=lambda body: seen.append(body) or '{"steps": [{"tool": "clarify", "question": "which?"}]}')
    assert seen and seen[0]["messages"][-1]["content"] == english and not NATIVE.search(json.dumps(seen[0], ensure_ascii=False))


def test_decide_leaves_english_alone_and_asks_nobody(layer, monkeypatch):
    from jarvis.plugins.desktop import decide
    monkeypatch.setattr(decide, "FAST_PATH", False)
    seen = []
    decide.decide("open notepad", [], post=lambda body: seen.append(body) or '{"steps": [{"tool": "open_app", "app": "notepad"}]}')
    assert seen[0]["messages"][-1]["content"] == "open notepad" and layer.calls == []


def test_decide_treats_reply_in_telugu_as_a_setting_not_a_pc_task(monkeypatch):
    from jarvis.plugins.desktop import decide
    plan = decide.decide("reply in Telugu", [], post=lambda body: pytest.fail("no model call for a setting"))
    assert plan.status == "clarify" and "Telugu" in plan.question and T.reply_language() == "te"


def _desktop(monkeypatch):
    from jarvis.plugins import desktop as D
    ran = []

    async def fake_run_task(self, request, plan=None):
        ran.append(request)
        return {"status": "done", "id": "t1", "steps": []}
    monkeypatch.setattr(D.DesktopPlugin, "_run_task", fake_run_task)
    monkeypatch.setattr(D.DesktopPlugin, "_task_result", staticmethod(lambda rec: D.Ok({"status": rec["status"], "summary": "done"})))
    return D, ran


@pytest.mark.parametrize("text,english", CASES)
def test_run_pc_task_runs_the_task_in_english(text, english, monkeypatch):
    D, ran = _desktop(monkeypatch)
    res = asyncio.run(D.DesktopPlugin.run_pc_task(SimpleNamespace(), request=text))
    assert ran == [english] and res.value["translation"]["source_lang"] in ("te", "hi", "te-Latn") and english in res.value["note"]


def test_run_pc_task_english_is_untouched_and_has_no_translation_note(layer, monkeypatch):
    D, ran = _desktop(monkeypatch)
    res = asyncio.run(D.DesktopPlugin.run_pc_task(SimpleNamespace(), request="open notepad"))
    assert ran == ["open notepad"] and "translation" not in res.value and layer.calls == []


def test_run_pc_task_with_a_missing_translator_runs_the_original_and_says_so(monkeypatch):
    D, ran = _desktop(monkeypatch)
    T.set_translator(T.Translator([], {"keep_original": False}, glossary=[]))
    res = asyncio.run(D.DesktopPlugin.run_pc_task(SimpleNamespace(), request="ఓపెన్ నోట్‌ప్యాడ్ చేయి"))
    assert ran == ["ఓపెన్ నోట్‌ప్యాడ్ చేయి"] and res.value["summary"].startswith("(I could not translate your message on this PC")


def test_run_pc_task_asks_before_acting_on_a_doubtful_translation_and_acts_once_the_owner_confirmed(monkeypatch):
    D, ran = _desktop(monkeypatch)
    T.set_translator(T.Translator([T.FakeBackend(table=TABLE, confidence=0.3)], {"keep_original": False, "min_confidence": 0.55}, glossary=[]))
    res = asyncio.run(D.DesktopPlugin.run_pc_task(SimpleNamespace(), request="ఓపెన్ నోట్‌ప్యాడ్"))
    assert ran == [] and res.value["status"] == "needs_clarification" and "I understood this as" in res.value["question"] and "open notepad" in res.value["question"]
    asyncio.run(D.DesktopPlugin.run_pc_task(SimpleNamespace(), request="ఓపెన్ నోట్‌ప్యాడ్", answers={"clarification": "yes"}))
    assert len(ran) == 1 and "open notepad" in ran[0] and "yes" in ran[0], "the owner confirmed: it runs, in English"


def test_do_goal_plans_in_english(monkeypatch):
    from jarvis.plugins import desktop as D
    seen = []

    class FakeRunner:
        def __init__(self, hands):
            pass

        async def run(self, goal, flag, **kw):
            seen.append(goal)
            return {"status": "done", "steps": []}
    monkeypatch.setattr(D.goal_mod, "GoalRunner", FakeRunner)
    stub = SimpleNamespace(_goal_pc=None, _goal_call=None, goal_readers={})
    res = asyncio.run(D.DesktopPlugin.do_goal(stub, goal="ఓపెన్ నోట్‌ప్యాడ్ చేయి"))
    assert seen == ["open notepad do"] and "open notepad do" in res.value["note"]


# ------------------------------------------------------------------------------------------------------------ the other plugins

@pytest.mark.parametrize("text,english", CASES)
def test_the_bridge_hands_the_console_english(text, english, monkeypatch):
    from jarvis.plugins.jarvis_bridge.client import JarvisClient
    sent = []

    async def fake_request(self, method, path, body=None, **kw):
        sent.append((method, path, body))
        return {"id": "c-1", "state": "running"}
    monkeypatch.setattr(JarvisClient, "_request", fake_request)
    asyncio.run(JarvisClient("http://127.0.0.1:1").delegate(text))
    assert sent[0][2]["text"] == english and not NATIVE.search(sent[0][2]["text"])


@pytest.mark.parametrize("text,english", CASES)
def test_the_local_team_models_get_english(text, english, monkeypatch):
    from jarvis.plugins.local_team import core
    monkeypatch.setattr(core, "loaded_models", lambda get=None: {"general", "code", "math", "router-llm"})
    bodies = []

    def post(url, body, headers=None, timeout=0):
        bodies.append(body)
        return {"choices": [{"message": {"content": "ok"}}], "timings": {"predicted_per_second": 50.0}}
    core.ask_local(text, "general", post=post)
    assert bodies[-1]["messages"][-1]["content"] == english


@pytest.mark.parametrize("text,english", CASES)
def test_research_plans_and_searches_in_english(text, english, monkeypatch):
    from jarvis.plugins import research as R
    seen = []
    monkeypatch.setattr(R, "_redactor", lambda: (None, None))
    monkeypatch.setattr(R.core, "run", lambda question, **kw: seen.append(question) or (_ for _ in ()).throw(R.ResearchError("stop here")))
    res = asyncio.run(R.ResearchPlugin.research_topic(SimpleNamespace(plugins=None), question=text))
    assert seen == [english] and res.is_err()


@pytest.mark.parametrize("text,english", CASES)
def test_the_main_agent_plans_in_english(text, english):
    from jarvis.plugins import main_agent as M
    planned = []

    async def make_plan(task, caps, mode, memory):
        planned.append(task)
        raise M.PlanError("stop here")

    async def catalogue(mode):
        return []

    async def plan_memory(task, ctx):
        return None, {}
    stub = SimpleNamespace(_catalogue=catalogue, _plan_memory=plan_memory, _make_plan=make_plan)
    asyncio.run(M.MainAgentPlugin.plan_task(stub, task=text))
    assert planned == [english]


@pytest.mark.parametrize("text,english", CASES)
def test_the_main_agent_run_task_plans_in_english(text, english):
    from jarvis.plugins import main_agent as M
    planned = []

    async def plan_unless_stopped(task, mode, cancel, run, ctx):
        planned.append(task)
        raise M.PlanError("stop here")
    stub = SimpleNamespace(_claim=lambda: "r1", _cancel=object(), _runs={}, _mark=lambda run: None, _end_unstarted=lambda *a, **k: None,
                           _plan_unless_stopped=plan_unless_stopped)
    asyncio.run(M.MainAgentPlugin.run_task(stub, task=text))
    assert planned == [english]


def test_the_workbench_drafter_reads_the_owners_task_in_english(monkeypatch):
    from jarvis.plugins.workbench import localmodel
    prompts = []
    monkeypatch.setattr(localmodel, "chat", lambda messages, max_tokens=0, timeout=0: prompts.append(messages[-1]["content"]) or "{}")
    localmodel.drafter("ఓపెన్ నోట్‌ప్యాడ్ చేయి", {})
    assert "Task: open notepad do" in prompts[0] and "ఓపెన్" not in prompts[0]
