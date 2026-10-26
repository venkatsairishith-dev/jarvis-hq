"""D54 (2026-10-26): "there is a problem statement on screen, solve it and type the solution".

read (local) -> reason (the ONLINE model, through the privacy gateway, redacted, no tools, no image, only that one window's text) -> type (local, then
save if asked). Fakes only: a fake privacy gateway on a real local socket (records every request it gets, has /health and /redact and the chat route),
fake hands (the window read, the editor pick, the coordinator). No model, no network beyond 127.0.0.1, no Windows."""
import asyncio
import json
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.plugins._shared import screen_text
from jarvis.plugins.desktop import DesktopPlugin, composite, core, decide, goal as G

PROBLEM = "Problem 7: Priyanka has 3 apples and buys 4 more. Write a program that prints the total number of apples."
OTHER_WINDOW = "CANARY-OTHER-WINDOW private notes of the owner"
SOLUTION = "apples = 3 + 4\nprint(apples)"
GOAL = "there is a problem statement on screen, solve it and type the solution"
HEALTH = {"ok": True, "ready": True, "degraded": [], "pii_model": True, "names": True, "public_egress": True}


class Gateway:
    """The privacy gateway as the solve path sees it: GET /health, POST /redact (hides names), POST /v1/chat/completions (the online chain)."""

    def __init__(self, reply=SOLUTION, health=None, redact="ok"):
        self.reply, self.health, self.redact, self.requests = reply, dict(HEALTH if health is None else health), redact, []
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
                if self.path == "/redact":
                    if gw.redact == "error":
                        return self._send(500, {"error": "boom"})
                    text = body["text"].replace("Priyanka", "[PERSON]")
                    return self._send(200, {"text": text, "names": gw.redact != "no-names", "deep": True, "ready": True})
                if isinstance(gw.reply, int):                                      # the online chain answers with an error code
                    return self._send(gw.reply, {"error": {"message": "no"}})
                self._send(200, {"choices": [{"message": {"role": "assistant", "content": gw.reply}}]}, {"X-Jarvis-Provider": "fakeonline"})
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def chat(self):
        return [r for r in self.requests if r["path"] != "/redact"]

    def redacts(self):
        return [r for r in self.requests if r["path"] == "/redact"]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def gateway(monkeypatch):
    made = []

    def make(**kw):
        g = Gateway(**kw)
        made.append(g)
        monkeypatch.setenv("JARVIS_PRIVACY_PORT", str(g.port))
        return g
    monkeypatch.delenv("JARVIS_SCREEN_TEXT_TO_MODEL", raising=False)            # the global default: OFF
    yield make
    for g in made:
        g.close()


class Hands:
    """Fake hands for the solve path: records every call; the window read, the editor pick and the coordinator are scripted."""

    def __init__(self, text=PROBLEM, record=None, editor=None, read_error=None):
        self.text, self.read_error = text, read_error
        self.reads, self.picks, self.pc_runs = [], [], []
        self.editor = editor or {"ok": True, "app": "Code"}
        self.record = record

    async def read_window(self, app, avoid):
        self.reads.append((app, avoid))
        if self.read_error:
            return {"ok": False, "error": self.read_error}
        return {"ok": True, "app": "chrome", "text": self.text, "source": "OCR"}

    async def pick_editor(self, named, read_app):
        self.picks.append((named, read_app))
        return self.editor

    async def run_pc(self, goal, plan):
        self.pc_runs.append((goal, plan))
        if self.record:
            return self.record
        steps = [{"n": i, "tool": s["tool"], "passed": True, "checked": f"{s['tool']} checked"} for i, s in enumerate(plan.steps, 1)]
        return {"id": "t1", "status": "completed", "steps": steps}

    async def call(self, ref, args):
        raise AssertionError("the solve path calls no helper entry")

    def hands(self, **kw):
        return G.Hands(run_pc=self.run_pc, call=self.call, read_window=self.read_window, pick_editor=self.pick_editor, **kw)


def solve(h, goal=GOAL, enabled=True, **kw):
    return asyncio.run(G.GoalRunner(h.hands(**kw)).run(goal, enabled))


# ---------------------------------------------------------------------------------------------------------------- how the goal is recognised
@pytest.mark.parametrize("goal,read_app,type_app", [
    (GOAL, None, None),
    ("solve the question on my screen and write the answer in notepad", None, "notepad"),
    ("solve the problem in Chrome and type the code in VS Code", "Chrome", "vs code"),
    ("there is a problem statement on the screen, solve it and put the solution into the editor", None, None)])
