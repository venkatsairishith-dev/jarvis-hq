"""D64: site playbooks. The shipped files load; a playbook is replayed through the engine; a step that only worked through a recovery rung repairs the playbook
(and the repair is saved); nothing secret is ever saved; 'learn this site' records what worked with parameters instead of the typed words."""
import json

import pytest

from jarvis.plugins.desktop import browser_agent as BA
from jarvis.plugins.desktop import browser_playbooks as PB
from jarvis.plugins.desktop.browser_planner import LocalPlanner
from jarvis.plugins.desktop.tests.chromeharness import Harness
from jarvis.plugins.desktop.tests.deckfixtures import decks_playbook, make_pptx


@pytest.fixture
def h(tmp_path, monkeypatch):
    rig = Harness(tmp_path, monkeypatch)
    rig.web.signed_in.add("decks.test")
    rig.web.files[("decks.test", "/exports/deck.pptx")] = make_pptx(3)
    rig.grant("decks.test", ["signin_existing_session", "accept_terms", "download_to_quarantine"])
    yield rig
    rig.close()


def store(tmp_path, *pbs):
    b, l = tmp_path / "builtin", tmp_path / "learned"
    b.mkdir(exist_ok=True)
    for pb in pbs:
        (b / f"{pb['host']}.{pb['name']}.json").write_text(json.dumps(pb), encoding="utf-8")
    return PB.Store(b, l)


# ------------------------------------------------------------------------------------------------------------------------------ the shipped files
def test_every_shipped_playbook_loads_and_tells_the_owner_what_it_knows():
    st = PB.Store()
    got = st.all()
    assert st.problems == []
    assert {p.host for p in got} >= {"gamma.app", "pitch.com", "slidesgo.com"}
    for p in got:
        assert p.name == "make_deck" and p.terms_note and p.terms_read == "2026-10-26" and p.terms_source
        assert p.verified_live is False, "nothing is claimed as tried on the real site until the laptop has run it"
        assert "download_to_quarantine" in p.needs and p.required_params() == ["topic"]
        assert all(h.endswith(tuple(p.hosts)) or h in p.hosts for h in [p.host])
        steps = [s for ph in p.phases for s in ph.steps]
        assert any(s.do == "download" for s in steps) and any(s.do == "wait" and s.until for s in steps), "generation is waited for by condition, the file is collected"
        assert all("password" not in (s.value or "").lower() for s in steps)


def test_the_shortlist_names_a_playbook_file_that_exists_for_every_site_that_has_one():
    from jarvis.plugins.desktop import ppt_flow
    sl = ppt_flow.load_shortlist()
    names = {p.host for p in PB.Store().all()}
    ranked = ppt_flow.ranked_sites(sl)
    assert [r["host"] for r in ranked][:3] == ["pitch.com", "gamma.app", "slidesgo.com"]
    for r in sl["sites"]:
        if r.get("playbook"):
            assert r["host"] in names
        if r["status"] == "excluded":
            assert r["host"] not in [x["host"] for x in ranked], "a site whose terms forbid bots is never tried"
        assert r.get("source_urls") and r.get("read_on") == "2026-10-26"


def test_a_playbook_with_a_bad_host_or_no_phase_is_refused_and_reported():
    with pytest.raises(PB.PlaybookError):
        PB.Playbook.from_dict({"name": "x", "host": "not a host", "phases": [{"name": "a", "steps": []}]})
    with pytest.raises(PB.PlaybookError):
        PB.Playbook.from_dict({"name": "x", "host": "a.test", "phases": []})
    with pytest.raises(PB.PlaybookError):
        PB.Playbook.from_dict({"name": "x", "host": "a.test", "hosts": ["b.test"], "phases": [{"name": "a", "steps": []}]})


def test_a_learned_playbook_wins_over_the_shipped_one_for_the_same_host(tmp_path):
    st = store(tmp_path, decks_playbook())
    pb = st.get("decks.test")
    pb.version = 7
    st.save_learned(pb)
    again = PB.Store(tmp_path / "builtin", tmp_path / "learned")
    assert again.get("decks.test").version == 7 and again.get("decks.test").source == "learned"


