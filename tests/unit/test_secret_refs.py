"""API keys stored as `file:` references resolve at runtime and never become part of the stored config."""

import json
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from utils.config_manager import secret_refs as sr


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """A folder allowed as a secret root, with one key file inside it and one file outside it."""
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "provider.key").write_text("sk-test-INSIDE-0001\r\nsecond line ignored\n", encoding="utf-8")
    outside = tmp_path / "elsewhere.key"
    outside.write_text("sk-test-OUTSIDE-0002\n", encoding="utf-8")
    monkeypatch.setenv(sr.ROOTS_ENV, str(root))
    sr._cache.clear()
    return root, outside


def test_reference_inside_an_allowed_root_resolves_to_the_first_line(vault):
    root, _ = vault
    assert sr.resolve_reference(f"file:{root / 'provider.key'}") == "sk-test-INSIDE-0001"


def test_without_configured_roots_nothing_resolves(vault, monkeypatch):
    root, _ = vault
    monkeypatch.delenv(sr.ROOTS_ENV)
    assert sr.resolve_reference(f"file:{root / 'provider.key'}") == ""


def test_files_outside_the_roots_relative_paths_and_traversal_are_refused(vault):
    root, outside = vault
    assert sr.resolve_reference(f"file:{outside}") == ""
    assert sr.resolve_reference("file:provider.key") == ""
    assert sr.resolve_reference(f"file:{root / '..' / 'elsewhere.key'}") == ""
    assert sr.resolve_reference("file:") == ""


def test_missing_oversized_and_directory_targets_resolve_to_empty(vault):
    root, _ = vault
    (root / "big.key").write_text("x" * (sr.MAX_BYTES + 1), encoding="utf-8")
    assert sr.resolve_reference(f"file:{root / 'nope.key'}") == ""
    assert sr.resolve_reference(f"file:{root / 'big.key'}") == ""
    assert sr.resolve_reference(f"file:{root}") == ""


def test_a_changed_file_is_picked_up_without_a_restart(vault):
    root, _ = vault
    ref = f"file:{root / 'provider.key'}"
    assert sr.resolve_reference(ref) == "sk-test-INSIDE-0001"
    p = root / "provider.key"
    p.write_text("sk-test-ROTATED-0003\n", encoding="utf-8")
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 5_000_000_000))
    assert sr.resolve_reference(ref) == "sk-test-ROTATED-0003"


def test_only_api_key_fields_that_hold_a_reference_are_changed(vault):
    root, _ = vault
    ref = f"file:{root / 'provider.key'}"
    cfg = {
        "AGENT_MODEL_API_KEY": ref,
        "ASSIST_API_KEY_QWEN": ref,
        "AGENT_MODEL_URL": ref,                 # not a key field: untouched
        "CORE_API_KEY": "sk-plain",             # not a reference: untouched
        "SOME_FLAG": True,
    }
    sr.resolve_config_secrets(cfg)
    assert cfg["AGENT_MODEL_API_KEY"] == "sk-test-INSIDE-0001"
    assert cfg["ASSIST_API_KEY_QWEN"] == "sk-test-INSIDE-0001"
    assert cfg["AGENT_MODEL_URL"] == ref and cfg["CORE_API_KEY"] == "sk-plain" and cfg["SOME_FLAG"] is True


def test_runtime_config_resolves_the_reference_while_the_stored_file_keeps_only_the_reference(vault, clean_user_data_dir):
    from utils.config_manager import get_config_manager
    root, _ = vault
    ref = f"file:{root / 'provider.key'}"
    cm = get_config_manager('N.E.K.O')
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    stored = {
        "enableCustomApi": True,
        "agentModelProvider": "custom", "agentModelUrl": "https://provider.example/v1",
        "agentModelId": "some-model", "agentModelApiKey": ref,
        "conversationModelProvider": "custom", "conversationModelUrl": "https://provider.example/v1",
        "conversationModelId": "some-model", "conversationModelApiKey": ref,
    }
    path = cm.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as f:
        json.dump(stored, f)
    cm._core_config_cache = None

    runtime = cm.get_core_config()
    assert runtime["AGENT_MODEL_API_KEY"] == "sk-test-INSIDE-0001"
    assert runtime["CONVERSATION_MODEL_API_KEY"] == "sk-test-INSIDE-0001"
    agent = cm.get_model_api_config("agent")
    assert agent["api_key"] == "sk-test-INSIDE-0001" and agent["base_url"] == "https://provider.example/v1"

    on_disk = json.loads(open(str(path), encoding='utf-8').read())
    assert on_disk["agentModelApiKey"] == ref and "sk-test-INSIDE-0001" not in json.dumps(on_disk)
