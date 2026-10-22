"""Tests for jarvis.routines.recipe."""

from __future__ import annotations

import copy
import json

import pytest
import yaml

from jarvis.routines import recipe as r

YAML_TEXT = """\
version: 1
title: Weekly report
description: Summarise a topic
parameters:
  - key: topic
    input_type: string
    requirement: required
    description: what to cover
  - key: count
    input_type: number
    requirement: optional
    default: 3
  - key: tone
    input_type: select
    requirement: optional
    options: [formal, casual]
    default: formal
  - key: verbose
    input_type: boolean
    requirement: optional
    default: false
instructions: "Write about {{ topic }} in a {{tone}} tone, {{ count }} items, verbose={{verbose}}."
prompt: "Start: {{ topic }}"
tools: [search, write_file]
steps:
  - name: look
    tool: search
    args: {q: "{{ topic }}", n: "{{ count }}", nested: ["{{ tone }}"]}
"""


def base() -> dict:
    return yaml.safe_load(YAML_TEXT)


def err(d: dict, exc=r.RecipeError) -> str:
    with pytest.raises(exc) as e:
        r.from_dict(d)
    return str(e.value)


def test_load_yaml_and_json():
    ry = r.load_recipe(YAML_TEXT)
    rj = r.load_recipe(json.dumps(base()))
    assert ry.title == "Weekly report"
    assert r.to_dict(ry) == r.to_dict(rj)
    assert r.load_recipe(json.dumps(base()), fmt="json").title == ry.title
    assert ry.warnings == []


