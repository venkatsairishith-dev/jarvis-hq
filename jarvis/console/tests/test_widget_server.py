"""Desktop widget backend: safety checks and status assembly (fake backends, nothing real is called)."""
import http.client
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("widget_server_t", os.path.join(HERE, "..", "..", "widget", "widget_server.py"))
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)
PAGE = os.path.join(HERE, "..", "..", "widget", "index.html")


def fake_fetch(table, calls=None):
    def fetch(url, timeout=2.0, data=None):
        if calls is not None:
            calls.append((url, data))
        v = table.get(url)
        return v(data) if callable(v) else v
    return fetch


ALL_UP = {u: {"status": "ok"} for u in ws.SERVERS.values()}


class Checks(unittest.TestCase):
    def test_only_loopback_hosts(self):
        for h in ("127.0.0.1:48940", "localhost:48940", "localhost", "[::1]:48940"):
            self.assertTrue(ws.host_ok(h), h)
        for h in ("evil.example", "evil.example:48940", "192.168.1.9:48940", "", None):
            self.assertFalse(ws.host_ok(h), h)

    def test_post_must_be_same_origin(self):
        self.assertTrue(ws.origin_ok(None, "127.0.0.1:48940"))
        self.assertTrue(ws.origin_ok("http://127.0.0.1:48940", "127.0.0.1:48940"))
        self.assertFalse(ws.origin_ok("http://evil.example", "127.0.0.1:48940"))
        self.assertFalse(ws.origin_ok("https://127.0.0.1:48940", "127.0.0.1:48940"))

    def test_task_validation(self):
        self.assertEqual(ws.validate_task({"task": "  open   my  notes ", "mode": "WORK"}), ({"task": "open my notes", "mode": "work"}, None))
        self.assertEqual(ws.validate_task({"task": "x"})[0]["mode"], "work")                       # default mode
        for bad in ({"task": "   "}, {"task": ""}, {}, {"task": "a" * 2001}, {"task": "ok", "mode": "root"}, "not a dict", None):
            self.assertIsNone(ws.validate_task(bad)[0])
            self.assertTrue(ws.validate_task(bad)[1])


