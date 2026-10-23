"""Tests for jarvis.muse.runner: durable checks, merge with console edits, one runner, checked fetching."""
from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

import pytest

from jarvis.egress.ssrf_guard import Verdict
from jarvis.muse import runner as R
from jarvis.muse.notify import Inbox, Notifier
from jarvis.muse.watch import Watch, load, save


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def mk(id="w1", url="https://shop.example/a", cond=None, **kw):
    return Watch(id=id, url=url, condition=cond or {"type": "changed"}, interval_s=100, **kw)


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "watches.json", tmp_path / "inbox.jsonl"


def runner(paths, fetch, clock=None):
    wpath, ipath = paths
    return R.WatchRunner(wpath, Notifier(Inbox(ipath)), fetch, clock or Clock())


def test_run_once_checks_due_and_saves(paths):
    wpath, ipath = paths
    save(wpath, [mk(), mk("w2", next_check=5000)])
    r = runner(paths, lambda url: "price 10")
    assert r.run_once() == 0                                       # first check: a baseline, no hit
    by_id = {w.id: w for w in load(wpath)}
    assert by_id["w1"].last_snapshot == "price 10" and by_id["w1"].next_check == 1100
    assert by_id["w2"].last_snapshot is None                       # not due


def test_change_files_a_notice(paths):
    wpath, ipath = paths
    save(wpath, [mk()])
    clock = Clock()
    pages = iter(["price 10", "price 9"])
    r = runner(paths, lambda url: next(pages), clock)
    r.run_once()
    clock.t += 100
    assert r.run_once() == 1
    rows = Inbox(ipath).unread()
    assert rows[0]["notice"]["kind"] == "hit" and rows[0]["notice"]["watch"] == "w1"


def test_state_persisted_when_later_fetch_fails(paths):
    wpath, _ = paths
    save(wpath, [mk("a"), mk("b")])

    def fetch(url):
        if url.endswith("/b"):
            raise OSError("down")
        return "ok"
    save(wpath, [mk("a"), mk("b", url="https://shop.example/b")])
    runner(paths, fetch).run_once()
    by_id = {w.id: w for w in load(wpath)}
    assert by_id["a"].last_snapshot == "ok"
    assert by_id["b"].failures == 1 and by_id["b"].history[-1] == {"at": 1000.0, "ok": False, "error": "OSError"}


def test_crash_between_deliver_and_save_does_not_duplicate(paths):
    wpath, ipath = paths
    save(wpath, [mk(last_snapshot="old")])
    r = runner(paths, lambda url: "new")
    r._store = lambda *a: (_ for _ in ()).throw(SystemExit("crash"))
    with pytest.raises(SystemExit):
        r.run_once()
    assert len(Inbox(ipath).unread()) == 1
    r2 = runner(paths, lambda url: "new")                          # restart: the watch was never saved, so it re-checks
    assert r2.run_once() == 0
    assert len(Inbox(ipath).unread()) == 1


def test_watch_deleted_during_fetch_stays_deleted(paths):
    wpath, _ = paths
    save(wpath, [mk("a"), mk("b", next_check=9999)])

    def fetch(url):
        R.update_watches(wpath, lambda ws: [w for w in ws if w.id != "a"])
        return "x"
    runner(paths, fetch).run_once()
    assert [w.id for w in load(wpath)] == ["b"]


def test_watch_added_during_fetch_is_kept(paths):
    wpath, _ = paths
    save(wpath, [mk("a")])

    def fetch(url):
        R.update_watches(wpath, lambda ws: ws + [mk("new", next_check=9999)])
        return "x"
    runner(paths, fetch).run_once()
    assert {w.id for w in load(wpath)} == {"a", "new"}
    assert {w.id: w for w in load(wpath)}["a"].last_snapshot == "x"


def test_console_pause_during_fetch_wins(paths):
    wpath, _ = paths
    save(wpath, [mk("a")])

    def fetch(url):
        def pause(ws):
            ws[0].state = "paused"
            return ws
        R.update_watches(wpath, pause)
        return "x"
    runner(paths, fetch).run_once()
    w = load(wpath)[0]
    assert w.state == "paused" and w.last_snapshot is None


def test_second_runner_refused(paths):
    a, b = runner(paths, str), runner(paths, str)
    a.acquire()
    try:
        with pytest.raises(R.RunnerBusy):
            b.acquire()
    finally:
        a.release()
    b.acquire()
    b.release()


def test_next_wake(paths):
    wpath, _ = paths
    save(wpath, [mk("a", next_check=1500), mk("b", next_check=1200, state="paused")])
    assert runner(paths, str).next_wake() == 1500
    save(wpath, [])
    assert runner(paths, str).next_wake() == 1000 + R.MAX_SLEEP_S


def test_html_to_text():
    html = ("<html><head><title>T</title><style>p{}</style></head><body><script>alert(1)</script>"
            "<h1>Deal</h1><p>Price  &amp; <b>Rs 499</b></p><ul><li>a</li><li>b</li></ul><noscript>x</noscript></body></html>")
    assert R.html_to_text(html) == "Deal\nPrice & Rs 499\na\nb"