# ------------------------------------------------------------------------------------------------------------------------------ replay
def test_a_playbook_is_replayed_through_the_engine_and_the_file_arrives_in_quarantine(h, tmp_path):
    st = store(tmp_path, decks_playbook())
    pb = st.get("decks.test")
    ag = h.agent()
    r = PB.Runner(ag, st).run(pb, {"topic": "solar panels for schools"})
    assert r.ok and r.failed is None and r.repaired == []
    assert r.done[-1] == "pptx_file" and r.data["ext"] == "pptx" and r.data["size"] > 1000
    doc = h.backend.take_local(r.data["local_result"])
    assert doc["file"].endswith(".pptx") and "downloads" in doc["file"].replace("\\", "/") and "decks.test" in doc["file"]
    assert pb.stats["ok"] == 1 and pb.stats["last_ok"]
    assert any(e["kind"] == "phase_skipped" and e.get("wall") == "signin" for e in ag.events), "already signed in: the sign-in phase is skipped"


def test_a_missing_parameter_is_asked_for_and_not_guessed(h, tmp_path):
    pb = store(tmp_path, decks_playbook()).get("decks.test")
    with pytest.raises(PB.PlaybookError):
        PB.Runner(h.agent()).run(pb, {})


def test_the_sign_in_phase_uses_the_existing_google_session_when_the_site_asks(h, tmp_path):
    h.web.signed_in.discard("decks.test")
    st = store(tmp_path, decks_playbook())
    pb = st.get("decks.test")
    ag = h.agent(hosts=("decks.test",))
    r = PB.Runner(ag, st).run(pb, {"topic": "an ai deck"})
    assert r.ok, r
    assert h.world.click_log.index("Continue with Google") < h.world.click_log.index("Continue")
    assert any("@" in c for c in h.world.click_log), "the account that is already signed in was chosen from the chooser"
    assert "Use another account" not in h.world.click_log and "Cancel" not in h.world.click_log


def test_a_playbook_run_never_leaves_the_sites_it_lists(h, tmp_path):
    pb = store(tmp_path, decks_playbook()).get("decks.test")
    ag = h.agent()
    PB.Runner(ag).run(pb, {"topic": "an ai deck"})
    assert all("evil.test" not in t.url for t in h.world.tabs)


# ------------------------------------------------------------------------------------------------------------------------------ self-healing
def test_a_renamed_button_is_found_another_way_and_the_playbook_is_repaired_and_saved(h, tmp_path):
    pbd = decks_playbook()
    gen = next(s for ph in pbd["phases"] for s in ph["steps"] if s["id"] == "generate")
    gen["loc"] = {"role": "button", "name": "Create slides", "text": "Generate"}          # the site renamed it; its text still says Generate
    st = store(tmp_path, pbd)
    pb = st.get("decks.test")
    r = PB.Runner(h.agent(), st).run(pb, {"topic": "an ai deck"})
    assert r.ok and len(r.repaired) == 1 and r.repaired[0]["step"] == "generate"
    saved = PB.Store(tmp_path / "builtin", tmp_path / "learned").get("decks.test")
    assert saved.source == "learned" and saved.version == 2
    loc = saved.step("generate").loc
    assert loc.text == "Generate" and "Create slides" in loc.any_of, "the old name stays as a synonym"
    assert saved.repairs and saved.repairs[-1]["how"] == "text"
    # the repaired copy now works on the first try
    h.world.click_log.clear()
    h.world.tab.url = "about:blank"
    r2 = PB.Runner(h.agent(), PB.Store(tmp_path / "builtin", tmp_path / "learned")).run(saved, {"topic": "an ai deck"})
    assert r2.ok and r2.repaired == []


def test_a_repair_by_the_vision_model_is_noted_but_not_turned_into_a_locator():
    pb = PB.Playbook.from_dict(decks_playbook())
    before = pb.step("generate").loc
    assert PB.apply_repair(pb, {"step": "generate", "how": "vision", "from": {"name": "Generate"}, "to": {"x": 4, "y": 9}})
    assert pb.step("generate").loc == before
    assert "vision" in pb.step("generate").note and pb.repairs[-1]["to"] == {"vision": True}


def test_a_step_no_rung_can_do_fails_the_run_and_counts_the_failure(h, tmp_path):
    pbd = decks_playbook()
    gen = next(s for ph in pbd["phases"] for s in ph["steps"] if s["id"] == "generate")
    gen["loc"] = {"role": "button", "name": "Zzz nothing"}
    st = store(tmp_path, pbd)
    pb = st.get("decks.test")
    with pytest.raises(BA.StepFailed):
        PB.Runner(h.agent(), st).run(pb, {"topic": "an ai deck"})
    assert pb.stats["failed"] == 1 and pb.stats["last_failed"]
    assert not (tmp_path / "learned").exists() or not list((tmp_path / "learned").glob("*.json")), "a failed run saves nothing"


