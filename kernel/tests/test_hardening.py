"""Regression tests for the independent review of kernel v0.1."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.policy import Action, DENY, RED, GREEN, YELLOW_APPROVAL, decide
from jarvis_kernel.store import Kernel

KDIR = os.path.join(os.path.dirname(__file__), "..")


class H(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "data"))

    def tearDown(self):
        self.k.db.close()
        self.tmp.cleanup()

    # 1 approval double-use across connections/threads
    def test_one_approval_allows_exactly_one_of_many_concurrent_callers(self):
        a = Action("pkg", "install", "requests")
        d, aid = self.k.request(a)
        self.k.approve(aid)
        results, kernels = [], [Kernel(self.k.dir) for _ in range(8)]
        def go(k):
            results.append(k.authorize(a, aid).allowed)
        ts = [threading.Thread(target=go, args=(k,)) for k in kernels]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sum(results), 1)
        for k in kernels: k.db.close()

    # 7 audit appends from several connections never collide
    def test_concurrent_audit_appends_keep_chain_valid(self):
        kernels = [Kernel(self.k.dir) for _ in range(4)]
        def go(k):
            for i in range(25): k.audit.append("t", {"i": i})
        ts = [threading.Thread(target=go, args=(k,)) for k in kernels]
        [t.start() for t in ts]; [t.join() for t in ts]
        ok, msg = self.k.audit.verify()
        self.assertTrue(ok, msg)
        self.assertEqual(self.k.audit.head()[0], 100)
        for k in kernels: k.db.close()

    # 2 egress / name normalization
    def test_egress_name_variants_denied_after_untrusted_read(self):
        for tool in ["Message", "message ", "MESSAGE", "HTTP_POST", "Email", " upload", "ｍｅｓｓａｇｅ"]:
            self.assertEqual(decide(Action(tool, "send_routine", "x", untrusted_session=True)).level, DENY, tool)

    def test_case_variants_do_not_lower_a_rule(self):
        self.assertEqual(decide(Action("MONEY", "Pay", "1")).level, RED)
        self.assertEqual(decide(Action("FS", "READ", "a")).level, GREEN)

    # 5 secrets path tricks
    def test_secret_path_tricks_denied(self):
        for tgt, params in [
            ("D:/JARVIS-SECRETS/Keys.JSON", {}),
            ("D:\\jarvis-secrets\\ssh\\x", {}),
            ("C:/work/../x/../jarvis-secrets/keys.json", {}),
            ("D:/jarvis%2Dsecrets/keys.json", {}),
            ("D:/jarvis%252Dsecrets/keys.json", {}),
            ("ｊａｒｖｉｓ－ｓｅｃｒｅｔｓ/k", {}),
            ("~/.jarvis", {}),
            ("home/u/.jarvis/secrets.env", {}),
            ("backup/id_ed25519", {}),
            ("x/oci_api_key.PEM", {}),
            ("", {"dir": "D:/jarvis-", "n": "secrets"}),
            ("", {"nested": {"deep": ["a", "D:/jarvis-secrets/oci"]}}),
        ]:
            self.assertEqual(decide(Action("fs", "read", tgt, params)).level, DENY, (tgt, params))

    def test_ordinary_paths_still_green(self):
        for tgt in ["D:/jarvis/docs/a.md", "https://docs.python.org/3/", "C:/work/readme.md", "repo/src/main.py"]:
            self.assertEqual(decide(Action("fs", "read", tgt)).level, GREEN, tgt)

    def test_symlink_or_junction_to_secrets_denied(self):
        base = os.path.join(self.tmp.name, "store"); real = os.path.join(base, "jarvis-secrets")
        os.makedirs(real); link = os.path.join(self.tmp.name, "innocent")
        try:
            if os.name == "nt":
                r = subprocess.run(["cmd", "/c", "mklink", "/J", link, real], capture_output=True)
                if r.returncode: self.skipTest("cannot create junction")
            else:
                os.symlink(real, link)
        except OSError:
            self.skipTest("cannot create link")
        self.assertEqual(decide(Action("fs", "read", link + "/x.txt")).level, DENY)

    # 6 and 8 protected resources
    def test_kernel_data_dir_is_protected(self):
        for name in ["KILL", "state.db", "state.db-wal"]:
            d = decide(Action("fs", "write", str(self.k.dir / name)), protected=self.k.protected)
            self.assertEqual(d.level, DENY, name)

    def test_protected_id_matching_ignores_case(self):
        prot = {"OCID1.Instance.OC1..AbCdEf"}
        self.assertEqual(decide(Action("oci", "terminate", "ocid1.instance.oc1..abcdef"), protected=prot).level, DENY)
        self.assertEqual(decide(Action("oci", "terminate", "", {"id": "OCID1.INSTANCE.OC1..ABCDEF"}), protected=prot).level, DENY)

    # common credential stores found missing by the 260-item risk benchmark (2026-10-08)
    def test_credential_stores_are_denied_for_reads_copies_and_uploads(self):
        for tgt in (".env", "C:/proj/.env.production", "api/.env.local", "~/.aws/credentials", "D:/x/.AWS/config", "~/.ssh/config", "~/.gnupg/secring.gpg",
                    "~/.npmrc", "~/.pypirc", "~/.netrc", "~/.pgpass", "~/.git-credentials", "~/.kube/config", "deploy/kubeconfig", "repo/.git/config",
                    "~/.docker/config.json", "secrets.yaml", "config/secrets.yml", "app/credentials.json", "service-account-prod.json", "terraform.tfstate",
                    "prod.tfvars/terraform.tfvars", "C:/Users/x/AppData/Roaming/Mozilla/Firefox/Profiles/a.default/logins.json", "key4.db",
                    "C:/Users/x/AppData/Local/Google/Chrome/User Data/Default/Cookies", "Cookies.sqlite", "Login Data", "Web Data", "Local State",
                    "wallet.dat", "/etc/shadow", "vault.kdbx", "cert.p12", "id.pfx", "release.keystore", "google recovery codes.txt", "~/.config/gcloud/credentials.db"):
            for op in ("read", "copy", "upload"):
                self.assertEqual(decide(Action("fs", op, tgt)).level, DENY, (tgt, op))

    def test_credential_patterns_do_not_catch_ordinary_files(self):
        for tgt in ("README.md", "src/environment.py", "notes/environment-setup.md", "docs/secret-santa.md", "config.json", "package.json", "app/envelope.py",
                    "data/cookies-recipe.md", "keys.md", "C:/work/gitconfig.txt", "repo/.gitignore", "repo/.github/workflows/ci.yml", ".environment", "my.env.sample.txt"):
            self.assertEqual(decide(Action("fs", "read", tgt)).level, GREEN, tgt)

    def test_changing_logs_and_history_needs_the_console(self):
        for tgt in ("/var/log/auth.log", "/var/log/syslog", "~/.bash_history", "~/.zsh_history", "C:/Users/x/AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt", "/var/log/wtmp"):
            for op in ("write", "delete", "truncate"):
                self.assertGreaterEqual(decide(Action("fs", op, tgt)).level, RED, (tgt, op))
        self.assertEqual(decide(Action("fs", "read", "/var/log/syslog")).level, GREEN)       # reading logs stays fine

    # rules, hooks and settings files; the protected server by name
    def test_changing_rule_files_needs_the_console(self):
        for tgt in ("D:/jarvis/CLAUDE.md", "~/.claude/settings.json", "repo/.claude/settings.local.json", "kernel/jarvis_kernel/policy.py",
                    "config/providers.json", "C:/Users/x/.claude/hooks.json", "CLAUDE.MD"):
            for op in ("write", "edit", "delete"):
                self.assertGreaterEqual(decide(Action("fs", op, tgt)).level, RED, (tgt, op))
        self.assertEqual(decide(Action("fs", "read", "D:/jarvis/CLAUDE.md")).level, GREEN)      # reading them is fine
        self.assertEqual(decide(Action("fs", "write", "notes/todo.md")).level, YELLOW_APPROVAL)  # ordinary files are not affected

    def test_protected_server_name_is_denied_for_every_tool(self):
        for tool, op, tgt in (("oci", "terminate", "hermes-agent-vm-2"), ("fs", "write", "/mnt/hermes-agent-vm-2/disk"), ("ssh", "run", "ubuntu@hermes-agent-vm-2")):
            self.assertEqual(decide(Action(tool, op, tgt), protected=self.k.protected).level, DENY, (tool, op))

    # 10 suggested clamp
    def test_suggested_is_clamped_and_bools_ignored(self):
        self.assertEqual(decide(Action("fs", "read", "a"), suggested=99).level, DENY)
        self.assertEqual(decide(Action("fs", "read", "a"), suggested=-5).level, GREEN)
        self.assertEqual(decide(Action("fs", "read", "a"), suggested=True).level, GREEN)

    # 9 anchors
    def test_empty_or_malformed_anchor_not_accepted(self):
        self.k.audit.append("t", {})
        for bad in [{}, {"seq": 1}, {"hash": "x"}, {"seq": "1", "hash": "y"}, []]:
            self.assertFalse(self.k.audit.verify(bad)[0], bad)

    def test_cli_unreadable_anchor_fails(self):
        r = subprocess.run([sys.executable, "-m", "jarvis_kernel", "--data", str(self.k.dir), "audit", "verify", "--anchor-file", "nope.json"],
                           cwd=KDIR, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)

    # 3 resume needs a console
    def test_resume_refused_without_console(self):
        self.k.kill("t")
        headless = {"creationflags": 0x00000008} if os.name == "nt" else {"start_new_session": True}
        r = subprocess.run([getattr(sys, "_base_executable", sys.executable), "-m", "jarvis_kernel", "--data", str(self.k.dir), "resume"], cwd=KDIR,
                           input="RESUME\n", capture_output=True, text=True, timeout=30, **headless)
        self.assertEqual(r.returncode, 3)
        self.assertTrue(self.k.killed())

    # 4 blind approval
    def test_approval_shows_the_action(self):
        a = Action("pkg", "install", "requests", {"version": "2.32.3"})
        d, aid = self.k.request(a)
        info = self.k.approval_info(aid)
        self.assertEqual(info["action"]["target"], "requests")
        self.assertEqual(info["action"]["params"]["version"], "2.32.3")
        self.assertEqual(info["state"], "pending")

    def test_natural_low_approval_cannot_cover_other_action(self):
        d, aid = self.k.request(Action("pkg", "install", "requests"))
        self.k.approve(aid)
        self.assertFalse(self.k.authorize(Action("money", "pay", "1"), aid).allowed)
        self.assertFalse(self.k.authorize(Action("pkg", "install", "evil"), aid).allowed)


if __name__ == "__main__":
    unittest.main()
