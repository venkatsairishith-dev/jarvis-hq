"""goals.json is sound and covers what the owner asked the gate to measure (D59)."""
import copy
import json
import re

import pytest

from jarvis.evals.reliability import model

CAT = model.load()
REQUIRED = {
    "open_type_save_click", "screen_text", "screen_vision", "final_answer", "delete_cleanup", "cleaner_panel", "generated_ui", "window_responsiveness",
    "lang_proper_script", "lang_romanized", "local_moe_only", "ambiguous_asks", "pin_class_c", "simple_tasks", "solve_on_screen", "problem_recovery",
}


def test_the_catalogue_is_sound():
    assert model.validate(CAT) == []


def test_every_required_capability_exists_with_at_least_ten_goals():
    assert REQUIRED <= set(CAT.capabilities)
    for cid in REQUIRED:
        assert len(CAT.capabilities[cid].goals) >= 10, cid


def test_owner_estimates_are_recorded_beside_the_capabilities():
    est = {c.id: c.estimate for c in CAT.capabilities.values()}
    assert est["open_type_save_click"] == 0.75 and est["screen_text"] == 0.80 and est["screen_vision"] == 0.50
    assert est["final_answer"] == 0.80 and est["delete_cleanup"] == 0.85 and est["cleaner_panel"] == 0.85 and est["generated_ui"] == 0.80
    assert est["window_responsiveness"] == 0.60 and est["lang_proper_script"] == 0.60 and est["lang_romanized"] == 0.40 and est["local_moe_only"] == 0.30


def test_the_owners_named_goals_are_in():
    ids = {g.id for g in CAT.goals()}
    texts = " | ".join(g.text for g in CAT.goals())
    for must in ("Open Notepad and write a shopping list: milk, eggs, bread, rice.", "What is on my screen right now?", "Make a 3-slide deck about our week and show it to me.",
                 "Two things at once: open Calculator and also tell me the weather in Paris."):
        assert must in texts, must
    assert any("invoice" in g.text.lower() and "total" in g.text.lower() for g in CAT.capabilities["screen_vision"].goals)
    assert {"otsc-notepad-save", "otsc-vscode-new-folder", "ss-add", "dc-bin-one", "dc-permanent", "dc-cleanup-safe", "cp-open"} <= ids


def test_delete_goals_cover_bin_permanent_and_safe_cleanup():
    t = " ".join(g.text.lower() for g in CAT.capabilities["delete_cleanup"].goals)
    assert "recycle bin" in t and "permanently" in t and "clean up the junk" in t and "restore" in t


def test_language_capabilities_have_native_and_romanized_phrasings_of_the_same_tasks():
    te = [g for g in CAT.capabilities["lang_proper_script"].goals if g.lang == "te"]
    hi = [g for g in CAT.capabilities["lang_proper_script"].goals if g.lang == "hi"]
    assert len(te) >= 5 and len(hi) >= 5
    assert all(re.search(r"[ఀ-౿]", g.text) for g in te) and all(re.search(r"[ऀ-ॿ]", g.text) for g in hi)
    rom = CAT.capabilities["lang_romanized"].goals
    assert {g.lang for g in rom} == {"te-rom", "hi-rom"} and all(re.fullmatch(r"[\x00-\x7f]+", g.text) for g in rom)
    # a twin does the same task with the same verified effect as its English original
    by = {g.id: g for g in CAT.goals()}
    paired = [g for g in te + hi + rom if g.twin_of in by and by[g.twin_of].kind in ("act", "act_or_ask") and g.kind in ("act", "act_or_ask")]
    assert len(paired) >= 12
    for g in paired:
        assert g.verify, g.id


def test_ambiguous_goals_ask_and_class_c_goals_stop_for_the_pin():
    for g in CAT.capabilities["ambiguous_asks"].goals:
        assert g.kind == "ask" and g.forbid, g.id
    for g in CAT.capabilities["pin_class_c"].goals:
        assert g.kind == "pin" and g.forbid, g.id
        assert all(f["severity"] == 1 or f["severity"] == 2 for f in g.forbid)
    assert any("email" in g.text.lower() for g in CAT.capabilities["pin_class_c"].goals)


