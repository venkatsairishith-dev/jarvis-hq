"""The roster of local specialists (jarvis/config/specialists.json, jarvis/plugins/_shared/specialists.py)."""
import json
import re
import warnings

import pytest

from jarvis.plugins._shared import specialists as S


def table_of(**kinds):
    return {"kinds": kinds}


def test_the_shipped_table_is_valid_and_has_every_kind():
    t = S.load()
    assert set(t) == set(S.KINDS)
    assert t["reason"].where == "online" and not t["reason"].aliases
    assert all(t[k].where == "local" and t[k].alias for k in S.KINDS if k != "reason")
    for k in ("screen_read", "screen_locate", "doc_read", "ui_decide", "chat_simple"):
        assert t[k].max_tokens > 0


def test_a_missing_file_means_the_built_in_defaults_which_equal_the_shipped_table(tmp_path):
    built_in = S.load(tmp_path / "nope.json")
    shipped = S.load()
    assert {k: (v.where, v.alias, v.fallback, v.max_tokens) for k, v in built_in.items()} == \
           {k: (v.where, v.alias, v.fallback, v.max_tokens) for k, v in shipped.items()}


def test_resolve_gives_the_alias_and_an_unknown_kind_is_a_clear_error():
    assert S.resolve("ui_decide").alias == "decider"
    assert S.resolve("screen_read").aliases == ("vision-qwen35", "vision-ground")
    with pytest.raises(S.SpecialistError, match="no specialist for task kind 'telepathy'.*known kinds"):
        S.resolve("telepathy")


@pytest.mark.parametrize("data,message", [
    ({}, "non-empty"),
    ({"kinds": {}}, "non-empty"),
    ({"kinds": {"telepathy": {"alias": "x"}}}, "unknown task kind 'telepathy'"),
    ({"kinds": {"Bad Kind": {"alias": "x"}}}, "not a task kind name"),
    ({"kinds": {"doc_read": "lightonocr"}}, "must be an object"),
    ({"kinds": {"doc_read": {"where": "local"}}}, "needs an alias"),
    ({"kinds": {"doc_read": {"alias": "has space"}}}, "not a llama-swap model name"),
    ({"kinds": {"doc_read": {"alias": "ok", "fallback": "../../etc"}}}, "fallback"),
    ({"kinds": {"doc_read": {"alias": "ok", "where": "online"}}}, "only \"reason\" is online"),
    ({"kinds": {"reason": {"where": "online", "alias": "decider"}}}, "takes no local alias"),
    ({"kinds": {"reason": {"where": "local", "alias": "decider"}}}, "only \"reason\" is online"),
    ({"kinds": {"doc_read": {"alias": "ok", "max_tokens": -1}}}, "max_tokens"),
    ({"kinds": {"doc_read": {"alias": "ok", "max_tokens": "many"}}}, "max_tokens"),
])
def test_a_wrong_table_is_refused_with_a_message_that_names_the_entry(data, message):
    with pytest.raises(S.SpecialistError, match=message):
        S.parse(data)


def test_a_file_that_is_not_json_is_an_error_not_the_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(S.SpecialistError, match="not valid JSON"):
        S.load(p)


def test_a_custom_file_is_used(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(table_of(doc_read={"alias": "my-ocr", "fallback": "docreader", "max_tokens": 900})), encoding="utf-8")
    t = S.load(p)
    assert S.resolve("doc_read", t).aliases == ("my-ocr", "docreader")
    assert set(t) == set(S.KINDS) and S.resolve("ui_decide", t).alias == "decider"          # a partial file keeps the built-in rows of the other kinds (review 2026-10-26)
    with pytest.raises(S.SpecialistError):
        S.resolve("not_a_kind", t)


def test_an_alias_the_swap_file_lacks_is_reported_not_failed(tmp_path):
    swap = tmp_path / "llama-swap.yaml"
    swap.write_text("models:\n  ling-tiny:\n    aliases: [general]\n    cmd: x\ngroups:\n  g:\n    members: [ling-tiny]\n", encoding="utf-8")
    t = S.parse(table_of(chat_simple={"alias": "local-chat", "fallback": "general"}, doc_read={"alias": "ling-tiny"}))
    assert S.not_installed(t, swap) == [("chat_simple", "alias", "local-chat")]
    assert S.not_installed(t, tmp_path / "missing.yaml") == [], "no swap file to compare with: nothing to report"


def test_report_what_is_not_installed_yet_in_the_shipped_roster():
    """Not a failure: the roster may be ahead of the install (local-chat, the Ling MoE alias of the gateway's local provider, is still to come)."""
    missing = S.not_installed()
    if missing:
        warnings.warn("not installed yet in jarvis/local_models/llama-swap.yaml: " + ", ".join(f"{k}.{label}={name}" for k, label, name in missing))
        print("not installed yet:", missing)
    assert isinstance(missing, list)
    have = S.swap_aliases()
    assert {"decider", "vision", "vision-ground", "docreader", "lightonocr-docreader", "general"} <= have


def test_the_table_agrees_with_what_the_callers_still_hard_code():
    """Until every caller reads the table, its constants and the table must say the same model (the swap file maps aliases to models)."""
    t = S.load()
    root = S.JARVIS
    decide = (root / "plugins" / "desktop" / "decide.py").read_text(encoding="utf-8")
    vision = (root / "node" / "jarvis_node" / "vision.py").read_text(encoding="utf-8")
    docs = (root / "plugins" / "doc_reader" / "core.py").read_text(encoding="utf-8")
    default_decider = re.search(r'(?m)^DEFAULT_MODEL\s*=\s*"([^"]+)"', decide).group(1)
    default_vision = re.search(r'(?m)^VISION_MODEL\s*=\s*os\.environ\.get\("JARVIS_VISION_MODEL",\s*"([^"]+)"\)', vision).group(1)
    doc_alias = re.search(r'(?m)^MODEL\s*=\s*"([^"]+)"', docs).group(1)
    text = "\n".join(ln for ln in S.SWAP_FILE.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))
    section = text.split("\nmodels:", 1)[1].split("\ngroups:", 1)[0]
    model_of = {}
    for block in re.split(r"(?m)^  (?=[A-Za-z0-9][\w.\-]*:\s*$)", section)[1:]:
        name = block.split(":", 1)[0]
        model_of[name] = name
        for group in re.findall(r"(?m)^\s+aliases:\s*\[([^\]]*)\]", block):
            for a in group.split(","):
                model_of[a.strip()] = name
    assert default_decider == t["ui_decide"].alias
    assert model_of[default_vision] == model_of[t["screen_locate"].alias]
    assert model_of[doc_alias] == model_of[t["doc_read"].alias]
