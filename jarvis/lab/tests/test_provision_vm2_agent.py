"""provision_vm2_agent.sh: the pinned Hermes Agent + model forwarder install for the Agent PC VM. Static checks, plus plan mode where bash really runs."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
SCRIPT = DEPLOY / "provision_vm2_agent.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")


def _bash_works() -> bool:
    if shutil.which("bash") is None:
        return False
    try:
        return subprocess.run(["bash", "-c", "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs_bash = pytest.mark.skipif(not _bash_works(), reason="a working bash is needed (on the laptop, bash is the empty WSL)")


@needs_bash
def test_it_is_valid_shell():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_nothing_is_piped_into_a_shell_and_nothing_unpinned_is_installed():
    assert not re.search(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba)?sh", TEXT)
    assert "uv sync --frozen" in TEXT and "--no-build" in TEXT                    # upstream's own uv.lock, hashes checked by uv, no source builds
    assert "--require-hashes" in TEXT
    assert not re.search(r"pip install(?![^\n]*--require-hashes)", TEXT.replace("uv pip install", "uv pip install"))      # every pip install is hash-pinned


def test_the_pins_are_the_documented_ones():
    assert 'HERMES_COMMIT="818c13be1dc4fd28987e1e881a9408224afd4535"' in TEXT        # v0.21.6, docs/01_RESEARCH_AUDIT.md
    assert 'UV_SHA256="4bb69cf95ed13f23713bb7bc09f241896ea0c75b7c557e615a20604b82f9c403"' in TEXT
    assert 'PY_VERSION=3.14.7' in TEXT and 'UV_VERSION=0.13.5' in TEXT
    for h in ("cc8b6eecb8661c146b8f89a60e97456ee086e91a784ed31ac450c3a9e613dd36", "7e04258ce01013d7d615e5b910a3b27fac937d7a95038227e79652b4ba3b4ceb",
              "ad0cef71c756a56e74863c2919cf0985f72decbcfcb550ee2f422e7c62b5eedc"):
        assert h in TEXT


def test_hashes_and_commit_are_enforced_not_just_printed():
    assert "hash differs from the pinned one" in TEXT and "the vendor's .sha256 does not list the pinned hash" in TEXT
    assert "commit differs from the pin" in TEXT and "source tree is not clean" in TEXT


def test_no_model_key_is_written_and_the_token_is_never_created_here():
    assert "api_key: jarvis-local" in TEXT                              # the dummy key the forwarder replaces
    assert not re.search(r"(?i)(sk-|gsk_|bearer\s+[A-Za-z0-9]{12,}|ghp_)", TEXT)
    assert "/etc/jarvis-lab/model.token" in TEXT
    assert not re.search(r">\s*/etc/jarvis-lab/model\.token|echo[^\n]*>\s*/etc/jarvis-lab", TEXT)       # only checked for, never written
    assert "root:600" in TEXT                                           # the forwarder starts only if the token is root 600


def test_hermes_runs_for_the_agent_and_the_verifier_with_a_restricted_config_pointing_at_the_loopback_forwarder():
    assert "for u in agent jarvis-verify" in TEXT
    assert "http://127.0.0.1:${FWD_PORT}/v1" in TEXT and "FWD_PORT=18790" in TEXT
    for off in ("browser", "computer_use", "cronjob", "kanban", "connections", "discord", "image_gen"):
        assert f"    - {off}\n" in TEXT
    for on in ("terminal", "file", "code_execution", "delegation"):
        assert f"    - {on}\n" not in TEXT                              # the lab design's tools stay enabled (match the lab, never widen)
    assert "memory_enabled: false" in TEXT and "send: false" in TEXT
    assert "chmod 0600" in TEXT and "mcp_servers" not in TEXT          # no MCP server, no kernel tool access from the lab agent


def test_the_install_is_root_owned_so_the_agent_cannot_change_its_own_runtime():
    assert 'chown -R root:root "$OPT"' in TEXT and "chmod -R go-w" in TEXT
    assert "usermod" not in TEXT and "sudoers" not in TEXT


@needs_bash
def test_plan_mode_only_prints_what_it_would_run():
    r = subprocess.run(["bash", str(SCRIPT), "--plan"], capture_output=True, text=True)
    if r.returncode == 2:                                               # not root, not aarch64, not Ubuntu 24.04 or provision_vm2.sh not run: it refuses first
        assert any(s in r.stdout for s in ("run as root", "aarch64", "Ubuntu 24.04 expected", "run provision_vm2.sh first"))
        return
    assert r.returncode == 0 and "would run: git clone" in r.stdout and "uv sync --frozen" in r.stdout and "would run: apt-get install" in r.stdout


# ---- the forwarder unit that this script installs exists next to it
def test_the_forwarder_files_it_installs_exist():
    for name in ("model_forwarder.py", "jarvis-model-forwarder.service"):
        assert (DEPLOY / name).is_file()
    assert "model_forwarder.py" in TEXT and "jarvis-model-forwarder.service" in TEXT

def test_the_verifier_gets_a_root_owned_cli_path_because_it_cannot_enter_the_agents_home():
    # first real smoke test: jarvis-verify ran /home/agent/.local/bin/hermes -> "Permission denied" (/home/agent is 0700), so no VERIFY.json was written
    assert "/opt/jarvis-verify/bin" in TEXT and "JARVIS_LAB_VERIFY_BIN=/opt/jarvis-verify/bin" in TEXT
    assert 'install -d -o root -g root -m 0755 /opt/jarvis-verify /opt/jarvis-verify/bin' in TEXT
    assert 'ln -sfn "$OPT/venv/bin/hermes" /opt/jarvis-verify/bin/hermes' in TEXT
