"""D65: run_pc_action goes through closed_loop.arun_step: the structured route, then ONE retry by the next route (keyboard, then coordinates the cascade confirmed), then a question.
A fake console answers each kernel job as the test says; no hardware."""
from __future__ import annotations

import asyncio
import json

import pytest

from jarvis.plugins.desktop import DesktopPlugin, core
from jarvis.plugins.desktop.tests.test_ocr_entries import Host as _Host, is_err


class Console:
    """The console's owner route: records each job body and answers with `rule(body)` -> result dict."""

    def __init__(self, rule):
        self.rule, self.bodies, self.n, self.jobs = rule, [], 0, {}

    def __call__(self, method, name, body=None, timeout=20.0):
        if method == "POST" and name == "pc":
            self.bodies.append(body)
            self.n += 1
            res = dict(self.rule(body))
            state = res.pop("_state", "done" if res.get("ok") else "failed")
            self.jobs[f"j{self.n}"] = {"id": f"j{self.n}", "state": state, "result": res}
            return 202, {"job": f"j{self.n}", "status": "queued"}
        if method == "GET" and name.startswith("job/"):
            return 200, self.jobs[name[4:]]
        if method == "GET" and name == "state":
            return 200, {"approvals": []}
        return 404, {"error": "no such route"}


@pytest.fixture
def use(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    monkeypatch.setattr(core, "LOCAL_RESULTS", tmp_path)
    real = core.Pc

    def install(console):
        monkeypatch.setattr(core, "Pc", lambda on_state=None: real(call=console, sleep=lambda s: None, on_state=on_state))
        return console
    return install


class Host(_Host):
    def __init__(self):
        super().__init__()
        self._one = DesktopPlugin._one.__get__(self)
        self._sync_port = lambda loop: DesktopPlugin._sync_port(self, loop)


def act(**kw):
    return asyncio.run(DesktopPlugin.run_pc_action(Host(), **kw))


def checks(con):
    return [(b.get("action") or b.get("op"), b.get("target"), b.get("text")) for b in con.bodies]


def test_a_click_that_the_node_confirms_is_one_job_and_one_route(use):
    con = use(Console(lambda b: {"ok": True}))
    r = act(action="click", target="notepad|Save")
    assert not is_err(r) and r.value["ok"] and r.value["route"] == "uia" and len(con.bodies) == 1 and not r.value.get("retried")


def test_a_click_by_name_that_fails_is_retried_once_by_the_keyboard(use):
    def rule(b):
        if b.get("action") == "click":
            return {"ok": False, "error": "RuntimeError", "reason": "no element"}
        return {"ok": True}
    con = use(Console(rule))
    r = act(action="click", target="notepad|Save")
    assert r.value["ok"] and r.value["route"] == "keyboard" and r.value["retried"]
    assert checks(con) == [("click", "notepad|Save", ""), ("focus", "notepad|", ""), ("key", "notepad|", "ctrl+s")]


def test_with_no_other_route_a_failed_click_ends_as_a_question_not_a_second_click(use):
    con = use(Console(lambda b: {"ok": False, "error": "RuntimeError", "reason": "no element"}))
    r = act(action="click", target="notepad|Frobnicate")
    assert not r.value["ok"] and r.value["ask"]["kind"] == "jarvis_ask" and "I tried uia" in r.value["note"]
    assert [c[0] for c in checks(con)].count("click") == 1


def test_the_pixel_route_is_used_only_when_two_rungs_agree(use):
    def rule(b):
        if b.get("action") == "click":
            return {"ok": False, "error": "RuntimeError", "reason": "no element"}
        if b.get("op") == "diag" and b.get("check") == "window_vision":
            return {"ok": True, "ocr_point": {"x": 100, "y": 40}, "vision_point": {"x": 108, "y": 44}}
        return {"ok": True}
    con = use(Console(rule))
    r = act(action="click", target="notepad|Frobnicate")
    assert r.value["ok"] and r.value["route"] == "coordinates" and ("click_at", "notepad|100,40", "") in checks(con)
    con = use(Console(lambda b: {"ok": False, "error": "RuntimeError", "reason": "no element"} if b.get("action") == "click" else
                      {"ok": True, "ocr_point": {"x": 100, "y": 40}, "vision_point": {"x": 700, "y": 500}} if b.get("check") == "window_vision" else {"ok": True}))
    r = act(action="click", target="notepad|Frobnicate")
    assert not r.value["ok"] and not any(c[0] == "click_at" for c in checks(con))


def test_a_sensitive_label_is_never_clicked_from_pixels(use):
    con = use(Console(lambda b: {"ok": False, "error": "RuntimeError", "reason": "no element"} if b.get("action") == "click" else
                      {"ok": True, "ocr_point": {"x": 100, "y": 40}, "vision_point": {"x": 101, "y": 41}} if b.get("check") == "window_vision" else {"ok": True}))
    r = act(action="click", target="shop|Pay now")
    assert not r.value["ok"] and not any(c[0] == "click_at" for c in checks(con))


def test_typing_that_did_not_land_is_refocused_then_pasted(use):
    state = {"n": 0}

    def rule(b):
        if b.get("action") == "type":
            state["n"] += 1
            return {"ok": False, "error": "RuntimeError", "reason": "text not in field"}
        return {"ok": True}
    con = use(Console(rule))
    r = act(action="type", target="notepad|*", text="hello")
    assert r.value["ok"] and r.value["route"] == "paste" and state["n"] == 2
    assert [c[0] for c in checks(con)] == ["type", "focus", "type", "focus", "type_focused"]


def test_a_shortcut_that_fails_is_done_through_the_menu(use):
    con = use(Console(lambda b: {"ok": False, "error": "RuntimeError", "reason": "target is not the foreground window"} if b.get("action") == "key" else {"ok": True}))
    r = act(action="key", target="notepad|", text="ctrl+s")
    assert r.value["ok"] and r.value["route"] == "menu" and [(c[0], c[1]) for c in checks(con)][1:] == [("click", "notepad|File"), ("click", "notepad|Save")]


def test_a_held_action_is_not_retried_and_keeps_its_approval(use, monkeypatch):
    real = core.Pc

    class Held(Console):
        def __call__(self, method, name, body=None, timeout=20.0):
            if method == "POST" and name == "pc":
                self.bodies.append(body)
                return 200, {"status": "needs_approval", "approval_id": "ap1", "level": "yellow", "reason": "held"}
            return super().__call__(method, name, body, timeout)
    con = Held(lambda b: {"ok": True})
    monkeypatch.setattr(core, "Pc", lambda on_state=None: real(call=con, sleep=lambda s: None, on_state=on_state, approval_wait_s=0.0))
    r = act(action="click", target="notepad|Pay now")
    assert r.value.get("state") == "waiting_for_permission" and r.value.get("approval_id") == "ap1" and len(con.bodies) == 1


def test_a_plain_action_with_no_other_route_is_one_job(use):
    con = use(Console(lambda b: {"ok": True, "opened": "notepad", "verified": "verified"}))
    r = act(action="open_app", target="notepad")
    assert r.value["ok"] and len(con.bodies) == 1 and r.value["route"] == "uia"
