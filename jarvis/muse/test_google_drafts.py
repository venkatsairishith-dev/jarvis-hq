"""Tests for google_drafts using fake service objects (no network)."""

import ast
import base64
import email
import inspect
import socket
from email import policy

import pytest

from jarvis.muse import google_drafts as gd


class Exec:
    def __init__(self, svc, result):
        self.svc, self.result = svc, result

    def execute(self):
        if self.svc.fail:
            raise RuntimeError("secret body text")
        return self.result


class Group:
    def __init__(self, svc, prefix):
        self.svc, self.prefix = svc, prefix

    def __getattr__(self, name):
        def call(**kw):
            full = f"{self.prefix}.{name}"
            self.svc.calls.append((full, kw))
            res = self.svc.results.get(full, {})
            if callable(res):
                res = res(**kw)
            return Exec(self.svc, res)
        return call


class Svc:
    def __init__(self, results=None, fail=False):
        self.calls, self.results, self.fail = [], results or {}, fail

    def users(self):
        return self

    def drafts(self):
        return Group(self, "drafts")

    def threads(self):
        return Group(self, "threads")

    def events(self):
        return Group(self, "events")

    def getProfile(self, **kw):
        self.calls.append(("getProfile", kw))
        return Exec(self, self.results.get("getProfile", {}))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket, "socket", boom)


@pytest.mark.parametrize("field", ["to", "cc", "subject", "in_reply_to", "references"])
@pytest.mark.parametrize("bad", ["\r", "\n", "\0"])
def test_header_injection(field, bad):
    kw = dict(to=["a@b.com"], subject="s", body="b")
    if field in ("to", "cc"):
        kw[field] = ["a@b.com" + bad + "Bcc: e@e.com"]
    else:
        kw[field] = "x" + bad + "Bcc: e@e.com"
    with pytest.raises(gd.BadMessage):
        gd.build_message(**kw)


def test_validation_limits():
    for bad in ["nope", "a@b", "", "a b@c.com"]:
        with pytest.raises(gd.BadMessage):
            gd.build_message(["a@b.com", bad], "s", "b")
    with pytest.raises(gd.BadMessage):
        gd.build_message(["a@b.com"] * 21, "s", "b")
    with pytest.raises(gd.BadMessage):
        gd.build_message(["a@b.com"], "s" * 301, "b")
    with pytest.raises(gd.BadMessage):
        gd.build_message(["a@b.com"], "s", "x" * (200 * 1024 + 1))
    with pytest.raises(gd.Unsupported):
        gd.build_message(["a@b.com"], "s", "b", attachments=["f"])


def test_round_trip():
    m = gd.build_message(["a@b.com", "Bob <c@d.org>"], "Héllo", "Body ünï",
                         cc=["e@f.com"], in_reply_to="<1@x>", references="<1@x>",
                         reply_to_message_id="t1")
    assert m["threadId"] == "t1"
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(m["raw"]),
                                      policy=policy.default)
    assert parsed["To"] == "a@b.com, Bob <c@d.org>"
    assert parsed["Cc"] == "e@f.com"
    assert parsed["Subject"] == "Héllo"
    assert parsed["In-Reply-To"] == "<1@x>"
    assert parsed["Bcc"] is None
    assert parsed.get_content().strip() == "Body ünï"


def test_save_and_update():
    svc = Svc({"drafts.create": {"id": "d1", "message": {"id": "m1", "threadId": "t1"}},
               "drafts.update": {"id": "d1", "message": {"id": "m2", "threadId": "t1"}}})
    msg = gd.build_message(["a@b.com"], "SECRET", "BODY")
    g = gd.DraftGuard()
    r = g.save_draft(svc, msg)
    assert r["draft_id"] == "d1" and r["message_id"] == "m1" and r["thread_id"] == "t1"
    assert svc.calls[0] == ("drafts.create", {"userId": "me", "body": {"message": msg}})
    r2 = g.update_draft(svc, "d1", msg)
    assert svc.calls[1][0] == "drafts.update" and r2["message_id"] == "m2"
    assert not hasattr(g, "delete_draft")
    for rec in (r, r2):
        assert "SECRET" not in repr(rec) and "BODY" not in repr(rec)
        assert "a@b.com" not in repr(rec)


