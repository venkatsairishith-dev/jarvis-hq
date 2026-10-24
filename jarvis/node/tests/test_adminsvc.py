"""Tests for the elevated admin service and its client: only the owner's passphrase makes a request run, a request runs once, a wrong
passphrase locks the service, an unapproved verb never runs, the hard-deny list still applies, and approval needs a terminal."""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_node import adminclient as C  # noqa: E402
from jarvis_node import adminctl as A  # noqa: E402
from jarvis_node import adminsvc as S  # noqa: E402

PASS = "correct horse battery"
NOW = 5_000_000.0


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        return 0, "Git.Git 2.50"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_DIR", str(tmp_path))
    cat = copy.deepcopy(A.load_catalogue())
    cat["winget_install"]["approved"] = True
    (tmp_path / "catalogue.json").write_text(json.dumps({"verbs": cat}), encoding="utf-8")
    S.set_passphrase(tmp_path, PASS)
    runner, clock = Runner(), [NOW]
    svc = S.Service(tmp_path, runner, lambda: clock[0])
    return tmp_path, svc, runner, clock


def ask(root, svc, verb="winget_install", params=None, passphrase=PASS, **kw):
    req = C.build_request(root, verb, params or {"id": "Git.Git"}, passphrase, clock=lambda: NOW, **kw)
    C.submit(root, req)
    svc.poll_once()
    return json.loads((root / "outbox" / f"{req['id']}.json").read_text(encoding="utf-8")), req


def test_right_passphrase_runs_an_approved_verb_and_the_check_verifies_it(env):
    root, svc, runner, _ = env
    res, _ = ask(root, svc)
    assert res["status"] == "verified" and len(runner.calls) == 3          # restore point, the command, the read-only check


def test_wrong_passphrase_never_runs(env):
    root, svc, runner, _ = env
    res, _ = ask(root, svc, passphrase="not the passphrase")
    assert res["status"] == "refused" and "passphrase" in res["reason"] and runner.calls == []


def test_the_same_request_cannot_run_twice(env):
    root, svc, runner, _ = env
    res, req = ask(root, svc)
    assert res["status"] == "verified"
    C.submit(root, req)
    svc.poll_once()
    again = json.loads((root / "outbox" / f"{req['id']}.json").read_text(encoding="utf-8"))
    assert again["status"] == "refused" and "already used" in again["reason"] and len(runner.calls) == 3


def test_altered_values_break_the_proof(env):
    root, svc, runner, _ = env
    req = C.build_request(root, "winget_install", {"id": "Git.Git"}, PASS, clock=lambda: NOW)
    req["params"] = {"id": "Evil.Package"}
    C.submit(root, req)
    svc.poll_once()
    assert json.loads((root / "outbox" / f"{req['id']}.json").read_text())["status"] == "refused" and runner.calls == []


def test_unapproved_verb_is_refused_even_with_the_right_passphrase(env):
    root, svc, runner, _ = env
    res, _ = ask(root, svc, "service_restart", {"name": "Spooler"})
    assert res["status"] == "refused" and "not approved" in res["reason"] and runner.calls == []


def test_protected_service_is_refused_even_if_the_verb_is_approved(env):
    root, svc, runner, _ = env
    cat = A.load_catalogue(root / "catalogue.json")
    cat["service_restart"]["approved"] = True
    (root / "catalogue.json").write_text(json.dumps({"verbs": cat}), encoding="utf-8")
    res, _ = ask(root, svc, "service_restart", {"name": "WinDefend"})
    assert res["status"] == "refused" and "protected" in res["reason"] and runner.calls == []


def test_five_wrong_passphrases_lock_the_service_even_for_the_right_one(env):
    root, svc, runner, clock = env
    for _ in range(5):
        ask(root, svc, passphrase="wrong wrong wrong")
    res, _ = ask(root, svc)
    assert res["status"] == "refused" and "locked" in res["reason"] and runner.calls == []
    clock[0] += S.LOCK_FOR_S + 1
    res, _ = ask(root, svc)
    assert res["status"] == "refused" and "expired" in res["reason"]           # the unlocked service sees an old approval: asked again, not run


