"""Helper LLM calls (emotion enrichment, chips, topic search) can have their budgets scaled for models that reason inline."""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from config.providers import aux_llm_scales


def test_defaults_leave_upstream_budgets_unchanged(monkeypatch):
    monkeypatch.delenv("NEKO_AUX_LLM_TIMEOUT_SCALE", raising=False)
    monkeypatch.delenv("NEKO_AUX_LLM_TOKEN_SCALE", raising=False)
    assert aux_llm_scales() == (1.0, 1.0)


@pytest.mark.parametrize("raw,expected", [("3", 3.0), ("2.5", 2.5), ("20", 20.0), ("1", 1.0)])
def test_valid_scales_are_applied(monkeypatch, raw, expected):
    monkeypatch.setenv("NEKO_AUX_LLM_TIMEOUT_SCALE", raw)
    monkeypatch.setenv("NEKO_AUX_LLM_TOKEN_SCALE", raw)
    assert aux_llm_scales() == (expected, expected)


@pytest.mark.parametrize("raw", ["", "abc", "0", "-2", "0.5", "21", "1e9", "nan"])
def test_bad_values_fall_back_to_one_never_shrink_or_explode(monkeypatch, raw):
    monkeypatch.setenv("NEKO_AUX_LLM_TIMEOUT_SCALE", raw)
    monkeypatch.setenv("NEKO_AUX_LLM_TOKEN_SCALE", raw)
    assert aux_llm_scales() == (1.0, 1.0)


def _run_emotion_call(monkeypatch, timeout_scale, token_scale):
    from main_logic.activity import llm_enrichment as le

    seen = {}

    class FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def ainvoke(self, _msgs):
            return SimpleNamespace(content='{"ok": true}')

    async def fake_create(model, base_url, api_key, **kw):
        seen.update(kw)
        return FakeLLM()

    class FakeCfg:
        async def aget_model_api_config(self, kind):
            return {"model": "m", "api_key": "k", "base_url": "http://x", "provider_type": None}

    monkeypatch.setattr("utils.llm_client.create_chat_llm_async", fake_create)
    monkeypatch.setattr("utils.config_manager.get_config_manager", lambda: FakeCfg())
    monkeypatch.setattr("utils.token_tracker.set_call_type", lambda *_a, **_k: None)
    monkeypatch.setenv("NEKO_AUX_LLM_TIMEOUT_SCALE", str(timeout_scale))
    monkeypatch.setenv("NEKO_AUX_LLM_TOKEN_SCALE", str(token_scale))
    out = asyncio.run(le._invoke_emotion_tier("prompt", timeout=8.0, label="t"))
    return out, seen


def test_the_emotion_call_gets_the_scaled_timeout_and_token_cap(monkeypatch):
    out, seen = _run_emotion_call(monkeypatch, 3, 4)
    assert out == '{"ok": true}'
    assert seen["timeout"] == 24.0 and seen["max_completion_tokens"] == 2048


def test_the_emotion_call_is_unchanged_without_scaling(monkeypatch):
    out, seen = _run_emotion_call(monkeypatch, 1, 1)
    assert seen["timeout"] == 8.0 and seen["max_completion_tokens"] == 512


from config.providers import memory_fetch_timeout_seconds


def test_memory_fetch_timeout_defaults_to_upstreams_five_seconds(monkeypatch):
    monkeypatch.delenv("NEKO_MEMORY_FETCH_TIMEOUT_S", raising=False)
    assert memory_fetch_timeout_seconds() == 5.0


@pytest.mark.parametrize("raw,expected", [("120", 120.0), ("5", 5.0), ("300", 300.0), ("45.5", 45.5)])
def test_memory_fetch_timeout_accepts_five_to_three_hundred(monkeypatch, raw, expected):
    monkeypatch.setenv("NEKO_MEMORY_FETCH_TIMEOUT_S", raw)
    assert memory_fetch_timeout_seconds() == expected


@pytest.mark.parametrize("raw", ["", "x", "0", "4", "301", "-1", "nan"])
def test_memory_fetch_timeout_rejects_nonsense(monkeypatch, raw):
    monkeypatch.setenv("NEKO_MEMORY_FETCH_TIMEOUT_S", raw)
    assert memory_fetch_timeout_seconds() == 5.0


def test_the_session_start_request_uses_it_for_both_the_first_fetch_and_the_hot_swap(monkeypatch):
    from types import SimpleNamespace
    from main_logic.core.lifecycle import LifecycleMixin

    bare = SimpleNamespace(_user_language_explicit=False, _conversation_render_language=None)
    monkeypatch.delenv("NEKO_MEMORY_FETCH_TIMEOUT_S", raising=False)
    assert LifecycleMixin._new_dialog_request_kwargs(bare) == {"timeout": 5.0}
    monkeypatch.setenv("NEKO_MEMORY_FETCH_TIMEOUT_S", "90")
    assert LifecycleMixin._new_dialog_request_kwargs(bare)["timeout"] == 90.0
    bare._conversation_render_language = "en"
    assert LifecycleMixin._new_dialog_request_kwargs(bare) == {"timeout": 90.0, "params": {"render_language": "en"}}
