"""D51: an online model plans a goal (text only), local models execute it. Fakes only: a fake privacy gateway on a real local socket
(it records every request it gets), a fake local decider, fake hands. No model, no network beyond 127.0.0.1, no Windows."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.plugins._shared import specialists
from jarvis.plugins.desktop import DesktopPlugin, decide, goal as G
from jarvis.plugins.main_agent import planner

HEALTH = {"ok": True, "ready": True, "degraded": [], "pii_model": True, "names": False, "public_egress": True}


class FakeGateway:
    """/health and /v1/chat/completions of the privacy gateway. `reply` is the model's content (or an int: an HTTP error code)."""

    def __init__(self, reply, health=None):
        self.reply, self.health, self.requests = reply, HEALTH if health is None else health, []
        gw = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj, extra=None):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._send(200, gw.health)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                gw.requests.append({"path": self.path, "body": body, "headers": dict(self.headers)})
                if isinstance(gw.reply, int):
                    return self._send(gw.reply, {"error": {"message": "nope"}})
                self._send(200, {"choices": [{"message": {"role": "assistant", "content": gw.reply}}]}, {"X-Jarvis-Provider": "fakeonline"})
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def gateway(monkeypatch):
    made = []

    def make(reply, **kw):
        g = FakeGateway(reply, **kw)
        made.append(g)
        monkeypatch.setenv("JARVIS_PRIVACY_PORT", str(g.port))
        return g
    yield make
    for g in made:
        g.close()


def plan_json(*steps, summary="do it"):
    return json.dumps({"summary": summary, "steps": [
        {"id": f"s{i}", "agent": "plugin", "plugin": ex, "goal": goal, "check": check, "depends_on": [f"s{i - 1}"] if i > 1 else []}
        for i, (ex, goal, check) in enumerate(steps, 1)]})


class Fakes:
    """Fake hands: the local decider, the coordinator and the helper entries record what they were asked."""

    def __init__(self, pc_records=None, call=None):
        self.decided, self.pc_runs, self.calls, self.local_calls = [], [], [], []
        self.pc_records = list(pc_records or [])
        self.call_result = call or (lambda ref, args: (True, {"ok": True, "text": "Total 42"}, ""))

    def decide_fn(self, goal, model):
        self.decided.append((goal, model))
        return decide.Plan([{"tool": "open_app", "app": "notepad"}], "ok", model=model)

    async def run_pc(self, goal, plan):
        self.pc_runs.append((goal, plan))
        if self.pc_records:
            return self.pc_records.pop(0)
        return {"id": "t1", "status": "completed", "steps": [{"n": 1, "tool": "open_app", "passed": True, "checked": "notepad window is open"}]}

    async def call(self, ref, args):
        self.calls.append((ref, args))
        return self.call_result(ref, args)

    def local_post(self, body):
        self.local_calls.append(body)
        return json.dumps(self.args_reply)

    args_reply = {"app": "excel"}

    def hands(self, **kw):
        return G.Hands(run_pc=self.run_pc, call=self.call, decide_fn=self.decide_fn, local_post=self.local_post, **kw)


def run(runner, goal, enabled=True):
    return asyncio.run(runner.run(goal, enabled))


# ---------------------------------------------------------------------------------------------------- the split works
def test_the_online_plan_runs_locally_in_order(gateway):
    gw = gateway(plan_json(("pc", "open the sales workbook", "Excel window is in front"),
                           ("screen_read", "read the total in Excel", "a total is returned"), summary="open and read"))
    f = Fakes()
    out = run(G.GoalRunner(f.hands()), "open the sales workbook and tell me the total")
    assert out["status"] == "completed", out
    assert [s["executor"] for s in out["steps"]] == ["pc", "screen_read"]
    assert [s["status"] for s in out["steps"]] == ["completed", "completed"]
    assert out["planned_by"] == G.ONLINE_PLAN and out["provider"] == "fakeonline"
    # step 1 went through the local decider and the coordinator with the plan the decider made
    table = specialists.load()
    assert f.decided == [("open the sales workbook", table["ui_decide"].alias)]
    assert f.pc_runs[0][0] == "open the sales workbook" and f.pc_runs[0][1].steps[0]["tool"] == "open_app"
    # step 2: the local specialist for 'chat_simple' picked the arguments, the helper entry ran with exactly them
    assert f.calls == [("desktop:read_window_text", {"app": "excel"})]
    assert f.local_calls[0]["model"] == table["chat_simple"].alias
    assert out["steps"][1]["kind"] == "screen_read" and out["steps"][1]["model"] == table["screen_read"].alias
    assert out["steps"][0]["note"].endswith("Excel window is in front") and out["steps"][0]["verified"] is True
    assert out["steps"][1]["verified"] is False                     # a helper's "ok" is reported, not claimed as checked
    assert len(gw.requests) == 1


