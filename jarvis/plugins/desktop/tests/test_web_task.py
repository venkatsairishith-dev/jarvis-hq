"""D64: how the owner's "make a deck" reaches the browser agent: the plan (decide.py), the step's check (coordinator.verify), the journal line of each browser action,
the result the chat model sees (web_runner.py), and the ask panel a plugin uses (owner_ask)."""
import asyncio
import json
import threading

import pytest

from jarvis.node.jarvis_node import journal as J
from jarvis.plugins._shared import owner_ask
from jarvis.plugins.desktop import coordinator as coord
from jarvis.plugins.desktop import decide
from jarvis.plugins.desktop import journal_client as jc
from jarvis.plugins.desktop import ppt_flow, web_runner
from jarvis.plugins.desktop.tests.chromeharness import Harness
from jarvis.plugins.desktop.tests.deckfixtures import decks_playbook, make_pptx, row, shortlist

PROFILES = [{"profile": "Default", "name": "Satish", "account": "sat***@gmail.com"}]


# ------------------------------------------------------------------------------------------------------------------------------ the plan
@pytest.mark.parametrize("request_,topic,slides", [
    ("make a 3 slide deck about solar power for schools", "solar power for schools", 3),
    ("create a presentation on the history of jazz", "the history of jazz", 5),
    ("Please generate five slides about electric buses", "electric buses", 5),
    ("prepare a 10-slide presentation about our quarterly plan and open it", "our quarterly plan", 10),
    ("make me a PowerPoint about healthy eating", "healthy eating", 5)])
def test_a_deck_request_is_one_web_task_step_planned_by_rule(request_, topic, slides):
    p = decide.decide(request_, PROFILES, post=lambda body: (_ for _ in ()).throw(AssertionError("the small model must not be asked")))
    assert p.status == "ok" and p.model == "rule" and len(p.steps) == 1
    st = p.steps[0]
    assert st["tool"] == "web_task" and st["topic"] == topic and st["slides"] == slides and "free web generator" in st["post"]


def test_a_deck_without_a_topic_is_one_question():
    p = decide.decide("make slides", PROFILES)
    assert p.status == "clarify" and "about" in p.question


@pytest.mark.parametrize("request_", ["open the deck I made", "show me the slides", "what is on my screen", "write a report on electric buses"])
def test_other_requests_are_not_turned_into_a_deck(request_):
    assert decide.deck_rule(request_, PROFILES) is None


def test_the_model_cannot_make_a_file_through_research_any_more():
    raw = json.dumps({"steps": [{"tool": "research", "query": "solar power", "deliverable": "presentation"}]})
    p = decide.validate(raw, PROFILES, "research solar power and make slides about it")
    assert p.steps[0]["tool"] == "web_task" and p.steps[0]["topic"]
    raw = json.dumps({"steps": [{"tool": "research", "query": "electric buses", "deliverable": "report"}]})
    p = decide.validate(raw, PROFILES, "research electric buses and write a report")
    assert p.steps[0]["tool"] == "research" and p.steps[0]["deliverable"] == "none" and any("report file" in x for x in p.problems)
    assert "deliverable" not in json.dumps(decide._ARGS["research"]).replace('"none"', "") or decide._ARGS["research"]["deliverable"]["enum"] == ["none"]


def test_a_web_task_the_model_names_is_checked_and_capped():
    p = decide.validate(json.dumps({"steps": [{"tool": "web_task", "query": "tides", "text": "99"}]}), PROFILES, "make a deck about tides")
    assert p.steps[0]["slides"] == 20 and p.steps[0]["topic"] == "tides"
    p = decide.validate(json.dumps({"steps": [{"tool": "web_task", "query": "", "text": ""}]}), PROFILES, "do it")
    assert p.status == "clarify"


