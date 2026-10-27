"""D71: safe mode in the kernel. It can only make a decision STRICTER: the owner's auto-click list and the per-site grants are not used while it is on,
and nothing else about the policy moves. The property test runs the same actions with and without it and demands level(safe) >= level(normal) for every one."""
import json
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel import policy, safe_mode, sitegrants  # noqa: E402
from jarvis_kernel.policy import Action, GREEN, RED, YELLOW_APPROVAL, YELLOW_AUTO, decide  # noqa: E402

KEY = b"k" * 32
T0 = 1_800_000_000.0


@pytest.fixture
def world(tmp_path, monkeypatch):
    """An auto-click rule for chrome|Allow and a grant file for gamma.app, both live; safe mode off unless a test turns it on."""
    rules = tmp_path / "auto_click.json"
    rules.write_text(json.dumps({"rules": [{"process": "chrome", "element": "Allow"}, {"process": "chrome", "element": "Allow & select"}]}), encoding="utf-8")
    monkeypatch.setattr(policy, "AUTO_CLICK_FILE", str(rules))
    policy._auto_click_cache.update(mtime=None, rules=[])
    grants = tmp_path / "trusted_sites.json"
    monkeypatch.setattr(sitegrants, "FILE", str(grants))
    monkeypatch.setattr(sitegrants, "SEAL_KEY", lambda: KEY)
    monkeypatch.setattr(sitegrants, "NOW", lambda: T0)
    sitegrants.invalidate()
    req = sitegrants.clean_request(["gamma.app"], ["signin_existing_session", "signup", "accept_terms", "download_to_quarantine"], 30, "")
    sitegrants.add(req, "appr123456", key=KEY, now=T0, path=str(grants))
    monkeypatch.delenv("JARVIS_SAFE_MODE", raising=False)
    monkeypatch.delenv("JARVIS_SAFE_MODE_FILE", raising=False)
    yield
    sitegrants.invalidate()
    policy._auto_click_cache.update(mtime=None, rules=[])


def ui(target, text="", op="click", owner_direct=True):
    return Action(tool="ui", op=op, target=target, params={"text": text} if text else {}, lane="private", owner_direct=owner_direct)


def web(op, target, host="gamma.app", text="", ctx=None, via="", owner_direct=True, **extra):
    params = {"profile": "default", "host": host, **extra}
    if text:
        params["text"] = text
    if via:
        params["via"] = via
    a = Action("browser", op, target, params, "private", False, owner_direct=owner_direct)
    a.ctx = ctx
    return a


def corpus():
    acts = [ui("chrome|Allow"), ui("chrome|Allow & select"), ui("chrome|Allow all"), ui("notepad|Allow"), ui("chrome|Pay now"), ui("chrome|Allow and install"),
            ui("chrome|Allow", op="type"), ui("chrome|Allow", owner_direct=False), ui("notepad|OK"), ui("explorer|Delete"), ui("chrome|Sign in"),
            Action("fs", "read", "/srv/x/readme.txt"), Action("fs", "write", "/srv/jarvis/config/auto_click.json"), Action("fs", "write", "/srv/jarvis/config/safe_mode.json"),
            Action("fs", "write", "/srv/jarvis/safe_mode.py"), Action("fs", "delete", "/home/me/.jarvis/data/safe_mode.json"),
            Action("git", "status", "repo"), Action("tests", "run", "repo"), Action("unknown", "thing", "x")]
    for host in ("gamma.app", "pitch.com", "chase.com", "evil.test"):
        acts += [web("click", "Accept all cookies", host=host), web("click", "I agree to the terms", host=host), web("click", "Continue with Google", host=host),
                 web("click", "Generate", host=host), web("click", "Upgrade now", host=host), web("click", "Sign up", host=host),
                 web("type", "Email", host=host, text="@identity:email"), web("type", "Password", host=host, text="@cred:password"),
                 web("cred_new", "password", host=host), web("download", "deck", host=host, ext="pptx"), web("download", "deck", host=host, ext="exe"),
                 web("click", "Continue", host="accounts.google.com", via=host, ctx="Choose an account to continue to gamma.app")]
    return acts


