"""The desktop decision model: an outage, a timeout and malformed replies become a fallback plan, never a crash or a guessed plan."""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from plugin.plugins._shared import loopback_http
from plugin.plugins.desktop import decide


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("exc", [urllib.error.URLError("refused"), TimeoutError("slow"), ConnectionResetError("reset"),
                                 urllib.error.HTTPError("http://127.0.0.1:8080", 503, "degraded", {}, None)])
def test_outage_or_timeout_is_a_decision_error(monkeypatch, exc):
    def boom(req, timeout=0):
        raise exc
    monkeypatch.setattr(loopback_http, "urlopen", boom)
    with pytest.raises(decide.DecisionError):
        decide.ask_model("open notepad")


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}", b'{"choices": []}', b'{"choices": "x"}', b'{"choices": [null]}',
                                  b'{"choices": [{"message": null}]}', b'{"choices": [{"message": {"content": null}}]}', b'"text"', b"5"])
def test_malformed_replies_are_a_decision_error_or_an_empty_answer(monkeypatch, body):
    monkeypatch.setattr(loopback_http, "urlopen", lambda req, timeout=0: Resp(body))
    try:
        raw, _ = decide.ask_model("open notepad")
    except decide.DecisionError:
        return
    assert raw == ""                                                       # an empty answer; validate() turns it into "not understood"


def test_a_non_loopback_decision_url_is_refused_and_nothing_is_sent(monkeypatch):
    with pytest.raises(decide.DecisionError):
        decide.ask_model("open notepad", url="http://169.254.169.254/v1/chat/completions")


@pytest.mark.parametrize("site", ["file:///C:/Windows/win.ini", "javascript:alert(1)", "data:text/html,<script>x</script>", "ftp://x.example/",
                                  "chrome://settings", "\\\\server\\share", "C:\\Windows\\System32\\cmd.exe", "view-source:http://x", "about:blank",
                                  "vbscript:x", "", None])
def test_open_website_only_ever_resolves_to_http_or_https(site):
    assert decide.resolve_site(site) is None


def test_open_website_arguments_cannot_be_filled_from_an_earlier_result():
    """{{sN}} fills only content/text/body/message/note/summary: a URL argument is never taken from a tool result."""
    from plugin.plugins.main_agent.planner import PLACEHOLDER_ARGS
    assert not {"site", "url", "link", "address", "target", "app", "path", "query", "request"} & set(PLACEHOLDER_ARGS)