# ------------------------------------------------------------------------------------------------------------------------------ the check
def test_a_deck_step_passes_only_on_a_file_that_was_made_and_read():
    step = {"tool": "web_task", "topic": "x", "slides": 3}
    ok = {"ok": True, "verified_files": 1, "facts": ["pptx file of 3000 bytes with 3 slides", "made on gamma.app in the owner's Chrome"], "site": "gamma.app"}
    passed, checked, unc = coord.verify(step, ok)
    assert passed and "3 slides" in checked and unc == ""
    for bad in ({"ok": True, "verified_files": 0, "facts": ["made on gamma.app"]}, {"ok": True, "verified_files": 1, "facts": []},
                {"ok": False, "verified_files": 0, "needs_owner": {"detail": "waiting for your PIN"}}, {"ok": False, "stopped": True, "error": "stopped"}):
        passed, checked, unc = coord.verify(step, bad)
        assert not passed, bad
    assert "PIN" in coord.verify(step, {"ok": False, "verified_files": 0, "needs_owner": {"detail": "waiting for your PIN"}, "error": "waiting for your PIN"})[2]


# ------------------------------------------------------------------------------------------------------------------------------ the journal
@pytest.fixture
def jr(tmp_path):
    j = J.Journal(tmp_path / "journal")
    jc.use(j)
    yield j
    jc.use(None)


def test_each_browser_action_is_one_line_with_no_content(jr):
    for ev in ({"kind": "action", "host": "gamma.app", "act": "type", "role": "textbox", "ok": True, "res": "", "ms": 12, "cls": "B"},
               {"kind": "action", "host": "gamma.app", "act": "click", "role": "button", "ok": False, "res": "approval", "ms": 3, "cls": "C"},
               {"kind": "action", "host": "gamma.app", "act": "observe", "ok": True, "ms": 5},
               {"kind": "verify", "host": "gamma.app", "act": "type", "ok": True},
               {"kind": "wall", "host": "gamma.app", "wall": "captcha"},
               {"kind": "action", "host": "gamma.app", "act": "download", "ok": True, "res": "", "ms": 900, "cls": "B"}):
        jc.record_web_step(ev)
    steps = jr.steps(10)
    labels = sorted(e["step_label"] for e in steps)
    assert labels == ["A step only the owner can do on gamma.app", "Click on gamma.app", "Collect a download on gamma.app", "Type into a field on gamma.app"]
    held = next(e for e in steps if e["step_label"].startswith("Click"))
    assert held["class"] == "C" and held["verified"] == "failed" and "PIN" in held["result"]
    raw = "".join(f.read_text(encoding="utf-8") for f in jr.root.glob("journal-*.jsonl"))
    assert "solar" not in raw and "password" not in raw.lower()


def test_without_a_journal_recording_a_web_step_does_nothing():
    jc.use(None)
    jc.record_web_step({"kind": "action", "host": "a.test", "act": "click", "ok": True})
    jc.record_web_step("not an event")


# ------------------------------------------------------------------------------------------------------------------------------ the whole step
@pytest.fixture
def h(tmp_path, monkeypatch):
    rig = Harness(tmp_path, monkeypatch)
    rig.web.signed_in.add("decks.test")
    rig.web.files[("decks.test", "/exports/deck.pptx")] = make_pptx(3)
    yield rig
    rig.close()


def test_the_step_returns_verified_facts_and_a_preview_copy_and_nothing_else(h, tmp_path):
    import json as _json
    h.grant("decks.test", ["signin_existing_session", "accept_terms", "download_to_quarantine"])
    b = tmp_path / "pb"
    b.mkdir()
    (b / "decks.test.make_deck.json").write_text(_json.dumps(decks_playbook()), encoding="utf-8")
    from jarvis.plugins.desktop import browser_playbooks as PB
    r = web_runner.run_deck({"tool": "web_task", "topic": "solar panels for schools", "slides": 3, "formats": "pptx"}, pc_run=h.pc_run(), store=PB.Store(b, tmp_path / "l"),
                            shortlist=shortlist(row("decks.test", 1)), owner=h.call, deliverables_dir=str(tmp_path / "deliverables"), journal=jc.record_web_step)
    assert r["ok"] and r["verified_files"] == 1 and r["site"] == "decks.test" and r["file"].endswith(".pptx")
    assert any("3 slides" in f for f in r["facts"])
    blob = json.dumps(r)
    assert str(h.outputs) not in blob and "downloads" not in blob, "no quarantine path reaches the chat model"
    passed, checked, unc = coord.verify({"tool": "web_task"}, r)
    assert passed and unc == ""


