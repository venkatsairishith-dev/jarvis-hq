"""D54b (2026-10-26): the labelled cases and the runner the laptop session executes against the real models. Fakes only here: the rules-only mode needs no model, and a
fake model function stands in for the 4B / 1.7B to test the arithmetic (accuracy per kind, thresholds, the choice of the cheaper model, --apply)."""
import importlib.util
import json
from pathlib import Path

import pytest

from jarvis.plugins._shared import decider as D

EVAL = Path(__file__).resolve().parents[3] / "evals" / "pc_control" / "eval_decider.py"
spec = importlib.util.spec_from_file_location("eval_decider", EVAL)
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


def test_there_are_at_least_sixty_labelled_cases_of_every_kind_the_owner_named():
    cases = json.loads(E.CASES.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 60 and len({c["id"] for c in cases}) == len(cases)
    kinds = {c["kind"] for c in cases}
    assert kinds >= {"intent", "dialog_class", "specialist", "folder", "file", "app"}
    tags = {t for c in cases for t in c.get("tags", [])}
    assert tags >= {"hindi", "telugu", "mixed", "injection", "vague", "dialog"}
    assert sum("injection" in c.get("tags", []) for c in cases) >= 4
    for c in cases:
        opts = E.options_of(c)
        assert c["expect"] == "ask" or c["expect"] in opts, c["id"]
        assert all(a in opts for a in c.get("accept", [])), c["id"]
        if c["kind"] in ("folder", "file", "app"):
            assert c["candidates"], c["id"]
    assert {c["expect"] for c in cases if c["kind"] == "intent"} >= {"chat", "pc_task", "screen_read", "doc_read", "file_task", "delete_cleanup", "ask_ui", "web_search", "weather"}


def test_the_catalogue_examples_are_added_as_simple_task_cases():
    cases = E.load_cases()
    cat = [c for c in cases if c["id"].startswith("T-")]
    assert len(cat) >= 50 and all(c["expect"] == "simple_task" and c["kind"] == "intent" for c in cat)
    assert not [c for c in cat if c["id"] in ("T-send_message", "T-pay_buy")]            # class C and ask entries are not "simple task" cases


def test_rules_only_runs_without_a_model_and_writes_the_results(tmp_path):
    out = tmp_path / "r.json"
    assert E.main(["--rules-only", "--out", str(out)]) == 0
    r = json.loads(out.read_text(encoding="utf-8"))
    eng = r["rules_only"]["engine"]
    assert set(eng) >= {"intent", "dialog_class", "folder"} and eng["intent"]["accuracy"] >= 0.9
    for kind, s in eng.items():
        assert not s["sure_but_wrong"], (kind, s["sure_but_wrong"])                     # a wrong answer given with confidence is the thing that must not happen
        assert not s["lowered_class"]
    assert r["suggested_thresholds"]["dialog_class"] == 0.0 and set(r["suggested_thresholds"]) >= set(eng)
    assert "hindi" in eng["intent"]["by_tag"] and "injection" in eng["intent"]["by_tag"]


def test_a_fake_model_is_scored_per_kind_with_and_without_the_rules():
    case = {"id": "x1", "kind": "intent", "text": "zzz unclear", "expect": "weather"}
    ok = E.run_case(case, None, True, post=lambda b: json.dumps({"choice": "weather", "confidence": 0.9}))
    bad = E.run_case(case, None, True, post=lambda b: json.dumps({"choice": "chat", "confidence": 0.9}))
    assert ok["ok"] and not bad["ok"] and bad["got"] == "chat"
    asked = E.run_case(case, None, True, post=lambda b: json.dumps({"choice": "weather", "confidence": 0.2}))
    assert asked["got"] == "ask" and not asked["ok"]
    only_model = E.run_case({"id": "x2", "kind": "intent", "text": "what is on my screen", "expect": "weather"}, None, False, post=lambda b: json.dumps({"choice": "weather", "confidence": 0.9}))
    assert only_model["ok"] and only_model["source"] == "model"                        # rules skipped: the model alone answered


def test_the_dialog_check_reports_a_lowered_class_as_a_violation():
    case = {"id": "D07", "kind": "dialog_class", "text": "Delete file. Are you sure? buttons: Yes, No", "expect": "C"}
    r = E.run_case(case, None, True, post=lambda b: json.dumps({"choice": "A", "confidence": 1.0}))
    assert r["got"] == "C" and not r.get("lowered")


def rows(kind, pairs):
    return [{"kind": kind, "ok": ok, "got": "x" if conf is not None else "ask", "confidence": conf or 0.0, "expect": "x", "tags": [], "id": str(i), "ms": 10, "source": "model"}
            for i, (ok, conf) in enumerate(pairs)]


def test_thresholds_are_the_smallest_confidence_that_holds_the_accuracy_with_enough_coverage():
    pairs = [(True, 0.95)] * 8 + [(False, 0.55)] * 2                                   # the low-confidence ones are the wrong ones
    assert E.suggest_thresholds(rows("intent", pairs))["intent"] == 0.6
    assert E.suggest_thresholds(rows("intent", [(False, 0.9)] * 5 + [(True, 0.9)] * 5))["intent"] == 0.9      # nothing holds: the cautious default
    assert E.suggest_thresholds(rows("dialog_class", pairs))["dialog_class"] == 0.0


def test_the_cheaper_model_is_chosen_per_kind_only_when_it_holds_within_two_points():
    per = {"decider": {"model": {"intent": {"accuracy": 0.95}, "folder": {"accuracy": 0.9}}},
           "decider-qwen3-1.7b": {"model": {"intent": {"accuracy": 0.94}, "folder": {"accuracy": 0.7}}}}
    assert E.pick_models(per, "decider") == {"intent": "decider-qwen3-1.7b"}


def test_apply_writes_only_known_kinds_into_the_config_file(tmp_path):
    cfg = tmp_path / "decider.json"
    cfg.write_text(json.dumps({"_note": "n", "thresholds": {"intent": 0.7}, "models": {}, "timeout_s": 3.0}), encoding="utf-8")
    E.apply_config({"intent": 0.8, "bogus": 0.1}, {"intent": "decider-qwen3-1.7b", "bogus": "x"}, cfg)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["thresholds"]["intent"] == 0.8 and "bogus" not in data["thresholds"] and data["models"] == {"intent": "decider-qwen3-1.7b"} and data["timeout_s"] == 3.0
    assert D.config(cfg)["models"] == {"intent": "decider-qwen3-1.7b"}