def test_the_online_request_is_text_only_with_no_tools_and_no_image(gateway):
    gw = gateway(plan_json(("pc", "open notepad", "Notepad is open")))
    run(G.GoalRunner(Fakes().hands()), "open notepad and my key is sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2")
    req = gw.requests[0]
    body, raw = req["body"], json.dumps(req["body"])
    assert req["path"] == "/v1/chat/completions" and req["headers"].get("X-Jarvis-Route") == "online"
    assert set(body) <= {"model", "messages", "temperature", "max_tokens"}, set(body)
    for banned in ("tools", "tool_choice", "functions", "function_call", "response_format", "stream", "image_url", "images", "data:image"):
        assert banned not in body and banned not in raw, banned
    assert all(isinstance(m["content"], str) for m in body["messages"]), "plain text messages only"
    assert "A1b2C3d4E5f6G7h8I9j0K1l2" not in raw, "a secret in the goal is scrubbed before it even reaches the gateway"
    system = body["messages"][0]["content"]
    for executor in ("pc", "screen_read", "screen_locate", "doc_read", "deliverable_list", "deliverable_open"):
        assert f'"{executor}"' in system
    assert '"recall"' not in system, "the face-side readers are offered only when the host lent them"


def test_the_readers_the_host_lends_appear_in_the_catalogue_and_run(gateway):
    gw = gateway(plan_json(("recall", "what did we do yesterday", "bullets returned")))
    f = Fakes()
    out = run(G.GoalRunner(f.hands(readers={"recall": lambda days=3: "2026-10-24: planned the demo"})), "what did we do yesterday")
    assert '"recall"' in gw.requests[0]["body"]["messages"][0]["content"]
    f.args_reply = {"days": 2}
    # (the first run had no 'days' in the local reply: an optional value may be left out)
    assert out["status"] == "completed" and "planned the demo" in out["steps"][0]["result"]


def test_a_gated_pc_step_stops_the_goal_and_waits(gateway):
    gateway(plan_json(("pc", "open the bank site", "site is open"), ("screen_read", "read the balance", "a number"), ("pc", "close it", "closed")))
    gated = {"id": "t9", "status": "waiting_for_permission", "approval_id": "ap-77", "how": "Approve ap-77", "steps": [{"n": 1, "tool": "open_website", "passed": False}]}
    f = Fakes(pc_records=[gated])
    out = run(G.GoalRunner(f.hands()), "open my bank and read the balance then close it")
    assert out["status"] == "waiting_for_permission" and out["approval_id"] == "ap-77"
    assert [s["status"] for s in out["steps"]] == ["waiting_for_permission", "not_run", "not_run"]
    assert f.calls == [] and len(f.pc_runs) == 1, "nothing after the gated step ran"
    assert "approval" in out["summary"] and "PIN" in out["summary"] and "ap-77" in out["summary"]


def test_a_gated_helper_step_waits_too(gateway):
    gateway(plan_json(("screen_read", "read the vault window", "text"), ("pc", "close it", "closed")))
    f = Fakes(call=lambda ref, args: (False, {}, "approval pending: ask the owner for the PIN"))
    out = run(G.GoalRunner(f.hands()), "read the vault window then close it")
    assert out["status"] == "waiting_for_permission" and out["steps"][1]["status"] == "not_run" and f.pc_runs == []