def test_the_step_without_a_grant_says_what_it_waits_for(h, tmp_path):
    b = tmp_path / "pb"
    b.mkdir()
    (b / "decks.test.make_deck.json").write_text(json.dumps(decks_playbook()), encoding="utf-8")
    from jarvis.plugins.desktop import browser_playbooks as PB
    r = web_runner.run_deck({"tool": "web_task", "topic": "solar panels for schools", "slides": 3}, pc_run=h.pc_run(), store=PB.Store(b, tmp_path / "l"),
                            shortlist=shortlist(row("decks.test", 1)), owner=h.call, deliverables_dir=str(tmp_path / "deliverables"), grant_wait_s=0.0)
    assert not r["ok"] and r["needs_owner"]["kind"] == "grant" and "PIN" in r["error"]
    assert not coord.verify({"tool": "web_task"}, r)[0]


def test_a_missing_topic_and_the_deck_parameters():
    assert web_runner.run_deck({"tool": "web_task", "topic": ""}, pc_run=None)["needs"] == "topic"
    assert web_runner.deck_params({"topic": " a  b  c ", "slides": "99", "formats": "pptx, exe"}) == {"topic": "a b c", "slides": 20, "theme": "", "formats": ("pptx",)}
    assert web_runner.deck_params({"topic": "abc"})["slides"] == 5


# ------------------------------------------------------------------------------------------------------------------------------ the ask panel
class Broker:
    def __init__(self, answer=None, boom=False):
        self.answer, self.boom, self.specs = answer or {"answered": True, "button": "done"}, boom, []

    async def ask(self, spec, timeout):
        self.specs.append(spec)
        if self.boom:
            raise RuntimeError("panel closed")
        return self.answer


@pytest.fixture
def loop():
    lp = asyncio.new_event_loop()
    t = threading.Thread(target=lp.run_forever, daemon=True)
    t.start()
    yield lp
    lp.call_soon_threadsafe(lp.stop)
    t.join(2)
    owner_ask.uninstall()


def test_without_a_panel_the_ask_is_unavailable_at_once():
    owner_ask.uninstall()
    assert not owner_ask.available()
    assert owner_ask.ask({"title": "x"}) == {"answered": False, "reason": "unavailable"}


def test_a_form_goes_to_the_panel_from_any_thread_and_the_button_comes_back(loop):
    b = Broker()
    owner_ask.install(b, loop)
    assert owner_ask.available()
    r = owner_ask.ask({"title": "JARVIS needs you for one step", "buttons": [{"id": "done"}]}, timeout_s=5)
    assert r == {"answered": True, "button": "done"} and b.specs[0]["timeout_s"] == 5.0


def test_a_broken_panel_is_no_answer_not_a_crash(loop):
    owner_ask.install(Broker(boom=True), loop)
    r = owner_ask.ask({"title": "x"}, timeout_s=5)
    assert r["answered"] is False and r["reason"] == "error"


# ------------------------------------------------------------------------------------------------------------------------------ the decision engine
def test_the_web_step_choice_is_a_known_kind_with_its_own_threshold_and_never_a_free_text_answer():
    from jarvis.plugins._shared import decider as D
    D.clear()
    assert "web_step" in D.KINDS and D.config()["thresholds"]["web_step"] == 0.75
    calls = []

    def post(body):
        calls.append(body)
        return json.dumps({"choice": "Let's go", "confidence": 0.9, "why": "fake"})
    d = D.decide("web_step", "go and generate the presentation", ["Cancel", "Let's go", "Pricing"], post=post)
    assert d.choice == "Let's go" and not d.ask
    d = D.decide("web_step", "go and generate the presentation", ["Cancel", "Let's go", "Pricing"], post=lambda b: json.dumps({"choice": "Delete everything", "confidence": 0.99, "why": "x"}))
    assert d.choice != "Delete everything", "an answer that is not on the list is never taken"
    d = D.decide("web_step", "go", ["Cancel", "Let's go"], post=lambda b: json.dumps({"choice": "Let's go", "confidence": 0.4, "why": "x"}))
    assert d.ask or d.choice is None, "below the threshold it asks"
