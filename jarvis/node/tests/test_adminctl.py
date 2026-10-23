"""Tests for jarvis_node.adminctl: nothing runs unless the owner approved that verb and those exact values, dangerous commands
are refused even if added to the catalogue, and "done" needs a passing check."""
from __future__ import annotations

import copy
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_node import adminctl as A  # noqa: E402

SHIPPED = A.load_catalogue()
NOW = 1_000_000.0


def approved(*names):
    cat = copy.deepcopy(SHIPPED)
    for n in names:
        cat[n]["approved"] = True
    return cat


class Runner:
    def __init__(self, results=None):
        self.calls, self.results = [], results or {}

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        for key, res in self.results.items():
            if key in " ".join(argv):
                return res
        return 0, "ok"


def req(verb, params, **kw):
    return A.AdminRequest(request_id=kw.pop("rid", "r1"), verb=verb, params=params, approved_digest=kw.pop("dg", A.digest(verb, params)),
                          expires=kw.pop("expires", NOW + 300), **kw)


def broker(tmp_path, cat, runner=None):
    return A.Broker(tmp_path / "ledger.jsonl", cat, runner or Runner(), clock=lambda: NOW)


def test_shipped_catalogue_is_all_unapproved_and_clean():
    assert len(SHIPPED) >= 20 and all(v["approved"] is False for v in SHIPPED.values())
    for name, v in SHIPPED.items():
        assert not A.HARD_DENY.search(" ".join(v["argv"])), name
        used = set(re.findall(r"\{(\w+)\}", " ".join(v["argv"] + v.get("check", []))))
        assert used == set(v.get("params", {})), name                          # every placeholder declared, every value used
        if v.get("undo"):
            assert v["undo"] in SHIPPED, name


def test_unapproved_verb_never_runs(tmp_path):
    r = Runner()
    with pytest.raises(A.AdminRefused, match="not approved"):
        broker(tmp_path, SHIPPED, r).run(req("service_restart", {"name": "WSearch"}))
    assert r.calls == []


def test_unknown_verb_is_no_raw_shell(tmp_path):
    with pytest.raises(A.AdminRefused, match="no raw admin shell"):
        broker(tmp_path, approved()).run(req("powershell", {"command": "whoami"}))


def test_install_flow_restore_point_command_check(tmp_path):
    r = Runner({"winget list": (0, "Name  Id  Version\nVLC media player VideoLAN.VLC 3.0.21")})
    out = broker(tmp_path, approved("winget_install"), r).run(req("winget_install", {"id": "VideoLAN.VLC"}))
    assert out["status"] == "verified"
    assert [s["step"] for s in out["steps"]] == ["restore_point", "command", "check"]
    assert r.calls[1][:4] == ["winget", "install", "--id", "VideoLAN.VLC"] and "--exact" in r.calls[1]
    assert "a restore point is created first" in out["said"] and "winget_uninstall" in out["said"]
    line = json.loads((tmp_path / "admin_transcript.jsonl").read_text().splitlines()[0])
    assert line["status"] == "verified" and line["request_id"] == "r1"


def test_check_must_confirm(tmp_path):
    r = Runner({"winget list": (0, "No installed package found matching input criteria.")})
    out = broker(tmp_path, approved("winget_install"), r).run(req("winget_install", {"id": "VideoLAN.VLC"}))
    assert out["status"] == "unverified"
    r = Runner({"sc.exe query": (0, "STATE              : 1  STOPPED")})
    assert broker(tmp_path, approved("service_start"), r).run(req("service_start", {"name": "WSearch"}, rid="r2"))["status"] == "unverified"
    r = Runner({"sc.exe query": (0, "STATE              : 4  RUNNING")})
    assert broker(tmp_path, approved("service_start"), r).run(req("service_start", {"name": "WSearch"}, rid="r3"))["status"] == "verified"


def test_no_restore_point_no_change(tmp_path):
    r = Runner({"Checkpoint-Computer": (1, "restore points are off")})
    out = broker(tmp_path, approved("winget_install"), r).run(req("winget_install", {"id": "VideoLAN.VLC"}))
    assert out["status"] == "refused" and len(r.calls) == 1


def test_failure_and_no_check_are_reported_honestly(tmp_path):
    out = broker(tmp_path, approved("dns_flush"), Runner({"flushdns": (1, "denied")})).run(req("dns_flush", {}))
    assert out["status"] == "failed"
    out = broker(tmp_path, approved("dns_flush")).run(req("dns_flush", {}, rid="r2"))
    assert out["status"] == "done_unchecked"


