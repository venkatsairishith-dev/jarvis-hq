"""D71: the safe-mode switch. One reader (jarvis/safe_mode.py, copied byte for byte into the node, the kernel and the gateway), one owner tool, and the property
that no consulting place can widen anything: the list of places is fixed here, so a new one cannot be added without a decision."""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from jarvis import safe_mode  # noqa: E402
from jarvis.tools import safe_mode_ctl as ctl  # noqa: E402

JARVIS = ROOT / "jarvis"
COPIES = [JARVIS / "node" / "jarvis_node" / "safe_mode.py", JARVIS / "kernel" / "jarvis_kernel" / "safe_mode.py", JARVIS / "privacy" / "safe_mode.py"]
# Every place that asks active(), and what it does when the answer is yes. A new place needs a line here AND a "never more" test.
CONSULTED_BY = {
    "plugins/desktop/lessons.py": "ignores learned routes, learns nothing",
    "plugins/desktop/browser_playbooks.py": "reads only the shipped playbooks, saves no repair",
    "plugins/desktop/browser_backend.py": "knows no site grant, makes no grant card",
    "plugins/desktop/quick.py": "the rules-before-model shortcut is off",
    "plugins/desktop/taskroute.py": "the open-ended task path is off: a goal stays on the older, simpler paths (D76)",
    "bridge/face_tools.py": "adds the words Safe mode to the HUD status",
    "node/jarvis_node/uia.py": "the UI Automation worker is off",
    "node/jarvis_node/browserext.py": "does not click the extension's connect page",
    "kernel/jarvis_kernel/policy.py": "ignores the auto-click list and the site grants",
    "privacy/gateway.py": "no local-model preference",
    "tools/nightly.py": "only reports it (a warning line in the night's report); changes no check",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("JARVIS_SAFE_MODE", raising=False)
    monkeypatch.delenv("JARVIS_SAFE_MODE_FILE", raising=False)


def test_the_four_copies_are_the_same_bytes():
    original = (JARVIS / "safe_mode.py").read_bytes()
    for c in COPIES:
        assert c.read_bytes() == original, f"{c.relative_to(ROOT)} differs from jarvis/safe_mode.py: copy the original over it"


def test_the_reader_only_reads():
    tree = ast.parse((JARVIS / "safe_mode.py").read_text(encoding="utf-8"))
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not called & {"open", "write_text", "write_bytes", "unlink", "replace", "rename", "mkdir", "remove", "rmtree", "touch", "dump", "system", "Popen", "run"}, called
    assert {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} == {"data_dir", "flag_path", "active"}, "no function here can set or clear the switch"


def test_nothing_but_the_owners_tool_changes_the_switch():
    imports = re.compile(r"^\s*(?:from\s+\S*safe_mode_ctl\s+import|import\s+\S*safe_mode_ctl|from\s+\S+\s+import\s+[^#\n]*\bsafe_mode_ctl\b)", re.M)
    calls = re.compile(r"safe_mode\.(turn_on|turn_off|set_on|set_off|set|clear|enable|disable)\b|safe_mode_ctl\.(?!py)")
    offenders = []
    for p in JARVIS.rglob("*.py"):
        rel = p.relative_to(JARVIS).as_posix()
        if rel.startswith(("vendor/", "third_party/", "face/")) or "/tests/" in rel or p.name.startswith("test_") or p.name in ("safe_mode_ctl.py", "safe_mode.py", "conftest.py"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if imports.search(text) or calls.search(text):
            offenders.append(rel)
    assert offenders == [], f"only the owner's console tool may change the switch: {offenders}"


def test_the_places_that_ask_are_exactly_the_known_ones():
    found = {}
    for p in JARVIS.rglob("*.py"):
        rel = p.relative_to(JARVIS).as_posix()
        if rel.startswith(("vendor/", "third_party/", "face/")) or "/tests/" in rel or p.name.startswith("test_") or p.name in ("safe_mode.py", "safe_mode_ctl.py"):
            continue
        if re.search(r"\bsafe_mode\.active\(\)", p.read_text(encoding="utf-8", errors="replace")):
            found[rel] = True
    assert set(found) == set(CONSULTED_BY), (set(found) ^ set(CONSULTED_BY))


# ------------------------------------------------------------------------------------------------------------------------------ the reader
def test_environment_variable_and_flag_file(tmp_path, monkeypatch):
    flag = tmp_path / "safe_mode.json"
    monkeypatch.setenv("JARVIS_SAFE_MODE_FILE", str(flag))
    assert safe_mode.active() is False
    for v in ("1", "true", "YES", " on "):
        monkeypatch.setenv("JARVIS_SAFE_MODE", v)
        assert safe_mode.active() is True, v
    for v in ("0", "", "off", "no", "maybe"):
        monkeypatch.setenv("JARVIS_SAFE_MODE", v)
        assert safe_mode.active() is False, v
    flag.write_text("anything, even junk", encoding="utf-8")
    assert safe_mode.active() is True, "a file that exists keeps it on: breaking the file cannot switch it off"
    monkeypatch.setenv("JARVIS_SAFE_MODE", "0")
    assert safe_mode.active() is True, "the environment cannot clear the file"


def test_the_file_lives_in_the_data_folder_and_a_test_run_never_sees_the_owners_real_one(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert safe_mode.flag_path() == tmp_path / "safe_mode.json"
    (tmp_path / "safe_mode.json").write_text("{}", encoding="utf-8")
    assert safe_mode.active() is False, "under pytest the data folder's flag is ignored unless a test names its own file (JARVIS_SAFE_MODE_FILE)"
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert safe_mode.active() is True, "outside a test run the same file switches it on"


def test_an_unreadable_folder_counts_as_off_not_as_a_crash(monkeypatch):
    class Locked:
        def exists(self):
            raise PermissionError("no access")
    monkeypatch.setenv("JARVIS_SAFE_MODE_FILE", "x")
    monkeypatch.setattr(safe_mode, "flag_path", lambda: Locked())
    assert safe_mode.active() is False


# ------------------------------------------------------------------------------------------------------------------------------ the owner's tool
def run(argv, tmp_path, monkeypatch, tty=True, answer="yes"):
    monkeypatch.setenv("JARVIS_SAFE_MODE_FILE", str(tmp_path / "safe_mode.json"))
    lines = []
    code = ctl.main(argv, is_tty=lambda: tty, ask=lambda prompt: answer, out=lines.append)
    return code, "\n".join(lines)


def test_the_tool_switches_on_and_off_after_a_typed_yes(tmp_path, monkeypatch):
    code, text = run(["status"], tmp_path, monkeypatch)
    assert code == 0 and "off" in text and not (tmp_path / "safe_mode.json").exists()
    code, text = run(["on"], tmp_path, monkeypatch)
    assert code == 0 and (tmp_path / "safe_mode.json").exists() and "gates stay exactly as it is" in text.replace("every safety gate stays exactly as it is", "gates stay exactly as it is")
    data = json.loads((tmp_path / "safe_mode.json").read_text(encoding="utf-8"))
    assert data["on"] is True and "owner" in data["by"]
    assert safe_mode.active() is True
    code, text = run(["status"], tmp_path, monkeypatch)
    assert "ON" in text and "ignored" in text
    code, text = run(["off"], tmp_path, monkeypatch)
    assert code == 0 and not (tmp_path / "safe_mode.json").exists() and safe_mode.active() is False


def test_the_tool_refuses_without_a_keyboard_and_without_a_yes(tmp_path, monkeypatch):
    code, text = run(["on"], tmp_path, monkeypatch, tty=False)
    assert code == 2 and "keyboard" in text and not (tmp_path / "safe_mode.json").exists(), "a script, a pipe or an agent's shell cannot switch it"
    code, text = run(["on"], tmp_path, monkeypatch, answer="no")
    assert code == 1 and not (tmp_path / "safe_mode.json").exists()
    (tmp_path / "safe_mode.json").write_text("{}", encoding="utf-8")
    code, text = run(["off"], tmp_path, monkeypatch, tty=False)
    assert code == 2 and (tmp_path / "safe_mode.json").exists(), "and it cannot clear it either"
    code, text = run(["off"], tmp_path, monkeypatch, answer="")
    assert code == 1 and (tmp_path / "safe_mode.json").exists()


def test_off_says_when_the_environment_variable_still_holds_it_on(tmp_path, monkeypatch):
    (tmp_path / "safe_mode.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("JARVIS_SAFE_MODE", "1")
    code, text = run(["off"], tmp_path, monkeypatch)
    assert code == 0 and "still set" in text and safe_mode.active() is True


def test_the_tool_leaves_learned_data_alone(tmp_path, monkeypatch):
    lessons = tmp_path / "lessons.json"
    lessons.write_text('{"v":1,"entries":{}}', encoding="utf-8")
    run(["on"], tmp_path, monkeypatch)
    run(["off"], tmp_path, monkeypatch)
    assert lessons.read_text(encoding="utf-8") == '{"v":1,"entries":{}}'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["lessons.json"]
