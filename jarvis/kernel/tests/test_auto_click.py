"""Owner 2026-10-25: JARVIS clicks listed on-screen permission buttons itself; everything risky still waits for the PIN."""
import json

from jarvis_kernel import policy


def decide(target, text="", owner_direct=True, tool="ui", op="click"):
    a = policy.Action(tool=tool, op=op, target=target, params={"text": text} if text else {}, lane="private", owner_direct=owner_direct)
    return policy.decide(a)


def with_rules(tmp_path, monkeypatch, rules):
    f = tmp_path / "auto_click.json"
    f.write_text(json.dumps({"rules": rules}), encoding="utf-8")
    monkeypatch.setattr(policy, "AUTO_CLICK_FILE", str(f))
    policy._auto_click_cache.update(mtime=None, rules=[])


def test_a_listed_permission_button_is_clicked_without_the_pin(tmp_path, monkeypatch):
    with_rules(tmp_path, monkeypatch, [{"process": "chrome", "element": "Allow"}])
    assert decide("chrome|Allow").level == policy.YELLOW_AUTO


def test_an_unlisted_allow_button_still_needs_the_owner(tmp_path, monkeypatch):
    with_rules(tmp_path, monkeypatch, [{"process": "chrome", "element": "Allow"}])
    assert decide("notepad|Allow").level == policy.YELLOW_APPROVAL
    assert decide("chrome|Allow all").level == policy.YELLOW_APPROVAL      # the whole name must match


def test_hard_words_are_never_waived(tmp_path, monkeypatch):
    with_rules(tmp_path, monkeypatch, [{"process": "chrome", "element": ".*"}])
    for el in ("Allow and install", "Accept and pay", "Confirm delete", "Sign in", "Allow admin access"):
        assert decide(f"chrome|{el}").level == policy.YELLOW_APPROVAL, el


def test_only_the_owners_own_request_and_only_a_click(tmp_path, monkeypatch):
    with_rules(tmp_path, monkeypatch, [{"process": "chrome", "element": "Allow"}])
    assert decide("chrome|Allow", owner_direct=False).level == policy.YELLOW_APPROVAL      # an agent's click
    assert decide("chrome|Allow", op="type").level == policy.YELLOW_APPROVAL


def test_no_file_means_nothing_is_waived(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "AUTO_CLICK_FILE", str(tmp_path / "missing.json"))
    policy._auto_click_cache.update(mtime=None, rules=[])
    assert decide("chrome|Allow").level == policy.YELLOW_APPROVAL


def test_the_rules_file_is_console_only():
    assert "auto_click.json" in policy.SAFETY_COMPONENTS


def test_lookalikes_and_invisible_characters_do_not_hide_a_consequential_word(tmp_path, monkeypatch):
    """Review 2026-10-26: fullwidth 'Ｐａｙ', Cyrillic 'а' in 'pаy' and a soft hyphen in 'Se-nd' all returned YELLOW_AUTO."""
    with_rules(tmp_path, monkeypatch, [{"process": "chrome", "element": ".*"}])
    for el in ("Ｐａｙ now", "Pаy now", "Se­nd", "Delete​", "Reset all"):
        assert decide(f"chrome|{el}").level == policy.YELLOW_APPROVAL, el
    assert decide("chrome|Allow").level == policy.YELLOW_AUTO


def test_the_rules_files_are_protected_from_an_agent_push():
    from jarvis_kernel import push_gate
    for f in ("jarvis/config/auto_click.json", "jarvis/config/specialists.json", "jarvis/config/simple_tasks.json", "jarvis/config/providers.json"):
        assert push_gate.PROTECTED.search(f), f


def test_the_real_config_lists_the_playwright_extensions_allow_and_select_and_nothing_broad():
    """Read from the real connect page on 2026-10-26 (UI Automation): the button is named "Allow & select", in chrome."""
    policy._auto_click_cache.update(mtime=None, rules=[])
    rules = json.loads(open(policy.AUTO_CLICK_FILE, encoding="utf-8").read())["rules"]
    assert policy._auto_click_rules()                                                       # the file loads
    assert any(r.get("process") == "chrome" and r.get("element") == "Allow & select" for r in rules)
    assert decide("chrome|Allow & select").level == policy.YELLOW_AUTO
    assert decide("chrome|Allow & select all tabs").level == policy.YELLOW_APPROVAL       # the whole name must match
    assert decide("notepad|Allow & select").level == policy.YELLOW_APPROVAL
    assert not any(r.get("element") in (".*", ".+", "") or r.get("process") in (".*", ".+", "") for r in rules)
