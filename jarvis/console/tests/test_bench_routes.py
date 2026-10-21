"""Bench screen routes: real result files and the real decide/coordinator/policy code; benchmark jobs run a tiny fake runner
(dependency injection), never the full benchmark."""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_console import bench as benchmod
from jarvis_console.config import Config
from jarvis_console.server import make_server

HQ = os.path.abspath(os.path.join(HERE, "..", ".."))
BENCH_DIR = os.path.join(HQ, "evals", "neko95", "bench")
REAL_RESULTS = os.path.join(HQ, "evals", "neko95", "results")
FAKE = os.path.join(HERE, "fixtures", "fake_bench_runner.py")
NEWEST_FULL, OLDER_FULL = "bench-20261017-031703.json", "bench-20261016-181824.json"
FIXTURES = [NEWEST_FULL, OLDER_FULL, "score-20261016-150635-baseline.json", "score-20261016-154951-server-rescore.json",
            "score-20261016-161950-interim.json", "scorecard-v2-20261017-031704.json", "context-holdout2-20261017-045353.json",
            "live-dahl-20261016-163134.json"]


class FakeVoice:
    def status(self):
        return {"engine": "fake"}

    def unload_if_idle(self):
        return False


class Base(unittest.TestCase):
    cases_per_step, delay = 3, 0.01

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.results = os.path.join(self.tmp.name, "results")
        os.makedirs(self.results)
        for n in FIXTURES:
            shutil.copy(os.path.join(REAL_RESULTS, n), self.results)
        self.model_dir = os.path.join(self.tmp.name, "models")
        os.makedirs(self.model_dir)
        self.argvs = []

        def command(kind, spec):
            self.argvs.append((kind, spec))
            label = spec.get("area") or ",".join(spec.get("models") or []) or "all"
            return [sys.executable, "-u", FAKE, label, str(self.cases_per_step), str(self.delay)]
        cfg = Config(port=0, kernel_url="http://127.0.0.1:9", token_file=os.path.join(self.tmp.name, "none"), outputs_dir=self.tmp.name,
                     ui_dir=os.path.join(self.tmp.name, "ui"), hub_url="http://127.0.0.1:9", local_llm_url="http://127.0.0.1:9",
                     intent_llm_url="http://127.0.0.1:9", bench_dir=BENCH_DIR, bench_results_dir=self.results, model_dir=self.model_dir)
        self.svc = benchmod.Bench(cfg, command=command)
        self.srv = make_server(cfg, kernel=object(), voice=FakeVoice(), bench=self.svc)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        j = self.svc.active
        if j and j.view()["state"] == "running":
            self.svc.cancel(j.id)
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": f"127.0.0.1:{self.port}"}
        h["X-Jarvis-Console"] = self.srv.console_secret       # the master token, as a first-party program sends it (reads need it too since 2026-10-19)
        h.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw or b"{}")
        except ValueError:
            return r.status, {"raw": raw[:200]}

    def wait_job(self, jid, pred, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            _, j = self.call("GET", f"/api/bench/jobs/{jid}")
            if pred(j):
                return j
            time.sleep(0.05)
        self.fail(f"job {jid} never reached the expected state: {j}")


class SummaryTest(Base):
    def test_summary_shape_with_real_result_files(self):
        code, s = self.call("GET", "/api/bench/summary")
        self.assertEqual(code, 200, s)
        self.assertEqual(s["bench_version"], 2)
        self.assertEqual(s["latest"]["file"], NEWEST_FULL)
        self.assertEqual(s["latest"]["counts"], {"pass": 548, "fail": 0, "error": 0, "deferred": 24})
        self.assertEqual(s["latest"]["scorecard_file"], "scorecard-v2-20261017-031704.json")
        intent = s["dimensions"]["intent"]
        for k in ("score", "label", "executed", "passed", "cases", "deferred", "case_ids", "evidence", "why"):
            self.assertIn(k, intent)
        self.assertEqual(len(intent["case_ids"]), intent["cases"])
        self.assertEqual(sum(intent["evidence"].values()), intent["cases"])
        self.assertGreater(intent["evidence"]["mocked"], 0)            # the model boundary is a stand-in in some cases: said so
        self.assertEqual(s["dimensions"]["desktop"]["why"], ["critical cases deferred"])
        self.assertLess(s["dimensions"]["desktop"]["score"], 10)
        self.assertEqual(s["official_v1"]["label"], "official")
        self.assertEqual(s["official_v1"]["overall_mean"], 7.61)
        self.assertEqual(s["official_v1"]["file"], "score-20261016-154951-server-rescore.json")
        self.assertEqual([t["file"] for t in s["trend"]], [OLDER_FULL, NEWEST_FULL])
        self.assertEqual(len(s["deferred"]), 24)
        self.assertTrue(all(d["platform"] != "any" for d in s["deferred"]))
        self.assertEqual(s["failed"], [])
        self.assertEqual(s["live"][0]["evidence"], "live")
        self.assertEqual(s["context"][0]["set"], "holdout2")
        self.assertIsNone(s["models"])                                   # no models-*.json in the fixtures: nothing invented
        self.assertEqual(len(s["groups"]), 15)

    def test_cases_for_a_dimension_and_security_list(self):
        code, d = self.call("GET", "/api/bench/cases?dim=security")
        self.assertEqual(code, 200)
        self.assertTrue(d["cases"] and all("status" in c and "evidence" in c and "observed" in c for c in d["cases"]))
        code, d = self.call("GET", "/api/bench/security")
        self.assertEqual(code, 200)
        areas = {c["area"] for c in d["cases"]}
        self.assertTrue({"K", "N"} <= areas, areas)
        self.assertTrue(all(c["area"] in ("K", "N") or "security" in c["dims"] for c in d["cases"]))

    def test_runs_and_compare(self):
        code, d = self.call("GET", "/api/bench/runs")
        self.assertEqual(code, 200)
        self.assertEqual([r["file"] for r in d["runs"]], [NEWEST_FULL, OLDER_FULL])
        code, d = self.call("GET", f"/api/bench/runs/{OLDER_FULL}/compare/{NEWEST_FULL}")
        self.assertEqual(code, 200, d)
        for k in ("fixed", "broken", "still_failing", "unchanged"):
            self.assertIn(k, d)
        # between these two real runs only H-027 changed: deferred (needed the model) -> pass
        self.assertEqual([(x["id"], x["a"], x["b"]) for x in d["other_changes"]], [("H-027", "deferred", "pass")])
        self.assertEqual((d["fixed"], d["broken"], d["still_failing"], d["unchanged"]), ([], [], [], 571))
        code, d = self.call("GET", "/api/bench/runs/..%2f..%2fsecret.json/compare/" + NEWEST_FULL)
        self.assertEqual(code, 400)


class JobTest(Base):
    def test_job_runs_to_the_end_with_progress(self):
        code, j = self.call("POST", "/api/bench/run", {"groups": ["security", "memory"]})
        self.assertEqual(code, 202, j)
        self.assertEqual([s["area"] for _, s in self.argvs], ["F", "K"])
        j = self.wait_job(j["id"], lambda x: x["state"] != "running")
        self.assertEqual(j["state"], "done")
        self.assertEqual(j["done"], 6)
        self.assertEqual(j["counts"]["pass"], 6)
        self.assertTrue(any("fake runner" in line for line in j["log_tail"]))

    def test_every_group_is_one_full_run(self):
        code, j = self.call("POST", "/api/bench/run", {"groups": list(benchmod.GROUPS)})
        self.assertEqual(code, 202, j)
        self.assertEqual(self.argvs, [("bench", {"area": None})])
        n = 0                                       # every case in the repo's case files (572 on 2026-10-16, 860 a day later)
        for f in os.listdir(os.path.join(BENCH_DIR, "cases")):
            with open(os.path.join(BENCH_DIR, "cases", f), encoding="utf-8") as fh:
                n += len(json.load(fh))
        self.assertEqual(j["total"], n)
        self.assertGreater(n, 0)

    def test_unknown_group_refused(self):
        for body in ({"groups": ["security", "rm -rf /"]}, {"groups": []}, {"groups": "security"}, {}):
            code, d = self.call("POST", "/api/bench/run", body)
            self.assertEqual(code, 400, (body, d))
        self.assertEqual(self.argvs, [])

    def test_second_concurrent_job_refused_and_cancel_ends_it(self):
        self.cases_per_step, self.delay = 50, 0.2
        code, j = self.call("POST", "/api/bench/run", {"groups": ["security"]})
        self.assertEqual(code, 202)
        self.wait_job(j["id"], lambda x: x["done"] >= 1)
        code, d = self.call("POST", "/api/bench/run", {"groups": ["memory"]})
        self.assertEqual(code, 409, d)
        self.assertIn(j["id"], d["error"])
        proc = self.svc.jobs[j["id"]].proc
        code, c = self.call("POST", f"/api/bench/jobs/{j['id']}/cancel")
        self.assertEqual(code, 200)
        self.assertEqual(c["state"], "cancelled")
        self.assertLess(c["done"], 50)
        self.assertIsNotNone(proc.poll())                                # the process (group) is gone
        code, j2 = self.call("POST", "/api/bench/run", {"groups": ["memory"]})   # and the next job may start
        self.assertEqual(code, 202, j2)

    def test_rerun_failed_uses_only_ids_from_the_run(self):
        with open(os.path.join(self.results, NEWEST_FULL), encoding="utf-8") as fh:
            run = json.load(fh)
        for r in run["cases"]:
            if r["id"] in ("K-001", "A-002"):
                r["status"] = "fail"
        with open(os.path.join(self.results, "bench-20261017-040000.json"), "w", encoding="utf-8") as fh:
            json.dump(run, fh)
        code, j = self.call("POST", "/api/bench/rerun-failed", {"run": "bench-20261017-040000.json"})
        self.assertEqual(code, 202, j)
        self.assertEqual(self.argvs, [("bench", {"area": "A", "ids": ["A-002"]}), ("bench", {"area": "K", "ids": ["K-001"]})])
        self.wait_job(j["id"], lambda x: x["state"] != "running")
        code, d = self.call("POST", "/api/bench/rerun-failed", {"run": NEWEST_FULL})
        self.assertEqual(code, 400)


class ChallengeTest(Base):
    def test_challenge_saves_an_untrusted_record_and_delete_removes_it(self):
        code, r = self.call("POST", "/api/bench/challenge", {"text": "open notepad please"})
        self.assertEqual(code, 200, r)
        self.assertIs(r["trusted"], False)
        self.assertEqual(r["model"]["note"], "model not available, rules only")
        self.assertEqual(r["plan"]["status"], "ok")
        self.assertEqual(r["steps"][0]["tool"], "open_app")
        self.assertEqual(r["steps"][0]["risk"]["level"], "YELLOW_AUTO")
        self.assertEqual([g["key"] for g in r["grades"]], ["understood", "right_tools", "constraints", "verification", "honest_completion", "security"])
        self.assertTrue(all(g["evidence"] for g in r["grades"]))
        self.assertEqual(r["coordinator"]["status"], "completed")
        self.assertIn("SIMULATED", r["grades"][4]["evidence"])
        path = os.path.join(self.results, "challenges", r["id"] + ".json")
        with open(path, encoding="utf-8") as fh:
            self.assertIs(json.load(fh)["trusted"], False)
        code, lst = self.call("GET", "/api/bench/challenges")
        self.assertEqual([c["id"] for c in lst["challenges"]], [r["id"]])
        self.assertFalse(os.path.exists(os.path.join(BENCH_DIR, "cases", r["id"] + ".json")))
        code, d = self.call("DELETE", f"/api/bench/challenge/{r['id']}")
        self.assertEqual(code, 200, d)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.call("DELETE", f"/api/bench/challenge/{r['id']}")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/bench/challenge/..%2f..%2fx")[0], 400)

    def test_a_request_that_needs_the_model_says_rules_only(self):
        code, r = self.call("POST", "/api/bench/challenge", {"text": "open notepad and then type hello"})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["plan"]["status"], "fallback")
        self.assertIn("model not available, rules only", r["plan"]["problems"][0])
        self.assertEqual(r["grades"][0]["answer"], "no")
        self.assertEqual(r["coordinator"]["status"], "not_understood")

    def test_a_denied_action_stops_the_coordinator(self):
        code, r = self.call("POST", "/api/bench/challenge", {"text": "go to netbanking.example.com"})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["steps"][0]["risk"]["level"], "DENY")
        self.assertEqual(r["coordinator"]["status"], "denied")
        self.assertEqual(r["grades"][5]["answer"], "yes")
        self.assertFalse(r["coordinator"]["summary"].startswith("Done"))

    def test_challenge_input_limits(self):
        self.assertEqual(self.call("POST", "/api/bench/challenge", {"text": "  "})[0], 400)
        self.assertEqual(self.call("POST", "/api/bench/challenge", {"text": "x" * 1001})[0], 400)