def test_load_from_path(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text(YAML_TEXT, encoding="utf-8")
    assert r.load_recipe(p).title == "Weekly report"
    assert r.load_recipe(str(p)).title == "Weekly report"


def test_size_cap():
    with pytest.raises(r.RecipeTooLarge):
        r.load_recipe("x: " + "a" * 70000)


def test_required_fields():
    for k in ("title", "description", "tools"):
        d = base()
        del d[k]
        err(d)
    d = base()
    d["title"] = "t" * 101
    err(d)
    d = base()
    d["bogus"] = 1
    err(d)
    with pytest.raises(r.RecipeError):
        r.from_dict(["not", "a", "dict"])


def test_parameter_validation():
    d = base()
    d["parameters"][1]["key"] = "topic"
    err(d)
    d = base()
    d["parameters"][0]["key"] = "Bad-Key"
    err(d)
    d = base()
    d["parameters"][0]["input_type"] = "date"
    err(d)
    d = base()
    d["parameters"][2]["options"] = []
    err(d)
    d = base()
    d["parameters"][2]["options"] = [1, 2]
    err(d)
    d = base()
    d["parameters"][2]["default"] = "other"
    err(d)
    d = base()
    d["parameters"][1]["default"] = "abc"
    err(d)
    d = base()
    d["parameters"][3]["default"] = "maybe"
    err(d)


def test_placeholder_validation():
    d = base()
    d["instructions"] += " {{ ghost }}"
    err(d)
    d = base()
    d["instructions"] += " {{ broken"
    err(d)
    d = base()
    d["parameters"].append({"key": "extra", "input_type": "string"})
    err(d)  # required but unused
    d = base()
    d["parameters"].append({"key": "extra", "input_type": "string", "requirement": "optional"})
    rec = r.from_dict(d)
    assert len(rec.warnings) == 1 and "extra" not in rec.warnings[0]


def test_tools_validation():
    for bad in (["*"], ["a*"], [""], [1], "search", ["{{ topic }}"]):
        d = base()
        d["tools"] = bad
        err(d)
    d = base()
    d["tools"] = ["search"]  # step uses search; write_file removed is fine
    r.from_dict(d)
    d["steps"][0]["tool"] = "other"
    err(d)
    d = base()
    d["steps"][0]["tool"] = "{{ topic }}"
    err(d)
    d = base()
    d["steps"][0]["args"] = {"{{ topic }}": 1}
    err(d)
    d = base()
    d["tools"] = []
    d["steps"] = None
    d["instructions"] = "{{ topic }}"
    d["prompt"] = None
    d["parameters"] = [d["parameters"][0]]
    assert r.from_dict(d).tools == []


def test_yaml_python_tag_refused():
    text = YAML_TEXT + "\nextra: !!python/object/apply:os.system ['echo hi']\n"
    with pytest.raises(r.RecipeError):
        r.load_recipe(text)


def test_garbage_documents():
    for t in ("{not json", ": : :\n - [", "just a string", "- a\n- b"):
        with pytest.raises(r.RecipeError):
            r.load_recipe(t)


def test_render_ok():
    rec = r.load_recipe(YAML_TEXT)
    plan = r.render(rec, {"topic": "cats"})
    assert plan.instructions == "Write about cats in a formal tone, 3 items, verbose=false."
    assert plan.prompt == "Start: cats"
    assert plan.steps == [{"name": "look", "tool": "search",
                           "args": {"q": "cats", "n": "3", "nested": ["formal"]}}]
    assert plan.values_used == {"topic": "cats", "count": 3, "tone": "formal", "verbose": False}
    assert plan.tools == ["search", "write_file"]


def test_missing_and_unknown():
    rec = r.load_recipe(YAML_TEXT)
    with pytest.raises(r.MissingParameter):
        r.render(rec, {})
    with pytest.raises(r.UnknownParameter):
        r.render(rec, {"topic": "a", "zzz": 1})


def test_coercion():
    rec = r.load_recipe(YAML_TEXT)

    def run(**kw):
        return r.render(rec, {"topic": "t", **kw}).values_used

    assert run(count=5)["count"] == 5
    assert run(count=2.5)["count"] == 2.5
    assert run(count="7")["count"] == 7
    assert run(count="1.5")["count"] == 1.5
    for bad in (True, "abc", None, [1], float("nan"), "inf"):
        with pytest.raises(r.BadValue):
            run(count=bad)
    assert run(verbose=True)["verbose"] is True
    assert run(verbose="true")["verbose"] is True
    assert run(verbose="false")["verbose"] is False
    for bad in ("yes", 1, 0, "True"):
        with pytest.raises(r.BadValue):
            run(verbose=bad)
    assert run(tone="casual")["tone"] == "casual"
    with pytest.raises(r.BadValue):
        run(tone="rude")
    with pytest.raises(r.BadValue):
        r.render(rec, {"topic": 5})


@pytest.mark.parametrize("value", [
    "a {{ other }} b", "x }} y", "{{", "nul\x00byte", "bell\x07", "esc\x1b[0m",
    "del\x7f", "c1\x85", "a" * 2001,
])
def test_injection_refused(value):
    rec = r.load_recipe(YAML_TEXT)
    with pytest.raises(r.BadValue):
        r.render(rec, {"topic": value})


def test_injection_allowed_whitespace_and_cap():
    rec = r.load_recipe(YAML_TEXT)
    assert r.render(rec, {"topic": "a\nb\tc"}).prompt == "Start: a\nb\tc"
    assert r.render(rec, {"topic": "a" * 2000}).values_used["topic"] == "a" * 2000


def test_single_pass_substitution():
    # braces split across values cannot reassemble into a placeholder
    d = base()
    d["parameters"].append({"key": "other", "input_type": "string", "requirement": "optional"})
    d["instructions"] = "{{ topic }}{{ other }} end"
    rec = r.from_dict(d)
    # a value that merely names another parameter is kept literal
    plan = r.render(rec, {"topic": "topic", "other": "{ other }"})
    assert plan.instructions == "topic{ other } end"
    # a value naming a placeholder-ish string is not expanded
    plan = r.render(rec, {"topic": "other", "other": "topic"})
    assert plan.instructions == "othertopic end"


def test_fingerprint_stable_and_sensitive():
    rec = r.load_recipe(YAML_TEXT)
    fp = r.fingerprint(rec)
    assert len(fp) == 64
    assert fp == r.fingerprint(r.load_recipe(YAML_TEXT))
    assert fp == r.fingerprint(r.load_recipe(json.dumps(base())))
    # whitespace is part of the recipe (indentation can matter in a step argument): different spacing is a different recipe
    d = base()
    d["description"] = "  Summarise   a\ntopic "
    assert r.fingerprint(r.from_dict(d)) != fp
    mutations = [
        lambda d: d.update(title="Other"),
        lambda d: d.update(description="Different"),
        lambda d: d.update(instructions=d["instructions"] + "!"),
        lambda d: d.update(prompt="Begin: {{ topic }}"),
        lambda d: d.update(tools=["search"]),
        lambda d: d["parameters"][1].update(default=4),
        lambda d: d["parameters"][0].update(description="other"),
        lambda d: d["parameters"][2].update(options=["formal", "casual", "x"]),
        lambda d: d["steps"][0]["args"].update(q="{{ tone }}"),
        lambda d: d["steps"][0].update(name="other"),
        lambda d: d.update(steps=None),
    ]
    seen = {fp}
    for m in mutations:
        d = copy.deepcopy(base())
        m(d)
        f = r.fingerprint(r.from_dict(d))
        assert f not in seen
        seen.add(f)


def test_replay_three_runs_with_one_bad():
    rec = r.load_recipe(YAML_TEXT)
    runs = [
        {"topic": "cats", "count": 1},
        {"topic": "{{ evil }}"},
        {"topic": "dogs", "tone": "casual"},
        {"topic": "birds", "verbose": "true"},
    ]
    out = r.replay(rec, runs)
    assert len(out) == 4
    assert isinstance(out[0], r.RunPlan) and out[0].values_used["count"] == 1
    assert isinstance(out[1], r.ReplayFailure) and out[1].index == 1
    assert out[1].kind == "BadValue"
    assert "evil" not in out[1].error
    assert out[2].values_used["tone"] == "casual"
    assert out[3].values_used["verbose"] is True
    # replay does not mutate the recipe
    assert r.fingerprint(rec) == r.fingerprint(r.load_recipe(YAML_TEXT))


def test_round_trip_and_dump():
    rec = r.load_recipe(YAML_TEXT)
    assert r.to_dict(r.from_dict(r.to_dict(rec))) == r.to_dict(rec)
    for fmt in ("yaml", "json"):
        text = r.dump(rec, fmt)
        again = r.load_recipe(text, fmt)
        assert r.fingerprint(again) == r.fingerprint(rec)
        assert r.dump(again, fmt) == text
    with pytest.raises(r.RecipeError):
        r.dump(rec, "toml")


def test_errors_never_echo_recipe_text():
    secret = "SECRET-TOKEN-xyz"
    bad_docs = []
    d = base(); d["title"] = secret * 20; bad_docs.append(d)
    d = base(); d["instructions"] += " {{ " + secret.lower().replace("-", "_") + " }}"; bad_docs.append(d)
    d = base(); d["tools"] = [secret + "*"]; bad_docs.append(d)
    d = base(); d["parameters"][0]["key"] = secret; bad_docs.append(d)
    d = base(); d["parameters"][0]["input_type"] = secret; bad_docs.append(d)
    d = base(); d["parameters"][2]["default"] = secret; bad_docs.append(d)
    d = base(); d["steps"][0]["tool"] = secret; bad_docs.append(d)
    d = base(); d[secret] = 1; bad_docs.append(d)
    d = base(); d["version"] = secret; bad_docs.append(d)
    for d in bad_docs:
        with pytest.raises(r.RecipeError) as e:
            r.load_recipe(json.dumps(d))
        assert secret.lower() not in str(e.value).lower()
    for text in ("{" + secret, secret + ": [", "!!python/object:" + secret):
        with pytest.raises(r.RecipeError) as e:
            r.load_recipe(text)
        assert secret not in str(e.value)
    rec = r.load_recipe(YAML_TEXT)
    for vals in ({"topic": secret + "{{"}, {"topic": "x", secret: 1}, {"topic": "x", "count": secret}):
        with pytest.raises(r.RenderError) as e:
            r.render(rec, vals)
        assert secret not in str(e.value)
