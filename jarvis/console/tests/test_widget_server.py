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

    def test_everything_unreachable_does_not_crash(self):
        s = ws.build_status(fake_fetch({}), self.runs, self.dog)
        self.assertFalse(s["healthy"])
        self.assertFalse(any(s["servers"].values()))
        self.assertIsNone(s["task"])


class Http(unittest.TestCase):
    def setUp(self):
        self.calls = []
        table = {"http://127.0.0.1:48916/runs": lambda d: {"run_id": "r1"},
                 "http://127.0.0.1:48916/runs/r1": {"status": "succeeded"}}
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), ws.make_handler(PAGE, fake_fetch(table, self.calls)))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
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
                                       "args": {"task": "find the capital of Italy", "mode": "work"}})

    def test_bad_input_is_refused_without_calling_anything(self):
        code, body = self.req("POST", "/api/task", {"task": "   "})
        self.assertEqual(code, 400)
        self.assertEqual(self.calls, [])

    def test_foreign_host_and_foreign_origin_are_refused(self):
        self.assertEqual(self.req("GET", "/api/status", headers={"Host": "evil.example"})[0], 403)
        code, _ = self.req("POST", "/api/task", {"task": "x"}, headers={"Origin": "http://evil.example"})
        self.assertEqual(code, 403)
        self.assertEqual([c for c in self.calls if c[1]], [])

    def test_unknown_routes_are_404(self):
        self.assertEqual(self.req("GET", "/api/approve")[0], 404)
        self.assertEqual(self.req("POST", "/api/approve", {"id": "x"})[0], 404)           # the widget has no approve or kill-switch route


if __name__ == "__main__":
    unittest.main()