@pytest.mark.parametrize("verb,params,why", [
    ("winget_install", {"id": "VLC; rm -rf"}, "not a valid id"),
    ("winget_install", {"id": "VideoLAN.VLC", "extra": "x"}, "takes exactly"),
    ("service_restart", {"name": "WinDefend"}, "protected Windows service"),
    ("service_startup", {"name": "mpssvc", "mode": "disabled"}, "protected Windows service"),
    ("service_startup", {"name": "WSearch", "mode": "off"}, "not a valid choice"),
    ("restore_point", {"description": "x'; Remove-Item C:\\ -Recurse; '"}, "not a valid text"),
    ("reboot", {"delay_s": 5}, "not a valid int"),
    ("reboot", {"delay_s": True}, "not a valid int"),
    ("defender_exclusion_add", {"path": "C:\\"}, "must be a folder under"),
    ("defender_exclusion_add", {"path": "D:\\jarvis\\models\\..\\..\\Windows"}, "must be a folder under"),
    ("defender_exclusion_add", {"path": "D:\\jarvis\\modelsX"}, "must be a folder under"),
    ("defender_exclusion_add", {"path": "D:\\jarvis\\models\\a';x'"}, "must be a folder under"),
])
def test_bad_values_refused_before_anything_runs(tmp_path, verb, params, why):
    r = Runner()
    with pytest.raises(A.AdminRefused, match=why):
        broker(tmp_path, approved(verb), r).run(req(verb, params))
    assert r.calls == []


def test_powershell_values_are_literals(tmp_path):
    r = Runner()
    broker(tmp_path, approved("defender_exclusion_add")).run(req("defender_exclusion_add", {"path": "D:\\jarvis\\models\\big model"}))
    b = broker(tmp_path, approved("defender_exclusion_add"), r)
    b.run(req("defender_exclusion_add", {"path": "D:\\jarvis\\models\\big model"}, rid="r2"))
    assert r.calls[0][-1] == "'D:\\jarvis\\models\\big model'"
    r2 = Runner()
    broker(tmp_path, approved("service_stop"), r2).run(req("service_stop", {"name": "WSearch"}, rid="r3"))
    assert r2.calls[0] == ["sc.exe", "stop", "WSearch"]                          # not PowerShell: no quoting


def test_approval_binding(tmp_path):
    b = broker(tmp_path, approved("service_restart"))
    with pytest.raises(A.AdminRefused, match="not the values the owner approved"):
        b.run(req("service_restart", {"name": "WSearch"}, dg=A.digest("service_restart", {"name": "Spooler"})))
    with pytest.raises(A.AdminRefused, match="expired"):
        b.run(req("service_restart", {"name": "WSearch"}, expires=NOW - 1))
    b.run(req("service_restart", {"name": "WSearch"}, rid="once"))
    with pytest.raises(A.AdminRefused, match="already used"):
        b.run(req("service_restart", {"name": "WSearch"}, rid="once"))


def test_tainted_values_need_to_be_seen(tmp_path):
    b = broker(tmp_path, approved("winget_install"))
    with pytest.raises(A.AdminRefused, match="came from web or tool content"):
        b.run(req("winget_install", {"id": "Evil.App"}, tainted=["id"]))
    assert b.run(req("winget_install", {"id": "Evil.App"}, rid="r2", tainted=["id"], seen_verbatim=["id"]))["status"] in ("verified", "unverified")


def test_hard_deny_even_if_added_to_the_catalogue(tmp_path):
    for argv in (["powershell.exe", "-Command", "Set-MpPreference", "-DisableRealtimeMonitoring", "1"],
                 ["bcdedit", "/set", "testsigning", "on"], ["net", "user", "x", "/add"], ["netsh", "advfirewall", "set", "allprofiles", "state", "off"],
                 ["reg", "add", "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "/v", "EnableLUA"],
                 ["vssadmin", "delete", "shadows", "/all"], ["wevtutil", "cl", "Security"],
                 ["powershell.exe", "-EncodedCommand", "AAAA"]):
        p = tmp_path / f"cat{len(argv)}{argv[0]}.json"
        p.write_text(json.dumps({"verbs": {"bad": {"approved": True, "params": {}, "argv": argv}}}))
        with pytest.raises(A.AdminRefused, match="hard-deny"):
            A.load_catalogue(p)
        with pytest.raises(A.AdminRefused, match="hard-deny"):
            A.render("bad", {"approved": True, "params": {}, "argv": argv}, {})


def test_a_value_inside_a_larger_argument_is_refused():
    with pytest.raises(A.AdminRefused, match="whole argument"):
        A.render("x", {"params": {"id": {"type": "id"}}, "argv": ["winget", "--id={id}"]}, {"id": "VideoLAN.VLC"})


def test_runner_errors_are_kinds_only(tmp_path):
    def boom(argv, timeout):
        raise OSError("C:\\secret\\path not found")
    out = broker(tmp_path, approved("dns_flush"), boom).run(req("dns_flush", {}))
    assert out["status"] == "failed" and out["steps"][0]["output"] == "OSError"


def test_default_runner_refuses_off_windows():
    if os.name == "nt":
        pytest.skip("Windows")
    with pytest.raises(A.AdminRefused, match="only on Windows"):
        A.default_runner(["ipconfig.exe", "/flushdns"], 10)


def test_list_cli(capsys):
    assert A.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "not approved" in out and "APPROVED" not in out and "winget_install" in out
