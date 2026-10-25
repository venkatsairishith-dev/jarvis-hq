"""Windows execution adapter: job validation and policy in the kernel, observation and verification on the node."""
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zlib
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_kernel.jobs import JobError, Jobs, _check_params, action_for
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.policy import Action, decide
from jarvis_kernel.store import Kernel
from jarvis_node import jobs as nodejobs
from jarvis_node import windows


class Validation(unittest.TestCase):
    def test_new_kinds_accept_exactly_their_fields(self):
        _check_params("ui_windows", {})
        _check_params("screenshot", {})
        _check_params("ui_snapshot", {"target": "notepad"})
        _check_params("ui_snapshot", {"target": ""})                       # the foreground window
        for kind, bad in (("ui_windows", {"x": 1}), ("screenshot", {"path": "C:\\x"}), ("ui_snapshot", {"target": "a b"}),
                          ("ui_snapshot", {"target": "x;y"}), ("ui_snapshot", {"target": "n" * 41}), ("ui_snapshot", {})):
            with self.assertRaises(JobError, msg=str((kind, bad))):
                _check_params(kind, bad)

    def test_policy_levels(self):
        self.assertEqual(decide(action_for("ui_windows", {}, "private")).name, "GREEN")
        self.assertEqual(decide(action_for("ui_snapshot", {"target": "notepad"}, "private")).name, "GREEN")
        self.assertEqual(decide(action_for("screenshot", {}, "private")).name, "YELLOW_AUTO")
        for kind, params in (("ui_windows", {}), ("ui_snapshot", {"target": "notepad"}), ("screenshot", {})):
            self.assertEqual(decide(action_for(kind, params, "public", untrusted=True)).name, "YELLOW_APPROVAL", kind)
        self.assertEqual(decide(Action("ui", "snapshot", "keepass", {}, "private")).name, "DENY")         # password manager

    def test_a_lab_agent_cannot_look_at_the_desktop_without_approval(self):
        with tempfile.TemporaryDirectory() as t:
            k = Kernel(os.path.join(t, "d"))
            H, tokens = make_handler(k, [], owner_hermes=None)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{srv.server_address[1]}"
            pub, priv = tokens.issue("lab", "public"), tokens.issue("hermes", "private")

            def act(tok, body):
                r = urllib.request.Request(url + "/v1/jarvis/actions", data=json.dumps(body).encode(), method="POST",
                                           headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(r, timeout=10) as resp:
                        return resp.status, json.loads(resp.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())
            code, r = act(pub, {"tool": "ui", "op": "list_windows", "target": ""})
            self.assertEqual((code, r["status"]), (202, "needs_approval"))
            code, r = act(priv, {"tool": "ui", "op": "list_windows", "target": ""})
            self.assertEqual((code, r["status"]), (200, "queued"))
            code, r = act(priv, {"tool": "ui", "op": "snapshot", "target": "notepad"})
            self.assertEqual((code, r["status"]), (200, "queued"))
            self.assertEqual(k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind IN ('ui_windows','ui_snapshot')").fetchone()[0], 2)
            srv.shutdown(); k.db.close()


class NodeSide(unittest.TestCase):
    WINS = [{"hwnd": 1, "pid": 10, "process": "explorer", "title": "Downloads", "class": "CabinetWClass", "foreground": True, "minimized": False, "w": 800, "h": 600},
            {"hwnd": 2, "pid": 11, "process": "Code", "title": "main.py - Visual Studio Code", "class": "Chrome_WidgetWin_1", "foreground": False, "minimized": False, "w": 1200, "h": 800}]

    def test_ui_windows_reports_what_the_lister_saw(self):
        r = nodejobs.ui_windows({}, lister=lambda: self.WINS)
        self.assertTrue(r["ok"])
        self.assertEqual([w["title"] for w in r["windows"]], ["Downloads", "main.py - Visual Studio Code"])
        self.assertNotIn("hwnd", r["windows"][0])                       # handles are not sent to the kernel

    def test_ui_windows_gives_each_window_a_stable_opaque_id(self):
        """Audit PC-D1 (2026-10-18): without an identity per window the plugin could never tell a NEW window from an old
        one. The node sends an opaque id (not the handle), the same for the same window on every listing, different for
        different windows, the ids of all windows (not only the 40 described), and the epoch of this node process."""
        a = nodejobs.ui_windows({}, lister=lambda: self.WINS)
        b = nodejobs.ui_windows({}, lister=lambda: list(reversed(self.WINS)))
        ids_a = [w["id"] for w in a["windows"]]
        self.assertTrue(all(isinstance(i, str) and i.startswith("w") and len(i) == 17 for i in ids_a))
        self.assertEqual(len(set(ids_a)), 2)
        self.assertEqual({w["title"]: w["id"] for w in a["windows"]}, {w["title"]: w["id"] for w in b["windows"]})
        self.assertEqual(sorted(a["ids"]), sorted(ids_a))
        self.assertTrue(a["ids_complete"])
        self.assertEqual(a["id_epoch"], b["id_epoch"])
        for w in a["windows"]:
            self.assertNotIn(w["id"], ("1", "2", "w1", "w2"))                 # not the handle itself
            self.assertNotIn("hwnd", w)
            self.assertNotIn("pid", w)
        many = [dict(self.WINS[0], hwnd=100 + i, title=f"W{i}") for i in range(45)]
        m = nodejobs.ui_windows({}, lister=lambda: many)
        self.assertEqual((len(m["windows"]), len(m["ids"]), m["ids_complete"]), (40, 45, True))
        # a window whose handle is unknown has no id, and the list says it is incomplete (the plugin then claims nothing)
        broken = nodejobs.ui_windows({}, lister=lambda: [self.WINS[0], dict(self.WINS[1], hwnd=None)])
        self.assertIsNone(broken["windows"][1]["id"])
        self.assertFalse(broken["ids_complete"])

    def test_open_is_verified_by_looking_for_the_window(self):
        before = set()
        ok = nodejobs._verify_open({"ok": True, "opened": "Downloads"}, {"op": "open_folder"}, lister=lambda: self.WINS, before=before, timeout=0.5)
        self.assertEqual(ok["verified"], "verified")
        self.assertEqual(ok["window"]["title"], "Downloads")
        # a window that was already open counts only when it is in front now
        behind = [dict(self.WINS[0], foreground=False), self.WINS[1]]
        old = nodejobs._verify_open({"ok": True, "opened": "Downloads"}, {"op": "open_folder"}, lister=lambda: behind, before={1}, timeout=0.5)
        self.assertEqual(old["verified"], "unconfirmed")
        self.assertIn("no window titled like", old["note"])
        raised = nodejobs._verify_open({"ok": True, "opened": "Downloads"}, {"op": "open_folder"}, lister=lambda: self.WINS, before={1}, timeout=0.5)
        self.assertEqual(raised["verified"], "verified")
        self.assertIn("already open", raised["note"])
        # nested folder: the window is named after the last component
        nested = nodejobs._verify_open({"ok": True, "opened": "Downloads\\main.py"}, {"op": "open_folder"}, lister=lambda: self.WINS, timeout=0.5)
        self.assertEqual(nested["verified"], "verified")
        # a failed open is passed through untouched, and listing needs no verification
        self.assertEqual(nodejobs._verify_open({"ok": False, "error": "x"}, {"op": "open_app"}, lister=lambda: []), {"ok": False, "error": "x"})
        self.assertNotIn("verified", nodejobs._verify_open({"ok": True, "folder": "Downloads"}, {"op": "list_folder"}, lister=lambda: []))

    def test_an_explorer_window_that_already_shows_the_folder_counts_as_open_even_when_it_is_not_in_front(self):
        # found on the real laptop: Explorer re-used a window and did not raise it, so the open was reported as "no window showing it was seen"
        behind = [dict(self.WINS[0], foreground=False), self.WINS[1]]
        shown = lambda: ["c:/users/venkat/downloads", "d:/jarvis/outputs"]
        ok = nodejobs._verify_open({"ok": True, "opened": "Downloads"}, {"op": "open_folder"}, lister=lambda: behind, before={1}, timeout=0.5,
                                   path="C:/Users/venkat/Downloads", locations=shown)
        self.assertEqual(ok["verified"], "verified")
        self.assertIn("already showing the folder", ok["note"])
        other = nodejobs._verify_open({"ok": True, "opened": "Downloads"}, {"op": "open_folder"}, lister=lambda: behind, before={1}, timeout=0.5,
                                      path="C:/Users/venkat/Documents", locations=shown)
        self.assertEqual(other["verified"], "unconfirmed")
        # only folders: a file open never claims a folder window
        f = nodejobs._verify_open({"ok": True, "opened": "Downloads/a.pdf"}, {"op": "open_file"}, lister=lambda: [], before=set(), timeout=0.2,
                                  path="C:/Users/venkat/Downloads", locations=shown)
        self.assertEqual(f["verified"], "unconfirmed")

    def test_snapshot_refuses_password_managers(self):
        snap = lambda proc, n, d, v: {"window": "KeePass", "process": "KeePass", "elements": []}
        self.assertFalse(nodejobs.ui_snapshot({"target": ""}, snap=snap)["ok"])                        # the foreground window was a password manager
        self.assertFalse(nodejobs.ui_snapshot({"target": "1password"}, snap=lambda *a: {})["ok"])
        good = nodejobs.ui_snapshot({"target": "notepad"}, snap=lambda p, n, d, v: {"window": "a.txt - Notepad", "process": "notepad",
                                                                                   "elements": [{"depth": 1, "type": "Edit", "name": "Text editor", "id": "15", "enabled": True, "value": "SECRET"}]})
        self.assertTrue(good["ok"])
        self.assertNotIn("value", good["elements"][0])                                                  # field values never leave the node

    def test_screenshot_is_written_locally_and_not_returned(self):
        with tempfile.TemporaryDirectory() as t:
            def shooter(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(windows.encode_png(b"\x10\x20\x30" * 6, 3, 2))
                return {"width": 3, "height": 2, "bytes": 70}
            r = nodejobs.screenshot({}, root=t, shooter=shooter)
            self.assertTrue(r["ok"])
            self.assertTrue(os.path.isfile(os.path.join(t, "screens", r["saved"])))
            self.assertNotIn("data", r)

    def test_png_encoder_makes_a_decodable_image(self):
        w, h = 4, 3
        rgb = bytes(range(w * h * 3))
        png = windows.encode_png(rgb, w, h)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        pos, chunks = 8, {}
        while pos < len(png):
            n = struct.unpack(">I", png[pos:pos + 4])[0]
            tag = png[pos + 4:pos + 8]
            data = png[pos + 8:pos + 8 + n]
            self.assertEqual(struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0], zlib.crc32(tag + data) & 0xFFFFFFFF)
            chunks[tag] = data
            pos += 12 + n
        self.assertEqual(struct.unpack(">II", chunks[b"IHDR"][:8]), (w, h))
        raw = zlib.decompress(chunks[b"IDAT"])
        self.assertEqual(len(raw), h * (1 + w * 3))
        self.assertEqual(raw[1:1 + w * 3], rgb[:w * 3])

    def test_run_job_dispatches_the_new_kinds(self):
        self.assertFalse(nodejobs.run_job({"kind": "ui_snapshot", "params": {"target": "bad name!"}})["ok"])
        self.assertEqual(nodejobs.run_job({"kind": "nope", "params": {}})["error"], "unknown job kind")


@unittest.skipUnless(os.name == "nt", "Win32 observation needs Windows")
class RealDesktop(unittest.TestCase):
    """Reads this laptop's real desktop. Read-only: it opens nothing and changes nothing."""
    def test_lists_real_windows_with_titles_and_processes(self):
        ws = windows.list_windows()
        self.assertGreater(len(ws), 0)
        for w in ws[:5]:
            self.assertTrue(w["title"])
            self.assertIsInstance(w["pid"], int)
        self.assertTrue(any(w["process"] for w in ws))

    def test_real_screenshot_is_a_valid_png_of_the_screen(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "s.png")
            info = windows.screenshot_png(p)
            self.assertGreater(info["width"], 300)
            self.assertGreater(info["height"], 200)
            png = open(p, "rb").read()
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", png[16:24]), (info["width"], info["height"]))
            self.assertGreater(len(png), 2000)                              # not an all-black or empty capture


if __name__ == "__main__":
    unittest.main()
