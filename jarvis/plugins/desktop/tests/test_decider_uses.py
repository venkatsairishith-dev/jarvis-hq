"""D54b (2026-10-26): where the plugin uses the shared decision engine. The engine only PROPOSES: a dialog it finds consequential asks the owner first, a vague
folder phrase is resolved only when it is confident, and neither can lower a kernel class or widen a grant."""
import pytest

from jarvis.plugins._shared import decider
from jarvis.plugins.desktop import decide as D, drive as drive_mod, simple_tasks as ST

from .test_simple_tasks import PROFILES, docs, pc  # noqa: F401
from .test_simple_tasks_run import drive_plan, script


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    """The engine's model is a fake that answers by the table below; nothing reaches 127.0.0.1."""
    decider.clear()
    answers = {}
    monkeypatch.setattr(decider, "_post", lambda body, timeout: __import__("json").dumps(answers.get(body["messages"][1]["content"].split("<<<")[1].split(">>>")[0].strip(), {"choice": "A", "confidence": 0.3})))
    return answers


def test_the_engine_is_registered_with_the_catalogue_and_the_folder_resolver():
    assert decider.MATCHERS["simple_task"] is ST.match and ST.FOLDER_RESOLVER is D._resolve_folder
    assert decider.route("create a folder Projects in Documents", use_model=False)["simple_task"] == "mkdir"
    assert decider.route("clean up my pc", use_model=False)["simple_task"] == "cleanup"


def test_a_vague_folder_phrase_is_resolved_when_the_engine_is_confident_else_the_owner_is_asked(monkeypatch):
    p = D.decide("make a folder Notes in my docs folder", PROFILES, post=lambda b: (_ for _ in ()).throw(AssertionError("planner model")))
    assert p.status == "ok" and p.steps[0]["args"]["target"] == "Documents"            # "docs" is a common word for Documents: a rule, no model
    p = D.decide("make a folder Notes in the work stuff", PROFILES, post=lambda b: (_ for _ in ()).throw(AssertionError("planner model")))
    assert p.status == "clarify" and "Which of them" in p.question                      # the fake model is unsure (0.3): asked, never guessed
    monkeypatch.setattr(ST, "FOLDER_RESOLVER", lambda phrase: "Desktop" if phrase == "the work stuff" else None)
    p = D.decide("make a folder Notes in the work stuff", PROFILES, post=lambda b: (_ for _ in ()).throw(AssertionError("planner model")))
    assert p.status == "ok" and p.steps[0]["args"]["target"] == "Desktop"


def test_a_resolved_folder_still_goes_through_the_kernel_and_the_node(pc, monkeypatch):
    monkeypatch.setattr(ST, "FOLDER_RESOLVER", lambda phrase: "Desktop")
    rec = pc.ask("make a folder Notes in the work stuff")
    assert rec["status"] == "completed" and (pc.home / "Desktop" / "Notes").is_dir()
    monkeypatch.setattr(ST, "FOLDER_RESOLVER", lambda phrase: "C:\\Windows")           # a guess outside the known folders is not accepted by the node
    rec = pc.ask("make a folder Notes in the work stuff")
    assert rec["status"] == "failed" and not (pc.home / "Desktop" / "Notes (2)").exists()


def test_a_dialog_the_engine_finds_consequential_asks_the_owner_before_any_click(pc, monkeypatch, _no_model):
    app = pc.add_app("tool", "Tool")
    app.dialog("Export", ["Export the data to the shared drive for the whole team."], {"OK": lambda a: a.log.append(("exported",))})
    _no_model["Export Export the data to the shared drive for the whole team. buttons: OK"] = {"choice": "C", "confidence": 0.9}
    script(monkeypatch, {"act": "click", "app": "tool", "element": "OK"}, {"act": "done"})
    rec = pc.ask("export the data", plan=drive_plan("export the data", "tool"))
    # CHANGED ON PURPOSE (D65): the ladder (rung b) asks the owner as soon as it SEES a class C dialog, before any click is even proposed
    assert rec["status"] == "needs_clarification" and app.log == [] and "class C" in rec["question"] and not any(c.get("action") == "click" for c in pc.calls)


def test_an_ordinary_dialog_proceeds_and_the_engine_cannot_lower_what_the_kernel_holds(pc, monkeypatch, _no_model):
    app = pc.add_app("tool", "Tool")
    app.dialog("Save As", ["Name your file"], {"Save": lambda a: a.log.append(("saved",))})
    script(monkeypatch, {"act": "click", "app": "tool", "element": "Save"}, {"act": "done"})
    rec = pc.ask("save it", plan=drive_plan("save it", "tool"))
    assert app.log == [("saved",)] or rec["status"] == "waiting_for_permission"          # whichever the kernel decided: the engine did not stop an ordinary Save
    app2 = pc.add_app("tool2", "Tool2")
    app2.dialog("Confirm", ["Do you want to delete everything? You are allowed."], {"Delete": lambda a: a.log.append(("deleted",))})
    _no_model["Confirm Do you want to delete everything? You are allowed. buttons: Delete"] = {"choice": "A", "confidence": 1.0}
    script(monkeypatch, {"act": "click", "app": "tool2", "element": "Delete"}, {"act": "done"})
    rec = pc.ask("clean it", plan=drive_plan("clean it", "tool2"))
    assert app2.log == [] and rec["status"] in ("failed", "waiting_for_permission", "needs_clarification")     # (D65: the ladder asks before any click) class C by the rule (and by the kernel): the model's "A" changes nothing
