"""D64: the free presentation generator flow, end to end against fake sites, through the real kernel (grants, policy, approvals) and the real node (downloads into
quarantine). Grant flow (none, given, expired, wrong host), downloads that are refused, the file checked by reading it, the shortlist and its fallbacks."""
import os
import time
from pathlib import Path

import pytest

from jarvis.plugins.desktop import browser_agent as BA
from jarvis.plugins.desktop import browser_playbooks as PB
from jarvis.plugins.desktop import core, ppt_flow
from jarvis.plugins.desktop.browser_backend import GrantsClient
from jarvis.plugins.desktop.tests.chromeharness import PIN, Harness
from jarvis.plugins.desktop.tests.deckfixtures import decks_playbook, make_pdf, make_pptx, row, second_playbook, shortlist


@pytest.fixture
def h(tmp_path, monkeypatch):
    rig = Harness(tmp_path, monkeypatch)
    rig.web.signed_in.add("decks.test")
    rig.web.files[("decks.test", "/exports/deck.pptx")] = make_pptx(3)
    rig.web.files[("second.test", "/exports/deck.pptx")] = make_pptx(3)
    yield rig
    rig.close()


def flow(h, tmp_path, rows, grants=None, ask=None, outline_fn=None, wait=0.0, show=None, stop=None, playbooks=None):
    b = tmp_path / "pb"
    b.mkdir(exist_ok=True)
    import json
    for d in playbooks if playbooks is not None else (decks_playbook(), second_playbook()):
        (b / f"{d['host']}.{d['name']}.json").write_text(json.dumps(d), encoding="utf-8")
    store = PB.Store(b, tmp_path / "learned")
    agents = []

    def factory(hosts):
        ag = h.agent(hosts=tuple(hosts))
        agents.append(ag)
        return ag
    f = ppt_flow.DeckFlow(factory, store, grants or GrantsClient(owner=h.call, ttl=0.0), shortlist=shortlist(*rows), outline_fn=outline_fn or (lambda t, n: None),
                          show=show, take_local=core.take_local, stop=stop, grant_wait_s=wait, deliverables_dir=str(tmp_path / "deliverables"), ask=ask)
    f.agents = agents
    return f


ALL = ["signin_existing_session", "accept_terms", "download_to_quarantine"]


# ------------------------------------------------------------------------------------------------------------------------------ the grant flow
def test_without_a_grant_the_owner_gets_a_pin_card_and_nothing_is_driven(h, tmp_path):
    f = flow(h, tmp_path, [row("decks.test", 1)])
    res = f.run("solar panels for schools", slides=3)
    assert not res.ok and res.needs_owner["kind"] == "grant" and res.needs_owner["site"] == "decks.test"
    assert "PIN" in res.summary
    pend = h.kernel.pending_approvals()
    assert len(pend) == 1 and pend[0]["action"]["tool"] == "sitegrant"
    card = pend[0]["action"]["params"]
    assert card["hosts"] == ["decks.test"] and set(card["abilities"]) == set(ALL)
    assert not [c for c in h.world.calls if c[0] == "browser_navigate"], "the site was not even opened"
    assert h.jobs_run == []


def test_a_grant_the_owner_confirms_while_the_flow_waits_lets_it_go_on(h, tmp_path):
    def sleeper(_s):
        for a in list(h.kernel.pending_approvals()):
            h.owner.approve(a["id"], a["id"], PIN)
    grants = GrantsClient(owner=h.call, ttl=0.0, sleep=sleeper)
    f = flow(h, tmp_path, [row("decks.test", 1)], grants=grants, wait=30.0)
    res = f.run("solar panels for schools", slides=3)
    assert res.ok and res.site == "decks.test", res.summary


def test_a_grant_for_another_host_does_not_unlock_this_one(h, tmp_path):
    h.grant("second.test", ALL)
    f = flow(h, tmp_path, [row("decks.test", 1)])
    res = f.run("solar panels", slides=3)
    assert not res.ok and res.needs_owner["kind"] == "grant" and res.needs_owner["site"] == "decks.test"


