"""Screen- and disk-derived text in a main-agent run reaches only what may see it (audit 2026-10-18, Low: OCR / file-name egress).

A step result that holds OCR text, page text or window text is marked (StepResult.screen). Here a plugin step that returns such a
result, in the RAW form a source that did not clear it would give, is followed by every kind of dependent step, and the HTTP edge
of each sink is recorded: the model provider (think steps), the plugin bus
(JARVIS delegate_task, messenger, research, learn_vault) and the chat push (what the owner is told). SYNTHETIC canaries only.

Cases per sink: flag off (the default), flag on with a verified LOCAL model, flag on with a public planner and a ready gateway
(text arrives cleared), flag on with the gateway down / degraded / not confirming names / failing, and the helper raising."""
from __future__ import annotations

import asyncio
import json
import logging
import types

import httpx
import pytest

from jarvis.pluginapi.log import BraceLogger
from jarvis.plugins import main_agent as ma
from jarvis.plugins._shared import screen_text
from jarvis.plugins._shared.tests import fakes
from jarvis.plugins._shared.tests.fakes import C_EMAIL, C_KEY, C_OCR, C_PATH, C_STREET, leaked
from jarvis.plugins.main_agent.executor import StepResult, run_plan
from jarvis.plugins.main_agent.planner import Capability, Plan, PlanError, Step, parse_plan
from jarvis.plugins.main_agent.store import RunStore, result_from_dict, result_to_dict
from jarvis.pluginapi import Err, Ok

LOCAL = "http://127.0.0.1:8080/v1"
PUBLIC = "https://inference.dahl.global/v1"
RAW = "\n".join([f"Dear {C_OCR},", f"Your file {C_PATH} is ready", f"key = {C_KEY}", f"write to {C_EMAIL}", f"home: {C_STREET}"])
REAL_CLIENT = httpx.AsyncClient
NOT_CLEARED = fakes.NOT_CLEARED


@pytest.fixture
def gateways(monkeypatch):
    g = fakes.Gateways(monkeypatch)
    yield g
    g.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("JARVIS_SCREEN_TEXT_TO_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_LOCAL_LLM", raising=False)
    monkeypatch.delenv("MAIN_AGENT_MEMORY_TO_EXTERNAL_STEPS", raising=False)
    real_sleep = asyncio.sleep

    async def quick(delay=0, *a, **k):
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", quick)


class Provider:
    """HTTP edge of the model provider: the 'agent' model's chat endpoint. Everything the plugin sends it is recorded."""

    def __init__(self):
        self.requests: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((str(request.url), request.content.decode("utf-8", "replace")))
        return httpx.Response(200, json={"choices": [{"message": {"content": "SUMMARY"}}]})

    def sent(self) -> str:
        return "\n".join(body for _, body in self.requests)


@pytest.fixture
def provider(monkeypatch):
    import jarvis.pluginapi.models as cm
    import jarvis.pluginapi.models as lc
    edge = Provider()
    state = {"base": PUBLIC}

    class Chat:
        def __init__(self, base_url):
            self.base_url = base_url

        async def ainvoke(self, messages):
            async with REAL_CLIENT(transport=httpx.MockTransport(edge.handle)) as client:
                r = await client.post(self.base_url.rstrip("/") + "/chat/completions", json={"messages": messages})
            return types.SimpleNamespace(content=r.json()["choices"][0]["message"]["content"])

    class Cfg:
        def get_model_api_config(self, kind):
            return {"base_url": state["base"], "model": "m", "api_key": "", "provider_type": None}

    async def create(**kw):
        return Chat(kw["base_url"])
    monkeypatch.setattr(cm, "agent_model", lambda: Cfg().get_model_api_config("agent"))
    monkeypatch.setattr(lc, "create_chat_llm_async", create)
    edge.use = lambda base: state.__setitem__("base", base)
    return edge


class Bus:
    """The plugin bus: the OCR source, and every other plugin entry as a recorder of what it was given."""

    def __init__(self, ocr_value=None):
        self.calls: list[tuple[str, dict]] = []
        self.ocr_value = ocr_value if ocr_value is not None else {"ok": True, "app": "notepad", "text": RAW, fakes_key(): True}

    async def call_entry(self, ref, args, timeout=0):
        self.calls.append((ref, dict(args)))
        if ref == "desktop:read_window_text":
            return Ok(self.ocr_value)
        if ref == "jarvis_bridge:delegate_task":
            return Ok({"command_id": "c1"})
        if ref == "jarvis_bridge:task_result":
            return Ok({"state": "done", "reply": "ok"})
        if ref == "jarvis_bridge:pending_approvals":
            return Ok({"count": 0})
        return Ok({"ok": True, "saved": True})

    call_entry_json = call_entry

    def given(self, ref):
        return [a for r, a in self.calls if r == ref]

    def sent(self, skip=("desktop:read_window_text",)) -> str:
        return json.dumps([(r, a) for r, a in self.calls if r not in skip], default=str, ensure_ascii=False)


