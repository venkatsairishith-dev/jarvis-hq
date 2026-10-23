"""Tests for jarvis.muse.notify: the inbox keeps everything, the push says nothing but a count."""
from __future__ import annotations

import json

import pytest

from jarvis.muse.notify import MAX_NOTICE_BYTES, Inbox, Notifier, NotifyError, NtfyPush, main, notice_key

TOPIC = "https://ntfy.example/secret-topic-abc123"
HIT = {"kind": "hit", "watch": "w1", "summary": "1 line(s) added, 0 removed",
       "diff": [{"op": "add", "text": "Price now Rs 4,999 at https://shop.example/item"}]}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Recorder:
    def __init__(self, status=200, exc=None):
        self.calls, self.status, self.exc = [], status, exc

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        if self.exc:
            raise self.exc
        return self.status


@pytest.fixture
def topic(tmp_path):
    p = tmp_path / "ntfy.topic"
    p.write_text(TOPIC + "\n", encoding="utf-8")
    return p


def test_inbox_add_and_dedupe(tmp_path):
    box = Inbox(tmp_path / "inbox.jsonl")
    assert box.add(HIT) is not None
    assert box.add(dict(HIT)) is None
    assert len(box.unread()) == 1
    assert box.unread()[0]["notice"] == HIT


def test_inbox_mark_read(tmp_path):
    box = Inbox(tmp_path / "inbox.jsonl")
    row = box.add(HIT)
    box.add({"kind": "paused", "watch": "w2", "reason": "5 failed checks"})
    box.mark_read(row["key"])
    assert [r["notice"]["watch"] for r in box.unread()] == ["w2"]


def test_inbox_skips_torn_line(tmp_path):
    p = tmp_path / "inbox.jsonl"
    box = Inbox(p)
    box.add(HIT)
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"key": "half')                     # a crash mid-write
    assert len(box.unread()) == 1
    assert box.add({"kind": "paused", "watch": "w2", "reason": "x"}) is not None


def test_inbox_missing_file_is_empty(tmp_path):
    assert Inbox(tmp_path / "none.jsonl").unread() == []


def test_inbox_caps_huge_notice(tmp_path):
    p = tmp_path / "inbox.jsonl"
    big = dict(HIT, diff=[{"op": "add", "text": "x" * 1000}] * 100)
    Inbox(p).add(big)
    line = p.read_text(encoding="utf-8").splitlines()[0]
    assert len(line.encode()) <= MAX_NOTICE_BYTES
    row = json.loads(line)
    assert row["notice"]["summary"] == HIT["summary"]
    assert row["notice"]["diff"][0]["op"] == "note"


def test_inbox_caps_huge_summary_too(tmp_path):
    p = tmp_path / "inbox.jsonl"
    Inbox(p).add({"kind": "hit", "watch": "w1", "summary": "y" * 50_000})
    assert len(p.read_text(encoding="utf-8").splitlines()[0].encode()) <= MAX_NOTICE_BYTES


def test_notice_key_stable_and_distinct():
    assert notice_key(HIT) == notice_key(json.loads(json.dumps(HIT)))
    assert notice_key(HIT) != notice_key(dict(HIT, summary="other"))


def test_push_is_content_free(topic):
    rec = Recorder()
    NtfyPush(topic, transport=rec).ping(2)
    url, body, headers = rec.calls[0]
    assert url == TOPIC
    text = body.decode()
    assert text == "JARVIS: 2 new notices. Open JARVIS to read them."
    for leak in ("http", "Rs", "4,999", "shop", "w1"):
        assert leak not in text
    assert set(headers) == {"Title", "Tags", "Priority"}           # no Click or Actions: a ping can never approve or open a link


def test_push_singular(topic):
    rec = Recorder()
    NtfyPush(topic, transport=rec).ping(1)
    assert rec.calls[0][1] == b"JARVIS: 1 new notice. Open JARVIS to read it."


def test_push_rate_limit_coalesces(topic):
    rec, clock = Recorder(), Clock()
    push = NtfyPush(topic, transport=rec, clock=clock, max_per_hour=2)
    assert push.ping() and push.ping()
    assert push.ping() is False and push.ping() is False          # held back, counted
    assert len(rec.calls) == 2
    clock.t += 3601
    assert push.ping()
    assert rec.calls[-1][1].startswith(b"JARVIS: 3 new notices")


@pytest.mark.parametrize("content", ["http://ntfy.example/t", "https://a b", "", "ftp://x"])
def test_push_topic_must_be_one_https_url(tmp_path, content):
    p = tmp_path / "t"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(NotifyError):
        NtfyPush(p, transport=Recorder()).ping()


def test_push_missing_topic_file(tmp_path):
    with pytest.raises(NotifyError, match="missing"):
        NtfyPush(tmp_path / "nope", transport=Recorder()).ping()


def test_push_error_never_contains_topic(topic):
    push = NtfyPush(topic, transport=Recorder(exc=OSError(f"cannot reach {TOPIC}")))
    with pytest.raises(NotifyError) as ei:
        push.ping()
    assert "secret-topic" not in str(ei.value) and "ntfy.example" not in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


def test_push_refused_status_keeps_count(topic):
    rec = Recorder(status=500)
    push = NtfyPush(topic, transport=rec)
    with pytest.raises(NotifyError, match="HTTP 500"):
        push.ping(2)
    rec.status = 200
    push.ping(1)
    assert rec.calls[-1][1].startswith(b"JARVIS: 3 new notices")


def test_notifier_files_then_pings_new_only(tmp_path, topic):
    rec = Recorder()
    n = Notifier(Inbox(tmp_path / "inbox.jsonl"), NtfyPush(topic, transport=rec))
    assert n.deliver([HIT]) == 1
    assert n.deliver([HIT]) == 0                                    # a retried check pings nothing
    assert len(rec.calls) == 1


def test_notifier_failed_push_loses_nothing(tmp_path, topic):
    box = Inbox(tmp_path / "inbox.jsonl")
    n = Notifier(box, NtfyPush(topic, transport=Recorder(exc=OSError("down"))))
    assert n.deliver([HIT]) == 1
    assert len(box.unread()) == 1
    assert n.push_errors == ["push failed (OSError)"]


def test_notifier_without_push(tmp_path):
    n = Notifier(Inbox(tmp_path / "inbox.jsonl"))
    assert n.deliver([HIT, {"kind": "paused", "watch": "w2", "reason": "r"}]) == 2


def test_cli_lists_unread(tmp_path, capsys):
    p = tmp_path / "inbox.jsonl"
    Inbox(p).add(HIT)
    assert main(["--inbox", str(p), "--list"]) == 0
    out = capsys.readouterr().out
    assert "hit" in out and "w1" in out and "1 line(s) added" in out