def test_an_expired_grant_covers_nothing_and_a_new_card_is_made(h, tmp_path, monkeypatch):
    from jarvis_kernel import sitegrants
    h.grant("decks.test", ALL, days=30)
    monkeypatch.setattr(sitegrants, "NOW", lambda: time.time() + 31 * 86400)
    sitegrants.invalidate()
    late = GrantsClient(owner=h.call, ttl=0.0, clock=lambda: time.time() + 31 * 86400)
    f = flow(h, tmp_path, [row("decks.test", 1)], grants=late)
    res = f.run("solar panels", slides=3)
    assert not res.ok and res.needs_owner["kind"] == "grant"
    assert [p for p in h.kernel.pending_approvals() if p["action"]["tool"] == "sitegrant"]
    # a client whose own clock is stale still believes the grant: the KERNEL is the one that decides, and it holds the click for the PIN
    f2 = flow(h, tmp_path, [row("decks.test", 1)])
    res2 = f2.run("solar panels", slides=3)
    assert not res2.ok and res2.needs_owner["kind"] in ("grant", "approval")
    assert not [p for p in (h.outputs / "downloads").rglob("*") if p.is_file()] if (h.outputs / "downloads").exists() else True


def test_the_download_is_refused_without_the_download_ability(h, tmp_path):
    h.grant("decks.test", ["signin_existing_session", "accept_terms"])
    pb = decks_playbook()
    pb["needs"] = ["signin_existing_session", "accept_terms"]          # a playbook that forgot to ask for it: the kernel still decides
    f = flow(h, tmp_path, [row("decks.test", 1)], playbooks=[pb])
    res = f.run("solar panels for schools", slides=3)
    assert not res.ok
    assert not (h.outputs / "downloads").exists() or not list((h.outputs / "downloads").rglob("*.pptx"))


# ------------------------------------------------------------------------------------------------------------------------------ the deck itself
def test_a_granted_site_makes_the_deck_and_only_checked_facts_are_reported(h, tmp_path):
    h.grant("decks.test", ALL)
    shown = []
    f = flow(h, tmp_path, [row("decks.test", 1)], show=shown.append)
    res = f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert res.ok and res.site == "decks.test"
    assert any("pptx file of" in x and "with 3 slides" in x for x in res.facts), res.facts
    assert res.files[0]["slides"] == 3 and res.files[0]["ext"] == "pptx"
    q = Path(res.local_paths[0])
    assert q.is_file() and "downloads" in q.parts and "decks.test" in q.parts, "the file sits in the quarantine folder of the site it came from"
    assert len(shown) == 1 and shown[0] == res.shown and Path(res.shown).parent == tmp_path / "deliverables" and Path(res.shown).read_bytes() == q.read_bytes()
    assert "path" not in str(res.files) and str(tmp_path) not in " ".join(res.facts), "no path in anything that can reach a model"
    assert res.tried[0].outcome == "done"
    assert h.world.click_log.count("Generate") == 1
    assert "Remove watermark" not in h.world.click_log and "Publish" not in h.world.click_log and "Share" not in h.world.click_log


def test_a_deck_with_another_number_of_slides_is_reported_as_it_is(h, tmp_path):
    h.grant("decks.test", ALL)
    h.web.files[("decks.test", "/exports/deck.pptx")] = make_pptx(5)
    f = flow(h, tmp_path, [row("decks.test", 1)])
    res = f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert res.ok and any("5 slides" in x and "3 were asked for" in x for x in res.facts)


