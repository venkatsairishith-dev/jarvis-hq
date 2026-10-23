"""Research fetch and search egress (tool inventory pass F, 2026-10-19): SSRF targets, DNS rebinding, and what a search query may carry."""
from __future__ import annotations

import json
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.plugins.research import core, fetch

BAD_URLS = [
    "http://169.254.169.254/latest/meta-data/", "http://[::ffff:169.254.169.254]/", "http://127.0.0.1/", "http://127.1/",
    "http://2130706433/", "http://0x7f000001/", "http://0177.0.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:7f00:1]/", "http://0.0.0.0/", "http://10.0.0.5/", "http://172.16.0.1/", "http://172.31.255.254/",
    "http://192.168.1.1/", "http://100.64.0.1/", "http://[fe80::1]/", "http://[fc00::1]/", "http://localhost./", "http://LOCALHOST/",
    "http://user:pw@example.com/", "http://example.com@127.0.0.1/", "file:///etc/passwd", "file:///C:/Windows/win.ini",
    "ftp://example.com/", "gopher://127.0.0.1/", "data:text/html,x", "javascript:alert(1)", "http://example.com:22/",
    "https://example.com:8443/", "http://example.com:99999/", "http://127。0。0。1/", "", "http://", "not a url", None,
]


@pytest.mark.parametrize("url", BAD_URLS)
def test_every_ssrf_and_odd_target_is_refused_before_any_connection(url, monkeypatch):
    def never(*a, **k):
        raise AssertionError("a connection was attempted")
    monkeypatch.setattr(socket.socket, "connect", never)
    with pytest.raises(fetch.FetchError):
        fetch.check_url(url)


def test_a_name_that_rebinds_to_a_private_address_at_connect_time_is_refused(monkeypatch):
    """check_url sees a public answer; urllib then resolves again and the name answers 127.0.0.1 (DNS rebinding)."""
    hits = []

    class Secret(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<p>" + b"internal admin page " * 30 + b"</p>")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Secret)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    answers = iter([[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],            # check_url: public
                    [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]])             # connect: private
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: next(answers) if h == "rebind.example" else real(h, *a, **k))
    try:
        with pytest.raises(fetch.FetchError, match="not public"):
            fetch._get("http://rebind.example/")
        assert hits == []                                       # nothing was requested from the internal server
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_mixed_answer_with_one_private_address_is_refused(monkeypatch):
    mixed = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 80))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: mixed)
    assert fetch.public_host("mixed.example") is False
    with pytest.raises(fetch.FetchError):
        fetch._pinned_socket("mixed.example", 80, 1)


def test_a_redirect_to_a_private_address_is_refused(monkeypatch):
    class Redir(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Redir)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
                        if h == "redir.example" else real(h, *a, **k))
    monkeypatch.setattr(fetch, "_OPENER", urllib.request.build_opener(
        fetch._NoRedirect, urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{srv.server_address[1]}"})))
    try:
        with pytest.raises(fetch.FetchError, match="not public"):
            fetch._get("http://redir.example/")
    finally:
        srv.shutdown()
        srv.server_close()


QUERIES = [
    ("what is https://example.com/docs/page?token=AbCdEf1234567890&email=priya@example.com#frag", "token", "priya", "frag"),
    ("see https://user:hunter2@example.com/a/b for details", "hunter2", "user:", "@"),
    ("read file:///C:/Users/Priya/secret.txt and http://169.254.169.254/latest?x=1 now", "Priya", "file://", "x=1"),
    ("lookup sk_live_AbCdEfGhIjKlMnOpQrStUvWx12345 pricing", "sk_live", "AbCdEf", ""),
    ("session 0123456789abcdef0123456789abcdef0123 api limits", "0123456789abcdef", "", ""),
]


@pytest.mark.parametrize("question,a,b,c", QUERIES)
def test_search_queries_carry_no_link_parameters_or_tokens(question, a, b, c):
    out = core.search_safe(question)
    for gone in (a, b, c):
        if gone:
            assert gone not in out
    assert out.strip() and "169.254" not in out and "://" not in out.replace("https://example.com", "").replace("http://", "http:")


def test_search_safe_keeps_ordinary_questions_and_other_scripts_unchanged():
    for q in ("How should a beginner learn web security?", "ಬೆಂಗಳೂರು ಹವಾಮಾನ ಬಗ್ಗೆ ಹೇಳಿ", "प्रिया शर्मा की रिपोर्ट 2026", "OWASP top 10 2025",
              "what does https://example.com/docs/page say about caching"):
        assert core.search_safe(q) == q


def test_run_searches_only_the_safe_query_and_the_planned_ones_too():
    from .test_core import DRAFT, fake_fetch, fake_search, fake_verifier
    searched = []
    q = "explain https://example.com/a?token=AbCdEf1234567890 caching"
    out = core.run(q, depth="quick", redact=lambda t: t, search=lambda s, n: searched.append(s) or fake_search(s, n), fetch=fake_fetch,
                   plan_llm=lambda s, u: json.dumps({"queries": ["see https://example.com/b?session=ZZZZ1111 more", "caching headers"]}),
                   write_llm=lambda s, u: json.dumps(DRAFT), verifier=fake_verifier)
    assert searched and all("token" not in s and "session" not in s and "?" not in s for s in searched)
    assert any("link parameters" in n for n in out["notes"])


def test_a_question_that_is_only_a_token_is_not_searched():
    from .test_core import fake_fetch, fake_search
    with pytest.raises(core.ResearchError, match="Nothing is left"):
        core.run("AbCdEf1234567890AbCdEf1234567890", search=fake_search, fetch=fake_fetch, plan_llm=None, write_llm=lambda s, u: "{}",
                 verifier=None, redact=lambda t: t)
