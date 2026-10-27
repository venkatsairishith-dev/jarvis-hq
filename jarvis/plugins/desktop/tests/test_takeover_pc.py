"""D79: the PC side of a takeover. The control loop (pcagent.py) hands a UAC prompt and a dialog it cannot read to the owner on HIS screen: input stops, one card, a fresh look, carry on.
Fake desktop (fake_desktop.py) and the shared takeover test rig."""
import pytest

from jarvis.plugins._shared.tests.vm2_rig import Rig
from jarvis.plugins.desktop import pcagent, takeover_wiring, taskstate
from jarvis.plugins.desktop.tests.fake_desktop import FakeDesktop, classify_by_words
from jarvis.plugins.desktop.tests.test_pcagent_ladder import Script


@pytest.fixture
def fd():
    return FakeDesktop()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path, with_lab=False)
    yield r
    r.close()


def agent_with(fd, rig, tmp_path, *actions, hook=True):
    ts = taskstate.TaskState(tmp_path / "state.json", tmp_path / "control.json", clock=fd.clock)
    rig.task_state = ts
    return pcagent.PcAgent(fd, Script(*actions), classify=classify_by_words, state=ts, goal="type the note", clock=fd.clock, sleep=fd.sleep,
                           takeover=takeover_wiring.pc_hook(rig.tk) if hook else None)


def test_a_uac_prompt_is_handed_to_the_owner_nothing_is_sent_and_the_task_goes_on_after_he_answers(fd, rig, tmp_path):
    app = fd.add_app("notepad", "Untitled - Notepad")
    app.dialog("User Account Control", ["Do you want to allow this app to make changes to your device?"], {"Yes": lambda a: None})

    def owner(spec):
        sent_while_waiting = [j for j in fd.jobs if j["op"] == "ui"]
        assert sent_while_waiting == [] and rig.tk.holding("pc")
        app.dialogs.clear()                                                       # the owner answered it himself, on his own screen
        return "done"
    rig.owner.script = [owner]
    r = agent_with(fd, rig, tmp_path, {"act": "type", "app": "notepad", "element": "", "text": "the note"}, {"act": "done"}).run("type the note", app="notepad")
    assert r["status"] == "completed" and app.doc == "the note"
    assert rig.notices[0][0]["summary"] == "Needs you: answer the Windows permission prompt"
    assert "admin" not in app.log and rig.tk.holding("pc") is False


def test_a_uac_prompt_that_stays_pauses_with_the_record_and_the_old_form_for_the_chat_model(fd, rig, tmp_path):
    app = fd.add_app("notepad", "Untitled - Notepad")
    app.dialog("User Account Control", ["Do you want to allow this app to make changes to your device?"], {"Yes": lambda a: None})
    rig.owner.script = ["done"] * 6
    r = agent_with(fd, rig, tmp_path, {"act": "click", "app": "notepad", "element": "Yes"}).run("type the note", app="notepad")
    assert r["status"] == "stopped_to_ask" and r["takeover"]["status"] == "exhausted" and r["takeover"]["target"] == "pc" and r["takeover"]["kind"] == "uac"
    assert "ask" in r and not [j for j in fd.jobs if j["op"] == "ui"], "the form is still there and not one job was sent"
    assert len(rig.owner.forms) == 4


def test_the_owner_pressing_stop_ends_the_task(fd, rig, tmp_path):
    app = fd.add_app("notepad", "Untitled - Notepad")
    app.dialog("User Account Control", ["Do you want to allow this app to make changes to your device?"], {"Yes": lambda a: None})
    rig.owner.script = ["stop"]
    r = agent_with(fd, rig, tmp_path, {"act": "done"}).run("type the note", app="notepad")
    assert r["status"] == "cancelled"


def test_an_unreadable_dialog_is_handed_over_too(fd, rig, tmp_path):
    app = fd.add_app("notepad", "Untitled - Notepad")
    app.dialog("", [], {"OK": lambda a: a.log.append("clicked")})

    def owner(spec):
        app.dialogs.clear()
        return "done"
    rig.owner.script = [owner]
    r = agent_with(fd, rig, tmp_path, {"act": "type", "app": "notepad", "element": "", "text": "after"}, {"act": "done"}).run("type the note", app="notepad")
    assert rig.notices[0][0]["summary"] == "Needs you: answer a dialog JARVIS cannot read"
    assert r["status"] == "completed" and app.doc == "after" and "clicked" not in app.log


def test_without_a_takeover_hook_the_old_behaviour_is_unchanged(fd, rig, tmp_path):
    app = fd.add_app("notepad", "Untitled - Notepad")
    app.dialog("User Account Control", ["Do you want to allow this app to make changes to your device?"], {"Yes": lambda a: None})
    r = agent_with(fd, rig, tmp_path, {"act": "done"}, hook=False).run("type the note", app="notepad")
    assert r["status"] == "stopped_to_ask" and "administrator" in r["question"] and "takeover" not in r and rig.owner.forms == []