def test_the_topic_is_typed_as_the_owner_said_it_and_an_outline_is_text_only(h, tmp_path):
    h.grant("decks.test", ALL)
    seen = []

    def outline(topic, n):
        seen.append((topic, n))
        return ("Solar for schools\nSlide 1: Why - cost\nSlide 2: How - roofs\nSlide 3: Next - plan", "the gateway")
    f = flow(h, tmp_path, [row("decks.test", 1)], outline_fn=outline)
    res = f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert res.ok and seen == [("solar panels for schools", 3)]
    assert any("outline written by the gateway" in x for x in res.facts)
    typed = [c["text"] for c in h.calls if c.get("action") == "type"]
    assert typed and "Slide 1: Why - cost | Slide 2: How - roofs" in typed[0] and "\n" not in typed[0], "the outline is typed as one line"


def test_the_default_outline_asks_the_gateway_with_the_topic_only_and_survives_its_failure():
    got = {}

    def ask(msgs, max_tokens=0):
        got["msgs"] = msgs
        return ("Title\nSlide 1: A - b", "gateway")
    text, prov = ppt_flow.default_outline("solar panels for schools", 3, ask_online=ask)
    assert prov == "gateway" and text.startswith("Title")
    assert [m["role"] for m in got["msgs"]] == ["system", "user"] and "solar panels for schools" in got["msgs"][1]["content"]
    assert "http" not in got["msgs"][1]["content"]

    def boom(msgs, max_tokens=0):
        raise RuntimeError("offline")
    assert ppt_flow.default_outline("x y z", 3, ask_online=boom) is None


# ------------------------------------------------------------------------------------------------------------------------------ the file is checked
def test_a_download_that_is_not_what_its_name_says_is_refused_and_left_alone(h, tmp_path):
    h.grant("decks.test", ALL)
    h.web.files[("decks.test", "/exports/deck.pptx")] = b"MZ" + b"\x90" * 4000          # an executable dressed as a deck
    f = flow(h, tmp_path, [row("decks.test", 1)])
    res = f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert not res.ok and res.tried[0].outcome in ("failed", "limit")
    assert not [p for p in (h.outputs / "downloads").rglob("*") if p.is_file()] if (h.outputs / "downloads").exists() else True, "nothing was taken into quarantine"


def test_a_pdf_and_a_pptx_are_read_to_count_their_slides_and_pages(tmp_path):
    p = tmp_path / "a.pptx"
    p.write_bytes(make_pptx(4))
    i = ppt_flow.verify_file(str(p))
    assert i["slides"] == 4 and i["ext"] == "pptx" and len(i["sha256"]) == 16 and i["titles"][0] == "Slide 1 title"
    q = tmp_path / "a.pdf"
    q.write_bytes(make_pdf(2) + b"\n%" + b"x" * 300)
    assert ppt_flow.verify_file(str(q))["pages"] == 2