class Status(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = os.path.join(self.tmp.name, "runs")
        os.makedirs(self.runs)
        self.dog = os.path.join(self.tmp.name, "watchdog_status.json")

    def tearDown(self):
        self.tmp.cleanup()

    def table(self, **over):
        t = dict(ALL_UP)
        t["http://127.0.0.1:48916/plugins"] = {"plugins": [{"id": p, "status": "running"} for p in ws.PLUGINS]}
        t["http://127.0.0.1:8799/api/health"] = {"kernel": {"reachable": True, "authorized": True, "killed": False}}
        t["http://127.0.0.1:8799/api/owner/state"] = {"busy": False, "approvals": []}
        t.update(over)
        return t

    def test_a_lone_pc_action_shows_as_pc_busy_and_stale_or_idle_does_not(self):
        """The wake word's task_running reads status["pc"]["busy"]: a single PC action from chat is no main-agent task."""
        act = os.path.join(self.tmp.name, "activity.json")
        status = lambda: ws.build_status(fake_fetch(self.table()), self.runs, self.dog, act)["pc"]
        self.assertEqual(status(), {"busy": False, "state": "", "detail": ""})                     # no file
        json.dump({"state": "working", "detail": "YouTube: lofi", "at": time.time()}, open(act, "w"))
        self.assertEqual(status(), {"busy": True, "state": "working", "detail": "YouTube: lofi"})
        json.dump({"state": "idle", "detail": "", "at": time.time()}, open(act, "w"))
        self.assertFalse(status()["busy"])
        json.dump({"state": "working", "detail": "old", "at": time.time() - 3600}, open(act, "w"))
        self.assertFalse(status()["busy"])                                                          # a stale entry is ignored

    def test_the_operation_registry_decides_pc_busy_not_a_file_a_long_step_never_refreshes(self):
        """A research step is silent for up to 16 minutes: the registry snapshot is refreshed by the plugin while any operation lives,
        so pc.busy (the wake listener's 'something to stop') stays true after the activity file has gone stale."""
        act = os.path.join(self.tmp.name, "activity.json")
        ops = os.path.join(self.tmp.name, "pc_operations.json")
        json.dump({"state": "researching", "detail": "old", "at": time.time() - 3600}, open(act, "w"))          # stale for an hour
        status = lambda: ws.build_status(fake_fetch(self.table()), self.runs, self.dog, act, ops)["pc"]
        self.assertFalse(status()["busy"])                                                          # no registry file, stale activity
        snap = {"at": time.time(), "busy": True, "operations": [
            {"id": "task-1", "kind": "task", "state": "running", "age_s": 700.0, "idle_s": 650.0, "holds_lock": True}]}
        json.dump(snap, open(ops, "w"))
        got = status()
        self.assertTrue(got["busy"])
        self.assertEqual((got["source"], got["operations"][0]["id"], got["state"]), ("registry", "task-1", "running"))
        snap["operations"][0]["stop_requested_s_ago"] = 3.0
        json.dump(snap, open(ops, "w"))
        self.assertEqual(status()["stopping"], 1)
        json.dump(dict(snap, busy=False, operations=[]), open(ops, "w"))                             # every operation is gone
        self.assertFalse(status()["busy"])
        json.dump(dict(snap, at=time.time() - 3600), open(ops, "w"))                                 # the plugin process died
        self.assertFalse(status()["busy"])

    def test_healthy_system(self):
        s = ws.build_status(fake_fetch(self.table()), self.runs, self.dog)
        self.assertTrue(s["healthy"])
        self.assertEqual(s["problems"], [])
        self.assertTrue(all(s["servers"].values()))
        self.assertEqual(s["jarvis"]["approvals_waiting"], 0)

    def test_a_down_server_and_a_stopped_plugin_are_reported(self):
        t = self.table(**{ws.SERVERS["speech"]: None,
                          "http://127.0.0.1:48916/plugins": {"plugins": [{"id": "doc_tools", "status": "stopped"},
                                                                         {"id": "main_agent", "status": "running"}]}})
        s = ws.build_status(fake_fetch(t), self.runs, self.dog)
        self.assertFalse(s["healthy"])
        self.assertIn("speech", s["problems"])
        self.assertIn("doc_tools", s["problems"])

    def test_waiting_approvals_are_counted_but_only_counted(self):
        t = self.table(**{"http://127.0.0.1:8799/api/owner/state": {"busy": True, "approvals": [{"id": "a", "action": {"secret": "x"}}, {"id": "b"}]}})
        s = ws.build_status(fake_fetch(t), self.runs, self.dog)
        self.assertEqual(s["jarvis"]["approvals_waiting"], 2)
        self.assertNotIn("secret", json.dumps(s))                                                 # no action details leave the console

    def test_the_kill_switch_makes_it_unhealthy(self):
        t = self.table(**{"http://127.0.0.1:8799/api/health": {"kernel": {"reachable": True, "authorized": True, "killed": True}}})
        s = ws.build_status(fake_fetch(t), self.runs, self.dog)
        self.assertTrue(s["jarvis"]["killed"])
        self.assertFalse(s["healthy"])

    def test_the_newest_task_is_summarised(self):
        old = {"state": "done", "plan": {"summary": "old", "steps": []}, "results": {}}
        new = {"state": "running", "mode": "work", "plan": {"summary": "Save Rome", "steps": [{"id": "s1", "agent": "think"}, {"id": "s2", "agent": "doc_tools:write_document"}]},
               "results": {"s1": {"outcome": "success"}}}
        json.dump(old, open(os.path.join(self.runs, "a.json"), "w"))
        time.sleep(0.05)
        json.dump(new, open(os.path.join(self.runs, "b.json"), "w"))
        s = ws.build_status(fake_fetch(self.table()), self.runs, self.dog)
        self.assertEqual(s["task"]["summary"], "Save Rome")
        self.assertEqual([x["outcome"] for x in s["task"]["steps"]], ["success", "running"])

    def test_watchdog_verdict_is_passed_through(self):
        json.dump({"healthy": False, "gave_up_on": ["stack:speech"], "checked": "2026-10-15 20:00:00"}, open(self.dog, "w"))
        s = ws.build_status(fake_fetch(self.table()), self.runs, self.dog)
        self.assertEqual(s["watchdog"]["gave_up_on"], ["stack:speech"])

    def test_a_stopped_desktop_plugin_turns_the_icon_to_error(self):
        """PRO-D6: build_status listed it, but the icon's decision ignored plugins, so the PC monitor could stop unseen."""
        import neko_state as ns
        t = self.table(**{"http://127.0.0.1:48916/plugins": {"plugins": [{"id": p, "status": "stopped" if p == "desktop" else "running"}
                                                                        for p in ws.PLUGINS]}})
        s = ws.build_status(fake_fetch(t), self.runs, self.dog)
        self.assertIn("desktop", s["problems"])
        out = ns.decide(s, [], None, None, None)
        self.assertEqual(out["state"], "error")
        self.assertIn("desktop plugin", out["detail"])
        self.assertEqual(ns.decide(ws.build_status(fake_fetch(self.table()), self.runs, self.dog), [], None, None, None)["state"], "sleeping")

    def test_everything_unreachable_does_not_crash(self):
        s = ws.build_status(fake_fetch({}), self.runs, self.dog)
        self.assertFalse(s["healthy"])
        self.assertFalse(any(s["servers"].values()))
        self.assertIsNone(s["task"])


def export(data):
    """What the plugin runtime's GET /runs/{id}/export holds for a finished entry (runs/manager.py): its Ok value in json.data."""
    return {"items": [{"export_item_id": "e1", "label": "trigger_response", "type": "json",
                       "json": {"success": True, "code": 0, "data": data, "error": None}, "metadata": {"kind": "trigger_response"}}]}


class TruthfulAnswers(unittest.TestCase):
    """VOI-D2/D3/D4: the widget says what main_agent really answered, and sees a task that is still being planned."""

    def setUp(self):
        ws.time.sleep, self._sleep = (lambda s: None), ws.time.sleep
        self.runs = tempfile.mkdtemp()

    def tearDown(self):
        ws.time.sleep = self._sleep

    def stop_with(self, data, status="succeeded"):
        table = {"http://127.0.0.1:48916/runs": lambda d: {"run_id": "s1"},
                 "http://127.0.0.1:48916/runs/s1": {"status": status, "result_refs": ["e1"]},
                 "http://127.0.0.1:48916/runs/s1/export": export(data) if data is not None else {"items": []}}
        return ws.do_action({"action": "stop"}, fake_fetch(table))

    def test_stop_that_stopped_nothing_is_not_reported_as_success(self):
        out = self.stop_with({"stopped": False, "message": "Nothing is running."})
        self.assertEqual((out["ok"], out["stopped"], out["error"]), (False, False, "nothing to stop"))

    def test_stop_that_stopped_the_task_is_reported_as_success(self):
        out = self.stop_with({"stopped": True, "run_id": "r1", "message": "Stopped while it was still being planned; no step will start."})
        self.assertEqual((out["ok"], out["stopped"]), (True, True))
        self.assertIn("planned", out["message"])

    def test_a_stop_that_was_requested_but_whose_helpers_are_still_running_is_not_ok(self):
        out = self.stop_with({"requested": True, "stopped": False, "still_running": [{"id": "task-1"}, {"id": "m-2"}],
                              "message": "Stop requested, 2 still running"})
        self.assertEqual((out["ok"], out["stopped"], out["requested"], out["still_running"]), (False, False, True, 2))
        self.assertEqual(out["error"], "stop requested, 2 still running")
        self.assertEqual(self.stop_with({"requested": True, "stopped": False, "still_running": []})["error"],
                         "stop requested, not yet confirmed ended")

    def test_a_stop_whose_helpers_all_ended_is_ok(self):
        out = self.stop_with({"requested": True, "stopped": True, "still_running": [], "message": "Stopped"})
        self.assertEqual((out["ok"], out["stopped"], out["still_running"]), (True, True, 0))

    def test_a_stop_whose_answer_cannot_be_read_is_not_claimed(self):
        out = self.stop_with(None)
        self.assertFalse(out["ok"])
        self.assertIsNone(out["stopped"])
        self.assertFalse(self.stop_with({"stopped": True}, status="canceled")["ok"])     # the runtime's spelling

    def test_a_task_still_planning_is_reported_as_planning_not_started(self):
        json.dump({"run_id": "r42", "state": "planning"}, open(os.path.join(self.runs, ws.ACTIVE_MARKER), "w"))
        table = {"http://127.0.0.1:48916/runs": lambda d: {"run_id": "p1"}, "http://127.0.0.1:48916/runs/p1": {"status": "running"}}
        out = ws.start_task("open notepad", "work", fake_fetch(table), runs_dir=self.runs)
        self.assertEqual((out["ok"], out["state"], out["run_id"]), (True, "planning", "r42"))

    def test_the_entrys_own_answer_decides_started_or_stopped(self):
        table = {"http://127.0.0.1:48916/runs": lambda d: {"run_id": "p1"}, "http://127.0.0.1:48916/runs/p1": {"status": "succeeded"},
                 "http://127.0.0.1:48916/runs/p1/export": export({"run_id": "r1", "state": "running", "message": "On it: Think"})}
        out = ws.start_task("x", "work", fake_fetch(table), runs_dir=self.runs)
        self.assertEqual((out["ok"], out["state"], out["run_id"]), (True, "running", "r1"))
        table["http://127.0.0.1:48916/runs/p1/export"] = export({"run_id": "r1", "state": "cancelled", "message": "Stopped as you asked"})
        out = ws.start_task("x", "work", fake_fetch(table), runs_dir=self.runs)
        self.assertEqual((out["ok"], out["state"]), (False, "cancelled"))
        table["http://127.0.0.1:48916/runs/p1"] = {"status": "canceled", "error": {"message": "Execution cancelled"}}
        self.assertEqual(ws.start_task("x", "work", fake_fetch(table), runs_dir=self.runs)["ok"], False)

    def test_a_task_being_planned_is_the_newest_task(self):
        now = time.time()                                    # explicit times: time.sleep is a no-op in this class
        with open(os.path.join(self.runs, "old.json"), "w") as f:
            json.dump({"state": "done", "plan": {"summary": "old"}, "results": {}}, f)
        os.utime(os.path.join(self.runs, "old.json"), (now - 20, now - 20))
        with open(os.path.join(self.runs, ws.ACTIVE_MARKER), "w") as f:
            json.dump({"run_id": "r9", "state": "planning"}, f)
        os.utime(os.path.join(self.runs, ws.ACTIVE_MARKER), (now - 10, now - 10))
        t = ws.newest_run(self.runs)
        self.assertEqual((t["state"], t["id"], t["steps"]), ("planning", "r9", []))
        with open(os.path.join(self.runs, "r9.json"), "w") as f:
            json.dump({"state": "running", "plan": {"summary": "new", "steps": []}, "results": {}}, f)
        self.assertEqual(ws.newest_run(self.runs)["state"], "running")             # once started, the run file takes over

    def test_a_plan_that_failed_shows_its_reason(self):
        json.dump({"run_id": "r9", "state": "failed", "error": "The planner did not return a plan."},
                  open(os.path.join(self.runs, ws.ACTIVE_MARKER), "w"))
        t = ws.newest_run(self.runs)
        self.assertEqual((t["state"], t["error"]), ("failed", "The planner did not return a plan."))


class Http(unittest.TestCase):
    def setUp(self):
        self.calls = []
        table = {"http://127.0.0.1:48916/runs": lambda d: {"run_id": "r1"},
                 "http://127.0.0.1:48916/runs/r1": {"status": "succeeded"}}
        handler = ws.make_handler(PAGE, fake_fetch(table, self.calls))
        self.secret = handler.control_secret
        self.gate = handler.gate
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, method, path, body=None, headers=None, auth=True):
        """`auth`: send this launch's control secret (audit M15: every POST needs it); auth=False sends none."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if auth:                                      # the master token, as the wake listener sends it (reads need it too since 2026-10-19)
            h.setdefault("X-Jarvis-Control", self.secret)
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = (r.status, r.read())
        c.close()
        return out

    def test_page_and_status_are_served(self):
        code, body = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"JARVIS Widget", body)
        self.assertEqual(self.req("GET", "/api/status")[0], 200)

    def test_a_valid_task_starts_one_main_agent_run(self):
        code, body = self.req("POST", "/api/task", {"task": "find the capital of Italy", "mode": "work"})
        self.assertEqual((code, json.loads(body)["ok"]), (200, True))
        posts = [c for c in self.calls if c[1]]
        self.assertEqual(posts[0][1], {"plugin_id": "main_agent", "entry_id": "run_task",
                                       "args": {"task": "find the capital of Italy", "mode": "work", "source": "widget"}})

    def test_bad_input_is_refused_without_calling_anything(self):
        code, body = self.req("POST", "/api/task", {"task": "   "})
        self.assertEqual(code, 400)
        self.assertEqual(self.calls, [])

    def test_foreign_host_and_foreign_origin_are_refused(self):
        self.assertEqual(self.req("GET", "/api/status", headers={"Host": "evil.example"})[0], 403)
        code, _ = self.req("POST", "/api/task", {"task": "x"}, headers={"Origin": "http://evil.example"})
        self.assertEqual(code, 403)                          # even WITH the secret, a foreign Origin is refused
        self.assertEqual([c for c in self.calls if c[1]], [])

    def test_bare_page_loads_get_a_login_page_and_no_secret(self):
        """Re-review M3: the old test pinned "GET / carries the secret". Now nothing hands a credential to a bare client."""
        for path in ("/", "/neko", "/index.html"):
            code, body = self.req("GET", path, auth=False)
            self.assertEqual(code, 401, path)
            self.assertNotIn(self.secret.encode(), body)
            self.assertNotIn(b"jarvis-widget-secret", body)
            self.assertIn(b"/auth/login.js", body)
        self.assertEqual(self.req("GET", "/auth/login.js", auth=False)[0], 200)

    def test_a_signed_in_page_carries_the_session_token_not_the_master_token(self):
        gate = self.gate
        sid = gate.redeem(gate.issue_code(), f"127.0.0.1:{self.port}")
        cookie = {"Cookie": f"jarvis_widget_session={sid}"}
        for path in ("/", "/neko"):
            code, body = self.req("GET", path, headers=cookie, auth=False)
            self.assertEqual(code, 200)
            self.assertIn(b'<meta name="jarvis-widget-secret" content="' + gate.csrf(sid).encode() + b'">', body)
            self.assertNotIn(self.secret.encode(), body)
        for path in ("/api/status", "/api/neko", "/health"):
            code, body = self.req("GET", path)
            self.assertNotIn(self.secret.encode(), body, path)

    def test_unknown_routes_are_404(self):
        self.assertEqual(self.req("GET", "/api/approve")[0], 404)
        self.assertEqual(self.req("POST", "/api/approve", {"id": "x"})[0], 404)           # the widget has no approve or kill-switch route


class Deliverable(unittest.TestCase):
    """PRO-D5: the newest deliverable's QA result reaches the icon as it is, with what failed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, qa):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(d)
        json.dump({"file": os.path.join(d, name + ".pptx"), "kind": "pptx", "qa": qa}, open(os.path.join(d, "qa.json"), "w"))
        return d

    def test_failed_checks_and_an_unclear_result_are_passed_on_never_as_a_pass(self):
        self.write("deck", {"passed": False, "checks": {"opens": True, "fonts": False, "overflow": False}})
        d = ws.newest_deliverable(self.tmp.name)
        self.assertIs(d["passed"], False)
        self.assertEqual(d["failed_checks"], ["fonts", "overflow"])
        import neko_state as ns
        out = ns.decide({"servers": {}, "jarvis": {"reachable": True}, "watchdog": {}, "task": None}, [], None, None, d)
        self.assertEqual(out["state"], "attention")
        self.assertNotIn("Ready", out["detail"])
        d2 = self.write("deck2", {"passed": "yes", "checks": {}})            # not a boolean: unknown, not a pass
        later = time.time() + 5
        os.utime(os.path.join(d2, "qa.json"), (later, later))
        self.assertIsNone(ws.newest_deliverable(self.tmp.name)["passed"])


