"""The console's connector endpoints: the local allowlist (the one place a command line is accepted), the Add and Remove
workflows that keep it and the kernel's registry together, and the refusals around them."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_console import Base  # noqa: E402

FIXTURE = os.path.abspath(os.path.join(HERE, "..", "..", "kernel", "tests", "fixtures", "fake_mcp_server.py"))


class Connectors(Base):
    def setUp(self):
        super().setUp()
        t = self.tmp.name
        self.cfg.connectors_file = os.path.join(t, "connectors.local.json")
        self.cfg.secrets_dir = os.path.join(t, "secrets")
        os.makedirs(os.path.join(self.cfg.secrets_dir, "connectors"))
        self.local = {"command": [sys.executable, FIXTURE], "env_refs": {"FAKE_TOKEN": "connectors/fake.token"}, "env_static": {"MODE": "test"}, "timeout_s": 10}
        self.definition = {"name": "fake", "label": "Fake", "purpose": "tests", "transport": "stdio", "node": "laptop", "lanes": ["private"]}

    def add(self, **kw):
        body = {"definition": dict(self.definition), "local": dict(self.local)}
        body.update(kw)
        return self.call("POST", "/api/connectors/add", body)

    def test_add_writes_the_local_allowlist_and_the_kernel_record_and_they_agree(self):
        code, body, _ = self.add()
        self.assertEqual(code, 201, body)
        self.assertEqual(body["name"], "fake")
        self.assertEqual(body["status"], "configured")
        self.assertNotIn("command", json.dumps(body))                                   # the kernel record holds no command line
        code, local, _ = self.call("GET", "/api/connectors/local")
        self.assertEqual(code, 200)
        spec = local["local"]["fake"]
        self.assertEqual(spec["command"], [sys.executable, FIXTURE])
        self.assertTrue(spec["installed"])
        self.assertEqual(spec["secrets_present"], {"FAKE_TOKEN": False})                 # a boolean, never the contents
        with open(os.path.join(self.cfg.secrets_dir, "connectors", "fake.token"), "w") as fh:
            fh.write("SUPER-SECRET-VALUE")
        code, local, _ = self.call("GET", "/api/connectors/local")
        self.assertEqual(local["local"]["fake"]["secrets_present"], {"FAKE_TOKEN": True})
        self.assertNotIn("SUPER-SECRET-VALUE", json.dumps(local))
        code, owner, _ = self.call("GET", "/api/owner/connectors")
        self.assertEqual([c["name"] for c in owner["connectors"]], ["fake"])
        self.assertNotIn(FIXTURE, json.dumps(owner))                                     # not in the kernel's view either

    def test_commands_that_are_shells_scripts_or_eval_are_refused_and_nothing_is_written(self):
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        bad = [
            {"command": ["cmd.exe", "/c", "calc"]},                                      # not absolute
            {"command": [os.path.join(sysroot, "System32", "cmd.exe"), "/c", "calc"]},   # a shell, in the Windows folder
            {"command": [sys.executable, "-c", "import os; os.system('calc')"]},         # an interpreter given code
            {"command": "python server.py"},                                             # a string a shell would parse
            {"command": [sys.executable, FIXTURE, "x" * 400]},
            {"command": [os.path.join(self.tmp.name, "missing.exe")]},                   # not installed
            {"command": [sys.executable, FIXTURE, os.path.join("D:\\jarvis-secrets", "keys.json")]},   # arguments into the secrets folder
            {"command": [sys.executable, FIXTURE], "env_static": {"API_TOKEN": "abc"}},  # a credential as a plain setting
            {"command": [sys.executable, FIXTURE], "env_static": {"PATH": "x"}},         # a protected variable
            {"command": [sys.executable, FIXTURE], "env_refs": {"TOKEN": "../../keys.json"}},
            {"command": [sys.executable, FIXTURE], "env_refs": {"TOKEN": "C:\\Windows\\win.ini"}},
            {"command": [sys.executable, FIXTURE], "timeout_s": 9999},
        ]
        for local in bad:
            code, body, _ = self.add(local=local)
            self.assertEqual(code, 400, str(local)[:90] + " -> " + str(body)[:100])
        self.assertFalse(os.path.exists(self.cfg.connectors_file))                       # no half-added program
        self.assertEqual(self.call("GET", "/api/owner/connectors")[1]["connectors"], [])

    def test_a_kernel_refusal_rolls_the_local_entry_back(self):
        self.assertEqual(self.add()[0], 201)
        code, body, _ = self.add()                                                       # the same name again: the kernel says 409
        self.assertEqual(code, 409)
        with open(self.cfg.connectors_file) as fh:
            self.assertEqual(list(json.load(fh)["connectors"]), ["fake"])
        d = dict(self.definition, name="other", lanes=["owner"])                         # a definition the kernel refuses
        code, body, _ = self.add(definition=d)
        self.assertEqual(code, 400)
        with open(self.cfg.connectors_file) as fh:
            self.assertEqual(list(json.load(fh)["connectors"]), ["fake"])                # "other" was not left behind

    def test_remove_needs_the_name_typed_and_removes_both_sides(self):
        self.assertEqual(self.add()[0], 201)
        code, body, _ = self.call("POST", "/api/connectors/remove", {"name": "fake", "confirm": "wrong"})
        self.assertEqual(code, 400)
        self.assertTrue(self.call("GET", "/api/connectors/local")[1]["local"])
        code, body, _ = self.call("POST", "/api/connectors/remove", {"name": "fake", "confirm": "fake"})
        self.assertEqual(code, 200, body)
        self.assertEqual(self.call("GET", "/api/connectors/local")[1]["local"], {})
        self.assertEqual(self.call("GET", "/api/owner/connectors")[1]["connectors"], [])

    def test_a_https_connector_has_no_local_part_and_plain_http_is_refused(self):
        d = {"name": "wiki", "label": "Wiki", "purpose": "docs", "transport": "https", "endpoint": "https://mcp.example.org/mcp", "lanes": ["private"]}
        code, body, _ = self.call("POST", "/api/connectors/add", {"definition": d})
        self.assertEqual(code, 201, body)
        self.assertFalse(os.path.exists(self.cfg.connectors_file))
        code, body, _ = self.call("POST", "/api/connectors/add", {"definition": dict(d, name="wiki2", endpoint="http://mcp.example.org/mcp")})
        self.assertEqual(code, 400)

    def test_the_templates_are_served_and_say_what_is_tested(self):
        code, body, _ = self.call("GET", "/api/connectors/templates")
        self.assertEqual(code, 200)
        by = {t["id"]: t for t in body["templates"]}
        self.assertEqual(by["time"]["status"], "tested")
        self.assertEqual(by["github"]["status"], "template")
        for t in body["templates"]:
            self.assertNotIn("ghp_", json.dumps(t))                                      # no credential in a template
            for ref in ((t.get("local") or {}).get("env_refs") or {}).values():
                self.assertTrue(ref.startswith("connectors/"))

    def test_cross_site_requests_cannot_add_a_program(self):
        code, body, _ = self.call("POST", "/api/connectors/add", {"definition": self.definition, "local": self.local}, headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        self.assertFalse(os.path.exists(self.cfg.connectors_file))


if __name__ == "__main__":
    unittest.main()
