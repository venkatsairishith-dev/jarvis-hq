"""Tests for jarvis/tools/doctor.py: each check on fixtures, and the whole run on this checkout."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.tools import doctor as D  # noqa: E402

PROC_TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:225F 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1 1 0 100 0 0 10 0
   1: 00000000:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 2 1 0 100 0 0 10 0
   2: 0100007F:225F 0100007F:C350 01 00000000:00000000 00:00000000 00000000     0        0 3 1 0 100 0 0 10 0
"""
PROC_TCP6 = """  sl  local_address                         remote_address                        st
   0: 00000000000000000000000001000000:BD8E 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 4 1
"""
NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1100
  TCP    127.0.0.1:8799         0.0.0.0:0              LISTENING       4242
  TCP    127.0.0.1:8799         127.0.0.1:50000        ESTABLISHED     4242
  TCP    100.101.102.103:8790   0.0.0.0:0              ABHÖREN         77
  TCP    [::]:445               [::]:0                 LISTENING       4
  TCP    [::1]:48911            [::]:0                 LISTENING       900
"""


def test_parse_proc_net_v4_and_v6():
    assert D.parse_proc_net(PROC_TCP) == [("127.0.0.1", 8799), ("0.0.0.0", 8080)]
    assert D.parse_proc_net(PROC_TCP6) == [("::1", 48526)]


def test_parse_netstat_any_language():
    assert D.parse_netstat(NETSTAT) == [("0.0.0.0", 135), ("127.0.0.1", 8799), ("100.101.102.103", 8790), ("::", 445),
                                        ("::1", 48911)]


@pytest.mark.parametrize("ip,want", [("127.0.0.1", "loopback"), ("::1", "loopback"), ("0.0.0.0", "all"), ("::", "all"),
                                     ("100.101.102.103", "tailscale"), ("fd7a:115c:a1e0::1", "tailscale"), ("192.168.1.5", "lan"),
                                     ("8.8.8.8", "public"), ("::ffff:127.0.0.1", "loopback"), ("nonsense", "public")])
def test_reach(ip, want):
    assert D.reach(ip) == want


def levels(findings):
    return [f.level for f in findings]


def test_listeners_ok_when_ours_are_local():
    f = D.check_listeners([("127.0.0.1", 8799), ("100.101.102.103", 8790)], {8799: "console", 8790: "kernel"})
    assert levels(f) == ["ok"] and "2 JARVIS port(s)" in f[0].detail


def test_listeners_fail_when_ours_is_exposed():
    f = D.check_listeners([("0.0.0.0", 8799), ("192.168.1.5", 8790)], {8799: "console", 8790: "kernel"})
    assert levels(f) == ["fail", "fail"]
    assert "every interface" in f[1].detail or "every interface" in f[0].detail


def test_listeners_warn_on_other_exposed_ports():
    f = D.check_listeners([("0.0.0.0", 135), ("::", 445), ("127.0.0.1", 9000)], {8799: "console"})
    assert levels(f) == ["warn", "ok"] and "135, 445" in f[0].detail


def test_listeners_skip_when_unknown():
    assert levels(D.check_listeners(None, {})) == ["skip"]


# ---- secrets --------------------------------------------------------------------------------------------------------------------------

FAKE_KEY = "sk-ant-" + "a1B2c3D4" * 4


def reader(contents):
    return lambda f: contents[f]


def test_secrets_key_named_files(tmp_path):
    files = ["jarvis/config/claude.key", "jarvis/x/.env", "jarvis/x/.env.example", "tests/fixtures/fake.pem", "jarvis/a.py"]
    f = D.check_secrets(files, reader({}), tmp_path / "absent")
    fails = [x.detail for x in f if x.level == "fail"]
    assert len(fails) == 2 and any("claude.key" in d for d in fails) and any(".env " in d for d in fails)


def test_secrets_key_shaped_string_never_printed(tmp_path):
    files = ["jarvis/config/providers.json", "jarvis/config/models.json", "jarvis/other/notes.json"]
    contents = {"jarvis/config/providers.json": f'{{"key": "{FAKE_KEY}"}}', "jarvis/config/models.json": '{"m": "deepseek-ai/DeepSeek-V4-Flash-0731"}',
                "jarvis/other/notes.json": FAKE_KEY}
    f = D.check_secrets(files, reader(contents), tmp_path / "absent")
    fails = [x for x in f if x.level == "fail"]
    assert [x.detail.split()[0] for x in fails] == ["jarvis/config/providers.json"]      # outside config folders is not scanned
    assert all(FAKE_KEY not in x.detail for x in f)


def test_secrets_clean_and_no_git(tmp_path):
    assert levels(D.check_secrets([], reader({}), tmp_path / "absent")) == ["ok", "skip"]
    assert levels(D.check_secrets(None, reader({}), tmp_path / "absent")) == ["skip", "skip"]


def test_secrets_dir_inside_repo_fails():
    inside = D.ROOT / "jarvis"
    f = D.check_secrets([], reader({}), inside)
    assert any(x.level == "fail" and "inside the repo" in x.detail for x in f)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_secrets_dir_permissions(tmp_path):
    d = tmp_path / "secrets"
    d.mkdir()
    d.chmod(0o755)
    assert any(x.level == "fail" and "other users" in x.detail for x in D.check_secrets([], reader({}), d))
    d.chmod(0o700)
    assert not any(x.level == "fail" for x in D.check_secrets([], reader({}), d))


# ---- egress and providers -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("proxy,level", [("http://100.125.91.51:4750", "ok"), ("http://127.0.0.1:4750", "ok"),
                                         ("http://proxy.example.com:4750", "fail"), ("https://100.125.91.51:4750", "fail")])
def test_egress(tmp_path, proxy, level):
    p = tmp_path / "egress.json"
    p.write_text(json.dumps({"proxy": proxy}))
    assert levels(D.check_egress(p)) == [level]


def test_egress_missing(tmp_path):
    assert levels(D.check_egress(tmp_path / "none.json")) == ["fail"]


def providers_file(tmp_path, providers):
    p = tmp_path / "providers.json"
    p.write_text(json.dumps({"providers": providers}))
    return p


def test_providers_clean(tmp_path):
    p = providers_file(tmp_path, [
        {"name": "local", "base_url": "http://127.0.0.1:8080/v1", "enabled": True, "paid": False},
        {"name": "dahl", "base_url": "https://inference.dahl.global/v1", "enabled": True, "default_model": "deepseek-ai/DeepSeek-V4-Flash-0731"},
        {"name": "off", "base_url": "http://plain.example/v1", "enabled": False, "paid": True}])
    assert levels(D.check_providers(p)) == ["ok"]


def test_providers_problems(tmp_path):
    p = providers_file(tmp_path, [
        {"name": "clear", "base_url": "http://api.example.com/v1", "enabled": True},
        {"name": "paid", "base_url": "https://api.example.com/v1", "enabled": True, "paid": True},
        {"name": "tunnel", "base_url": "https://abc.ngrok-free.app/v1", "enabled": True},
        {"name": "inline", "base_url": "https://api.example.com/v1", "enabled": False, "api_key": FAKE_KEY}])
    f = D.check_providers(p)
    got = {(x.level, x.detail.split(":")[0].split()[0]) for x in f}
    assert got == {("fail", "inline"), ("fail", "clear"), ("warn", "paid"), ("warn", "tunnel")}
    assert all(FAKE_KEY not in x.detail for x in f)


def test_repo_providers_and_egress_pass():
    assert "fail" not in levels(D.check_providers(D.JARVIS / "config" / "providers.json"))
    assert levels(D.check_egress(D.JARVIS / "config" / "egress.json")) == ["ok"]


# ---- skills ---------------------------------------------------------------------------------------------------------------------------

def stage(skills: Path, name: str, files: dict[str, bytes], tier="low", write=True) -> str:
    d = skills / "staging" / name
    d.mkdir(parents=True)
    recs = []
    for rel, data in files.items():
        if write:
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_bytes(data)
        recs.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest()})
    manifest = hashlib.sha256(json.dumps(recs, sort_keys=True).encode()).hexdigest()
    (d / "PROVENANCE.json").write_text(json.dumps({"files": recs, "risk": {"tier": tier}, "manifest_sha256": manifest}))
    return manifest


def activate(skills: Path, **lab):
    (skills / "active.json").write_text(json.dumps({"lab": {k: {"sha256": v} for k, v in lab.items()}}))


def test_skills_ok_with_rejected_record(tmp_path):
    m = stage(tmp_path, "good", {"SKILL.md": b"hello", "references/a.md": b"a"})
    stage(tmp_path, "bad", {"SKILL.md": b"jailbreak"}, tier="reject", write=False)
    activate(tmp_path, good=m)
    f = D.check_skills(tmp_path)
    assert levels(f) == ["ok"] and "1 rejected" in f[0].detail and "1 active" in f[0].detail


def test_skills_changed_and_missing(tmp_path):
    stage(tmp_path, "edited", {"SKILL.md": b"original"})
    (tmp_path / "staging" / "edited" / "SKILL.md").write_bytes(b"tampered")
    stage(tmp_path, "gone", {"SKILL.md": b"x"}, write=False)
    f = D.check_skills(tmp_path)
    assert levels(f) == ["fail", "fail"]
    assert "edited: changed" in f[0].detail and "gone: a recorded file is missing" in f[1].detail


def test_skills_active_mismatch_and_rejected_active(tmp_path):
    stage(tmp_path, "good", {"SKILL.md": b"hello"})
    m = stage(tmp_path, "bad", {"SKILL.md": b"x"}, tier="reject", write=False)
    activate(tmp_path, good="0" * 64, bad=m)
    details = [x.detail for x in D.check_skills(tmp_path)]
    assert "good is active but differs from its staged, checked copy" in details
    assert "bad is active although the skill gate rejected it" in details


def test_skills_none(tmp_path):
    assert levels(D.check_skills(tmp_path)) == ["skip"]


def test_repo_skills_pass():
    assert levels(D.check_skills(D.JARVIS / "skills")) == ["ok"]


# ---- whole run ------------------------------------------------------------------------------------------------------------------------

def test_crashing_check_is_reported(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(D, "check_docs", boom)
    f = D.run_all()
    assert any(x.level == "fail" and "crashed (RuntimeError)" in x.detail for x in f)


def test_main_json_and_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(D, "run_all", lambda: [D.Finding("a", "ok", "fine"), D.Finding("b", "warn", "hmm")])
    assert D.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)[1] == {"check": "b", "level": "warn", "detail": "hmm"}
    monkeypatch.setattr(D, "run_all", lambda: [D.Finding("a", "fail", "bad")])
    assert D.main([]) == 1
    assert "1 fail" in capsys.readouterr().out


def test_manifests_and_licences_pass_on_repo():
    assert set(levels(D.check_manifests())) == {"ok"}
    assert levels(D.check_licences()) == ["ok"]