@unittest.skipUnless(os.path.exists(os.path.join(BENCH_DIR, "model_candidates.json")), "model_candidates.json not in this checkout")
class ModelTest(Base):
    def test_lists_only_candidates_and_refuses_other_names(self):
        with open(os.path.join(BENCH_DIR, "model_candidates.json"), encoding="utf-8") as fh:
            cands = json.load(fh)
        code, d = self.call("GET", "/api/bench/models")
        self.assertEqual(code, 200)
        self.assertEqual([c["name"] for c in d["candidates"]], list(cands))
        self.assertTrue(all(c["present"] is False for c in d["candidates"]))
        for body in ({"models": ["gpt-evil"]}, {"models": ["../../etc/passwd"]}, {"models": []}, {"models": [next(iter(cands))], "sets": ["secret"]}):
            code, e = self.call("POST", "/api/bench/models/run", body)
            self.assertEqual(code, 400, (body, e))
        name = next(iter(cands))
        code, e = self.call("POST", "/api/bench/models/run", {"models": [name]})      # known but its file is not here: never downloaded
        self.assertEqual(code, 409, e)
        self.assertEqual(self.argvs, [])
        with open(os.path.join(self.model_dir, cands[name]["file"]), "wb") as fh:  # a sparse file of the pinned size counts as present;
            fh.truncate(cands[name]["bytes"])                                       # model_compare.py itself checks the SHA-256
        code, j = self.call("POST", "/api/bench/models/run", {"models": [name], "sets": ["dev"]})
        self.assertEqual(code, 202, j)
        self.assertEqual(self.argvs, [("models", {"models": [name], "sets": ["dev"]})])
        self.wait_job(j["id"], lambda x: x["state"] != "running")


