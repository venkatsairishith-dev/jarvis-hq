"""Tests for jarvis.bridge.readonly_mcp: read-only by construction, free text only after redaction, and a real MCP round trip."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from jarvis.bridge import readonly_mcp as M
from jarvis.muse.ideas import IdeaStore
from jarvis.muse.notify import Inbox
from jarvis.muse.taskstate import TaskStore
from jarvis.muse.watch import Watch, save
from jarvis.tools.doctor import Finding

ROOT = Path(__file__).resolve().parents[2]
NOW = 1_800_000_000.0


def fake_redact(text: str) -> str:
    return text.replace("ravi@example.com", "[EMAIL_1]").replace("98765 43210", "[PHONE_1]")


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "muse"
    d.mkdir()
    s = TaskStore(d / "tasks.db", clock=lambda: NOW)
    t1 = s.enqueue("send_email", {"title": "Reply to ravi@example.com", "to": "ravi@example.com", "body": "secret body"})
    s.claim("w")
    s.request_approval(t1, "w", {"draft": "x"})
    s.enqueue("fill_pdf", {"title": "Scholarship form"})
    s.close()
    box = Inbox(d / "inbox.jsonl")
    box.add({"kind": "hit", "watch": "t7", "summary": "price now 9,999 at https://shop.example/x?token=abc"})
    box.add({"kind": "paused", "watch": "w2", "reason": "5 failed checks"})
    save(d / "watches.json", [Watch("t7 price", "https://shop.example/item?session=SECRET", {"type": "price_below"}, 3600,
                                    history=[{"at": NOW - 60, "ok": True, "summary": "1 line(s) added, 0 removed", "diff": []}],
                                    next_check=NOW + 3600),
                              Watch("dead", "https://b.example/", {"type": "changed"}, 3600, state="paused", failures=5)])
    IdeaStore(d / "ideas.json", clock=lambda: NOW).refresh(
        [{"id": "m1", "label": "Inbox", "sender": "Ravi", "subject": "Coffee? call 98765 43210", "body": "available Tuesday?", "attachments": []}])
    vault = tmp_path / "vault"
    (vault / "daily").mkdir(parents=True)
    (vault / "daily" / "2027-01-15-brief.md").write_text(
        "## Morning brief, 2027-01-15\n\n**Needs you:** 1 item(s), listed below.\n\n### Today\n- 09:00: call ravi@example.com\n"
        "### Waiting for your approval\n- send_email (task 1234)\n", encoding="utf-8")
    return d, vault


def views(data, redact=fake_redact, **kw):
    d, vault = data
    return M.Views(d, vault, redact=redact, clock=lambda: NOW, **kw)


def test_status_counts(data):
    st = views(data).status()
    assert st["tasks_by_state"] == {"waiting_approval": 1, "queued": 1} and st["needs_owner"] == 1
    assert st["unread_notices"] == 2 and st["watches"] == {"active": 1, "paused": 1} and st["new_ideas"] == 1


def test_tasks_never_return_parameters(data):
    rows = views(data).tasks()
    blob = json.dumps(rows)
    assert "secret body" not in blob and "ravi@example.com" not in blob and "[EMAIL_1]" in blob
    assert {r["state"] for r in rows} == {"waiting_approval", "queued"}
    assert [r["kind"] for r in views(data).tasks("waiting_approval")] == ["send_email"]


def test_without_a_redactor_free_text_is_withheld(data):
    v = views(data, redact=None)
    blob = json.dumps([v.tasks(), v.watches(), v.ideas()])
    assert "ravi" not in blob.lower() and "Scholarship" not in blob and M.WITHHELD in blob
    b = v.brief("2027-01-15")
    assert "ravi" not in json.dumps(b) and b["needs_you"] == "Needs you: 1 item(s), listed below."
    assert b["sections"] == [{"section": "Today", "items": 1}, {"section": "Waiting for your approval", "items": 1}]


def test_a_failing_redactor_withholds(data):
    def boom(_):
        raise RuntimeError("model missing")
    assert views(data, redact=boom).ideas()[0]["title"] == M.WITHHELD


def test_notices_are_counts_only(data):
    n = views(data).notices()
    assert n == {"unread": 2, "by_kind": {"hit": 1, "paused": 1}}


def test_watches_show_host_not_address(data):
    w = views(data).watches()
    blob = json.dumps(w)
    assert "SECRET" not in blob and "session" not in blob and w[0]["site"] == "shop.example"
    assert w[0]["last_result"] == "1 line(s) added, 0 removed" and w[1]["next_check"] is None


def test_ideas_titles_redacted_no_evidence(data):
    i = views(data).ideas()
    assert len(i) == 1 and "[PHONE_1]" in i[0]["title"] and "evidence" not in i[0]
    assert "Tuesday" not in json.dumps(i)                                      # the mail body never leaves


def test_brief(data):
    v = views(data)
    b = v.brief("2027-01-15")
    assert "[EMAIL_1]" in b["brief"] and "ravi@" not in b["brief"]
    assert v.brief("2027-01-16")["brief"] is None
    assert "error" in v.brief("../../etc/passwd")
    assert "error" in M.Views(data[0], None, redact=fake_redact).brief()


def test_posture_lists_only_problems(data):
    v = views(data, posture=lambda: [Finding("a", "ok", "x"), Finding("b", "warn", "y"), Finding("c", "fail", "z")])
    assert [p["level"] for p in v.posture()] == ["warn", "fail"]


def test_every_call_is_logged_and_the_store_is_untouched(data):
    d, _ = data
    before = (d / "tasks.db").read_bytes()
    v = views(data)
    v.status(), v.tasks(), v.notices()
    assert (d / "tasks.db").read_bytes() == before
    log = [json.loads(x)["tool"] for x in (d / "mcp_reads.jsonl").read_text().splitlines()]
    assert log == ["status", "tasks", "notices"]


def test_missing_data_is_empty_not_an_error(tmp_path):
    v = M.Views(tmp_path / "none", None, redact=fake_redact)
    assert v.status()["tasks_by_state"] == {} and v.tasks() == [] and v.watches() == [] and v.ideas() == []
    assert not (tmp_path / "none" / "tasks.db").exists()


def test_server_exposes_only_read_only_tools(data):
    server = M.build_server(views(data))
    tools = asyncio.run(server.list_tools())
    assert sorted(t.name for t in tools) == sorted(M.READ_ONLY_TOOLS)
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
        assert not any(w in t.name for w in ("send", "approve", "write", "delete", "run", "set", "create", "update"))


def test_real_stdio_round_trip(data, tmp_path):
    """Start the server as a CLI would and call two tools through the MCP client."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    d, vault = data
    params = StdioServerParameters(command=sys.executable, args=["-m", "jarvis.bridge.readonly_mcp", "--data", str(d), "--vault", str(vault)],
                                   cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)})

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = sorted(t.name for t in (await s.list_tools()).tools)
                status = await s.call_tool("jarvis_status", {})
                tasks = await s.call_tool("jarvis_tasks", {"state": "waiting_approval"})
                return names, status, tasks

    names, status, tasks = asyncio.run(asyncio.wait_for(go(), 60))
    assert names == sorted(M.READ_ONLY_TOOLS)
    assert json.loads(status.content[0].text)["needs_owner"] == 1
    text = " ".join(c.text for c in tasks.content if hasattr(c, "text"))
    assert "send_email" in text and "ravi@example.com" not in text           # no redactor in this environment: withheld
