"""The Agent PC provisioning script for hermes-agent-vm-2: valid shell, safe by construction, and its plan mode changes nothing."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "provision_vm2.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash needed")
def test_it_is_valid_shell():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_no_pipe_to_shell_no_secrets_no_open_vnc():
    assert not re.search(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba)?sh", TEXT)
    assert not re.search(r"(?i)password|api[_-]?key|token\s*=", TEXT.replace("runner token", ""))
    assert "-localhost yes" in TEXT and "-SecurityTypes None" in TEXT       # no-auth VNC only because it is bound to loopback
    assert "ufw allow 22/tcp" in TEXT and "ufw allow 5901" not in TEXT


def test_the_mozilla_repo_is_signed_pinned_and_fingerprint_checked():
    assert "signed-by=/etc/apt/keyrings/packages.mozilla.org.asc" in TEXT
    assert re.search(r'MOZ_KEY_FPR="[0-9A-F]{40}"', TEXT) and "fingerprint mismatch" in TEXT


def test_lab_users_get_no_sudo_and_the_install_is_root_owned():
    assert not re.search(r"usermod|sudoers|-aG|adduser\s+\S+\s+sudo|--groups", TEXT)           # no user is added to a privileged group
    assert "install -o root -g root -m 0644" in TEXT and "chmod 0700" in TEXT


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash needed")
def test_plan_mode_only_prints_what_it_would_run():
    r = subprocess.run(["bash", str(SCRIPT), "--plan"], capture_output=True, text=True)
    if r.returncode == 2:                                    # not root, or not Ubuntu 24.04: it refuses before doing anything
        assert "run as root" in r.stdout or "Ubuntu 24.04 expected" in r.stdout
        return
    assert r.returncode == 0 and "would run: apt-get install" in r.stdout and "would run: ufw --force enable" in r.stdout
    assert "Mozilla key fingerprint mismatch" not in r.stdout


def test_the_hub_account_exists_before_lab_control_starts_and_rpcbind_is_off():
    # first real run: lab_control chowns its socket to group hub and crash-looped because nothing created it
    assert "groupadd --system hub" in TEXT and "--shell /usr/sbin/nologin hub" in TEXT
    assert TEXT.index("groupadd --system hub") < TEXT.index("enable --now jarvis-desktop.service jarvis-lab-control.service")
    assert "rpcbind" in TEXT and "disable --now" in TEXT
    assert "usermod" not in TEXT and "sudoers" not in TEXT


# ---- Tailscale on the Agent PC VM
TS = (Path(__file__).resolve().parents[1] / "deploy" / "provision_vm2_tailscale.sh").read_text(encoding="utf-8")


def test_tailscale_script_is_valid_signed_apt_no_pipe_no_auto_login():
    assert subprocess.run(["bash", "-n", str(Path(__file__).resolve().parents[1] / "deploy" / "provision_vm2_tailscale.sh")]).returncode == 0
    assert "pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg" in TS and "tailscale-archive-keyring.gpg" in TS
    assert not re.search(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba)?sh", TS) and "install.sh" not in re.sub(r"(?m)^#.*$", "", TS)
    code = re.sub(r"(?m)^\s*(#|echo|\").*$", "", TS)
    assert not re.search(r"tailscale up|authkey|auth-key|--login", code)               # it prints the command, the owner runs it


def test_tailscale_script_keeps_the_agent_accounts_off_the_tailnet_and_shields_the_vm():
    for u in ("agent", "jarvis-sandbox", "jarvis-verify"):
        assert u in TS
    assert "100.64.0.0/10" in TS and "fd7a:115c:a1e0::/48" in TS and "--uid-owner" in TS and "-j REJECT" in TS
    assert "--shields-up" in TS and "--accept-routes=false" in TS and "--ssh" not in TS


def test_tailscale_plan_mode_only_prints():
    r = subprocess.run(["bash", str(Path(__file__).resolve().parents[1] / "deploy" / "provision_vm2_tailscale.sh"), "--plan"], capture_output=True, text=True)
    assert r.returncode in (0, 2)
    if r.returncode == 0:
        assert "would fetch https://pkgs.tailscale.com" in r.stdout and "would run: systemctl enable --now tailscaled" in r.stdout


# ---- cua-driver on the Agent PC VM
CUA_PATH = Path(__file__).resolve().parents[1] / "deploy" / "provision_vm2_cua.sh"
CUA = CUA_PATH.read_text(encoding="utf-8")


def test_cua_script_pins_the_file_checks_two_hashes_and_does_not_pipe_to_a_shell():
    assert subprocess.run(["bash", "-n", str(CUA_PATH)]).returncode == 0
    assert 'PIN_SHA256="9db8b9084add57eb97be8164367b24b6be54ed4f3dc01213e64b72d7fc09fddb"' in CUA and "linux-arm64-binary.tar.gz" in CUA
    assert 'grep -q "^$PIN_SHA256  $FILE' in CUA and "SHA256SUMS" in CUA and "stopping" in CUA
    code = re.sub(r"(?m)^\s*#.*$", "", CUA)
    assert not re.search(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba)?sh", code) and "cua.ai/driver/install" not in code


def test_cua_telemetry_is_blocked_and_disabled_before_the_driver_does_anything_else():
    hosts, disable, doctor = CUA.index("jarvis-cua-telemetry-block"), CUA.index("/usr/local/bin/cua-driver telemetry disable"), CUA.index("DISPLAY=:1 /usr/local/bin/cua-driver doctor")
    install = CUA.index('tar -xzf "$tmp/$FILE"')
    assert hosts < install < disable < doctor                       # hosts blocked, then unpacked, then disabled, then anything else
    assert "eu.i.posthog.com" in CUA and "telemetry status" in CUA and "grep -qi 'disabled'" in CUA
    assert "ExecStartPre=/usr/local/bin/cua-driver telemetry disable" in CUA


def test_cua_service_is_installed_but_not_started_and_runs_as_the_agent_user():
    assert "User=agent" in CUA and "DISPLAY=:1" in CUA and "--permission-mode standard" in CUA
    code = re.sub(r"(?m)^\s*#.*$", "", CUA)
    assert "[Install]" in CUA                                       # without it the unit is "static" and the owner could never enable it
    assert not re.search(r"systemctl (enable|start)", code) and "--dangerously-bypass-approvals" not in CUA and "unrestricted" not in code


def test_cua_plan_mode_only_prints():
    r = subprocess.run(["bash", str(CUA_PATH), "--plan"], capture_output=True, text=True)
    assert r.returncode == 2 or "would download" in r.stdout
