"""D79 (2026-10-27): per-site grants FOR VM2 (`target: vm2`). The PC form and its rules are unchanged; a VM2 grant covers VM2 only, expires sooner, cannot sign up, is PIN-only
and console-only, and its card warns that the session lives on VM2."""
import json
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel import jobs, policy, sitegrants  # noqa: E402
from jarvis_kernel.owner import Owner, PREFIX  # noqa: E402
from jarvis_kernel.policy import Action, DENY, RED, YELLOW_APPROVAL, YELLOW_AUTO, decide  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402
from jarvis_kernel.tasks import Tasks  # noqa: E402

from test_site_grants import KEY, T0, DAY, book, step  # noqa: E402,F401


def vm2(hosts, abilities, days=None, note=""):
    return sitegrants.clean_request(hosts, abilities, days, note, "vm2")


def test_a_pc_request_is_byte_for_byte_what_it_was():
    req = sitegrants.clean_request("gamma.app", ["signin_existing_session"], 30, "n")
    assert "target" not in req and req == {"hosts": ["gamma.app"], "abilities": ["signin_existing_session"], "days": 30, "note": "n"}
    a = sitegrants.make_action(req)
    assert "target" not in a.params and "target" not in a.card and sitegrants.clean_request("gamma.app", ["signup"], 90)["days"] == 90


def test_a_vm2_request_is_shorter_has_no_signup_and_its_card_warns():
    req = vm2("files.example.test", ["signin_existing_session", "download_to_quarantine"])
    assert req["target"] == "vm2" and req["days"] == 14
    card = sitegrants.make_action(req).card
    assert "VM2" in card["what"] and "never types or reads a password" in card["what"] and card["target"] == "vm2"
    assert "lives on VM2" in card["warning"] and "expires in 14 days" in card["warning"] and "public-lane" in card["warning"]
    assert "PIN" in card["approve"] and "CAPTCHA" in card["never_covered"]
    with pytest.raises(sitegrants.GrantError):
        vm2("files.example.test", ["signup"])
    with pytest.raises(sitegrants.GrantError):
        vm2("files.example.test", ["accept_terms"], 31)
    with pytest.raises(sitegrants.GrantError):
        sitegrants.clean_request("files.example.test", ["accept_terms"], 10, "", "mars")
    assert vm2("files.example.test", ["accept_terms"], 30)["days"] == 30


def test_a_vm2_grant_does_not_open_the_pc_and_a_pc_grant_does_not_open_vm2(book):
    book.grant("gamma.app", ["signin_existing_session", "download_to_quarantine"])
    sitegrants.add(vm2("files.example.test", ["signin_existing_session", "download_to_quarantine"]), "ap1", key=KEY, now=T0, path=book.path)
    sitegrants.invalidate()
    assert sitegrants.find("gamma.app", "signin_existing_session") and not sitegrants.find("gamma.app", "signin_existing_session", target="vm2")
    assert sitegrants.find("files.example.test", "download_to_quarantine", target="vm2") and not sitegrants.find("files.example.test", "download_to_quarantine")
    # the PC policy, which asks `find` for the PC, still wants the owner's PIN on a site that only has a VM2 grant
    assert step("click", "Sign in", host="files.example.test").level == YELLOW_APPROVAL
    assert step("click", "Sign in", host="gamma.app").level == YELLOW_AUTO
    active = {e["host"]: e["target"] for e in sitegrants.active()}
    assert active == {"gamma.app": "pc", "files.example.test": "vm2"}


def test_the_same_host_can_hold_one_grant_per_target(book):
    book.grant("both.example", ["accept_terms"])
    sitegrants.add(vm2("both.example", ["accept_terms"]), "ap2", key=KEY, now=T0, path=book.path)
    sitegrants.invalidate()
    assert sitegrants.find("both.example", "accept_terms") and sitegrants.find("both.example", "accept_terms", target="vm2")
    book.grant("both.example", ["accept_terms", "download_to_quarantine"])                    # a new PC grant replaces only the PC one
    assert sitegrants.find("both.example", "accept_terms", target="vm2") and sitegrants.find("both.example", "download_to_quarantine")