@pytest.mark.parametrize("name,data", [("tiny.pptx", b"PK"), ("broken.pptx", b"PK\x03\x04" + b"x" * 500), ("broken.pdf", b"%PDF-1.4 nonsense" + b"x" * 500)])
def test_a_file_that_cannot_be_read_as_what_it_claims_to_be_is_not_reported(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    with pytest.raises(ppt_flow.FlowError):
        ppt_flow.verify_file(str(p))
    with pytest.raises(ppt_flow.FlowError):
        ppt_flow.verify_file(str(tmp_path / "missing.pptx"))


def test_promote_copies_and_never_overwrites(tmp_path):
    p = tmp_path / "x.pptx"
    p.write_bytes(make_pptx(1))
    a = ppt_flow.promote(str(p), "Solar panels, for schools!", str(tmp_path / "out"), now=lambda: 1_700_000_000)
    b = ppt_flow.promote(str(p), "Solar panels, for schools!", str(tmp_path / "out"), now=lambda: 1_700_000_000)
    assert a != b and Path(a).is_file() and Path(b).is_file() and p.is_file() and "solar-panels-for-schools" in a


# ------------------------------------------------------------------------------------------------------------------------------ the shortlist and its fallbacks
def test_a_site_at_its_free_limit_hands_over_to_the_next_site(h, tmp_path):
    h.grant("decks.test", ALL)
    h.grant("second.test", ["download_to_quarantine"])
    page = h.web.page("decks.test", "/create").replace("7:show:#done", "7:show:#limit").replace(
        '<section id="done" data-hidden>', '<section id="limit" data-hidden><h2>You\'ve used all your free credits</h2></section><section id="done" data-hidden>')
    h.web.put("decks.test", "/create.html", page)
    f = flow(h, tmp_path, [row("decks.test", 1), row("second.test", 2)])
    res = f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert res.ok and res.site == "second.test"
    assert [t.outcome for t in res.tried] == ["limit", "done"]
    assert "Upgrade" not in h.world.click_log and "Remove watermark" not in h.world.click_log, "no upgrade was clicked on the way"


def test_a_site_whose_steps_fail_is_left_and_the_next_one_is_used(h, tmp_path):
    h.grant("decks.test", ALL)
    h.grant("second.test", ["download_to_quarantine"])
    h.web.put("decks.test", "/create.html", "<html><head><title>Create - Decks</title></head><body><h1>Down for maintenance</h1></body></html>")
    f = flow(h, tmp_path, [row("decks.test", 1), row("second.test", 2)])
    res = f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert res.ok and res.site == "second.test" and res.tried[0].outcome == "failed" and res.tried[0].why


def test_a_site_without_a_playbook_is_skipped_and_an_excluded_one_is_never_tried(h, tmp_path):
    h.grant("second.test", ["download_to_quarantine"])
    rows = [row("nopb.test", 1), row("evil.test", 2, status="excluded"), row("second.test", 3)]
    f = flow(h, tmp_path, rows)
    res = f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert res.ok and res.site == "second.test" and res.tried[0].outcome == "no_playbook"
    assert not [x for x in h.web.log if x[0] in ("evil.test", "nopb.test")]


def test_when_every_site_fails_the_owner_gets_one_form_showing_what_blocked_each(h, tmp_path):
    h.grant("decks.test", ALL)
    h.grant("second.test", ["download_to_quarantine"])
    h.web.put("decks.test", "/create.html", "<html><head><title>x</title></head><body><h1>Down</h1></body></html>")
    h.web.put("second.test", "/index.html", "<html><head><title>x</title></head><body><h1>Gone</h1></body></html>")
    forms = []
    f = flow(h, tmp_path, [row("decks.test", 1), row("second.test", 2)], ask=forms.append)
    res = f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert not res.ok and res.needs_owner["kind"] == "all_failed" and len(res.needs_owner["tried"]) == 2
    assert len(forms) == 1 and "could not make the deck" in forms[0]["title"]
    table = forms[0]["fields"][0]["rows"]
    assert [r[0] for r in table] == ["decks.test", "second.test"]
    assert "Nothing was bought" in forms[0]["intro"]


def test_a_captcha_asks_the_owner_for_that_step_and_his_stop_ends_the_task(h, tmp_path):
    h.grant("decks.test", ALL)
    h.web.put("decks.test", "/create.html", h.web.page("decks.test", "/captcha"))
    h.answers = ["stop"]
    f = flow(h, tmp_path, [row("decks.test", 1)])
    with pytest.raises(BA.Stopped):
        f.run("solar panels", slides=3, formats=("pptx",))
    assert h.asks, "the owner was asked to do that one step"
    assert not [c for c in h.world.click_log if "robot" in c.lower() or "verify" in c.lower()], "the check was never clicked"


def test_the_stop_flag_ends_the_flow_before_the_next_site(h, tmp_path):
    h.grant("decks.test", ALL)
    flag = {"stop": False}
    f = flow(h, tmp_path, [row("decks.test", 1)], stop=lambda: flag["stop"])
    flag["stop"] = True
    with pytest.raises(BA.Stopped):
        f.run("solar panels", slides=3)


def test_a_topic_that_is_too_short_is_asked_for(h, tmp_path):
    f = flow(h, tmp_path, [row("decks.test", 1)])
    with pytest.raises(ppt_flow.FlowError):
        f.run("a", slides=3)
