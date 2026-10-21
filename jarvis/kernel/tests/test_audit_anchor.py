"""Audit anchors and two small hardening items (audit 2026-10-18).

SEC-D8: the owner's `audit/verify` route checked the chain with no anchor, so deleting the newest rows passed as "ok". The
kernel now stores its head outside state.db from the owner tick (rate-limited) and verify uses the newest stored anchor.
SEC-D10: `llm-proxy --bind 0.0.0.0` was accepted (only `serve` refused it); the PIN lockout reset on a kernel restart."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import cli  # noqa: E402
from jarvis_kernel import audit as A  # noqa: E402
from jarvis_kernel.jobs import Jobs  # noqa: E402
from jarvis_kernel.owner import PIN_TRIES, PIN_WINDOW, Owner  # noqa: E402
from jarvis_kernel.policy import Action  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402
from jarvis_kernel.tasks import Tasks  # noqa: E402

VERIFY = "/v1/jarvis/owner/audit/verify"


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Anchors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "k")
        self.k = Kernel(self.dir)
        self.clock = Clock()
        self.o = Owner(self.k, Tasks(self.k), Jobs(self.k), clock=self.clock)
        self.o.attach()
        self.path = os.path.join(self.dir, getattr(A, "ANCHOR_FILE", "audit-anchors.jsonl"))

    def tearDown(self):
        self.k.db.close(); self.tmp.cleanup()

    def events(self, n, kind="x"):
        for i in range(n):
            self.k.audit.append(kind, {"i": i})

    def verify(self):
        code, reply = self.o.handle("GET", VERIFY, {})
        self.assertEqual(code, 200)
        return reply

    def test_owner_verify_sees_a_cut_off_tail_once_an_anchor_is_stored(self):
        self.events(20)
        self.o.tick()                                                   # stores the head outside state.db
        self.assertTrue(os.path.exists(self.path))
        self.assertTrue(self.verify()["ok"])
        head = self.k.audit.anchor()["seq"]
        self.k.db.execute("DELETE FROM audit WHERE seq > ?", (head - 5,))
        r = self.verify()
        self.assertFalse(r["ok"], r)
        self.assertIn("truncated", r["message"])
        self.assertTrue(r["anchored"])

    def test_owner_verify_sees_a_rewrite_from_genesis(self):
        self.events(8)
        self.o.tick()
        self.k.db.execute("DELETE FROM audit")
        self.events(12, "forged")
        self.assertFalse(self.verify()["ok"])

    def test_no_anchor_yet_is_said_plainly(self):
        self.events(3)
        r = self.verify()
        self.assertTrue(r["ok"])
        self.assertFalse(r["anchored"]); self.assertIsNone(r["anchor"])
        self.assertIn("no anchor yet", r["note"])

    def test_with_an_anchor_the_note_says_the_off_box_copy_is_manual(self):
        self.events(3); self.o.tick()
        r = self.verify()
        self.assertTrue(r["anchored"]); self.assertIn("manual", r["note"])

    def test_export_is_rate_limited_and_only_when_the_head_moved(self):
        self.events(2)
        self.assertIsNotNone(self.o.tick()["anchor"])
        self.events(2)
        self.assertIsNone(self.o.tick()["anchor"])                     # too soon
        self.clock.t += getattr(A, "ANCHOR_EVERY", 900) + 1
        self.assertIsNotNone(self.o.tick()["anchor"])
        self.clock.t += getattr(A, "ANCHOR_EVERY", 900) + 1
        self.assertIsNone(self.o.tick()["anchor"])                     # head did not move
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().splitlines()), 2)

    def test_an_export_never_launders_a_truncation_or_a_rewrite(self):
        self.events(10); self.o.tick()
        stored = A.latest_anchor(self.path)[0]
        self.k.db.execute("DELETE FROM audit WHERE seq > 6")
        self.events(1)
        self.clock.t += getattr(A, "ANCHOR_EVERY", 900) + 1
        self.assertIsNone(A.export_anchor(self.k.audit, self.path, self.clock()))       # below the anchor
        self.events(10)
        self.clock.t += getattr(A, "ANCHOR_EVERY", 900) + 1
        self.assertIsNone(A.export_anchor(self.k.audit, self.path, self.clock()))       # past it, but rewritten at its seq
        self.assertEqual(A.latest_anchor(self.path)[0]["seq"], stored["seq"])
        self.assertFalse(self.verify()["ok"])

    def test_the_newest_valid_anchor_wins_and_junk_is_reported(self):
        self.events(5); self.o.tick()
        good = A.latest_anchor(self.path)[0]
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write('{"seq": 1, "hash": "' + "0" * 64 + '"}\n{half\n')      # a lower anchor and a torn line
        self.assertEqual(A.latest_anchor(self.path), ({"seq": good["seq"], "hash": good["hash"], "ts": good["ts"]}, 1))
        self.assertIn("anchor_file_problems", self.verify())

    def test_cli_verify_uses_the_stored_anchor(self):
        self.events(10); self.o.tick()
        self.k.db.execute("DELETE FROM audit WHERE seq > 4")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["--data", self.dir, "audit", "verify"])
        self.assertEqual(rc, 2, out.getvalue())
        self.assertIn("truncated", out.getvalue())


class Hardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "k")

    def tearDown(self):
        self.tmp.cleanup()

    def test_llm_proxy_refuses_all_interfaces(self):
        started = []
        with mock.patch("jarvis_kernel.gateway.load", lambda path: []), \
                mock.patch("jarvis_kernel.llm_proxy.serve", lambda *a, **kw: started.append(kw.get("binds"))):
            for addr in ("0.0.0.0", "::", "[::]", "", "0", "*"):
                with self.subTest(addr=addr):
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        rc = cli.main(["--data", self.dir, "llm-proxy", "--bind", "100.64.0.5", "--bind", addr])
                    self.assertEqual(rc, 3)
                    self.assertIn("not all interfaces", err.getvalue())
            self.assertEqual(started, [])                               # nothing was started
            self.assertEqual(cli.main(["--data", self.dir, "llm-proxy", "--bind", "100.64.0.5"]), 0)
            self.assertEqual(started, [["127.0.0.1", "100.64.0.5"]])    # one private address is still fine

    def test_serve_refuses_all_interfaces(self):
        started = []
        with mock.patch("jarvis_kernel.nodes.serve", lambda *a, **kw: started.append(a[1:])):
            for addr in ("0.0.0.0", "::", "0", "[::]"):
                with self.subTest(addr=addr), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main(["--data", self.dir, "serve", "--bind", addr]), 3)
        self.assertEqual(started, [])

    def test_the_pin_lockout_survives_a_restart(self):
        clock = Clock()
        k = Kernel(self.dir)
        o = Owner(k, Tasks(k), Jobs(k), clock=clock)
        o.set_pin("correct horse")
        _, aid = k.request(Action("fs", "write", "notes/a.md"))
        codes = [o.handle("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": "wrong pin"})[0] for _ in range(PIN_TRIES)]
        self.assertEqual(codes, [403] * PIN_TRIES)
        k.db.close()
        k = Kernel(self.dir)                                           # the kernel restarts
        o = Owner(k, Tasks(k), Jobs(k), clock=clock)
        c, r = o.handle("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": "correct horse"})
        self.assertEqual(c, 429, r)
        clock.t -= 3600                                                # the clock goes back across the restart: still locked
        k.db.close(); k = Kernel(self.dir); o = Owner(k, Tasks(k), Jobs(k), clock=clock)
        self.assertEqual(o.handle("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": "correct horse"})[0], 429)
        clock.t += 3600 + PIN_WINDOW + 1                               # the rest is over
        c, r = o.handle("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": "correct horse"})
        self.assertEqual(c, 200, r)
        k.db.close()

    def test_an_unreadable_lockout_record_fails_closed(self):
        k = Kernel(self.dir)
        Owner(k, Tasks(k), Jobs(k)).set_pin("correct horse")
        k.db.execute("INSERT OR REPLACE INTO owner_config(key, value) VALUES ('pin_failures', '{broken')")
        o = Owner(k, Tasks(k), Jobs(k))
        _, aid = k.request(Action("fs", "write", "notes/a.md"))
        self.assertEqual(o.handle("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": "correct horse"})[0], 429)
        k.db.close()


if __name__ == "__main__":
    unittest.main()
