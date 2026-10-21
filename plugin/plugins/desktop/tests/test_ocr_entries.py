"""The OCR / vision rung is reachable (audit PRO-D10, 2026-10-18): read_window_text(app) and find_on_screen(app, text) call
the kernel's existing diag jobs window_ocr / window_locate through core.Pc (so policy, approvals and audit apply), return
scrubbed text and honest errors, and the troubleshooter falls back to OCR - labelled "from OCR (less reliable)", hypothesis
only - when UI Automation gave no text. A fake console stands in for the JARVIS console and the node; no hardware."""
import asyncio
import json

import pytest

from plugin.plugins.desktop import DesktopPlugin
from plugin.plugins.desktop import core

FAKE_TOKEN = "ghp" + "_" + "Ab3" * 12                 # built at run time: no secret-like literal in the repo
CARD = "4111 1111 1111 1111"                            # the standard Luhn-valid test number


class Console:
    """The JARVIS console's owner route as the plugin sees it: records the job bodies, answers like the node would."""

    def __init__(self, local, result=None, lines=None):
        self.local, self.jobs, self.n, self.bodies = local, {}, 0, []
        self.result, self.lines = result, lines

    def __call__(self, method, name, body=None, timeout=20.0):
        if method == "POST" and name == "pc":
            self.bodies.append(body)
            self.n += 1
            jid = f"job{self.n}"
            res = dict(self.result if self.result is not None else {"ok": True})
            if res.get("ok") is True and self.lines is not None:
                f = self.local / f"ocr-{self.n}.json"
                f.write_text(json.dumps({"observed_lines": self.lines}), encoding="utf-8")
                res.update(local_result=str(f), lines=len(self.lines))
            self.jobs[jid] = {"id": jid, "state": "done" if res.get("ok") is True else "failed", "result": res}
            return 202, {"job": jid, "status": "queued"}
        if method == "GET" and name.startswith("job/"):
            return 200, self.jobs[name[4:]]
        if method == "GET" and name == "state":
            return 200, {"approvals": []}
        return 404, {"error": "no such route"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    local = tmp_path / "neko-local"
    local.mkdir()
    monkeypatch.setattr(core, "LOCAL_RESULTS", local)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    monkeypatch.delenv("NEKO_SCREEN_TEXT_TO_MODEL", raising=False)
    real_pc = core.Pc

    class Ctx:
        console = None

        def use(self, console):
            self.console = console
            monkeypatch.setattr(core, "Pc", lambda on_state=None: real_pc(call=console, sleep=lambda s: None, on_state=on_state))
            return console
    c = Ctx()
    c.local = local
    return c


class Host:
    def __init__(self):
        for name in ("_pc", "_run"):
            setattr(self, name, getattr(DesktopPlugin, name).__get__(self))


def call(name, **kw):
    return asyncio.run(getattr(DesktopPlugin, name)(Host(), **kw))


def is_err(r):
    return type(r).__name__ == "Err"


# ------------------------------------------------------------------ read_window_text
@pytest.fixture
def gateways(monkeypatch):
    from plugin.plugins._shared.tests.fakes import Gateways
    g = Gateways(monkeypatch)
    yield g
    g.close()


def test_read_window_text_goes_through_the_kernel_job_and_returns_scrubbed_text(env, monkeypatch, gateways):
    # CHANGED ON PURPOSE (screen-text egress audit, 2026-10-18): this test used to pin "flag on = the pattern-scrubbed OCR text is
    # returned to the model as it is". Flag on now also needs the privacy gateway to confirm it hid names and places in the text
    # (a ready fake gateway stands in here); without it the text is withheld (tests/test_screen_egress.py). Secrets are still
    # removed first, and they never even reach the gateway.
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    gw = gateways.make()
    con = env.use(Console(env.local, {"ok": True}, ["Disk full", f"token {FAKE_TOKEN}", f"card {CARD}", "PASSWORD = hunter2hunter2"]))
    r = call("read_window_text", app="notepad")
    assert not is_err(r), r
    assert con.bodies == [{"op": "diag", "check": "window_ocr", "target": "notepad"}]      # the kernel's own job, nothing else
    v = r.value
    assert v["ok"] and v["lines"] == 4 and "less reliable" in v["source"]
    assert "Disk full" in v["text"] and len(gw.redact_requests) == 1 and gw.redact_requests[0]["names"] is True
    for secret in (FAKE_TOKEN, CARD, "hunter2hunter2"):
        assert secret not in v["text"] and secret not in json.dumps(gw.redact_requests)
    assert set(v["removed"]) >= {"github_token", "card_number", "key_assignment"}      # rule names only, never the values
    assert list(env.local.iterdir()) == []                                              # the node's local file was read once and deleted


def test_without_the_owners_permission_the_text_stays_on_the_laptop(env):
    env.use(Console(env.local, {"ok": True}, ["secret looking words"]))
    r = call("read_window_text", app="notepad")
    assert not is_err(r) and "text" not in r.value and "stayed on this laptop" in r.value["text_note"]
    assert r.value["lines"] == 1 and list(env.local.iterdir()) == []


def test_a_window_holding_a_private_key_is_withheld_whole(env, monkeypatch):
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    env.use(Console(env.local, {"ok": True}, ["-----BEGIN OPENSSH PRIVATE KEY-----", "b3BlbnNzaC1rZXk"]))
    r = call("read_window_text", app="notepad")
    assert not is_err(r) and r.value.get("text", "") == "" and "private key" in r.value["text_note"]


def test_an_empty_ocr_read_says_so(env, monkeypatch):
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    env.use(Console(env.local, {"ok": True}, []))
    r = call("read_window_text", app="notepad")
    assert r.value["lines"] == 0 and "no text" in r.value["text_note"]


@pytest.mark.parametrize("error", ["refused: a password manager, wallet or bank", "no window", "window is minimized",
                                   "capture failed", "ocr failed"])
def test_read_window_text_reports_the_nodes_reason_honestly(env, error):
    env.use(Console(env.local, {"ok": False, "error": error}))
    r = call("read_window_text", app="KeePass")
    assert is_err(r) and error in str(r.error) and "OCR" in str(r.error)


def test_bad_app_names_are_refused_before_any_job(env):
    con = env.use(Console(env.local, {"ok": True}, ["x"]))
    for bad in ("", "a;b", "x" * 41, "note|pad", "../etc"):
        assert is_err(call("read_window_text", app=bad)), bad
    assert con.bodies == []


def test_a_console_that_is_down_is_an_error_not_a_crash(env, monkeypatch):
    def down(*a, **k):
        raise core.DesktopError("the JARVIS console is not answering (URLError); is it running?")
    real = core.Pc
    monkeypatch.setattr(core, "Pc", lambda on_state=None: real(call=down, on_state=on_state))
    r = call("read_window_text", app="notepad")
    assert is_err(r) and "not answering" in str(r.error)


def test_an_approval_wait_is_reported_not_hidden(env):
    class Needs(Console):
        def __call__(self, method, name, body=None, timeout=20.0):
            if method == "POST" and name == "pc" and not (body or {}).get("approval_id"):
                return 200, {"status": "needs_approval", "approval_id": "ap1", "reason": "x"}
            if method == "GET" and name == "state":
                return 200, {"approvals": [{"id": "ap1"}]}
            return super().__call__(method, name, body, timeout)
    real = core.Pc
    con = Needs(env.local, {"ok": True}, ["x"])
    core.Pc = lambda on_state=None: real(call=con, sleep=lambda s: None, on_state=on_state, approval_wait_s=0.0)
    try:
        r = call("read_window_text", app="notepad")
    finally:
        core.Pc = real
    assert is_err(r) and "approval" in str(r.error).lower() and "ap1" in str(r.error)


# ------------------------------------------------------------------ find_on_screen
def test_find_on_screen_returns_an_observed_point(env):
    con = env.use(Console(env.local, {"ok": True, "point": {"x": 120, "y": 44}, "found_by": "ocr", "basis": "observed",
                                      "size": {"width": 800, "height": 600}}, ["Save", "Cancel"]))
    r = call("find_on_screen", app="notepad", text="Save")
    assert con.bodies == [{"op": "diag", "check": "window_locate", "target": "notepad|Save"}]
    v = r.value
    assert (v["found"], v["x"], v["y"], v["basis"], v["found_by"]) == (True, 120, 44, "observed", "ocr")
    assert "less reliable" in v["source"] and v["window"] == {"width": 800, "height": 600}
    assert list(env.local.iterdir()) == []                                              # the OCR text file is deleted unread
    assert "text" not in v and "Cancel" not in json.dumps(v)                            # the window's text never comes back here


def test_an_inferred_point_is_marked_as_a_guess(env):
    env.use(Console(env.local, {"ok": True, "point": {"x": 5, "y": 6}, "found_by": "vision model", "basis": "inferred"}, ["x"]))
    v = call("find_on_screen", app="notepad", text="Submit").value
    assert v["found"] and v["basis"] == "inferred" and "INFERRED" in v["note"] and "not read from the screen" in v["note"]


def test_text_that_is_not_there_is_found_false_not_an_error(env):
    env.use(Console(env.local, {"ok": True, "point": None, "note": "not found by OCR or the vision model"}, ["x"]))
    r = call("find_on_screen", app="notepad", text="Submit")
    assert not is_err(r) and r.value["found"] is False and "not found" in r.value["note"]


def test_find_on_screen_refusals_and_limits_send_no_job(env):
    con = env.use(Console(env.local, {"ok": True}))
    assert is_err(call("find_on_screen", app="notepad", text=""))
    assert is_err(call("find_on_screen", app="note;pad", text="Save"))
    assert is_err(call("find_on_screen", app="notepad", text="two\nlines"))
    assert is_err(call("find_on_screen", app="notepad", text="a|b"))
    r = call("find_on_screen", app="notepad", text="x" * 90)
    assert is_err(r) and "too long" in str(r.error)                                      # the owner route keeps 80 characters
    assert con.bodies == []


def test_find_on_screen_reports_a_refused_window(env):
    env.use(Console(env.local, {"ok": False, "error": "refused: a password manager, wallet or bank"}))
    r = call("find_on_screen", app="KeePass", text="Unlock")
    assert is_err(r) and "refused" in str(r.error)


def test_both_entries_are_read_only_and_say_they_are_unverified_on_hardware():
    for name in ("read_window_text", "find_on_screen"):
        fn = getattr(DesktopPlugin, name)
        assert "UNVERIFIED on real hardware" in str(getattr(fn, "__doc__", "")) + str(getattr(fn, "__wrapped__", fn).__doc__), name


# ------------------------------------------------------------------ the troubleshooter's fallback
class ScriptedHost:
    """A stand-in plugin whose _run answers like the console: window_text (UIA) from a script, window_ocr from the OCR lines."""

    def __init__(self, local, uia, ocr_lines=None, ocr_error=None):
        self.local, self.uia, self.ocr_lines, self.ocr_error, self.bodies, self.n = local, list(uia), ocr_lines, ocr_error, [], 0

    async def _run(self, pc, body, timeout, what=""):
        self.bodies.append(body)
        self.n += 1
        if body.get("check") == "window_text":
            f = self.local / f"window_text-{self.n}.json"
            f.write_text(self.uia.pop(0) if self.uia else "[]", encoding="utf-8")
            return {"state": "done", "result": {"ok": True, "local_result": str(f)}}
        if body.get("check") == "window_ocr":
            if self.ocr_error:
                return {"state": "failed", "result": {"ok": False, "error": self.ocr_error}}
            f = self.local / f"ocr-{self.n}.json"
            f.write_text(json.dumps({"observed_lines": self.ocr_lines or []}), encoding="utf-8")
            return {"state": "done", "result": {"ok": True, "local_result": str(f), "lines": len(self.ocr_lines or [])}}
        if body.get("op") == "repair":
            return {"state": "done", "result": {"ok": True, "backup": "b-1", "undo": "repair restore b-1"}}
        return {"state": "done", "result": {"ok": True, "rows": []}}


def troubleshoot(tmp_path, monkeypatch, **kw):
    local = tmp_path / "neko-local"
    local.mkdir()
    monkeypatch.setattr(core, "LOCAL_RESULTS", local)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    h = ScriptedHost(local, **kw)
    res = asyncio.run(DesktopPlugin.run_troubleshooter(h, app="NekoSampleApp"))
    return res.value, h, list(local.iterdir())


def broken_config(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{\n  "name": "sample",\n  "port": 80x80\n}\n', encoding="utf-8")
    return cfg


def test_ocr_is_the_fallback_when_the_accessibility_tree_gives_nothing(tmp_path, monkeypatch):
    cfg = broken_config(tmp_path)
    rep, h, left = troubleshoot(tmp_path, monkeypatch, uia=["[]", "[]"], ocr_lines=["Neko Sample App", "configuration error", f"File: {cfg}"])
    checks = [b.get("check") for b in h.bodies if b.get("op") == "diag"]
    assert checks.index("window_ocr") > checks.index("window_text")                      # only after UI Automation came back empty
    assert any("from OCR (less reliable" in e for e in rep["evidence"]), rep["evidence"]
    assert "OCR" in rep["cause"] and "less reliable" in rep["cause"]                      # labelled in the cause itself
    assert rep["verified"] is False and rep["fix"] is None                                # OCR text is never the base of a fix
    assert rep["outcome"] == "hypothesis only"
    assert not [b for b in h.bodies if b.get("op") == "repair"] and left == []
    assert cfg.read_text().count("80x80") == 1                                            # the file was not touched


def test_the_same_fault_read_by_the_accessibility_tree_is_still_fixed(tmp_path, monkeypatch):
    cfg = broken_config(tmp_path)
    uia = json.dumps([{"title": "Neko Sample App - configuration error", "dialog": True, "texts": ["could not start.", f"File: {cfg}"]}])
    healthy = json.dumps([{"title": "Neko Sample App", "dialog": False, "texts": []}])
    rep, h, _ = troubleshoot(tmp_path, monkeypatch, uia=[uia, healthy])
    assert not [b for b in h.bodies if b.get("check") == "window_ocr"], "OCR must not run when UI Automation read the window"
    assert rep["verified"] is True and rep["fix"] and [b for b in h.bodies if b.get("op") == "repair"]


def test_a_failed_ocr_is_noted_and_the_diagnosis_goes_on(tmp_path, monkeypatch):
    rep, h, left = troubleshoot(tmp_path, monkeypatch, uia=["[]", "[]"], ocr_error="window is minimized")
    assert any("OCR of the window was tried and gave nothing (window is minimized)" in e for e in rep["evidence"]), rep["evidence"]
    assert rep["outcome"] == "hypothesis only" and left == []


def test_an_ocr_read_with_no_known_fault_does_not_invent_a_cause(tmp_path, monkeypatch):
    rep, h, _ = troubleshoot(tmp_path, monkeypatch, uia=["[]", "[]"], ocr_lines=["Welcome", "Press start"])
    assert "nothing in it matched a known fault" in rep["cause"] and rep["verified"] is False and rep["fix"] is None
