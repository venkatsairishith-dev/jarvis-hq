"""Policy tests for the builder runner. They call no API and read no key.
Run: python -m pytest jarvis/sdk_builder/test_run_builder.py -q -p no:cacheprovider"""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("run_builder", HERE / "run_builder.py")
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)

WT = Path(r"D:\jarvis\hq-neko-sdk")


def test_refused_after_expiry_and_for_other_models_and_over_cap():
    assert rb.check_allowed(dt.date(2026, 10, 31), "claude-sonnet-5-5", 0, 5) is None
    assert "expired" in rb.check_allowed(dt.date(2026, 11, 1), "claude-sonnet-5-5", 0, 5)
    assert "not allowed" in rb.check_allowed(dt.date(2026, 10, 25), "claude-opus-5-5", 0, 5)
    assert rb.check_allowed(dt.date(2026, 10, 25), "claude-haiku-5-5", 0, 5) is None
    assert "cap" in rb.check_allowed(dt.date(2026, 10, 25), "claude-sonnet-5-5", 72.0, 5)


def test_secrets_folder_is_denied_in_any_tool():
    assert rb.deny_reason("Read", {"file_path": r"D:\jarvis-secrets\claude.key"}, WT)
    assert rb.deny_reason("Bash", {"command": "type D:\\\\jarvis-secrets\\\\keys.json"}, WT)
    assert rb.deny_reason("Grep", {"pattern": "x", "path": "D:/jarvis-secrets"}, WT)


def test_file_tools_stay_inside_the_worktree():
    assert rb.deny_reason("Write", {"file_path": str(WT / "jarvis" / "x.py")}, WT) is None
    assert rb.deny_reason("Write", {"file_path": r"D:\jarvis\hq-neko\x.py"}, WT)
    assert rb.deny_reason("Edit", {"file_path": str(WT / ".." / "other" / "x.py")}, WT)


def test_dangerous_bash_is_denied_and_tests_are_allowed():
    for cmd in ("git push origin main", "git remote add x y", "ssh ubuntu@host ls", "curl http://x", "rm -rf /", "taskkill /f /im python.exe"):
        assert rb.deny_reason("Bash", {"command": cmd}, WT), cmd
    assert rb.deny_reason("Bash", {"command": "python -m pytest jarvis/tools -q"}, WT) is None
    assert rb.deny_reason("Bash", {"command": "git status"}, WT) is None


def test_web_tools_denied_and_key_scrubbed():
    assert rb.deny_reason("WebFetch", {"url": "http://x"}, WT)
    assert "sk-ant-SECRET" not in rb.scrub("token sk-ant-SECRET here", "sk-ant-SECRET")