def level(a):
    return decide(a).level


def test_the_switch_reads_the_environment_and_the_flag_file(world, tmp_path, monkeypatch):
    assert safe_mode.active() is False
    monkeypatch.setenv("JARVIS_SAFE_MODE", "1")
    assert safe_mode.active() is True
    monkeypatch.setenv("JARVIS_SAFE_MODE", "0")
    assert safe_mode.active() is False, "0 is not a way to turn it on"
    flag = tmp_path / "safe_mode.json"
    monkeypatch.setenv("JARVIS_SAFE_MODE_FILE", str(flag))
    assert safe_mode.active() is False
    flag.write_text("{}", encoding="utf-8")
    assert safe_mode.active() is True
    monkeypatch.setenv("JARVIS_SAFE_MODE", "0")
    assert safe_mode.active() is True, "the environment cannot clear the owner's file"


def test_the_listed_buttons_and_the_granted_sites_are_live_without_safe_mode(world):
    """The fixture works: the strictness below is a change, not an artefact."""
    assert level(ui("chrome|Allow")) == YELLOW_AUTO
    assert level(web("click", "Accept all cookies")) == YELLOW_AUTO
    assert level(web("download", "deck", ext="pptx")) == YELLOW_AUTO


def test_safe_mode_never_lowers_a_level_for_any_action(world, monkeypatch):
    normal = [(a, level(a)) for a in corpus()]
    monkeypatch.setenv("JARVIS_SAFE_MODE", "1")
    sitegrants.invalidate()
    widened = [(a.tool, a.op, a.target, n, level(a)) for a, n in normal if level(a) < n]
    assert widened == [], "safe mode must never make a decision more permissive"


def test_safe_mode_makes_the_auto_click_list_and_the_grants_wait_for_the_owner(world, monkeypatch):
    monkeypatch.setenv("JARVIS_SAFE_MODE", "1")
    sitegrants.invalidate()
    assert level(ui("chrome|Allow")) == YELLOW_APPROVAL
    assert level(ui("chrome|Allow & select")) == YELLOW_APPROVAL
    assert level(web("click", "Accept all cookies")) == YELLOW_APPROVAL
    assert level(web("click", "Continue with Google", host="accounts.google.com", via="gamma.app", ctx="Choose an account to continue to gamma.app")) == YELLOW_APPROVAL
    assert level(web("type", "Password", text="@cred:password")) == YELLOW_APPROVAL
    assert level(web("download", "deck", ext="pptx")) == RED


def test_safe_mode_changes_nothing_that_needed_neither(world, monkeypatch):
    """GREEN reads, RED/DENY things and the hard words are exactly as before: only the two lists were switched off."""
    same = [Action("fs", "read", "/srv/x/readme.txt"), Action("git", "status", "repo"), ui("chrome|Pay now"), ui("explorer|Delete"), web("click", "Upgrade now"),
            Action("unknown", "thing", "x"), ui("notepad|OK")]
    before = [level(a) for a in same]
    monkeypatch.setenv("JARVIS_SAFE_MODE", "1")
    assert [level(a) for a in same] == before


def test_an_agent_cannot_write_or_delete_the_switch(world):
    """The flag file and the reader are console-only, like the auto-click list and the grants."""
    for name in ("safe_mode.json", "safe_mode.py", "safe_mode_ctl.py"):
        assert name in policy.SAFETY_COMPONENTS, name
    from jarvis_kernel.push_gate import PROTECTED
    for path in ("jarvis/safe_mode.py", "jarvis/tools/safe_mode_ctl.py", "jarvis/kernel/jarvis_kernel/safe_mode.py", "jarvis/node/jarvis_node/safe_mode.py",
                 "jarvis/privacy/safe_mode.py", "D:/jarvis/data/safe_mode.json", "jarvis/config/safe_mode.json"):
        assert PROTECTED.search(path), path
    for act in (Action("fs", "write", "/srv/jarvis/config/safe_mode.json"), Action("fs", "delete", "/home/me/.jarvis/data/safe_mode.json"),
                Action("fs", "write", "/srv/jarvis/safe_mode.py")):
        assert level(act) >= RED, (act.op, act.target)
