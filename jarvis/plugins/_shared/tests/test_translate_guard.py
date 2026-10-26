"""Repo-wide guard (D58): every module that builds a chat request with a USER message either goes through the language layer, goes through the privacy gateway
(which translates), or is on a short allowlist with its reason. A new module that builds one and is on none of them FAILS this test, so "every model sees English
only" cannot erode one forgotten call at a time.

How it decides (grep based, on purpose): a non-test Python file under jarvis/ is a "request builder" when it has a `"messages":` key AND a `"role": "user"` message.
It is covered when its text (a) calls the layer (translate.english / entry_text / english_messages / lang.english_* / lang_hook.english / translate_input), or
(b) sends through the gateway (port 48950, `_gateway_base(`, a `GATEWAY = ...` address). Otherwise it must be in ALLOWED with a reason.
Stale allowlist entries (file gone, or the file now calls the layer) fail too: the list must stay honest.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]                       # .../jarvis
SKIP_DIRS = {"tests", "test", "node_modules", "face", "third_party", "vendor", "docs", "evals", "bench", "kaggle", "__pycache__", ".git", "ui", "fixtures"}
MESSAGES = re.compile(r"""["']messages["']\s*:""")
USER = re.compile(r"""["']role["']\s*:\s*["']user["']""")
LAYER = re.compile(r"translate\.(?:english|english_info|entry_text|english_messages|english_messages_ex|translate_to_english)\b|lang\.english_(?:for_agent|body)|lang_hook\.english"
                   r"|\btranslate_input\b")
GATEWAY = re.compile(r"48950|_gateway_base\(|\bGATEWAY\s*=")

# path (relative to jarvis/) -> why it may build a user message without calling the language layer itself
ALLOWED: dict[str, str] = {
    "node/jarvis_node/vision.py": "asks the local vision model about a screenshot; the prompt is built by code from the decider's English plan arguments, never from the owner's words",
    "bridge/second_opinion.py": "sends through privacy-gateway instances on ports 48954-48956 (config/second_opinion.json), the same privacy/gateway.py, which translates",
    "plugins/_shared/translate.py": "the language layer itself: its llama-swap backend sends fenced text to a LOCAL alias on 127.0.0.1 (test_translate.py proves nothing else is opened)",
    "plugins/doc_reader/core.py": "OCR: a fixed English instruction plus a page image; no owner text is in it",
    "console/jarvis_console/intent.py": "the intent classifier; its only caller (console server /api/classify) translates first (console/tests/test_lang_hook.py)",
    "kernel/jarvis_kernel/gateway.py": "a fixed English health probe, and call() whose caller (telegram /ask) translates first (kernel/tests/test_lang.py)",
    "kernel/jarvis_kernel/capability_probe.py": "a fixed English probe prompt",
    "kernel/jarvis_kernel/model_select.py": "builds bodies only to size a request and choose a provider; the request itself is made by llm_proxy, which uses the layer",
    "kernel/jarvis_kernel/router/translate.py": "converts the API SHAPE (OpenAI to Anthropic and back), not the language; the text goes through llm_proxy, which uses the layer",
    "scripts/self_repair.py": "JARVIS repairing its own code with prompts it wrote itself in English; no owner text",
    "persona/run_cases.py": "an evaluation runner with fixed English cases",
    "local_models/smoke.py": "a smoke test with a fixed English prompt",
    "privacy/demo_live.py": "a demonstration script with fixed text",
}


def request_builders() -> dict[str, str]:
    out = {}
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if SKIP_DIRS & set(rel.parts[:-1]) or p.name.startswith("test_") or p.name in ("conftest.py",) or p.name.endswith("_test.py"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if MESSAGES.search(text) and USER.search(text):
            out[rel.as_posix()] = text
    return out


def test_every_request_builder_with_a_user_message_uses_the_language_layer_or_the_gateway_or_is_allowed():
    uncovered = [rel for rel, text in sorted(request_builders().items()) if not LAYER.search(text) and not GATEWAY.search(text) and rel not in ALLOWED]
    assert not uncovered, (
        "These modules build a chat request with a user message but neither call the language layer (jarvis/plugins/_shared/translate.py: translate.english / "
        "entry_text / english_messages) nor send through the privacy gateway: " + ", ".join(uncovered) +
        ". Route the owner's text through translate.english() at the nearest entry, or (only if no owner text can reach it) add the file to ALLOWED with the reason.")


def test_the_allowlist_has_no_stale_entries():
    found = request_builders()
    gone = [rel for rel in ALLOWED if rel not in found]
    now_covered = [rel for rel in ALLOWED if rel in found and LAYER.search(found[rel])]
    assert not gone, f"allowlisted but no longer a request builder (or the file is gone): {gone}"
    assert not now_covered, f"allowlisted but now calls the language layer itself, so the entry can go: {now_covered}"


def test_the_guard_finds_the_known_entry_points_so_it_cannot_pass_by_finding_nothing():
    found = request_builders()
    for rel in ("plugins/desktop/decide.py", "plugins/local_team/core.py", "kernel/jarvis_kernel/telegram.py", "muse/chat_memory.py", "console/jarvis_console/intent.py"):
        assert rel in found, f"the scan no longer sees {rel}"
    assert len(found) >= 10


def test_a_made_up_new_module_would_be_caught(tmp_path):
    text = 'import urllib.request\nbody = {"model": "m", "messages": [{"role": "user", "content": owner_text}]}\nurllib.request.urlopen("http://127.0.0.1:8080/v1/chat/completions")\n'
    assert MESSAGES.search(text) and USER.search(text) and not LAYER.search(text) and not GATEWAY.search(text)
    fixed = text + "\nfrom jarvis.plugins._shared import translate\nowner_text = translate.english(owner_text)\n"
    assert LAYER.search(fixed)