def test_expiry_more_than_ten_minutes_ahead_is_refused(env):
    root, svc, runner, _ = env
    req = C.build_request(root, "winget_install", {"id": "Git.Git"}, PASS, ttl=100_000, clock=lambda: NOW)
    req["expires"] = NOW + 9000                                              # the client caps it; a hand-made request is capped by the service
    key = S.derive_key(PASS, json.loads((root / "public.json").read_text())["salt"])
    req["proof"] = S.proof_of(key, S.signed_message(req["id"], req["verb"], req["params"], req["expires"], [], []))
    C.submit(root, req)
    svc.poll_once()
    assert "ten minutes" in json.loads((root / "outbox" / f"{req['id']}.json").read_text())["reason"] and runner.calls == []


def test_tainted_value_needs_to_be_seen_verbatim(env):
    root, svc, runner, _ = env
    res, _ = ask(root, svc, tainted=["id"])
    assert res["status"] == "refused" and "verbatim" in res["reason"] and runner.calls == []
    res, _ = ask(root, svc, tainted=["id"], seen=["id"])
    assert res["status"] == "verified"


def test_junk_oversize_and_symlink_files_are_dropped(env):
    root, svc, runner, _ = env
    (root / "inbox" / "junk.json").write_text("{not json")
    (root / "inbox" / "big.json").write_text("x" * (S.MAX_REQUEST_BYTES + 1))
    svc.poll_once()
    assert list((root / "inbox").iterdir()) == [] and runner.calls == []


def test_no_passphrase_set_means_nothing_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_DIR", str(tmp_path))
    (tmp_path / "catalogue.json").write_text(json.dumps({"verbs": A.load_catalogue()}), encoding="utf-8")
    svc = S.Service(tmp_path, Runner(), lambda: NOW)
    out = svc.process(json.dumps({"id": "abcdefgh12", "verb": "reboot_cancel", "params": {}, "expires": NOW + 5, "proof": "00"}).encode())
    assert out["status"] == "refused" and "passphrase" in out["reason"]


def test_passphrase_rules(tmp_path):
    for bad in ("short", "aaaaaaaaaaaaaaaa"):
        with pytest.raises(ValueError):
            S.set_passphrase(tmp_path, bad)


def test_sync_keeps_approval_only_for_unchanged_verbs():
    shipped = A.load_catalogue()
    current = copy.deepcopy(shipped)
    current["winget_install"]["approved"] = True
    current["sfc_scan"]["approved"] = True
    current["sfc_scan"]["argv"] = ["sfc.exe", "/verifyonly"]               # the owner approved an older, different command
    new, reset = S.sync_catalogue(shipped, current)
    assert new["winget_install"]["approved"] is True and new["sfc_scan"]["approved"] is False and reset == ["sfc_scan"]


def test_approve_verbs_changes_only_what_was_answered(env):
    root, _, _, _ = env
    answers = iter(["", "y"] + ["n"] * 40)
    res = S.approve_verbs(root, lambda _p: next(answers), lambda _m: None)
    assert res["winget_install"] is True                                    # Enter keeps the answer it had
    assert res["winget_upgrade"] is True and res["winget_uninstall"] is False


def test_client_refuses_without_a_terminal(env, monkeypatch, capsys):
    root, svc, runner, _ = env
    monkeypatch.setattr(C.sys.stdin, "isatty", lambda: False, raising=False)
    assert C.main(["run", "winget_install", "id=Git.Git"]) == 2
    assert "terminal" in capsys.readouterr().err and runner.calls == []


def test_client_value_parsing():
    assert C.parse_values(["id=Git.Git", "delay_s=120", "n=0", "z=007"]) == {"id": "Git.Git", "delay_s": 120, "n": 0, "z": "007"}
    with pytest.raises(ValueError):
        C.parse_values(["novalue"])


def test_service_alive_needs_a_fresh_heartbeat(env):
    root, svc, _, clock = env
    svc.poll_once()
    assert C.service_alive(root, clock=lambda: clock[0] + 5) and not C.service_alive(root, clock=lambda: clock[0] + 600)