def fakes_key() -> str:
    return screen_text.SCREEN_KEY


def make_plugin(tmp_path, bus, base=PUBLIC):
    p = object.__new__(ma.MainAgentPlugin)
    p.__class__ = type("P", (ma.MainAgentPlugin,), {"plugins": bus})
    p.logger = BraceLogger("test.main_agent")
    p._agent_base_url = lambda: base
    p._runs, p._active, p._cancel = {}, None, None
    p._store = RunStore(tmp_path / "runs")
    p.pushed = []
    p.push_message = lambda **kw: p.pushed.append(kw)
    return p


def ocr_step(sid="s1"):
    return Step(sid, "plugin", "read the window", ref="desktop:read_window_text", args={"app": "notepad"})


def drive(p, steps):
    plan = Plan("t", steps)
    return asyncio.run(run_plan(plan, p._dispatch, step_timeout=30, total_timeout=60, retries=0))


def by_id(results):
    return {r.id: r for r in results}


def think(sid="s2", dep="s1"):
    return Step(sid, "think", "summarise it", depends_on=[dep])


CASES_OK = ("flag off", "local model", "public + gateway ready")


def configure(case, monkeypatch, gateways, provider):
    if case == "flag off":
        provider.use(PUBLIC)
        return None
    monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
    if case == "local model":
        provider.use(LOCAL)
        return gateways.make()
    provider.use(PUBLIC)
    return gateways.make()


# ------------------------------------------------------------------ the think step: the planner model's own provider edge
@pytest.mark.parametrize("case", CASES_OK)
def test_think_step_over_screen_text(case, tmp_path, monkeypatch, gateways, provider):
    gw = configure(case, monkeypatch, gateways, provider)
    bus = Bus()
    p = make_plugin(tmp_path, bus, base=LOCAL if case == "local model" else PUBLIC)
    res = by_id(drive(p, [ocr_step(), think()]))
    assert res["s2"].ok and res["s2"].screen, "what a think step writes from screen text is marked too"
    sent = provider.sent()
    assert len(provider.requests) == 1
    if case == "flag off":
        assert leaked(sent) == [] and screen_text.OFF_NOTE in sent                  # the default: not a character of it
    elif case == "local model":
        assert C_OCR in sent and C_STREET in sent and C_KEY not in sent             # a verified local model reads it, secrets removed
        assert gw.redact_requests == []                                              # and nothing needed the gateway
    else:
        assert leaked(sent) == [] and "[PERSON_1]" in sent                           # the public model gets what the gateway cleared
        sent_to_gateway = json.dumps(gw.redact_requests)
        assert len(gw.redact_requests) == 1 and C_KEY not in sent_to_gateway and C_PATH not in sent_to_gateway and C_EMAIL not in sent_to_gateway


@pytest.mark.parametrize("why,setup", NOT_CLEARED, ids=[w for w, _ in NOT_CLEARED])
def test_think_step_over_screen_text_is_withheld_when_nothing_cleared_it(why, setup, tmp_path, monkeypatch, gateways, provider):
    monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
    provider.use(PUBLIC)
    setup(gateways, monkeypatch)
    p = make_plugin(tmp_path, Bus())
    res = by_id(drive(p, [ocr_step(), think()]))
    sent = provider.sent()
    assert res["s2"].ok and leaked(sent) == [] and screen_text.WITHHELD in sent, why   # still answers, from a neutral note


def test_a_gateway_url_as_the_agent_model_is_not_local_so_the_text_is_cleared_first(tmp_path, monkeypatch, gateways, provider):
    """The privacy gateway is not a verified local model (it forwards to public providers): screen text goes through /redact."""
    monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
    gw = gateways.make()
    base = f"http://127.0.0.1:{gw.port}/v1"
    provider.use(base)
    p = make_plugin(tmp_path, Bus(), base=base)
    drive(p, [ocr_step(), think()])
    assert leaked(provider.sent()) == [] and len(gw.redact_requests) == 1


def test_an_ordinary_think_step_is_unchanged(tmp_path, monkeypatch, provider):
    bus = Bus(ocr_value={"ok": True, "text": f"plain weather text {C_OCR}"})             # not marked as screen text
    p = make_plugin(tmp_path, bus)
    res = by_id(drive(p, [ocr_step(), think()]))
    assert not getattr(res["s1"], "screen", False) and not getattr(res["s2"], "screen", False) and "plain weather text" in provider.sent()


