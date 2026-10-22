"""Audit events may carry a component {name, version, sha256}; the hash chain stays compatible with older events."""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import audit as A  # noqa: E402

GENESIS = "0" * 64


def old_hash(prev, ts, kind, payload):
    """Copy of the pre-change hashing in audit.py."""
    return hashlib.sha256(f"{prev}|{ts!r}|{kind}|{payload}".encode()).hexdigest()


def new_log():
    return A.AuditLog(sqlite3.connect(":memory:", isolation_level=None))


def raw_insert(log, kind, payload):
    """Write a correctly chained row with an arbitrary payload (what a buggy or hostile writer could do)."""
    seq, prev = log.head()
    ts = 1_800_000_000.0 + seq
    body = json.dumps(payload, sort_keys=True)
    log.db.execute("INSERT INTO audit(seq, ts, kind, payload, prev, hash) VALUES (?,?,?,?,?,?)",
                   (seq + 1, ts, kind, body, prev, old_hash(prev, ts, kind, body)))


GOOD = {"name": "kernel", "version": "1.0", "sha256": "a" * 64}


class ComponentTests(unittest.TestCase):
    def test_old_chain_verifies_with_new_verifier(self):
        log = new_log()
        prev = GENESIS
        for i, (kind, payload) in enumerate([("kill", {"why": "x"}), ("request", {"action": "a", "level": "ALLOW"}), ("resume", {})], 1):
            ts = 1_700_000_000.5 + i
            body = json.dumps(payload, sort_keys=True, default=str)
            h = old_hash(prev, ts, kind, body)
            log.db.execute("INSERT INTO audit(seq, ts, kind, payload, prev, hash) VALUES (?,?,?,?,?,?)",
                           (i, ts, kind, body, prev, h))
            prev = h
        self.assertEqual(log.verify(), (True, "ok: 3 events"))
        log.append("kill", {"why": "new"})                    # new code continues the old chain
        self.assertEqual(log.verify(), (True, "ok: 4 events"))

    def test_hash_without_component_unchanged(self):
        log = new_log()
        log.append("kill", {"why": "x"})
        seq, ts, kind, payload, prev, h = log.db.execute("SELECT seq,ts,kind,payload,prev,hash FROM audit").fetchone()
        self.assertEqual(payload, '{"why": "x"}')
        self.assertEqual(h, old_hash(GENESIS, ts, "kill", payload))

    def test_component_stored_and_verified(self):
        log = new_log()
        log.append("start", {"a": 1}, component=GOOD)
        payload = log.db.execute("SELECT payload FROM audit").fetchone()[0]
        self.assertEqual(json.loads(payload), {"a": 1, "component": GOOD})
        self.assertTrue(log.verify()[0])

    def test_append_rejects_bad_component(self):
        log = new_log()
        for bad in ("x", ["a"], {**GOOD, "sha256": "zz"}, {"name": "k"}):
            with self.assertRaises(ValueError):
                log.append("start", {}, component=bad)
        self.assertEqual(log.head()[0], 0)

    def test_verifier_rejects_bad_sha_shape(self):
        for sha in ("abc", "A" * 64, "g" * 64, "a" * 63, 5):
            log = new_log()
            raw_insert(log, "start", {"component": {**GOOD, "sha256": sha}})
            ok, msg = log.verify()
            self.assertFalse(ok, sha)
            self.assertIn("malformed component", msg)

    def test_verifier_rejects_non_dict_component(self):
        for bad in ("kernel", ["a"], 7, None):
            log = new_log()
            raw_insert(log, "start", {"component": bad})
            ok, msg = log.verify()
            self.assertFalse(ok, bad)
            self.assertIn("malformed component", msg)

    def test_edited_component_breaks_chain(self):
        log = new_log()
        log.append("start", {}, component=GOOD)
        log.append("kill", {})
        edited = json.dumps({"component": {**GOOD, "sha256": "b" * 64}}, sort_keys=True)
        log.db.execute("UPDATE audit SET payload=? WHERE seq=1", (edited,))
        ok, msg = log.verify()
        self.assertFalse(ok)
        self.assertIn("chain broken at seq 1", msg)

    def test_component_info_matches_hashlib(self):
        data = os.urandom(200_000)                             # more than one chunk
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.bin")
            with open(p, "wb") as fh:
                fh.write(data)
            self.assertEqual(A.component_info("n", "2", p),
                             {"name": "n", "version": "2", "sha256": hashlib.sha256(data).hexdigest()})

    def test_component_info_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            info = A.component_info("n", "2", os.path.join(d, "nope"))
        self.assertEqual(info, {"name": "n", "version": "2", "sha256": "missing"})
        log = new_log()
        log.append("start", {}, component=info)
        self.assertTrue(log.verify()[0])

    def test_different_components_different_hashes(self):
        log = new_log()
        log.append("start", {}, component=GOOD)
        log.append("start", {}, component={**GOOD, "version": "2.0"})
        h1, h2 = [r[0] for r in log.db.execute("SELECT hash FROM audit ORDER BY seq")]
        self.assertNotEqual(h1, h2)
        # identical payload apart from the component, same prev/ts/kind would still differ
        self.assertNotEqual(old_hash(GENESIS, 1.0, "k", json.dumps({"component": GOOD}, sort_keys=True)),
                            old_hash(GENESIS, 1.0, "k", json.dumps({"component": {**GOOD, "version": "2.0"}}, sort_keys=True)))


if __name__ == "__main__":
    unittest.main()