def test_a_failed_step_stops_the_rest(gateway):
    gateway(plan_json(("pc", "open notepad", "open"), ("pc", "type hello", "typed")))
    f = Fakes(pc_records=[{"id": "t", "status": "failed", "error": "step 1 (open_app): no such app", "steps": []}])
    out = run(G.GoalRunner(f.hands()), "open notepad and type hello")
    assert out["status"] == "failed" and out["steps"][1]["status"] == "not_run" and len(f.pc_runs) == 1
    assert "could not finish" in out["summary"]


# ---------------------------------------------------------------------------------------------------- the local fallback
BAD_PLANS = {
    "not json": "Sure! First open notepad, then type hello.",
    "truncated json": '{"summary": "x", "steps": [{"id": "s1", "agent": "plugin", "plugin": "pc", "goal": "open note',
    "no steps": '{"summary": "cannot", "steps": []}',
    "an executor that does not exist": plan_json(("run_shell", "format the disk", "done")),
    "a tool call instead of a plan": json.dumps({"tool_calls": [{"function": {"name": "pc_action", "arguments": "{}"}}]}),
    "an agent kind that is not offered": json.dumps({"summary": "x", "steps": [{"id": "s1", "agent": "jarvis", "goal": "do everything"}]}),
    "seven steps": plan_json(*[("pc", f"step {i}", "ok") for i in range(7)]),
}


@pytest.mark.parametrize("name", list(BAD_PLANS))
def test_an_unusable_online_plan_falls_back_to_the_local_model_alone_and_says_so(gateway, name):
    gateway(BAD_PLANS[name])
    f = Fakes()
    out = run(G.GoalRunner(f.hands()), "open notepad and type hello")
    assert out["planned_by"] == G.LOCAL_ALONE and out["reason"], out
    assert out["summary"].startswith("Planned by the local model alone:")
    assert f.decided == [("open notepad and type hello", specialists.load()["ui_decide"].alias)], "the whole goal went to the local decider"
    assert len(f.pc_runs) == 1 and f.pc_runs[0][0] == "open notepad and type hello" and f.calls == []
    assert out["status"] == "completed"


@pytest.mark.parametrize("how", ["http 500", "http 400", "degraded", "down"])
def test_a_gateway_that_is_down_or_refuses_falls_back(gateway, monkeypatch, how):
    if how == "http 500":
        gateway(500)
    elif how == "http 400":
        gateway(400)                                                    # the Dahl "Unterminated string" kind of refusal
    elif how == "degraded":
        gateway("unused", health={"ok": True, "ready": False, "degraded": ["pii_model_missing"], "pii_model": False})
    else:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            monkeypatch.setenv("JARVIS_PRIVACY_PORT", str(s.getsockname()[1]))
    f = Fakes()
    out = run(G.GoalRunner(f.hands()), "open notepad")
    assert out["planned_by"] == G.LOCAL_ALONE and out["status"] == "completed" and f.pc_runs


def test_the_decider_alias_falls_back_when_the_first_does_not_answer(gateway):
    gateway("not a plan")
    table = specialists.load()
    seen = []

    def decide_fn(goal, model):
        seen.append(model)
        if model == table["ui_decide"].alias:
            return decide.Plan([], "fallback", problems=["the local decision model did not answer (URLError)"], model=model)
        return decide.Plan([{"tool": "open_app", "app": "notepad"}], "ok", model=model)
    f = Fakes()
    hands = G.Hands(run_pc=f.run_pc, call=f.call, decide_fn=decide_fn, local_post=f.local_post)
    out = run(G.GoalRunner(hands), "open notepad")
    assert seen == [table["ui_decide"].alias, table["ui_decide"].fallback] and f.pc_runs[0][1].status == "ok" and out["status"] == "completed"


# ---------------------------------------------------------------------------------------------------- the flag
def test_the_flag_off_is_the_old_behaviour_and_asks_no_online_model(gateway):
    gw = gateway(plan_json(("pc", "open notepad", "open")))
    f = Fakes()
    out = run(G.GoalRunner(f.hands()), "open notepad", enabled=False)
    assert gw.requests == [], "no request left the PC"
    assert out["planned_by"] == G.LOCAL_ALONE and "off" in out["reason"]
    assert f.decided and len(f.pc_runs) == 1 and f.pc_runs[0][0] == "open notepad"