def test_responsiveness_probes_carry_the_owners_thresholds():
    probes = {g.id: g.probe for g in CAT.capabilities["window_responsiveness"].goals}
    assert probes["wr-input-idle"]["limit"] == 150 and probes["wr-input-local-model"]["limit"] == 150
    assert probes["wr-input-local-model"]["load"] == "local_model" and probes["wr-startup-interactive"]["limit"] == 3.0
    assert {p["probe"] for p in probes.values()} == {"input_latency", "click_to_paint", "startup"}


def test_the_local_moe_capability_pins_the_gateway_to_local():
    assert CAT.capabilities["local_moe_only"].pin == "local"


def test_every_goal_renders_with_a_context_and_leaves_no_template_behind():
    ctx = model.make_context("/tmp/w", "/", "/tmp/out", "seed-1")
    for g in CAT.goals():
        r = model.render(g, ctx)
        blob = json.dumps([r.text, r.setup, r.verify, r.forbid], ensure_ascii=False)
        stripped = re.sub(r'"regex": "[^"]*"', "", blob)             # regexes keep their own braces
        assert not re.search(r"\{(work|sep|tok|num|date|weekday|day|month|year|outputs|screenshots)\}", stripped), g.id


def test_every_path_a_goal_touches_is_inside_the_runs_own_folder():
    ok_prefix = ("{work}", "{screenshots}", "{outputs}")
    def walk(x, key=""):
        if isinstance(x, dict):
            for k, v in x.items():
                yield from walk(v, k)
        elif isinstance(x, list):
            for v in x:
                yield from walk(v, key)
        elif isinstance(x, str) and key in ("path", "dir", "marker"):
            yield x
    for g in CAT.goals():
        for p in walk([g.setup, g.verify, g.forbid]):
            assert p.startswith(ok_prefix), f"{g.id}: {p}"


def test_validate_catches_mistakes():
    bad = copy.deepcopy(CAT)
    bad.capabilities["screen_text"].goals = bad.capabilities["screen_text"].goals[:5]
    assert any("screen_text: 5 goals" in p for p in model.validate(bad))
    bad = copy.deepcopy(CAT)
    bad.capabilities["ambiguous_asks"].goals[0].forbid = []
    assert any("needs forbid" in p for p in model.validate(bad))
    bad = copy.deepcopy(CAT)
    bad.capabilities["open_type_save_click"].goals[0].verify[0]["check"] = "teleport"
    assert any("unknown check" in p for p in model.validate(bad))
    bad = copy.deepcopy(CAT)
    bad.capabilities["open_type_save_click"].goals[0].text += " {nonsense}"
    assert any("unknown template field" in p for p in model.validate(bad))
    bad = copy.deepcopy(CAT)
    bad.capabilities["pin_class_c"].goals[0].forbid[0]["severity"] = 9
    assert any("severity" in p for p in model.validate(bad))
    bad = copy.deepcopy(CAT)
    bad.capabilities["simple_tasks"].goals[0].id = bad.capabilities["open_type_save_click"].goals[0].id
    assert any("also used" in p for p in model.validate(bad))


def test_unknown_goal_fields_are_refused_at_load(tmp_path):
    data = json.loads(model.GOALS_FILE.read_text(encoding="utf-8"))
    data["capabilities"]["screen_text"]["goals"][0]["verfiy"] = []
    p = tmp_path / "g.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(model.GoalFileError):
        model.load(p)


def test_context_is_deterministic_per_seed_and_distinct_across_seeds():
    a = model.make_context("/w", "/", "/o", "x", now=1_700_000_000)
    b = model.make_context("/w", "/", "/o", "x", now=1_700_000_000)
    c = model.make_context("/w", "/", "/o", "y", now=1_700_000_000)
    assert a == b and a.tok != c.tok and a.work == f"/w/{a.tok}" and len(a.num) == 5
