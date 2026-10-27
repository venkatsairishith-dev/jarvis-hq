"""D71: safe mode in the desktop plugin. Learned lessons and learned playbook repairs are IGNORED (not deleted), the per-site grants are not used, and the speed
shortcut that skips a model is off. Each hook only ever removes something; the property tests below compare both modes and demand "never more".
"""
from __future__ import annotations

import json
import os

import pytest

from jarvis.plugins.desktop import browser_backend as BB
from jarvis.plugins.desktop import browser_playbooks as PB
from jarvis.plugins.desktop import lessons as L
from jarvis.plugins.desktop import quick

FIXED = "2026-10-27"


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.delenv("JARVIS_SAFE_MODE", raising=False)
    monkeypatch.delenv("JARVIS_SAFE_MODE_FILE", raising=False)
    monkeypatch.delenv("JARVIS_QUICK_PATH", raising=False)


def safe(monkeypatch):
    monkeypatch.setenv("JARVIS_SAFE_MODE", "1")


# ------------------------------------------------------------------------------------------------------------------------------ lessons
@pytest.fixture
def les(tmp_path):
    return L.Lessons(tmp_path / "lessons.json", clock=lambda: FIXED)


def test_lessons_are_ignored_not_deleted_and_nothing_new_is_learned(les, tmp_path, monkeypatch):
    for _ in range(3):
        les.record("excel", "element_missing", "keyboard", True)
    before = (tmp_path / "lessons.json").read_bytes()
    assert les.order("excel", "element_missing", ["wait", "scroll", "keyboard"]) == ["keyboard", "wait", "scroll"]
    safe(monkeypatch)
    assert les.best_route("excel", "element_missing") == ""
    assert les.order("excel", "element_missing", ["wait", "scroll", "keyboard"]) == ["wait", "scroll", "keyboard"], "the ladder keeps its own order"
    assert les.record("excel", "element_missing", "scroll", True) is True, "the call is accepted and does nothing"
    assert les.merge([{"op": "helpful", "app": "excel", "problem": "dialog", "route": "answer"}]) == {"applied": 0, "ignored": 1, "retired": 0}
    assert les.learn_from_task([{"app": "excel", "problem": "dialog", "route": "answer", "worked": True}])["applied"] == 0
    assert (tmp_path / "lessons.json").read_bytes() == before, "not one byte of the learned file changed"
    monkeypatch.delenv("JARVIS_SAFE_MODE")
    assert les.best_route("excel", "element_missing") == "keyboard", "switched off again: what was learned is all still there"


def test_the_owner_can_still_see_and_forget_lessons_in_safe_mode(les, monkeypatch):
    les.record("excel", "dialog", "answer", True)
    safe(monkeypatch)
    assert les.about("excel") and "excel" in les.describe("excel")
    assert les.forget("excel") == 1, "clearing is the owner's and makes JARVIS more careful, so it stays available"


def test_lessons_never_add_a_route_in_either_mode(les, monkeypatch):
    for r in ("keyboard", "scroll", "uia", "menu"):
        for _ in range(4):
            les.record("excel", "element_missing", r, True)
    offered = ["wait", "scroll"]
    for mode in (False, True):
        if mode:
            safe(monkeypatch)
        got = les.order("excel", "element_missing", offered)
        assert sorted(got) == sorted(offered), "the set of routes is what the ladder offered, in both modes"
    assert les.order("excel", "element_missing", offered) == offered, "safe mode: exactly the ladder's own order"


# ------------------------------------------------------------------------------------------------------------------------------ playbooks
def playbook(version=1, label="Generate"):
    return {"name": "make_deck", "host": "decks.test", "version": version, "needs": ["download_to_quarantine"],
            "phases": [{"name": "create", "steps": [{"id": "go", "do": "click", "loc": {"role": "button", "name": label}}]}]}


def stores(tmp_path):
    b, l = tmp_path / "builtin", tmp_path / "learned"
    b.mkdir()
    l.mkdir()
    (b / "decks.test.make_deck.json").write_text(json.dumps(playbook(1, "Generate")), encoding="utf-8")
    (l / "decks.test.make_deck.json").write_text(json.dumps(playbook(2, "Create deck")), encoding="utf-8")        # a repaired copy the laptop saved
    (l / "other.test.make_deck.json").write_text(json.dumps({**playbook(1), "host": "other.test"}), encoding="utf-8")
    return PB.Store(b, l)


