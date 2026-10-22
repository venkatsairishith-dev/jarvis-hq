"""jarvis/hermes/check_hermes_compat.py must catch the changes that would break our memory plugin in a new Hermes release."""
import importlib.util
import os
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("check_hermes_compat_t", os.path.join(HERE, "..", "check_hermes_compat.py"))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

BASE = """
from abc import ABC, abstractmethod
class MemoryProvider(ABC):
    @property
    @abstractmethod
    def name(self): ...
    @abstractmethod
    def is_available(self): ...
    @abstractmethod
    def initialize(self, session_id, **kwargs): ...
    @abstractmethod
    def get_tool_schemas(self): ...
%s
"""


def fake_hermes(tmp_path, extra=""):
    d = tmp_path / "agent"
    d.mkdir()
    (d / "memory_provider.py").write_text(BASE % textwrap.indent(extra, "    "))
    return str(tmp_path)


def test_a_compatible_release_passes(tmp_path):
    res = C.check(fake_hermes(tmp_path))
    assert res["ok"] and res["abstract"] == ["get_tool_schemas", "initialize", "is_available", "name"], res


def test_a_new_required_method_is_caught_before_deploy(tmp_path):
    res = C.check(fake_hermes(tmp_path, "@abstractmethod\ndef bind_profile(self, home): ...\n"))
    assert not res["ok"] and "cannot build the provider" in res["why"] and "bind_profile" in res["why"]


def test_a_missing_checkout_is_a_clear_failure(tmp_path):
    assert not C.check(str(tmp_path / "nope"))["ok"]
