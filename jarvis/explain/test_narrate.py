"""Tests for jarvis.explain.narrate, on rows written by the real kernel (store.Kernel with its own audit log)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kernel"))
from jarvis.explain import narrate as N  # noqa: E402

FILLER = re.compile(r"(?i)almost done|nearly there|working on it|just a moment|hang tight|making progress|any second")


def rows_from(kind_payloads):
    return [{"seq": i + 1, "ts": 1_800_000_000 + i, "kind": k, "payload": json.dumps(p)} for i, (k, p) in enumerate(kind_payloads)]


BROWSE = {"tool": "browser", "op": "click", "target": "https://www.example.com/page", "params": {}, "lane": "private"}
TYPE = {"tool": "ui", "op": "type", "target": "notepad|Text editor", "params": {"text": "hi"}, "lane": "private"}


def test_action_phrases():
    assert N.action_phrase(BROWSE) == "click example.com in the browser"
    assert N.action_phrase(TYPE) == "type Text editor in notepad"
    assert N.action_phrase({"tool": "pc", "op": "open_app", "target": "Spotify"}) == "open_app Spotify (pc)"
    assert N.action_phrase(None) == "an action"
    assert len(N.action_phrase({"tool": "fs", "op": "write", "target": "x" * 500})) < 80


def test_voice_speaks_only_when_it_matters():
    rows = rows_from([("request", {"action": BROWSE, "level": "YELLOW_APPROVAL", "reason": "rule browser.click"}),
                      ("approved", {"id": "a1"}),
                      ("authorize", {"action": BROWSE, "level": "YELLOW_APPROVAL", "allowed": True, "reason": "approved, single use"}),
                      ("job_queued", {"id": "j1", "kind": "browser_ext", "node": "laptop"}),
                      ("job_started", {"id": "j1", "node": "laptop"}),
                      ("job_finished", {"id": "j1", "ok": True}),
                      ("model_call", {"provider": "dahl", "lane": "public", "scrubbed": 2})])
    spoken = [N.say(r) for r in rows]
    assert spoken[0] == "I need your approval to click example.com in the browser. It's in the console; approve it with your PIN."
    assert spoken[1:] == [None] * 6                                         # routine progress is not spoken
    for s in filter(None, spoken):
        assert not FILLER.search(s)


def test_refusals_failures_and_endings_are_spoken_honestly():
    assert N.say(rows_from([("request", {"action": BROWSE, "level": "DENY", "reason": "a banking site"})])[0]) == \
        "I won't click example.com in the browser: a banking site."
    assert "stopped before I could type Text editor in notepad: approval expired" in N.say(
        rows_from([("authorize", {"action": TYPE, "allowed": False, "reason": "approval expired"})])[0])
    assert N.say(rows_from([("job_finished", {"id": "j", "ok": False})])[0]).startswith("That step failed")
    assert N.say(rows_from([("job_finished", {"id": "j", "ok": True})])[0]) is None    # a node's report is not news worth a claim
    done = N.say(rows_from([("task_completed", {"id": "t", "owner": "o"})])[0])
    assert "marked completed" in done and "checked" in done and "verified" not in done.split("checked")[0]
    assert N.say(rows_from([("lab_verdict", {"passed": True})])[0]) == "Checked: the result passed."
    assert N.say(rows_from([("lane_preference_set", {"lane": "private", "provider": "groq", "agent": "owner"})])[0]) == \
        "The private lane now prefers groq."


def test_timeline_uses_agui_names_and_skips_routine_green_requests():
    rows = rows_from([("task_created", {"id": "t"}), ("request", {"action": BROWSE, "level": "GREEN", "reason": "rule"}),
                      ("authorize", {"action": BROWSE, "allowed": True, "reason": "allowed by policy"}),
                      ("job_started", {"id": "j"}), ("job_finished", {"id": "j", "ok": True}), ("task_completed", {"id": "t"}),
                      ("vault_write", {"x": 1})])
    tl = N.timeline(rows)
    assert [e["type"] for e in tl] == ["RUN_STARTED", "CUSTOM", "STEP_STARTED", "STEP_FINISHED", "RUN_FINISHED"]
    assert tl[3]["text"] == "The step reports it finished"                  # "reports", never "done"
    assert not any(FILLER.search(e["text"]) for e in tl)


def test_why_a_click_was_allowed():
    rows = rows_from([("request", {"action": BROWSE, "level": "YELLOW_APPROVAL", "reason": "rule browser.click|type|select|press"}),
                      ("approved", {"id": "a1"}),
                      ("authorize", {"action": BROWSE, "level": "YELLOW_APPROVAL", "allowed": True, "reason": "approved, single use"})])
    w = N.why(rows, 3)
    assert w["step"] == "click example.com in the browser" and w["level"] == "YELLOW_APPROVAL"
    assert w["rule"].startswith("rule browser.click") and w["allowed"] is True and w["owner_approved"] is True
    assert "kernel's policy" in w["decided_by"]


def test_why_never_fills_gaps():
    rows = rows_from([("authorize", {"action": TYPE, "allowed": False, "reason": "approval required"})])
    w = N.why(rows, 1)
    assert w["level"] == "not recorded" and w["rule"] == "not recorded" and w["allowed"] is False
    assert N.why(rows, 99) == {"error": "no such event"}
    j = N.why(rows_from([("job_started", {"id": "j9"})]), 1)
    assert j["result"] == "not finished yet" and j["step"].startswith("a job (queue record not found)")
    assert j["checked"].startswith("no")
    t = N.why(rows_from([("task_completed", {"id": "t"})]), 1)
    assert "NOT verified" in t["checked"]


def test_why_a_job_and_a_model_call():
    rows = rows_from([("job_queued", {"id": "j1", "kind": "ui_action", "node": "laptop"}), ("job_started", {"id": "j1"}),
                      ("job_finished", {"id": "j1", "ok": False}), ("model_call", {"provider": "groq", "lane": "private", "scrubbed": {"EMAIL": 1}})])
    j = N.why(rows, 2)
    assert j["step"] == "ui_action job" and j["result"] == "failed"
    m = N.why(rows, 4)
    assert m["model"] == "groq" and m["lane"] == "private" and m["private_values_hidden"] == {"EMAIL": 1}


def test_on_the_real_kernel_audit_log():
    """The kernel writes the rows; the narrator reads them back exactly as stored."""
    from jarvis_kernel.policy import Action
    from jarvis_kernel.store import Kernel
    with tempfile.TemporaryDirectory() as d:
        k = Kernel(d)
        a = Action("ui", "type", "notepad|Text editor", {"text": "hello"}, "private", False)
        dec, aid = k.request(a)
        k.authorize(a, approval_id=None)
        rows = [{"seq": s, "ts": ts, "kind": kind, "payload": payload}
                for s, ts, kind, payload in k.db.execute("SELECT seq, ts, kind, payload FROM audit ORDER BY seq")]
    spoken = [s for s in (N.say(r) for r in rows) if s]
    assert any("I need your approval to type Text editor in notepad" in s for s in spoken)
    assert any("I stopped before I could type Text editor in notepad" in s for s in spoken)
    req_seq = next(r["seq"] for r in rows if r["kind"] == "request")
    w = N.why(rows, req_seq)
    assert w["level"] == "YELLOW_APPROVAL" and w["allowed"] is False and w["how"] == "approval required"