def test_the_solve_goal_is_recognised_and_names_are_read_from_it(goal, read_app, type_app):
    req = G.solve_request(goal)
    assert req is not None and req["read_app"] == read_app and req["type_app"] == type_app


@pytest.mark.parametrize("goal", ["solve 2+2 and type it", "open the problem on screen", "what is on the screen", "open notepad and type hello",
                                  "solve the problem on screen", "type the answer on screen"])
def test_other_goals_do_not_take_the_solve_path(goal):
    assert G.solve_request(goal) is None


# ---------------------------------------------------------------------------------------------------------------- the whole path
def test_read_then_reason_online_then_type_locally(gateway):
    gw = gateway()
    h = Hands()
    out = solve(h)
    assert out["status"] == "completed" and out["planned_by"] == G.SOLVED, out
    assert [s["executor"] for s in out["steps"]] == ["screen_read", "reason", "type_solution"] and all(s["status"] == "completed" for s in out["steps"])
    # step 3: the local side typed the model's solution (a new file in VS Code first, by the agent), nothing else
    (goal, plan), = h.pc_runs
    assert [s["tool"] for s in plan.steps] == ["type_in_editor"] and plan.steps[0]["text"] == SOLUTION and plan.steps[0]["app"] == "Code"
    assert h.picks == [(None, "chrome")] and h.reads == [(None, None)]


def test_the_online_request_has_no_tools_no_image_the_online_route_and_only_the_one_windows_redacted_text(gateway):
    gw = gateway()
    solve(Hands())
    (req,) = gw.chat()
    body = req["body"]
    assert req["path"] == "/v1/chat/completions" and req["headers"].get("X-Jarvis-Route") == "online"
    assert set(body) == {"model", "temperature", "max_tokens", "messages"}, "no tools, no tool_choice, no stream, no response_format"
    blob = json.dumps(body)
    assert "tools" not in body and "tool_choice" not in body and "image" not in blob.lower() and "data:image" not in blob
    assert all(isinstance(m["content"], str) for m in body["messages"]), "every part is plain text"
    user = body["messages"][1]["content"]
    assert "apples" in user and GOAL in user
    assert "Priyanka" not in blob and "[PERSON]" in user, "the gateway's redaction was applied to the window text before it was sent"
    assert OTHER_WINDOW not in json.dumps(gw.requests), "nothing but the one window that was read is sent"
    (red,) = gw.redacts()
    assert "Priyanka" in red["body"]["text"] and red["body"]["names"] is True       # the gateway got the raw text for redaction, then the model got the redacted one


def test_the_global_screen_text_switch_is_untouched_and_other_paths_still_withhold(gateway, monkeypatch):
    gateway()
    assert screen_text.flag_on() is False
    solve(Hands())
    assert screen_text.flag_on() is False and "JARVIS_SCREEN_TEXT_TO_MODEL" not in __import__("os").environ
    got = screen_text.release("some window text")                                   # every other path: still withheld with the switch off
    assert got.allowed is False and got.via == "withheld"


def test_what_the_step_results_say_holds_no_text_of_the_window_or_the_solution(gateway):
    gateway()
    out = solve(Hands())
    blob = json.dumps(out)
    for secret in ("apples", "Priyanka", "print(apples)", "Problem 7"):
        assert secret not in blob, secret
    s2 = out["steps"][1]
    assert s2["sent_chars"] > 0 and f"sent {s2['sent_chars']} characters" in s2["result"] and "redacted" in s2["result"]


# ---------------------------------------------------------------------------------------------------------------- redaction down: stop
@pytest.mark.parametrize("kw", [dict(health=dict(HEALTH, ready=False)), dict(health=dict(HEALTH, pii_model=False)), dict(health={"ok": False}),
                                dict(redact="error"), dict(redact="no-names")])
def test_when_the_gateway_or_its_redaction_is_down_nothing_is_sent_and_nothing_is_typed(gateway, kw):
    gw = gateway(**kw)
    h = Hands()
    out = solve(h)
    assert out["status"] == "failed" and gw.chat() == [], "the window's text never reached the online model"
    assert h.pc_runs == [] and h.picks == [], "nothing was typed"
    assert out["steps"][1]["status"] == "failed" and out["steps"][1]["sent_chars"] == 0 and out["steps"][2]["status"] == "not_run"
    assert "Nothing was sent online and nothing was typed" in out["steps"][1]["result"]
    assert "Priyanka" not in json.dumps(out)


