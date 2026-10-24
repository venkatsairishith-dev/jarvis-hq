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