def test_flag_on_reads_the_plugin_config_and_the_environment(tmp_path):
    toml = tmp_path / "plugin.toml"
    assert G.flag_on(toml, {}) is True, "default ON (no file)"
    toml.write_text("[config]\nplan_online_execute_local = false\n", encoding="utf-8")
    assert G.flag_on(toml, {}) is False
    assert G.flag_on(toml, {G.ENV_FLAG: "1"}) is True, "the environment wins"
    toml.write_text("[config]\nplan_online_execute_local = true\n", encoding="utf-8")
    assert G.flag_on(toml, {G.ENV_FLAG: "0"}) is False
    toml.write_text("not = [valid", encoding="utf-8")
    assert G.flag_on(toml, {}) is True


def test_the_shipped_plugin_config_has_the_flag_on():
    assert G.flag_on(env={}) is True


# ---------------------------------------------------------------------------------------------------- picking arguments
def table_ex(name):
    ex = G.EXECUTORS[name]
    return ex, specialists.resolve(ex.kind)


def test_a_file_path_the_owner_never_wrote_is_not_used(gateway):
    gateway(plan_json(("doc_read", "read the scanned contract", "text returned")))
    f = Fakes()
    f.args_reply = {"path": r"C:\Windows\System32\config\SAM"}
    out = run(G.GoalRunner(f.hands()), r"read the scanned contract at D:\scans\contract.pdf")
    assert out["status"] == "failed" and f.calls == [] and "not one the owner wrote" in out["steps"][0]["result"]


def test_a_file_path_the_owner_wrote_is_used_with_its_specialist(gateway):
    gateway(plan_json(("doc_read", "read the scanned contract", "text returned")))
    f = Fakes()
    f.args_reply = {"path": r"D:\scans\contract.pdf", "max_pages": 3}
    out = run(G.GoalRunner(f.hands()), r"read the scanned contract at D:\scans\contract.pdf")
    assert f.calls == [("doc_reader:read_document", {"path": r"D:\scans\contract.pdf", "max_pages": 3})]
    assert out["steps"][0]["model"] == specialists.load()["doc_read"].alias


def test_pick_args_uses_the_fallback_alias_and_refuses_guesses():
    ex, spec = table_ex("screen_locate")
    seen = []

    def post(body):
        seen.append(body["model"])
        if body["model"] == spec.aliases[0] or len(seen) == 1:
            raise G.GoalError("the local model did not answer (URLError)")
        return json.dumps({"app": "excel", "text": "Save"})
    chat = specialists.resolve("chat_simple")
    assert G.pick_args(ex, chat, "find Save in Excel", "where is Save in Excel", post) == {"app": "excel", "text": "Save"}
    assert seen == list(chat.aliases)
    with pytest.raises(G.GoalError, match="does not say text"):
        G.pick_args(ex, chat, "find it", "find it in excel", lambda b: json.dumps({"app": "excel"}))
    with pytest.raises(G.GoalError, match="wrong type"):
        G.pick_args(ex, chat, "x", "x", lambda b: json.dumps({"app": 5, "text": "a"}))
    with pytest.raises(G.GoalError):
        G.pick_args(ex, chat, "x", "x", lambda b: "not json")
    # the grammar sent to the local model forbids fields the entry does not declare
    sent = {}
    G.pick_args(ex, chat, "x", "x", lambda b: sent.update(b) or json.dumps({"app": "a", "text": "b"}))
    assert sent["response_format"]["json_schema"]["schema"]["additionalProperties"] is False


# ---------------------------------------------------------------------------------------------------- plan parsing
def test_parse_online_plan_keeps_checks_and_clips_and_drops_arguments():
    caps = G.catalogue()
    text = json.dumps({"summary": "s", "steps": [{"id": "s1", "agent": "plugin", "plugin": "pc", "goal": "g" * 900, "check": "c" * 500,
                                                   "args": {"action": "format_disk"}}]})
    plan = G.parse_online_plan(text, caps)
    s = plan.steps[0]
    assert len(s["goal"]) == G.STEP_GOAL_CHARS and len(s["check"]) == G.CHECK_CHARS and "args" not in s
    with pytest.raises(planner.PlanError):
        G.parse_online_plan(plan_json(*[("pc", "x", "y")] * 7), caps)