def test_learned_playbook_repairs_are_ignored_not_deleted(tmp_path, monkeypatch):
    st = stores(tmp_path)
    got = st.get("decks.test")
    assert got.version == 2 and got.source == "learned"
    safe(monkeypatch)
    got = st.get("decks.test")
    assert got.version == 1 and got.source == "builtin" and got.phases[0].steps[0].loc.name == "Generate", "the shipped playbook, not the repaired copy"
    assert st.get("other.test") is None, "a playbook that exists only as a learned file is not used"
    assert (tmp_path / "learned" / "decks.test.make_deck.json").exists() and (tmp_path / "learned" / "other.test.make_deck.json").exists()
    monkeypatch.delenv("JARVIS_SAFE_MODE")
    assert st.get("decks.test").version == 2


def test_nothing_is_saved_as_a_repair_in_safe_mode(tmp_path, monkeypatch):
    st = stores(tmp_path)
    before = (tmp_path / "learned" / "decks.test.make_deck.json").read_bytes()
    safe(monkeypatch)
    pb = st.get("decks.test")
    with pytest.raises(PB.PlaybookError):
        st.save_learned(pb)
    assert (tmp_path / "learned" / "decks.test.make_deck.json").read_bytes() == before


def test_safe_mode_playbooks_are_a_subset_of_the_normal_ones(tmp_path, monkeypatch):
    st = stores(tmp_path)
    normal = {(p.host, p.name): p for p in st.all()}
    safe(monkeypatch)
    in_safe = {(p.host, p.name): p for p in st.all()}
    assert set(in_safe) <= set(normal)
    for k, p in in_safe.items():
        assert p.source == "builtin" and set(p.needs) <= set(normal[k].needs) | {"download_to_quarantine"}
    shipped = {(p.host, p.name) for p in PB.Store(tmp_path / "builtin", tmp_path / "nothing").all()}
    assert set(in_safe) == shipped, "exactly the shipped files"


# ------------------------------------------------------------------------------------------------------------------------------ grants
class Owner:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET":
            return 200, {"sites": [{"host": "gamma.app", "allowed": ["signin_existing_session", "accept_terms"], "expires": 4_000_000_000}]}
        return 200, {"approval_id": "a1", "request": {"hosts": ["x.test"]}}


def test_site_grants_are_not_used_in_safe_mode(monkeypatch):
    owner = Owner()
    g = BB.GrantsClient(owner=owner, ttl=0.0, clock=lambda: 1_000.0)
    assert g.has("gamma.app", "accept_terms") is True and g.missing("gamma.app", ["accept_terms", "signup"]) == ["signup"]
    safe(monkeypatch)
    owner.calls.clear()
    assert g.has("gamma.app", "accept_terms") is False
    assert g.missing("gamma.app", ["accept_terms"]) == ["accept_terms"]
    assert "error" in g.request(["gamma.app"], ["accept_terms"]), "no card is made: the owner is not asked for a grant while safe mode is on"
    assert owner.calls == [], "the kernel was not even asked"


def test_safe_mode_grants_are_a_subset_of_the_normal_ones(monkeypatch):
    g = BB.GrantsClient(owner=Owner(), ttl=0.0, clock=lambda: 1_000.0)
    hosts, abilities = ["gamma.app", "pitch.com", "evil.test", ""], ["signin_existing_session", "accept_terms", "signup", "download_to_quarantine"]
    normal = {(h, a) for h in hosts for a in abilities if g.has(h, a)}
    safe(monkeypatch)
    assert {(h, a) for h in hosts for a in abilities if g.has(h, a)} <= normal == {("gamma.app", "signin_existing_session"), ("gamma.app", "accept_terms")}


# ------------------------------------------------------------------------------------------------------------------------------ speed shortcut
CORPUS = ["open notepad", "open calculator", "what time is it", "what is the date", "type hello into notepad", "open notepad and write hello",
          "write a shopping list: milk, eggs", "close everything", "delete my documents", "tell me a joke", "send an email to bob"]


def test_the_speed_shortcut_is_off_in_safe_mode_and_never_handles_more(monkeypatch):
    profiles = [{"name": "default"}]
    normal = {r for r in CORPUS if quick.match(r, profiles) is not None}
    assert normal, "the corpus has requests the rules do handle"
    safe(monkeypatch)
    assert quick.enabled() is False
    assert {r for r in CORPUS if quick.match(r, profiles) is not None} == set() <= normal
    assert quick.enabled({"JARVIS_QUICK_PATH": "1"}) is False, "an explicit environment mapping does not beat safe mode"


def test_the_flag_file_alone_turns_the_hooks_on(les, tmp_path, monkeypatch):
    flag = tmp_path / "safe_mode.json"
    monkeypatch.setenv("JARVIS_SAFE_MODE_FILE", str(flag))
    les.record("excel", "dialog", "answer", True)
    assert les.best_route("excel", "dialog") == "answer"
    flag.write_text("{}", encoding="utf-8")
    assert les.best_route("excel", "dialog") == "" and quick.enabled() is False
    flag.unlink()
    assert les.best_route("excel", "dialog") == "answer"
