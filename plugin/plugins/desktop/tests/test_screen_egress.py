"""Screen- and disk-derived text never leaves the desktop plugin unfiltered (audit 2026-10-18, Low: OCR / file-name egress).

SYNTHETIC canaries only (plugin/plugins/_shared/tests/fakes.py): a name, a Windows path to a salary file, an API-key-shaped string,
an e-mail address, a street address. They are planted where the PC would show them (OCR lines, page text, a dialog's words, a
file the dialog names, a node error) and the tests look at every boundary the plugin has:

  * the entry results the model reads (the return value of each entry), field by field;
  * the activity file the icon reads (core.set_activity), and every log record;
  * the error strings returned to the model or the owner;
  * the privacy gateway's HTTP edge (a real local HTTP server): what it is sent, and that it is asked at all.

Cases for every text sink: flag off (the default), flag on with the gateway ready (text arrives gateway-cleared), flag on with the
gateway down / degraded / not confirming names / failing, and the shared helper itself raising (withheld, never raw)."""
import asyncio
import json
import logging

import pytest

from plugin.plugins._shared import screen_text
from plugin.plugins._shared.tests import fakes
from plugin.plugins._shared.tests.fakes import C_EMAIL, C_FILE, C_KEY, C_OCR, C_PATH, C_STREET, leaked
from plugin.plugins.desktop import DesktopPlugin, core