def test_memory_influenced_think_over_screen_text_still_needs_a_local_model(tmp_path, monkeypatch, gateways, provider):
    """The two checks stack: the memory rule (verified local only) and the screen-text rule are both applied at the same call."""
    monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
    gateways.make()
    provider.use(PUBLIC)
    p = make_plugin(tmp_path, Bus())
    step = think()
    step.memory_influenced = True
    res = by_id(drive(p, [ocr_step(), step]))
    assert not res["s2"].ok and "Needs the owner" in res["s2"].error and provider.requests == []


# ------------------------------------------------------------------ a plugin step that takes the result into an argument
def save_step(ref="messenger_app:send_message", key="content"):
    return Step("s2", "plugin", "pass it on", ref=ref, args={key: "{{s1}}"}, depends_on=["s1"])


@pytest.mark.parametrize("ref,key", [("messenger_app:send_message", "content"), ("memo_reminder:add_note", "text"),
                                      ("jarvis_bridge:delegate_task", "message"), ("learn_vault:save_note", "note")])
def test_a_plugin_that_may_send_text_on_gets_screen_text_only_as_cleared(ref, key, tmp_path, monkeypatch, gateways):
    monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
    gateways.make()
    bus = Bus()
    p = make_plugin(tmp_path, bus)
    step = save_step(ref, key)
    if ref.startswith("learn_vault"):                 # writes a note file on this PC and sends nothing on
        res = by_id(drive(p, [ocr_step(), step]))
        assert C_OCR in json.dumps(bus.given(ref))                                  # a plugin that acts on this PC only gets it as it is
        assert res["s2"].screen
        return
    res = by_id(drive(p, [ocr_step(), step]))
    assert leaked(bus.sent()) == [] and "[PERSON_1]" in bus.sent() and res["s2"].screen


@pytest.mark.parametrize("flag", [False, True])
def test_a_sending_plugin_gets_a_note_when_the_flag_is_off_or_nothing_cleared_it(flag, tmp_path, monkeypatch, gateways):
    if flag:
        monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
        gateways.down()
    bus = Bus()
    p = make_plugin(tmp_path, bus)
    drive(p, [ocr_step(), save_step()])
    assert leaked(bus.sent()) == [] and "[" in bus.sent()
    assert (screen_text.WITHHELD if flag else screen_text.OFF_NOTE) in bus.sent()


@pytest.mark.parametrize("why,setup", NOT_CLEARED, ids=[w for w, _ in NOT_CLEARED])
def test_a_sending_plugin_never_gets_raw_text_whatever_fails(why, setup, tmp_path, monkeypatch, gateways):
    monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
    setup(gateways, monkeypatch)
    bus = Bus()
    drive(make_plugin(tmp_path, bus), [ocr_step(), save_step()])
    assert leaked(bus.sent()) == [], why


# ------------------------------------------------------------------ steps that take no input: goals of browser / computer / JARVIS / research
@pytest.mark.parametrize("flag", [False, True])
def test_a_jarvis_step_after_a_screen_step_sends_none_of_it(flag, tmp_path, monkeypatch, gateways):
    """delegate_task carries only the step's own goal: the earlier result is never part of what it is sent, with or without
    the flag, with a ready gateway or without one."""
    if flag:
        monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
        gateways.make()
    bus = Bus()
    p = make_plugin(tmp_path, bus)
    steps = [ocr_step(), Step("s2", "jarvis", "tidy the downloads folder", depends_on=["s1"])]
    res = by_id(drive(p, steps))
    assert res["s2"].ok, res["s2"].error
    assert not getattr(res["s2"], "screen", False)
    assert leaked(bus.sent()) == []
    assert bus.given("jarvis_bridge:delegate_task") == [{"task": "tidy the downloads folder"}]


@pytest.mark.parametrize("ref,arg", [("research:research_topic", "question"), ("local_team:solve_with_team", "task"),
                                      ("local_team:ask_local", "task"), ("web_search:search", "query"),
                                      ("jarvis_bridge:delegate_task", "task")])