def test_the_target_is_part_of_the_seal_so_a_hand_edit_makes_a_pc_grant_out_of_a_vm2_one_invalid(book):
    sitegrants.add(vm2("files.example.test", ["accept_terms"]), "ap3", key=KEY, now=T0, path=book.path)
    data = json.load(open(book.path))
    e = data["sites"][0]
    assert e["target"] == "vm2" and sitegrants.valid_entry(e, KEY)
    no_target = {k: v for k, v in e.items() if k != "target"}
    assert not sitegrants.valid_entry(no_target, KEY), "dropping the target to make it a PC grant breaks the seal"
    assert not sitegrants.valid_entry(dict(e, request={**e["request"], "target": "pc"}), KEY)
    pc = sitegrants.add(sitegrants.clean_request("x.example", ["accept_terms"]), "ap4", key=KEY, now=T0, path=book.path)[0]
    assert "target" not in pc and not sitegrants.valid_entry(dict(pc, target="vm2"), KEY), "adding a target to a PC grant breaks the seal"
    data["sites"][0] = {k: v for k, v in e.items() if k != "target"}
    json.dump(data, open(book.path, "w"))
    sitegrants.invalidate()
    assert not sitegrants.find("files.example.test", "accept_terms") and not sitegrants.find("files.example.test", "accept_terms", target="vm2")


def test_a_vm2_grant_expires_with_its_own_clock(book):
    sitegrants.add(vm2("files.example.test", ["accept_terms"]), "ap5", key=KEY, now=T0, path=book.path)
    book.at(T0 + 13 * DAY)
    assert sitegrants.find("files.example.test", "accept_terms", target="vm2")
    book.at(T0 + 14 * DAY + 5)
    assert not sitegrants.find("files.example.test", "accept_terms", target="vm2")


# ------------------------------------------------------------------------------------------------------------------------------ the owner's card and the PIN
@pytest.fixture
def plane(tmp_path, book):
    k = Kernel(str(tmp_path / "kd"))
    sitegrants.SEAL_KEY = k.seal_key
    sitegrants.invalidate()
    o = Owner(k, Tasks(k), jobs.Jobs(k), clock=lambda: book.clock_["now"])
    o.set_pin("correct horse battery")
    yield o, k
    k.db.close()


def call(o, method, name, body=None):
    return o.handle(method, PREFIX + name, body or {})


def test_only_the_pin_makes_a_vm2_grant_and_the_card_it_was_approved_for_is_the_only_one_that_applies(plane, book):
    o, k = plane
    body = {"hosts": ["files.example.test"], "abilities": ["signin_existing_session", "download_to_quarantine"], "days": 14, "note": "", "target": "vm2"}
    code, r = call(o, "POST", "site_grant/request", body)
    assert code == 202 and r["card"]["target"] == "vm2" and "lives on VM2" in r["card"]["warning"] and r["request"]["target"] == "vm2", r
    aid = r["approval_id"]
    pend = k.pending_approvals()
    assert pend[0]["action"]["params"]["target"] == "vm2"
    assert call(o, "POST", "site_grant/apply", dict(body, approval_id=aid))[0] == 403, "not approved yet"
    assert call(o, "POST", "approve", {"id": aid, "confirm": aid, "pin": "correct horse battery"})[0] == 200
    assert call(o, "POST", "site_grant/apply", {k2: v for k2, v in dict(body, approval_id=aid).items() if k2 != "target"})[0] == 403, "the same card for the PC is another card"
    code, r = call(o, "POST", "site_grant/apply", dict(body, approval_id=aid))
    assert code == 200 and r["granted"][0]["target"] == "vm2"
    sites = call(o, "GET", "site_grants")[1]["sites"]
    assert sites[0]["target"] == "vm2" and sites[0]["host"] == "files.example.test"
    assert step("click", "Sign in", host="files.example.test").level == YELLOW_APPROVAL, "still the owner's PIN on the PC"


def test_no_model_or_plan_can_ask_for_a_vm2_grant(plane):
    o, k = plane
    bad = Action("sitegrant", "grant", "files.example.test", {"hosts": ["files.example.test"], "abilities": ["accept_terms"], "days": 14, "note": "", "target": "vm2"},
                 "private", False, requester="agent:x")
    assert decide(bad).level == DENY


def test_the_vm2_configuration_is_console_only():
    assert "vm2.json" in policy.SAFETY_COMPONENTS
    from jarvis_kernel.push_gate import PROTECTED
    for path in ("jarvis/config/vm2.json", "config/vm2.json", "/srv/jarvis/config/vm2.json"):
        assert PROTECTED.search(path), path
    assert decide(Action("fs", "write", "/srv/jarvis/config/vm2.json")).level == RED, "an agent cannot move the viewer address or the routing rule"