OCR_LINES = [f"Dear {C_OCR},", f"Your file {C_PATH} is ready", f"key = {C_KEY}", f"write to {C_EMAIL}", f"home: {C_STREET}"]


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def gateways(monkeypatch):
    g = fakes.Gateways(monkeypatch)
    yield g
    g.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A console that answers like the node, an activity file the test can read, and the flag off (the default)."""
    local = tmp_path / "neko-local"
    local.mkdir()
    monkeypatch.setattr(core, "LOCAL_RESULTS", local)
    monkeypatch.delenv("NEKO_SCREEN_TEXT_TO_MODEL", raising=False)
    activity = tmp_path / "activity.json"
    seen = []
    real = core.set_activity

    def spy(state, detail="", path=None):                 # the real writer, pointed at a temp file; what each call WROTE is kept
        real(state, detail, activity)
        seen.append(json.loads(activity.read_text(encoding="utf-8")))
    monkeypatch.setattr(core, "set_activity", spy)

    class E:
        pass
    e = E()
    e.local, e.activity, e.activity_calls, e.tmp = local, activity, seen, tmp_path
    return e


class Console:
    """The JARVIS console's owner route as the plugin sees it; `jobs` maps (op, check) to the node's result and an optional local file."""

    def __init__(self, local, results):
        self.local, self.results, self.bodies, self.n, self.jobs = local, results, [], 0, {}

    def __call__(self, method, name, body=None, timeout=20.0):
        if method == "POST" and name == "pc":
            self.bodies.append(body)
            self.n += 1
            jid = f"job{self.n}"
            res = dict(self.results.get((body.get("op"), body.get("check") or body.get("action")), {"ok": True}))
            payload = res.pop("_local", None)
            if payload is not None:
                f = self.local / f"local-{self.n}.json"
                f.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
                res["local_result"] = str(f)
            self.jobs[jid] = {"id": jid, "state": "done" if res.get("ok") is True else "failed", "result": res}
            return 202, {"job": jid, "status": "queued"}
        if method == "GET" and name.startswith("job/"):
            return 200, self.jobs[name[4:]]
        if method == "GET" and name == "state":
            return 200, {"approvals": []}
        return 404, {"error": "no such route"}


class Host:
    def __init__(self):
        for name in ("_pc", "_run", "_one"):
            setattr(self, name, getattr(DesktopPlugin, name).__get__(self))


def use(env, monkeypatch, results):
    con = Console(env.local, results)
    real_pc = core.Pc
    monkeypatch.setattr(core, "Pc", lambda on_state=None: real_pc(call=con, sleep=lambda s: None, on_state=on_state))
    return con


def call(name, **kw):
    return asyncio.run(getattr(DesktopPlugin, name)(Host(), **kw))


def everything(env, result, caplog) -> str:
    """Every place the PC's data could surface: the entry's value or error, the activity file and each call to it, the logs."""
    parts = [json.dumps(getattr(result, "value", None), default=str), str(getattr(result, "error", "")), repr(result),
             json.dumps(env.activity_calls, default=str), caplog.text]
    if env.activity.exists():
        parts.append(env.activity.read_text(encoding="utf-8"))
    return "\n".join(parts)


NOT_CLEARED = fakes.NOT_CLEARED          # every way the text may fail to be cleared (shared with the main_agent tests)


# ------------------------------------------------------------------ read_window_text
def _ocr(env, monkeypatch):
    return use(env, monkeypatch, {("diag", "window_ocr"): {"ok": True, "lines": len(OCR_LINES), "_local": {"observed_lines": OCR_LINES}}})


def test_ocr_flag_off_is_the_default_nothing_but_counts_reaches_the_model(env, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    _ocr(env, monkeypatch)
    r = call("read_window_text", app="notepad")
    assert r.value["lines"] == 5 and "text" not in r.value and "stayed on this laptop" in r.value["text_note"]
    assert leaked(everything(env, r, caplog)) == []
    assert list(env.local.iterdir()) == []                      # read once, deleted


def test_ocr_flag_on_and_gateway_ready_the_model_gets_only_what_the_gateway_cleared(env, monkeypatch, gateways, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    gw = gateways.make()
    _ocr(env, monkeypatch)
    r = call("read_window_text", app="notepad")
    v = r.value
    assert "[PERSON_1]" in v["text"] and v["screen_derived"] is True        # the text is there, in its cleared form, and marked as screen text
    assert leaked(everything(env, r, caplog)) == [], leaked(everything(env, r, caplog))
    assert len(gw.redact_requests) == 1 and gw.redact_requests[0]["names"] is True
    sent_to_gateway = json.dumps(gw.redact_requests)
    assert C_KEY not in sent_to_gateway                                     # the secret pass runs before anything else sees the text
    assert C_PATH not in sent_to_gateway and C_FILE not in sent_to_gateway and C_EMAIL not in sent_to_gateway   # the gateway cannot hide these: reduced first
    assert "<a .xlsx file>" in sent_to_gateway


@pytest.mark.parametrize("why,setup", NOT_CLEARED, ids=[w for w, _ in NOT_CLEARED])
def test_ocr_flag_on_but_not_cleared_means_withheld_with_a_neutral_note(env, monkeypatch, gateways, caplog, why, setup):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    setup(gateways, monkeypatch)
    _ocr(env, monkeypatch)
    r = call("read_window_text", app="notepad")
    assert r.value["ok"] and "text" not in r.value and "screen_derived" not in r.value, why
    assert r.value["text_note"] == screen_text.WITHHELD
    assert leaked(everything(env, r, caplog)) == [], why


# ------------------------------------------------------------------ run_browser_action (page text)
def _page(env, monkeypatch):
    page = f"Inbox of {C_OCR}\nstatement {C_PATH}\nmail {C_EMAIL}\n{C_STREET}\nAPI_KEY={C_KEY}"
    return use(env, monkeypatch, {("browser", "read"): {"ok": True, "chars": len(page), "title": f"{C_FILE} - Excel", "url": "https://example.com/x",
                                                         "_local": page}})


def test_page_text_flag_off_stays_on_the_laptop(env, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    _page(env, monkeypatch)
    r = call("run_browser_action", action="read")
    assert "page" not in r.value and "stayed on this laptop" in r.value["page_note"]
    assert r.value["title"] == "<a .xlsx file> - Excel"                      # a file named in a title is reduced to its class
    assert leaked(everything(env, r, caplog)) == []


def test_page_text_flag_on_and_gateway_ready_arrives_cleared(env, monkeypatch, gateways, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    gw = gateways.make()
    _page(env, monkeypatch)
    r = call("run_browser_action", action="read")
    assert "[PERSON_1]" in r.value["page"] and r.value["screen_derived"] is True
    sent = json.dumps(gw.redact_requests)
    assert leaked(everything(env, r, caplog)) == [] and C_KEY not in sent and C_PATH not in sent and C_EMAIL not in sent


@pytest.mark.parametrize("why,setup", NOT_CLEARED, ids=[w for w, _ in NOT_CLEARED])
def test_page_text_flag_on_but_not_cleared_is_withheld(env, monkeypatch, gateways, caplog, why, setup):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    setup(gateways, monkeypatch)
    _page(env, monkeypatch)
    r = call("run_browser_action", action="read")
    assert "page" not in r.value and r.value["page_note"] == screen_text.WITHHELD, why
    assert leaked(everything(env, r, caplog)) == [], why


# ------------------------------------------------------------------ the troubleshooter (window text, file and library names)
class ScriptedHost:
    """A DesktopPlugin stand-in whose _run answers from a script: window reads come from files the node would have written."""

    def __init__(self, local, windows, repair_result=None, errors=None, ocr_lines=None):
        self.local, self.windows, self.bodies, self.whats, self.n = local, windows, [], [], 0
        self.repair_result = repair_result or {"ok": True, "backup": "20261017-101500-abcdef", "undo": "repair restore 20261017-101500-abcdef"}
        self.errors, self.ocr_lines = errors or [], ocr_lines

    async def _run(self, pc, body, timeout, what=""):
        self.bodies.append(body)
        self.whats.append(what)
        if what:                                           # a job waiting for an approval would announce `what` to the icon and the report
            pc.on_state("waiting_for_permission", f"Approve in the JARVIS console with your PIN: {what}")
        chk = body.get("check")
        if chk in ("window_text", "window_ocr"):
            self.n += 1
            f = self.local / f"{chk}-{self.n}.json"
            data = self.windows.pop(0) if chk == "window_text" and self.windows else ([] if chk == "window_text" else {"observed_lines": self.ocr_lines or []})
            if isinstance(data, dict) and chk == "window_text":
                data = [data]                              # one read = a list of windows; a bare dict in the script is one window
            f.write_text(json.dumps(data), encoding="utf-8")
            return {"state": "done", "result": {"ok": True, "local_result": str(f)}}
        if body.get("op") == "repair":
            if self.errors:
                return {"state": "failed", "result": {"ok": False, "error": self.errors[0]}}
            return {"state": "done", "result": dict(self.repair_result)}
        return {"state": "done", "result": {"ok": True, "rows": []}}


def troubleshoot(env, monkeypatch, host_args):
    h = ScriptedHost(env.local, **host_args)
    res = asyncio.run(DesktopPlugin.run_troubleshooter(h, app="NekoSampleApp"))
    return res, h


def _dialog(texts):
    return {"title": f"{C_FILE} - NekoSampleApp", "dialog": True, "texts": texts, "buttons": ["OK"]}


def test_troubleshooter_report_names_no_file_and_nothing_else_from_the_window(env, monkeypatch, caplog):
    """A dialog that names a JSON file on disk (a real file under a folder called SYNTH-Owner) and a missing library: the fix is
    made on the real path (the kernel job needs it), but the report, the steps, the icon and the logs say 'a .json file'."""
    caplog.set_level(logging.DEBUG)
    owner_dir = env.tmp / "SYNTH-Owner"
    owner_dir.mkdir()
    cfg = owner_dir / "Salary-2026.json"
    cfg.write_text('{\n  "name": "sample",\n  "port": 80x80\n}\n', encoding="utf-8")
    first = [_dialog([f"{C_OCR} could not start.", f"File: {cfg}", f"{C_STREET}"])]
    after = [{"title": f"{C_FILE} - NekoSampleApp", "dialog": False, "texts": ["ready"], "buttons": []}]
    res, h = troubleshoot(env, monkeypatch, {"windows": first + after})
    rep = res.value
    repair = [b for b in h.bodies if b.get("op") == "repair"]
    assert repair and repair[0]["target"] == str(cfg)                       # functionality kept: the kernel job gets the real path
    assert rep["verified"] and rep["fix"]["file"] == "a .json file" and rep["outcome"] == "fixed and verified"
    assert rep["backup"] == "20261017-101500-abcdef"                         # ids stay: the owner needs them to undo
    blob = everything(env, res, caplog)
    for needle in ("Salary-2026", "SYNTH-Owner", C_OCR, C_STREET, C_FILE):
        assert needle not in blob, needle
    assert any("a .json file" in w for w in h.whats)                         # even the approval text the icon shows is class-only


@pytest.mark.parametrize("flag", [False, True])
def test_troubleshooter_library_name_from_the_window_is_gated_like_screen_text(env, monkeypatch, gateways, caplog, flag):
    """'<name>.dll was not found' read from a dialog: the name reaches the report only when the owner allowed screen text AND the
    gateway cleared it. Here the name carries a canary the fake gateway replaces."""
    caplog.set_level(logging.DEBUG)
    if flag:
        monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
        gateways.make()
    dll = f"msvcp-{C_OCR}.dll"
    res, h = troubleshoot(env, monkeypatch, {"windows": [_dialog([f"The code execution cannot proceed because {dll} was not found."])]})
    rep = res.value
    assert leaked(everything(env, res, caplog)) == []
    assert ("[PERSON_1]" in rep["cause"]) is flag, rep["cause"]               # flag off: a class; flag on: the gateway-cleared name
    assert rep.get("screen_derived") is (True if flag else None)
    if not flag:
        assert "a .dll library" in rep["cause"]


@pytest.mark.parametrize("why,setup", NOT_CLEARED, ids=[w for w, _ in NOT_CLEARED])
def test_troubleshooter_flag_on_but_not_cleared_withholds_names_and_dialog_words(env, monkeypatch, gateways, caplog, why, setup):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    setup(gateways, monkeypatch)
    dll = f"msvcp-{C_OCR}.dll"
    res, h = troubleshoot(env, monkeypatch, {"windows": [_dialog([f"{dll} was not found"]), ]})
    assert leaked(everything(env, res, caplog)) == [], why
    assert "a .dll library" in res.value["cause"] and "screen_derived" not in res.value
    res, h = troubleshoot(env, monkeypatch, {"windows": [_dialog([f"{C_OCR} cannot continue at {C_STREET}"])]})     # an unrecognised dialog
    assert "its text stayed on this laptop" in res.value["cause"] and leaked(everything(env, res, caplog)) == [], why


def test_troubleshooter_ocr_fallback_text_never_reaches_the_report_unless_cleared(env, monkeypatch, gateways, caplog):
    caplog.set_level(logging.DEBUG)
    lines = [f"The code execution cannot proceed because msvcp-{C_OCR}.dll was not found.", C_STREET, C_EMAIL]
    for flag in (False, True):
        if flag:
            monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
            gateways.make()
        res, h = troubleshoot(env, monkeypatch, {"windows": [[]], "ocr_lines": lines})
        rep = res.value
        assert "from OCR" in rep["cause"] and rep["verified"] is False and rep["fix"] is None
        assert leaked(everything(env, res, caplog)) == [], flag


def test_a_node_error_with_a_path_and_a_key_is_cleaned_in_the_report_the_steps_and_the_icon(env, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    cfg = env.tmp / "Salary-2026.json"
    cfg.write_text('{"port": 80x80}\n', encoding="utf-8")
    err = f"cannot write {C_PATH}: token {C_KEY} refused for {C_EMAIL}"
    res, h = troubleshoot(env, monkeypatch, {"windows": [_dialog([f"File: {cfg}"])], "errors": [err]})
    rep = res.value
    assert rep["outcome"] == "the fix was not applied" and "cannot write" in rep["error"]
    assert leaked(everything(env, res, caplog)) == [] and C_KEY not in rep["error"]


def test_an_unexpected_exception_in_the_flow_reports_its_class_only(env, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    class Boom(ScriptedHost):
        async def _run(self, pc, body, timeout, what=""):
            raise ValueError(f"cannot parse {C_PATH} for {C_OCR} {C_KEY}")
    h = Boom(env.local, [])
    res = asyncio.run(DesktopPlugin.run_troubleshooter(h, app="NekoSampleApp"))
    assert type(res).__name__ == "Err" and "ValueError" in str(res.error)
    assert leaked(everything(env, res, caplog)) == []


# ------------------------------------------------------------------ the generic result and error boundaries
def test_check_pc_rows_name_no_file_and_the_window_title_is_cleaned(env, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    rows = [{"id": 7, "path": C_PATH, "version": "1.2", "window_title": f"{C_FILE} - Excel", "responding": True}]
    use(env, monkeypatch, {("diag", "app_info"): {"ok": True, "check": "app_info", "count": 1, "rows": rows}})
    r = call("check_pc", check="app_info", target="excel")
    assert r.value["ok"] and r.value["rows"][0]["version"] == "1.2" and r.value["rows"][0]["responding"] is True
    assert r.value["rows"][0]["path"] == "<a .xlsx file>"
    assert leaked(everything(env, r, caplog)) == []


def test_a_console_refusal_that_echoes_a_path_and_a_key_is_cleaned_before_it_is_returned(env, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def console(method, name, body=None, timeout=20.0):
        return 400, {"error": f"refused: {C_PATH} with {C_KEY}"}
    real_pc = core.Pc
    monkeypatch.setattr(core, "Pc", lambda on_state=None: real_pc(call=console, sleep=lambda s: None, on_state=on_state))
    r = call("run_pc_action", action="focus", target="notepad|")
    assert type(r).__name__ == "Err" and "refused" in str(r.error)
    assert leaked(everything(env, r, caplog)) == [] and "<a .xlsx file>" in str(r.error)


def test_the_activity_file_never_holds_a_file_name_or_a_secret(env):
    real = core.set_activity
    # `env` replaced core.set_activity by a spy that calls the real writer: go through the spy, as the plugin does
    core.set_activity("failed", f"cannot open {C_PATH} with {C_KEY}")
    assert env.activity.exists() and leaked(env.activity.read_text(encoding="utf-8")) == []
    assert real is not None