def test_a_result_cannot_be_put_into_the_free_text_argument_of_research_local_team_search_or_jarvis(ref, arg):
    """Those entries' only free text is not an argument a result may be put into (parse_plan refuses {{s1}} there), so a step
    result - screen text included - cannot reach research's question, local_team's task, a web query or a JARVIS task."""
    caps = [Capability("think", "think", "t"),
            Capability("plugin", "desktop:read_window_text", "ocr", {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"]}),
            Capability("plugin", ref, "x", {"type": "object", "properties": {arg: {"type": "string"}}, "required": [arg]})]
    plan = json.dumps({"summary": "s", "steps": [
        {"id": "s1", "agent": "plugin", "plugin": "desktop:read_window_text", "goal": "read", "args": {"app": "notepad"}},
        {"id": "s2", "agent": "plugin", "plugin": ref, "goal": "use it", "args": {arg: "{{s1}}"}, "depends_on": ["s1"]}]})
    with pytest.raises(PlanError, match="only .* may take one"):
        parse_plan(plan, caps, 6)


def test_a_research_step_reaches_the_research_plugin_without_any_screen_text(tmp_path, monkeypatch, gateways):
    monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
    gateways.make()
    bus = Bus()
    step = Step("s2", "plugin", "research it", ref="research:research_topic", args={"question": "which clinic is near me"}, depends_on=["s1"])
    drive(make_plugin(tmp_path, bus), [ocr_step(), step])
    assert bus.given("research:research_topic") == [{"question": "which clinic is near me"}] and leaked(bus.sent()) == []


# ------------------------------------------------------------------ what the owner is told, what is kept, what is logged
def execute(p, steps):
    rid = p._claim()
    run = {"run_id": rid, "task": "t", "mode": "work", "source": "chat", "summary": "", "started": 0, "plan": {"summary": "", "steps": []},
           "results": {}, "running": [], "state": "running"}
    p._runs[rid] = run
    plan = Plan("t", steps)
    asyncio.run(p._execute(run, plan, None, p._cancel))
    return run


@pytest.mark.parametrize("case", CASES_OK)
def test_the_answer_told_to_the_owner_never_carries_raw_screen_text(case, tmp_path, monkeypatch, gateways, provider, caplog):
    caplog.set_level(logging.DEBUG)
    configure(case, monkeypatch, gateways, provider)
    p = make_plugin(tmp_path, Bus(), base=LOCAL if case == "local model" else PUBLIC)
    run = execute(p, [ocr_step()])                                  # the OCR result itself is the answer (a leaf step)
    said = json.dumps(p.pushed, ensure_ascii=False)
    stored = json.dumps(p._store.load(run["run_id"]), ensure_ascii=False)
    assert leaked(said) == [] and leaked(stored) == [] and leaked(caplog.text) == [], case
    if case == "flag off":
        assert screen_text.OFF_NOTE in said
    else:
        assert "[PERSON_1]" in said


@pytest.mark.parametrize("why,setup", NOT_CLEARED, ids=[w for w, _ in NOT_CLEARED])
def test_the_answer_is_a_neutral_note_when_nothing_cleared_the_text(why, setup, tmp_path, monkeypatch, gateways):
    monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
    setup(gateways, monkeypatch)
    p = make_plugin(tmp_path, Bus())
    run = execute(p, [ocr_step()])
    said = json.dumps(p.pushed, ensure_ascii=False)
    assert leaked(said) == [] and screen_text.WITHHELD in said, why
    assert leaked(json.dumps(p._store.load(run["run_id"]))) == []


def test_screen_text_is_not_written_to_the_run_file_and_a_resumed_run_still_knows_it_was_screen_text(tmp_path):
    r = StepResult("s1", "success", RAW, screen=True)
    d = result_to_dict(r)
    assert leaked(d) == [] and d["screen"] is True and "not kept" in d["text"]
    back = result_from_dict({"id": "s1", **d})
    assert back.screen is True and back.ok
    plain = result_to_dict(StepResult("s2", "success", "ordinary text"))
    assert plain["text"] == "ordinary text" and plain["screen"] is False


def test_errors_from_a_plugin_are_cleaned_before_they_become_a_step_error_or_the_answer(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    class Failing(Bus):
        async def call_entry(self, ref, args, timeout=0):
            if ref == "doc_tools:read_doc":
                return Err(f"cannot read {C_PATH}: token {C_KEY} for {C_EMAIL}")
            if ref == "desktop:run_pc_action":
                return Ok({"ok": False, "state": "failed", "error": f"refused {C_PATH} {C_KEY}"})
            return await super().call_entry(ref, args, timeout)
    bus = Failing()
    p = make_plugin(tmp_path, bus)
    steps = [Step("a", "plugin", "read", ref="doc_tools:read_doc", args={}), Step("b", "plugin", "act", ref="desktop:run_pc_action", args={})]
    run = execute(p, steps)
    said = json.dumps(p.pushed, ensure_ascii=False)
    assert "cannot read" in said and "refused" in said
    assert leaked(said) == [] and leaked(json.dumps(p._store.load(run["run_id"]))) == [] and leaked(caplog.text) == []


def test_an_exception_in_the_gateway_call_ends_in_a_note_not_in_the_text(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
    monkeypatch.setattr(screen_text, "gateway_state", lambda: (_ for _ in ()).throw(RuntimeError(f"boom {C_OCR}")))
    provider.use(PUBLIC)
    drive(make_plugin(tmp_path, Bus()), [ocr_step(), think()])
    assert leaked(provider.sent()) == [] and screen_text.WITHHELD in provider.sent()