def test_when_no_gateway_answers_at_all_the_text_is_not_sent_anywhere(monkeypatch):
    monkeypatch.delenv("JARVIS_SCREEN_TEXT_TO_MODEL", raising=False)
    monkeypatch.setenv("JARVIS_PRIVACY_PORT", "1")                                  # nothing listens there
    h = Hands()
    out = solve(h)
    assert out["status"] == "failed" and h.pc_runs == []


def test_a_private_key_in_the_window_is_never_sent(gateway):
    gw = gateway()
    h = Hands(text="-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\nsolve this")
    out = solve(h)
    assert out["status"] == "failed" and gw.requests == [] and h.pc_runs == []


def test_the_online_model_failing_leaves_the_text_untyped(gateway):
    gw = gateway(reply=502)
    h = Hands()
    out = solve(h)
    assert out["status"] == "failed" and h.pc_runs == [] and out["steps"][1]["status"] == "failed" and len(gw.chat()) == 1
    assert "The text was not typed" in out["steps"][1]["result"]


def test_cannot_solve_and_an_empty_answer_type_nothing(gateway):
    for reply in ("CANNOT_SOLVE", "   ", "```\n```"):
        gateway(reply=reply)
        h = Hands()
        out = solve(h)
        assert out["status"] == "failed" and h.pc_runs == [], reply


def test_a_fenced_answer_is_typed_without_the_fence(gateway):
    gateway(reply="```python\nprint(7)\n```")
    h = Hands()
    solve(h)
    assert h.pc_runs[0][1].steps[0]["text"] == "print(7)"


def test_an_unreadable_window_stops_before_anything_is_sent(gateway):
    gw = gateway()
    h = Hands(read_error="nothing readable was found in the chrome window")
    out = solve(h)
    assert out["status"] == "failed" and gw.requests == [] and out["steps"][0]["status"] == "failed" and out["steps"][1]["status"] == "not_run"


# ---------------------------------------------------------------------------------------------------------------- where it is typed and saved
def test_the_editor_the_owner_named_is_the_one_asked_for(gateway):
    gateway()
    h = Hands(editor={"ok": True, "app": "notepad"})
    solve(h, "solve the question on my screen and write the answer in notepad")
    assert h.picks == [("notepad", "chrome")]
    assert h.pc_runs[0][1].steps[0]["app"] == "notepad"


def test_no_editor_open_is_a_failed_step_and_the_solution_is_not_lost_silently(gateway):
    gateway()
    h = Hands(editor={"ok": False, "error": "no editor window is open (VS Code, Notepad ...); say which app to type the solution in"})
    out = solve(h)
    assert out["status"] == "failed" and out["steps"][2]["status"] == "failed" and "no editor window" in out["steps"][2]["result"] and h.pc_runs == []


def test_save_as_is_added_when_he_asked_for_a_save_with_the_digest_of_the_solution(gateway):
    gateway()
    h = Hands()
    out = solve(h, GOAL + ", save it as answer.py in Documents")
    plan = h.pc_runs[0][1]
    assert [s["tool"] for s in plan.steps] == ["type_in_editor", "save_as"]
    save = plan.steps[1]
    assert (save["name"], save["folder"], save["digest"]) == ("answer.py", "Documents", composite.text_digest(SOLUTION))
    assert out["status"] == "completed"


@pytest.mark.parametrize("tail", [", save it as a.txt in C:\\Windows", ", save it as run.bat in documents"])
def test_a_wrong_save_target_is_asked_about_before_anything_is_read_or_sent(gateway, tail):
    gw = gateway()
    h = Hands()
    out = solve(h, GOAL + tail)
    assert out["status"] == "needs_clarification" and h.reads == [] and gw.requests == [] and h.pc_runs == []


def test_a_gated_typing_step_waits_for_the_pin_and_says_so(gateway):
    gateway()
    rec = {"id": "t1", "status": "waiting_for_permission", "approval_id": "ap9", "steps": [{"n": 1, "tool": "type_in_editor", "passed": False}]}
    out = solve(Hands(record=rec))
    assert out["status"] == "waiting_for_permission" and out["approval_id"] == "ap9" and "needs your approval" in out["summary"]


def test_with_the_online_option_off_the_solve_path_is_not_taken(gateway, monkeypatch):
    gw = gateway()
    h = Hands()
    monkeypatch.setattr(G.decide, "decide", lambda goal, profiles, model=None, post=None: decide.Plan([{"tool": "open_app", "app": "notepad"}], "ok", model=model))
    monkeypatch.setattr(G, "_profiles", lambda: [])
    out = solve(h, enabled=False)
    assert out["planned_by"] == G.LOCAL_ALONE and gw.requests == [] and h.reads == []


