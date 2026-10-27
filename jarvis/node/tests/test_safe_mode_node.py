"""D71: safe mode in the node. The persistent UI Automation worker is off (the PowerShell scripts serve, as before D68) and the node does not answer the
Playwright extension's connect page by itself (the owner clicks it). Nothing is turned ON by safe mode, and no job, class or permission changes."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

from jarvis_node import browserext, safe_mode, uia  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("JARVIS_SAFE_MODE", raising=False)
    monkeypatch.delenv("JARVIS_SAFE_MODE_FILE", raising=False)
    monkeypatch.delenv("JARVIS_UIA_WORKER", raising=False)
    uia.reset()
    yield
    uia.reset()


def test_the_worker_is_off_in_safe_mode_whatever_else_is_set(monkeypatch):
    class Fake:
        def shutdown(self):
            pass
    uia.set_client(Fake())                                   # a test double standing in for a running worker
    assert uia.enabled() is True
    assert uia.client() is not None
    monkeypatch.setenv("JARVIS_SAFE_MODE", "1")
    assert uia.enabled() is False and uia.client() is None, "injected clients included: safe mode means PowerShell serves"
    monkeypatch.setenv("JARVIS_UIA_WORKER", "1")             # asking for the worker does not beat safe mode
    assert uia.enabled() is False


def test_safe_mode_never_turns_the_worker_on(monkeypatch):
    for v in ("", "0", "1", "off", "on"):
        monkeypatch.setenv("JARVIS_UIA_WORKER", v)
        monkeypatch.delenv("JARVIS_SAFE_MODE", raising=False)
        normal = uia.enabled()
        monkeypatch.setenv("JARVIS_SAFE_MODE", "1")
        assert not (uia.enabled() and not normal), v


class _Mcp:
    def start(self): pass
    def handshake(self, timeout=60): pass
    def close(self): pass


def _session(monkeypatch, tmp_path):
    made = []

    class Spy:
        def __init__(self, **kw): made.append(kw)
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(browserext.connectclick, "Approver", Spy)
    monkeypatch.setattr(browserext, "token_file", lambda d: None)
    s = object.__new__(browserext.Session)
    s.profile, s._factory, s._isolated, s.agent, s.secrets = {"dir": "Default"}, None, False, False, {}
    s.work = str(tmp_path / "work")
    s.s = _Mcp()
    return s, made


def test_the_node_answers_the_extension_connect_page_itself_only_outside_safe_mode(monkeypatch, tmp_path):
    s, made = _session(monkeypatch, tmp_path)
    with s:
        pass
    assert len(made) == 1, "normal mode: the approver watches for the extension's page (the owner's auto-click permission)"
    monkeypatch.setenv("JARVIS_SAFE_MODE", "1")
    s, made = _session(monkeypatch, tmp_path)
    with s:
        pass
    assert made == [], "safe mode: nothing clicks for the owner"


def test_the_node_copy_reads_the_flag_file_like_the_original(monkeypatch, tmp_path):
    flag = tmp_path / "safe_mode.json"
    monkeypatch.setenv("JARVIS_SAFE_MODE_FILE", str(flag))
    assert safe_mode.active() is False
    flag.write_text("{}", encoding="utf-8")
    assert safe_mode.active() is True
