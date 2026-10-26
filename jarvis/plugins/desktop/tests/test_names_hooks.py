"""D62: the desktop plugin's door into the learned names: `quick_command` answers "what names do you know" and "forget X" by rule (no model, so the names never
reach one), and the plain commands still work. Fakes only (test_quick's host and fake PC)."""
import asyncio

import pytest

from jarvis.plugins._shared import names as N
from jarvis.plugins.desktop import DesktopPlugin, core

from .test_goal import Fakes, _Host
from .test_quick import PROFILES


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_QUICK_PATH", "1")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("JARVIS_NAMES_FILE", raising=False)
    monkeypatch.setattr(core, "profiles", lambda: PROFILES)
    st = N.get_store(tmp_path / "names.json")
    N.observe("my wife is Priya", store=st, manual=[])
    N.observe("my brother is Ravi", store=st, manual=[])
    return st


def ask(text):
    f = Fakes()
    res = asyncio.run(DesktopPlugin.quick_command(_Host(f), text=text))
    return res.value, f


def test_what_names_do_you_know_is_answered_by_rule_with_no_pc_job(data):
    v, f = ask("what names do you know")
    assert v["handled"] is True and v["status"] == "completed" and v["planned_by"] == "rules" and f.pc_runs == []
    assert "Priya: wife" in v["reply"] and "Ravi: brother" in v["reply"], "the cleaner of the result must not hide the owner's own names from him"


def test_forget_a_name_by_rule_and_it_is_not_learned_again_by_inference(data):
    v, f = ask("forget Priya")
    assert v["handled"] is True and "forgot Priya" in v["reply"] and data.find("Priya") is None and f.pc_runs == []
    assert "priya" in data.tombstones()


def test_forget_it_and_unknown_names_fall_through_to_the_normal_path(data):
    assert ask("forget it")[0] == {"handled": False}
    assert ask("forget Zebra")[0] == {"handled": False}
    assert data.find("Priya") and data.find("Ravi")


def test_plain_commands_are_unchanged(data):
    v, f = ask("open notepad")
    assert v["handled"] is True and v["reply"] == "Opened Notepad." and f.pc_runs