def test_a_host_that_lends_no_reader_never_takes_the_path(gateway):
    gw = gateway(reply=json.dumps({"summary": "cannot", "steps": []}))
    h = Hands()
    runner = G.GoalRunner(G.Hands(run_pc=h.run_pc, call=h.call))
    out = asyncio.run(runner.run(GOAL, True))
    assert out["planned_by"] != G.SOLVED and h.reads == []


# ---------------------------------------------------------------------------------------------------------------- the plugin's local reading
class Win:
    """Just enough of DesktopPlugin for _goal_read_window / _goal_pick_editor."""
    SOLVE_OWN, EDITORS = DesktopPlugin.SOLVE_OWN, DesktopPlugin.EDITORS
    _wname, _wkey = staticmethod(DesktopPlugin._wname), staticmethod(DesktopPlugin._wkey)

    def __init__(self, windows, uia=None, ocr=None):
        self.windows, self.uia, self.ocr, self.asked = windows, uia or [], ocr or [], []

    async def _goal_windows(self):
        return self.windows

    def _pc(self):
        return object()

    async def _run(self, pc, body, timeout, what=""):
        self.asked.append(body["check"] + ":" + body["target"])
        return {"state": "done", "result": {"ok": True, "local_result": body["check"]}}


@pytest.fixture
def local_files(monkeypatch):
    def take(path):
        return None
    monkeypatch.setattr(core, "take_local", lambda p: {"window_text": W.uia, "window_ocr": {"observed_lines": W.ocr}}.get(p))
    return None


W = types.SimpleNamespace(uia=[], ocr=[])


def w(title, process, fg=False):
    return {"title": title, "process": process, "foreground": fg, "minimized": False}


def read(host, app=None, avoid=None):
    W.uia, W.ocr = host.uia, host.ocr
    return asyncio.run(DesktopPlugin._goal_read_window(host, app, avoid))


def test_the_front_window_that_is_not_jarvis_is_the_one_read(local_files):
    host = Win([w("JARVIS", "jarvis", True), w("Problem 7 - Chrome", "chrome"), w("notes - Notepad", "notepad")], ocr=["Problem 7: Priyanka has 3 apples"])
    got = read(host)
    assert got["ok"] and got["app"] == "chrome" and got["source"] == "OCR" and "Priyanka" in got["text"]
    assert host.asked == ["window_text:chrome", "window_ocr:chrome"], "the accessibility tree first, OCR when it gave too little; only that one process"


def test_enough_accessibility_text_means_no_ocr(local_files):
    host = Win([w("Doc", "winword", True)], uia=[{"title": "Doc", "texts": ["x" * 300]}])
    got = read(host)
    assert got["ok"] and got["source"] == "accessibility tree" and host.asked == ["window_text:winword"]


def test_the_editor_to_type_into_is_not_the_window_that_is_read(local_files):
    host = Win([w("hello.py - Visual Studio Code", "Code", True), w("Problem - Chrome", "chrome")], ocr=["a problem"])
    got = read(host, avoid="Code")
    assert got["app"] == "chrome"


def test_a_named_app_is_the_one_read_and_a_missing_one_is_an_error(local_files):
    host = Win([w("A", "notepad", True), w("Problem - Chrome", "chrome")], ocr=["p"])
    assert read(host, app="Chrome")["app"] == "chrome"
    assert read(Win([w("A", "notepad", True)]), app="Chrome")["ok"] is False


def test_only_jarvis_windows_open_means_nothing_to_read(local_files):
    got = read(Win([w("JARVIS", "jarvis", True)]))
    assert got["ok"] is False and "nothing to read" in got["error"]


def test_secrets_in_the_window_are_scrubbed_before_the_text_leaves_the_plugin(local_files):
    host = Win([w("Doc", "chrome", True)], ocr=["token ghp_" + "a" * 30, "the problem"])
    got = read(host)
    assert "ghp_" not in got["text"] and "the problem" in got["text"]


def test_the_front_editor_is_found_among_the_open_windows():
    host = Win([w("Problem - Chrome", "chrome", True), w("x - Notepad", "notepad"), w("a.py - Visual Studio Code", "Code")])
    assert asyncio.run(DesktopPlugin._goal_pick_editor(host, None, "chrome")) == {"ok": True, "app": "notepad"}
    assert asyncio.run(DesktopPlugin._goal_pick_editor(host, "vs code", "chrome")) == {"ok": True, "app": "vs code"}
    none = asyncio.run(DesktopPlugin._goal_pick_editor(Win([w("Problem - Chrome", "chrome", True)]), None, "chrome"))
    assert none["ok"] is False and "say which app" in none["error"]