def test_egress_proxy_config(tmp_path):
    p = tmp_path / "egress.json"
    p.write_text(json.dumps({"proxy": "http://100.125.91.51:4750"}))
    assert R.egress_proxy(p) == "http://100.125.91.51:4750"
    with pytest.raises(R.FetchRefused):
        R.egress_proxy(tmp_path / "missing.json")
    p.write_text(json.dumps({"proxy": "socks5://x"}))
    with pytest.raises(R.FetchRefused):
        R.egress_proxy(p)


def test_repo_egress_config_has_a_proxy():
    assert R.egress_proxy().startswith("http://")


# ---- fetch, with the opener faked -----------------------------------------------------------------------------------------------------

class FakeResp(io.BytesIO):
    def __init__(self, body: bytes, ctype="text/html; charset=utf-8"):
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = ctype


def fake_opener(monkeypatch, responses):
    seen = []

    class Opener:
        def open(self, req, timeout):
            seen.append(req.full_url)
            r = responses[req.full_url]
            if isinstance(r, Exception):
                raise r
            return r
    monkeypatch.setattr(R.urllib.request, "build_opener", lambda *h: Opener())
    return seen


def allow_public(url):
    return Verdict(False, "private address") if "internal" in url else Verdict(True, ip="93.184.216.34")


def redirect(to):
    h = Message()
    h["Location"] = to
    return urllib.error.HTTPError("u", 302, "Found", h, None)


def test_fetch_html_to_text(monkeypatch):
    fake_opener(monkeypatch, {"https://a.example/": FakeResp(b"<p>Hello</p><p>World</p>")})
    assert R.make_fetch("http://proxy:1", allow_public)("https://a.example/") == "Hello\nWorld"


def test_fetch_refuses_private_before_any_request(monkeypatch):
    seen = fake_opener(monkeypatch, {})
    with pytest.raises(R.FetchRefused, match="private"):
        R.make_fetch("http://proxy:1", allow_public)("http://internal.example/")
    assert seen == []


def test_fetch_checks_every_redirect(monkeypatch):
    seen = fake_opener(monkeypatch, {"https://a.example/": redirect("http://internal.example/admin")})
    with pytest.raises(R.FetchRefused):
        R.make_fetch("http://proxy:1", allow_public)("https://a.example/")
    assert seen == ["https://a.example/"]


def test_fetch_follows_relative_redirect(monkeypatch):
    fake_opener(monkeypatch, {"https://a.example/x": redirect("/y"), "https://a.example/y": FakeResp(b"ok", "text/plain")})
    assert R.make_fetch("http://proxy:1", allow_public)("https://a.example/x") == "ok"


def test_fetch_redirect_loop_stops(monkeypatch):
    fake_opener(monkeypatch, {"https://a.example/x": redirect("/x")})
    with pytest.raises(R.FetchRefused, match="too many redirects"):
        R.make_fetch("http://proxy:1", allow_public)("https://a.example/x")


def test_fetch_refuses_binary_and_huge(monkeypatch):
    fake_opener(monkeypatch, {"https://a.example/f.zip": FakeResp(b"PK", "application/zip"),
                              "https://a.example/big": FakeResp(b"x" * (R.MAX_BYTES + 1), "text/plain")})
    f = R.make_fetch("http://proxy:1", allow_public)
    with pytest.raises(R.FetchRefused, match="not a text page"):
        f("https://a.example/f.zip")
    with pytest.raises(R.FetchRefused, match="too large"):
        f("https://a.example/big")


def test_fetch_uses_the_proxy_for_both_schemes(monkeypatch):
    handlers = []
    monkeypatch.setattr(R.urllib.request, "build_opener", lambda *h: handlers.extend(h) or object())
    R.make_fetch("http://proxy:1", allow_public)
    proxy = next(h for h in handlers if isinstance(h, R.urllib.request.ProxyHandler))
    assert proxy.proxies == {"http": "http://proxy:1", "https": "http://proxy:1"}


def test_cli_once(paths, monkeypatch, capsys):
    wpath, ipath = paths
    save(wpath, [mk()])
    monkeypatch.setattr(R, "make_fetch", lambda proxy: (lambda url: "page"))
    assert R.main(["--watches", str(wpath), "--inbox", str(ipath), "--once"]) == 0
    assert "0 new notice(s)" in capsys.readouterr().out
    assert load(wpath)[0].last_snapshot == "page"


def test_cli_busy(paths, monkeypatch):
    wpath, ipath = paths
    monkeypatch.setattr(R, "make_fetch", lambda proxy: str)
    holder = runner(paths, str)
    holder.acquire()
    try:
        assert R.main(["--watches", str(wpath), "--inbox", str(ipath), "--once"]) == 2
    finally:
        holder.release()
