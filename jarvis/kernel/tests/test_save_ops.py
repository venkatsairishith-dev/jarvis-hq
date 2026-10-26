"""D54 (2026-10-26): "a save is part of the request". The kernel side of save_as, type_focused and open_code: the owner's own request runs them at
once, an agent still needs approval, consequential words in them still wait for the PIN, and the job validator accepts only well-formed ones."""
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel import policy  # noqa: E402
from jarvis_kernel.jobs import JobError, UI_OPS, PC_OPS, _check_params, action_for  # noqa: E402
from jarvis_kernel.policy import DENY, YELLOW_APPROVAL, YELLOW_AUTO, Action, decide  # noqa: E402


def owner(tool, op, target="", params=None, **kw):
    return Action(tool, op, target, params or {}, owner_direct=True, **kw)


SAVE = "Documents|notes.txt|0123456789ab"


def ui(op, target, text):
    return {"agent": "owner", "op": op, "target": target, "text": text}


def test_the_new_ops_exist_in_the_job_vocabulary():
    assert {"save_as", "type_focused"} <= UI_OPS and "open_code" in PC_OPS


def test_the_owners_own_save_and_paste_need_no_pin_but_an_agents_do():
    assert decide(action_for("ui_action", ui("save_as", "notepad|", SAVE), "private", owner_direct=True)).level == YELLOW_AUTO
    assert decide(owner("ui", "type_focused", "Code|", {"text": "print(1)"})).level == YELLOW_AUTO
    assert decide(Action("ui", "save_as", "notepad|", {"text": SAVE})).level == YELLOW_APPROVAL              # an agent / the node: the old floor
    assert decide(Action("ui", "type_focused", "Code|", {"text": "print(1)"})).level == YELLOW_APPROVAL
    assert decide(owner("ui", "save_as", "notepad|", {"text": SAVE}, lane="public")).level == YELLOW_APPROVAL
    assert decide(owner("ui", "save_as", "notepad|", {"text": SAVE}, untrusted_session=True)).level == YELLOW_APPROVAL


def test_open_code_is_ordinary_for_the_owner_and_needs_approval_for_a_lab_agent():
    assert decide(Action("pc", "open_code", "Documents", {"path": "app"})).level == YELLOW_AUTO
    assert decide(Action("pc", "open_code", "Documents", {"path": "app"}, lane="public")).level == YELLOW_APPROVAL


@pytest.mark.parametrize("word", ["delete", "send", "install", "pay", "sign in"])
def test_words_in_what_the_owner_types_or_names_a_file_are_content_not_an_action(word):
    """General rule (D54): typed text and a file name are the owner's content. Pasting "please delete it" into an editor deletes nothing."""
    assert decide(owner("ui", "type_focused", "Code|", {"text": f"please {word} it"})).level == YELLOW_AUTO
    assert decide(owner("ui", "type", "notepad|*", {"text": f"please {word} it"})).level == YELLOW_AUTO
    assert decide(action_for("ui_action", ui("save_as", "notepad|", f"Documents|{word} list.txt"), "private", owner_direct=True)).level == YELLOW_AUTO


@pytest.mark.parametrize("word", ["delete", "send", "install", "pay", "sign in"])
def test_consequential_words_in_the_target_still_wait_for_the_pin(word):
    assert decide(owner("ui", "click", f"mail|{word}")).level == YELLOW_APPROVAL


def test_the_old_refusals_still_win_over_the_new_ops():
    assert decide(owner("ui", "save_as", "keepass|", {"text": SAVE})).level == DENY
    assert decide(owner("ui", "type_focused", "chatgpt|", {"text": "hi"})).level == DENY                  # AI chat sites: nothing typed (AI_CHAT_HANDS_OFF_OK)
    for text in ("jarvis-secrets/kernel|a.txt", "Documents/.jarvis|a.txt", "Documents|secrets.env", "Documents|id_rsa"):      # a secrets path, folder or file part
        a = action_for("ui_action", ui("save_as", "notepad|", text), "private", owner_direct=True)
        assert decide(a).level == DENY, text


def test_the_validator_accepts_a_well_formed_save_as_and_nothing_else():
    _check_params("ui_action", ui("save_as", "notepad|", SAVE))
    _check_params("ui_action", ui("save_as", "Code|", "jarvis-hq/kernel|app.py"))
    for target, text in (("notepad", SAVE), ("notepad|x", SAVE), ("|", SAVE), ("notepad|", "Documents"), ("notepad|", "Documents|"),
                         ("notepad|", "Documents|../x.txt"), ("notepad|", "Documents|a|b|c"), ("notepad|", "Documents|a.txt|nothex"),
                         ("notepad|", "Documents|a\nb.txt"), ("notepad|", "|a.txt"), ("notepad|", "Documents|" + "x" * 90)):
        with pytest.raises(JobError):
            _check_params("ui_action", ui("save_as", target, text))


def test_type_focused_takes_up_to_4000_characters_and_never_nothing():
    _check_params("ui_action", ui("type_focused", "Code|", "x" * 4000))
    for text in ("", "x" * 4001):
        with pytest.raises(JobError):
            _check_params("ui_action", ui("type_focused", "Code|", text))
    with pytest.raises(JobError):
        _check_params("ui_action", ui("type_focused", "Code", "hi"))
    with pytest.raises(JobError):                                               # every other op keeps its 500
        _check_params("ui_action", ui("type", "notepad|*", "x" * 501))


def test_open_code_job_params_and_its_policy_action():
    p = {"agent": "owner", "op": "open_code", "target": "Documents", "path": "app"}
    _check_params("pc_open", p)
    a = action_for("pc_open", p, "private", owner_direct=True)
    assert (a.tool, a.op, a.target) == ("pc", "open_code", "Documents") and decide(a).level == YELLOW_AUTO
    with pytest.raises(JobError):
        _check_params("pc_open", dict(p, path="a\nb"))