def _thread(last_from):
    def m(i, frm):
        return {"id": i, "payload": {"headers": [
            {"name": "From", "value": frm}, {"name": "Subject", "value": "S"},
            {"name": "Date", "value": "D"}]}}
    return {"messages": [m("a", "x@y.com"), m("b", last_from)]}


def test_find_unanswered():
    gets = {"t1": _thread("Me <me@me.com>"), "t2": _thread("Other <o@o.com>")}
    svc = Svc({"getProfile": {"emailAddress": "me@me.com"},
               "threads.list": {"threads": [{"id": "t1"}, {"id": "t2"}]},
               "threads.get": lambda **kw: gets[kw["id"]]})
    out = gd.find_unanswered(svc, days=3)
    assert [t["thread_id"] for t in out] == ["t2"]
    lst = [kw for n, kw in svc.calls if n == "threads.list"][0]
    assert lst["q"] == "in:inbox newer_than:3d -from:me" and lst["labelIds"] == ["INBOX"]
    get = [kw for n, kw in svc.calls if n == "threads.get"][0]
    assert get["format"] == "metadata"
    assert not [n for n, _ in svc.calls if "modify" in n]


def test_no_send_capability():
    tree = ast.parse(inspect.getsource(gd))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr != "send"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value != "send"
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            assert "send" not in name.lower()


def test_event():
    ev = gd.build_event("Lunch", "2026-11-01T12:00:00+00:00", "2026-11-01T13:00:00Z",
                        attendees=["a@b.com"])
    assert ev["status"] == "tentative" and "conferenceData" not in ev
    assert ev["reminders"] == {"useDefault": True}
    svc = Svc({"events.insert": {"id": "e1", "htmlLink": "http://l"}})
    r = gd.save_event_draft(svc, ev, allow_attendees=True)
    name, kw = svc.calls[0]
    assert name == "events.insert" and kw["sendUpdates"] == "none"
    assert kw["calendarId"] == "primary"
    assert r["event_id"] == "e1" and "a@b.com" not in repr(r) and "Lunch" not in repr(r)
    with pytest.raises(gd.BadEvent):
        gd.save_event_draft(svc, ev, sendUpdates="all")
    assert len(svc.calls) == 1


def test_event_validation():
    with pytest.raises(gd.BadEvent):
        gd.build_event("x", "2026-11-01T13:00:00", "2026-11-01T12:00:00")
    with pytest.raises(gd.BadEvent):
        gd.build_event("x", "2026-11-01T12:00:00", "2026-11-01T12:00:00")
    with pytest.raises(gd.BadEvent):
        gd.build_event("x", "garbage", "2026-11-01T12:00:00")
    with pytest.raises(gd.BadEvent):
        gd.build_event("x" * 201, "2026-11-01T11:00:00", "2026-11-01T12:00:00")
    with pytest.raises(gd.BadEvent):
        gd.build_event("x", "2026-11-01T11:00:00", "2026-11-01T12:00:00",
                       attendees=["bad"])
    with pytest.raises(gd.BadEvent):
        gd.build_event("x", "2026-11-01T11:00:00", "2026-11-01T12:00:00",
                       attendees=["a@b.com"] * 21)


def test_errors_become_google_error():
    svc = Svc(fail=True)
    msg = gd.build_message(["a@b.com"], "s", "b")
    ev = gd.build_event("x", "2026-11-01T11:00:00", "2026-11-01T12:00:00")
    for call in (lambda: gd.DraftGuard().save_draft(svc, msg),
                 lambda: gd.DraftGuard().update_draft(svc, "d", msg),
                 lambda: gd.find_unanswered(svc),
                 lambda: gd.save_event_draft(svc, ev)):
        with pytest.raises(gd.GoogleError) as ei:
            call()
        assert str(ei.value) == "RuntimeError"