# ------------------------------------------------------------------------------------------------------------------------------ no secret in a file
def test_a_playbook_never_stores_a_password_an_address_or_a_token():
    d = decks_playbook()
    d["phases"].append({"name": "extra", "steps": [
        {"id": "pw", "do": "fill", "loc": {"role": "textbox", "name": "Password"}, "value": "hunter2-Pa55w0rd!"},
        {"id": "em", "do": "fill", "loc": {"role": "textbox", "name": "Email"}, "value": "satish@example.com"},
        {"id": "tok", "do": "fill", "loc": {"role": "textbox", "name": "Invite code"}, "value": "A1b2C3d4E5f6G7h8I9j0K1l2"},
        {"id": "go", "do": "goto", "to": "https://decks.test/create?token=abc123secret#frag", "expect": {"host_is": "decks.test"}}]})
    clean = PB.scrub_for_save(PB.Playbook.from_dict(d))
    text = json.dumps(clean.to_dict())
    for secret in ("hunter2", "satish@example.com", "A1b2C3d4E5f6G7h8I9j0K1l2", "abc123secret", "frag"):
        assert secret not in text
    assert clean.step("pw").value == "@cred:password" and clean.step("em").value == "@identity:email"
    assert clean.step("tok").value.startswith("{")


def test_a_note_that_holds_a_secret_refuses_the_save(tmp_path):
    d = decks_playbook()
    d["terms_note"] = "key sk-ant-api03-" + "a" * 40
    d["phases"][0]["steps"][0]["value"] = "sk-ant-api03-" + "b" * 40
    st = store(tmp_path)
    with pytest.raises(PB.PlaybookError):
        st.save_learned(PB.Playbook.from_dict(d))


# ------------------------------------------------------------------------------------------------------------------------------ learn this site
def test_learn_this_site_records_what_worked_with_parameters_not_typed_words(h, tmp_path):
    ag = h.agent(planner=LocalPlanner(use_model=False))
    pb = PB.learn(ag, "make a presentation about the topic", "https://decks.test/create?utm=zzz", "make_deck", {"topic": "solar panels for schools"},
                  LocalPlanner(use_model=False), max_steps=4, wanted={"done_text": ["Generating slide"]})
    assert pb.source == "learned" and pb.host == "decks.test" and pb.verified_live
    fills = [s for ph in pb.phases for s in ph.steps if s.do == "fill"]
    assert fills and fills[0].value == "{topic}"
    assert "solar panels" not in json.dumps(pb.to_dict()) and "utm" not in json.dumps(pb.to_dict())
    assert pb.required_params() == ["topic"]
    st = store(tmp_path)
    st.save_learned(pb)
    assert st.get("decks.test").phases[0].steps[0].do == "goto"

def test_a_redesigned_page_with_new_ids_classes_and_order_is_replayed_without_a_repair(h, tmp_path):
    """Scout item 6: steps are found by role + accessible name (Playwright's getByRole), not by a selector, so a redesign that changes ids, classes and the order of
    the page costs nothing: every action re-resolves the control on a FRESH snapshot, and no repair is recorded."""
    page = h.web.page("decks.test", "/create")
    btn = '<button id="gen" data-disabled-until-text="#topic=a" data-on-click="timeline:start;hide:#form;show:#status">Generate</button>'
    assert btn in page
    page = page.replace(btn, "").replace('<label for="topic">', '<div class="wrap-x91"><span class="c-9f2">' + btn.replace('id="gen"', 'id="go-btn-7c1" class="btn-primary-xk29"') + '</span></div><label for="topic">')
    h.web.put("decks.test", "/create.html", page)
    st = store(tmp_path, decks_playbook())
    pb = st.get("decks.test")
    r = PB.Runner(h.agent(), st).run(pb, {"topic": "an ai deck"})
    assert r.ok and r.repaired == [] and r.done[-1] == "pptx_file"
    assert not list((tmp_path / "learned").glob("*.json")) if (tmp_path / "learned").exists() else True