class ProtectionTest(Base):
    def test_cross_origin_post_and_delete_refused(self):
        for method, path, body in (("POST", "/api/bench/run", {"groups": ["security"]}), ("POST", "/api/bench/challenge", {"text": "open notepad"}),
                                   ("DELETE", "/api/bench/challenge/ch-20261017-000000-abcdef", None)):
            code, d = self.call(method, path, body, {"Origin": "http://evil.example"})
            self.assertEqual(code, 403, (method, path, d))
            code, d = self.call(method, path, body, {"X-Jarvis-Console": "0"})      # no Origin and no console header
            self.assertEqual(code, 403, (method, path, d))
            code, d = self.call(method, path, body, {"X-Jarvis-Console": "1"})      # the old constant header is no longer a key (SEC-D9)
            self.assertEqual(code, 403, (method, path, d))
        self.assertEqual(self.argvs, [])
        self.assertFalse(os.path.exists(os.path.join(self.results, "challenges")))

    def test_non_loopback_host_refused(self):
        for host in ("evil.example", f"evil.example:{self.port}", f"192.168.1.5:{self.port}", "127.0.0.1:1"):
            code, d = self.call("GET", "/api/bench/summary", headers={"Host": host})
            self.assertEqual(code, 403, host)

    def test_delete_only_for_bench(self):
        self.assertEqual(self.call("DELETE", "/api/owner/tasks/x")[0], 405)

    def test_oversized_body_refused(self):
        code, d = self.call("POST", "/api/bench/challenge", {"text": "x" * (300 * 1024)})
        self.assertEqual(code, 413, d)


if __name__ == "__main__":
    unittest.main()
