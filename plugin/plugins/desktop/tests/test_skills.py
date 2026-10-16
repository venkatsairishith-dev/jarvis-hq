"""Controlled self-improvement (plugin/plugins/desktop/skills.py): versioned proposals, the regression gate, owner-only
promotion and rollback, and what a proposal can never contain. Windows or any OS."""
import json

import pytest

from plugin.plugins.desktop import decide as D
from plugin.plugins.desktop import skills as S

GOOD = {"sites": {"hacker news": "https://news.ycombinator.com/"}}


def suite(results):
    """A fake regression suite: content -> {case: passed}; results(content) decides."""
    return lambda content: results(content)


def test_versions_are_immutable_and_hash_checked(tmp_path):
    v1 = S.propose(GOOD, "owner asked for hacker news", root=tmp_path, builtin_sites=D.SITES)
    assert v1 == "v1" and S.load("v1", tmp_path)["sites"]["hacker news"].startswith("https://news.ycombinator.com")
    f = tmp_path / "versions" / "v1.json"
    f.write_text(f.read_text(encoding="utf-8").replace("ycombinator", "evil-example"), encoding="utf-8")
    with pytest.raises(S.SkillError, match="changed"):
        S.load("v1", tmp_path)


@pytest.mark.parametrize("bad", [
    {"tools": ["shell"]}, {"policy": {"approve": "all"}}, {"sites": {"youtube": "https://evil.example/"}},
    {"sites": {"bank": "http://insecure.example/"}}, {"sites": {"router": "https://192.168.1.1/"}},
    {"examples": [{"request": "wipe the disk", "plan": {"steps": [{"tool": "shell", "text": "format c:"}]}}]}, {}])
def test_a_proposal_cannot_expand_privileges_or_replace_built_ins(tmp_path, bad):
    with pytest.raises(S.SkillError):
        S.propose(bad, "x", root=tmp_path, builtin_sites=D.SITES)


def test_gate_blocks_a_regression_and_promotion_needs_a_passed_gate(tmp_path):
    v1 = S.propose(GOOD, "x", root=tmp_path, builtin_sites=D.SITES)
    with pytest.raises(S.SkillError, match="gate"):
        S.promote(v1, tmp_path)                                         # not gated yet
    rep = S.gate(v1, suite(lambda c: {"a": True, "b": bool(c)}), tmp_path)   # staged fixes b, breaks nothing
    assert rep["passed"] and S.promote(v1, tmp_path) == "v1"
    v2 = S.propose({"examples": [{"request": "open hn", "plan": {"steps": [{"tool": "open_website", "site": "hacker news"}]}}]},
                   "x", root=tmp_path, builtin_sites=D.SITES)
    rep = S.gate(v2, suite(lambda c: {"a": not c.get("examples"), "b": True}), tmp_path)   # staged breaks a
    assert not rep["passed"] and rep["regressed"] == ["a"]
    with pytest.raises(S.SkillError):
        S.promote(v2, tmp_path)
    assert json.loads((tmp_path / "registry.json").read_text())["active"] == "v1"


def test_rollback_restores_the_previous_version_and_the_decider_follows(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "ROOT", tmp_path)
    assert D.resolve_site("hacker news") is None
    v1 = S.propose(GOOD, "x", root=tmp_path, builtin_sites=D.SITES)
    S.gate(v1, suite(lambda c: {"a": True}), tmp_path)
    S.promote(v1, tmp_path)
    assert D.resolve_site("hacker news") == "https://news.ycombinator.com/"
    v2 = S.propose({"sites": {"lobsters": "https://lobste.rs/"}}, "x", root=tmp_path, builtin_sites=D.SITES)
    S.gate(v2, suite(lambda c: {"a": True}), tmp_path)
    S.promote(v2, tmp_path)
    assert D.resolve_site("lobsters") == "https://lobste.rs/" and D.resolve_site("hacker news")   # v2 builds on v1
    assert S.rollback(tmp_path) == "v1"
    assert D.resolve_site("lobsters") is None and D.resolve_site("hacker news")
    log = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert "promoted v1" in log and "rolled back v2 -> v1" in log


def test_no_plugin_entry_can_promote_or_roll_back():
    src = (S.Path(S.__file__).parent / "__init__.py").read_text(encoding="utf-8")
    assert "skills.promote" not in src and "skills.rollback" not in src