class ControlSurface(Http):
    """Audit M15: every state-changing route refuses a request without this launch's secret, with a forged Origin or with a
    foreign Host, and NOTHING happens (no plugin run, no feedback file). A request with the secret works."""
    ROUTES = (("/api/task", {"task": "CANARY-7f3a-SYNTH open notepad", "mode": "work"}),
              ("/api/action", {"action": "stop"}),
              ("/api/feedback", {"rating": "up", "run_id": "r1"}))

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = ws.FEEDBACK_FILE
        ws.FEEDBACK_FILE = os.path.join(self.tmp.name, "feedback.jsonl")

    def tearDown(self):
        ws.FEEDBACK_FILE = self.saved
        self.tmp.cleanup()
        super().tearDown()

    def nothing_happened(self):
        self.assertEqual([c for c in self.calls if c[1]], [])
        self.assertFalse(os.path.exists(ws.FEEDBACK_FILE))

    def refused(self, headers=None, auth=False):
        for route, body in self.ROUTES:
            code, raw = self.req("POST", route, body, headers=headers, auth=auth)
            self.assertEqual(code, 403, (route, headers))
            self.assertNotIn(self.secret.encode(), raw)
        self.nothing_happened()

    def test_no_secret_is_refused_on_every_state_changing_route(self):
        self.refused()

    def test_wrong_secret_is_refused(self):
        self.refused({"X-Jarvis-Control": self.secret[:-1] + ("A" if self.secret[-1] != "A" else "B")})
        self.refused({"X-Jarvis-Control": ""})
        self.refused({"X-Jarvis-Control": "1"})                      # the console's old constant header is no secret

    def test_forged_origin_is_refused_even_with_the_secret(self):
        for origin in ("http://evil.example", "null", "http://127.0.0.1:9999", "https://127.0.0.1:%d" % self.port):
            self.refused({"X-Jarvis-Control": self.secret, "Origin": origin})

    def test_foreign_or_wrong_port_host_is_refused_even_with_the_secret(self):
        for host in ("evil.example", "evil.example:%d" % self.port, "127.0.0.1", "127.0.0.1:1", "localhost:80", ""):
            self.refused({"X-Jarvis-Control": self.secret, "Host": host})

    def test_the_secret_with_the_pages_own_origin_and_exact_host_works(self):
        ok = {"Origin": "http://127.0.0.1:%d" % self.port, "Host": "localhost:%d" % self.port}
        code, body = self.req("POST", "/api/task", {"task": "CANARY-7f3a-SYNTH find the capital of Italy", "mode": "work"}, headers=ok)
        self.assertEqual((code, json.loads(body)["ok"]), (200, True))
        code, body = self.req("POST", "/api/feedback", {"rating": "down"}, headers=ok)
        self.assertEqual((code, json.loads(body)["saved"]), (200, True))
        self.assertTrue(os.path.exists(ws.FEEDBACK_FILE))
        self.assertEqual(self.req("POST", "/api/action", {"action": "stop"})[0], 200)       # no Origin at all (a program) is fine

    def test_sensitive_reads_need_a_credential_and_only_health_is_open(self):
        self.assertEqual(self.req("GET", "/health", auth=False)[0], 200)
        for path in ("/api/status", "/api/neko", "/api/events", "/open-console"):
            code, body = self.req("GET", path, auth=False)
            self.assertEqual(code, 401, path)
            self.assertEqual(set(json.loads(body)), {"error", "login"}, path)
        for path in ("/", "/neko", "/api/status", "/api/neko"):
            self.assertEqual(self.req("GET", path)[0], 200, path)                            # the master token works
        self.assertEqual(self.req("GET", "/api/status", headers={"X-Jarvis-Control": "1"}, auth=False)[0], 401)
        self.assertEqual(self.req("GET", "/api/status", headers={"Host": "evil.example"})[0], 403)    # still only on loopback
        self.assertEqual(self.req("GET", "/api/status", headers={"Host": f"127.0.0.1:{self.port + 1}"})[0], 401)   # exact port

    def test_one_time_code_session_cookie_and_the_double_submit_token(self):
        code, body = self.req("POST", "/auth/code", {})                                        # master token asks for a code
        self.assertEqual(code, 200)
        one_time = json.loads(body)["code"]
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/auth/session", body=json.dumps({"code": one_time}), headers={"Content-Type": "application/json"})
        r = c.getresponse(); r.read(); c.close()
        self.assertEqual(r.status, 200)
        set_cookie = r.getheader("Set-Cookie")
        self.assertTrue("HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie and "Domain" not in set_cookie)
        cookie = {"Cookie": set_cookie.split(";")[0]}
        sid = set_cookie.split(";")[0].split("=", 1)[1]
        self.assertEqual(self.req("GET", "/api/status", headers=cookie, auth=False)[0], 200)
        # replay of the used code, and a cookie presented under another Host string
        self.assertEqual(self.req("POST", "/auth/session", {"code": one_time}, auth=False)[0], 401)
        self.assertEqual(self.req("GET", "/api/status", headers={**cookie, "Host": f"localhost:{self.port}"}, auth=False)[0], 401)
        # writes need the session's double-submit token
        task = {"task": "CANARY-7f3a-SYNTH find the capital of Italy", "mode": "work"}
        self.assertEqual(self.req("POST", "/api/task", task, headers=cookie, auth=False)[0], 403)
        self.assertEqual([c_ for c_ in self.calls if c_[1]], [])
        ok = self.req("POST", "/api/task", task, headers={**cookie, "X-Jarvis-Control": self.gate.csrf(sid)}, auth=False)
        self.assertEqual(ok[0], 200)
        # only the master token mints codes
        self.assertEqual(self.req("POST", "/auth/code", {}, headers={**cookie, "X-Jarvis-Control": self.gate.csrf(sid)}, auth=False)[0], 401)

    def test_open_console_hands_a_signed_in_browser_to_the_console_with_a_one_time_code(self):
        seen = []
        saved = ws.CONSOLE_CODE_URL
        ws.CONSOLE_CODE_URL = "http://127.0.0.1:8799/auth/code"
        table = {ws.CONSOLE_CODE_URL: lambda d: seen.append(d) or {"code": "ONE-TIME-CODE-123"}}
        srv = ThreadingHTTPServer(("127.0.0.1", 0), ws.make_handler(PAGE, fake_fetch(table, [])))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            handler_secret = srv.RequestHandlerClass.control_secret
            def get(headers):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
                c.request("GET", "/open-console", headers=headers)
                r = c.getresponse(); r.read(); c.close()
                return r.status, r.getheader("Location")
            auth = {"X-Jarvis-Control": handler_secret}
            self.assertEqual(get(auth)[0], 403)                                             # not a same-origin navigation: refused
            self.assertEqual(get({**auth, "Sec-Fetch-Site": "cross-site"})[0], 403)
            self.assertEqual(get({"Sec-Fetch-Site": "same-origin"})[0], 401)                  # not signed in
            self.assertEqual(seen, [])                                                       # no code was requested for any of those
            status, location = get({**auth, "Sec-Fetch-Site": "same-origin"})
            self.assertEqual((status, location), (302, "http://127.0.0.1:8799/#code=ONE-TIME-CODE-123"))
        finally:
            ws.CONSOLE_CODE_URL = saved
            srv.shutdown(); srv.server_close()


if __name__ == "__main__":
    unittest.main()