def test_every_executor_kind_is_in_the_specialists_table():
    table = specialists.load()
    assert {e.kind for e in G.EXECUTORS.values()} <= set(table)
    assert all(table[e.kind].where == "local" for e in G.EXECUTORS.values())
    assert table["reason"].where == "online"


# ---------------------------------------------------------------------------------------------------- the plugin entry
class _Host:
    """Just enough of the plugin for do_goal: its own hands are replaced by the fakes."""
    def __init__(self, fakes, readers=None):
        self.f, self.goal_readers, self.plugins = fakes, readers or {}, object()
        self._goal_pc, self._goal_call = fakes.run_pc, fakes.call


def test_the_plugin_entry_runs_the_goal_and_cleans_the_result(gateway, monkeypatch):
    gateway(plan_json(("pc", "open notepad", "Notepad is open")))
    f = Fakes()
    monkeypatch.setattr(G, "decide", type("D", (), {"decide": staticmethod(lambda goal, profiles, model=None, post=None: f.decide_fn(goal, model)),
                                                  "LOCAL": decide.LOCAL, "Plan": decide.Plan}))
    monkeypatch.setattr(G, "_profiles", lambda: [])
    res = asyncio.run(DesktopPlugin.do_goal(_Host(f), goal="open notepad"))
    assert res.value["status"] == "completed" and res.value["steps"][0]["executor"] == "pc"
    assert f.pc_runs and f.decided


def test_the_plugin_entry_with_the_flag_off_does_not_ask_the_gateway(gateway, monkeypatch):
    gw = gateway(plan_json(("pc", "open notepad", "open")))
    monkeypatch.setenv(G.ENV_FLAG, "0")
    f = Fakes()
    monkeypatch.setattr(G, "_profiles", lambda: [])
    monkeypatch.setattr(G.decide, "decide", lambda goal, profiles, model=None, post=None: f.decide_fn(goal, model))
    res = asyncio.run(DesktopPlugin.do_goal(_Host(f), goal="open notepad"))
    assert gw.requests == [] and res.value["planned_by"] == G.LOCAL_ALONE


def test_run_task_hands_a_ready_plan_to_the_coordinator(monkeypatch):
    seen = {}

    class C:
        def __init__(self, agents, profiles, **kw):
            pass

        def run(self, request, plan=None):
            seen["args"] = (request, plan)
            return {"id": "t", "status": "completed", "steps": []}
    from jarvis.plugins.desktop import coordinator as coord
    monkeypatch.setattr(coord, "Coordinator", C)

    class H:
        async def _missions_ready(self):
            return True

        def _agents(self, loop, abort=None):
            return {}
    plan = decide.Plan([{"tool": "open_app", "app": "notepad"}], "ok")
    rec = asyncio.run(DesktopPlugin._run_task(H(), "open notepad", plan))
    assert rec["status"] == "completed" and seen["args"] == ("open notepad", plan)


def test_window_text_never_reaches_the_summary_and_stays_cleaned_when_screen_text_is_off(gateway, monkeypatch):
    monkeypatch.delenv("JARVIS_SCREEN_TEXT_TO_MODEL", raising=False)
    gateway(plan_json(("screen_read", "read the total in Excel", "a total is returned")))
    f = Fakes(call=lambda ref, args: (True, {"ok": True, "text": "Total 42 for CANARY-Priyanka"}, ""))
    monkeypatch.setattr(G, "_local_chat", lambda body, post, timeout=0: json.dumps({"app": "excel"}))
    monkeypatch.setattr(G, "_profiles", lambda: [])
    res = asyncio.run(DesktopPlugin.do_goal(_Host(f), goal="read the total in Excel"))
    blob = json.dumps(res.value)
    assert "Priyanka" not in blob and "Total 42" not in blob, "the window's words did not leave the PC through the result"
    assert res.value["status"] == "completed" and res.value["planned_by"] == G.ONLINE_PLAN, res.value
    assert res.value["steps"][0]["model"] == specialists.load()["screen_read"].alias
