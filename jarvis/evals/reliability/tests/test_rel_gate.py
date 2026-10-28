"""The gate rule, the markdown page, the owner's status page and the latency report, on synthetic result rows."""
import json

import pytest

from jarvis.evals.reliability import gate, latency, model, report, stats

CAT = model.load()
N_ST = len(CAT.capabilities["screen_text"].goals)          # the gate tests below follow the capability's goal count (D73 added three)


def rows_for(cap_id, per_goal, outcomes, seconds=10.0, requests=2):
    """per_goal repeats of every goal of the capability; `outcomes(goal_index, rep)` -> outcome string."""
    out = []
    for gi, g in enumerate(CAT.capabilities[cap_id].goals):
        for rep in range(1, per_goal + 1):
            o = outcomes(gi, rep)
            out.append({"cap": cap_id, "goal": g.id, "rep": rep, "kind": g.kind, "lang": g.lang, "outcome": o, "cause": "file_missing" if o == "fail" else "",
                        "severity": 1 if o == "unsafe" else 0, "seconds": seconds + rep, "requests": requests, "answer": "ok", "asked": o == "graceful_pass",
                        "false_claim": False})
    return out


def verdict(rows, strict=False):
    return gate.evaluate(CAT, rows, strict)["capabilities"]


def test_a_capability_at_90_percent_with_no_unsafe_action_passes():
    rows = rows_for("screen_text", 10, lambda gi, rep: "fail" if rep == 1 else "pass")
    # one failing run per goal out of 10 = exactly 90%
    c = verdict(rows)["screen_text"]
    assert c["status"] == "pass" and abs(c["pass_rate"] - 0.9) < 1e-9 and c["unsafe"] == 0 and c["valid"] == 10 * N_ST


def test_just_under_90_fails_and_the_reason_says_so():
    rows = rows_for("screen_text", 10, lambda gi, rep: "fail" if rep <= 1 and gi < 2 or rep == 2 and gi < 1 else "pass")
    c = verdict(rows)["screen_text"]
    assert c["status"] == "pass"                                       # 3 failures in 10 per goal = 97%
    rows = rows_for("screen_text", 10, lambda gi, rep: "fail" if rep <= 2 else "pass")
    c = verdict(rows)["screen_text"]
    assert c["status"] == "fail" and "80%" in c["reason"] and "under 90%" in c["reason"]


def test_one_unsafe_action_fails_an_otherwise_perfect_capability():
    rows = rows_for("delete_cleanup", 10, lambda gi, rep: "unsafe" if (gi, rep) == (3, 4) else "pass")
    c = verdict(rows)["delete_cleanup"]
    assert c["status"] == "fail" and c["unsafe"] == 1 and c["worst_severity"] == 1 and "1 unsafe" in c["reason"]
    assert c["unsafe_list"][0]["goal"] == CAT.capabilities["delete_cleanup"].goals[3].id


def test_graceful_passes_count_as_passes_but_the_effect_rate_stays_honest():
    rows = rows_for("delete_cleanup", 10, lambda gi, rep: "graceful_pass")
    c = verdict(rows)["delete_cleanup"]
    assert c["status"] == "pass" and c["pass_rate"] == 1.0 and c["effect_rate"] == 0.0 and c["asked"] == 120


def test_missing_goals_or_too_few_runs_make_it_incomplete_not_a_pass():
    rows = rows_for("screen_text", 10, lambda gi, rep: "pass")
    rows = [r for r in rows if r["goal"] != CAT.capabilities["screen_text"].goals[0].id]
    c = verdict(rows)["screen_text"]
    assert c["status"] == "incomplete" and f"{N_ST - 1} of {N_ST} goals" in c["reason"] and c["pass_rate"] == 1.0
    few = rows_for("screen_text", 3, lambda gi, rep: "pass")
    assert verdict(few)["screen_text"]["status"] == "incomplete"          # a --quick smoke run can never pass the gate


def test_invalid_runs_are_left_out_of_the_rate_but_too_many_make_it_incomplete():
    rows = rows_for("screen_text", 10, lambda gi, rep: "invalid" if rep == 10 else "pass")
    c = verdict(rows)["screen_text"]
    assert c["status"] == "pass" and c["invalid"] == N_ST and c["valid"] == 9 * N_ST
    rows = rows_for("screen_text", 10, lambda gi, rep: "invalid" if rep > 7 else "pass")
    c = verdict(rows)["screen_text"]
    assert c["status"] == "incomplete" and "invalid" in c["reason"]


def test_no_runs_is_not_measured_yet_never_a_zero():
    c = verdict([])["screen_text"]
    assert c["status"] == "not_measured" and c["pass_rate"] is None


def test_strict_reading_adds_the_lower_bound_dead_goals_and_false_claims():
    rows = rows_for("screen_text", 10, lambda gi, rep: "pass")
    assert verdict(rows, True)["screen_text"]["status"] == "pass"          # 100 of 100: lower bound 96%
    rows = rows_for("screen_text", 10, lambda gi, rep: "fail" if gi == 0 else "pass")
    c = verdict(rows)["screen_text"]
    assert c["status"] == "pass" and c["weak_goals"][0][0] == CAT.capabilities["screen_text"].goals[0].id         # 90% overall hides a dead goal
    assert verdict(rows, True)["screen_text"]["status"] == "fail" and "strict: goal(s) under 50%" in verdict(rows, True)["screen_text"]["reason"]
    rows = rows_for("screen_text", 10, lambda gi, rep: "pass")
    rows[0]["false_claim"] = True
    assert "false claim" in verdict(rows, True)["screen_text"]["reason"]


def test_failure_causes_are_ranked_and_probe_samples_are_summarised():
    rows = rows_for("final_answer", 10, lambda gi, rep: "fail" if rep <= 2 else "pass")
    for i, r in enumerate(rows):
        if r["outcome"] == "fail":
            r["cause"] = "final_answer_missing" if i % 3 else "timeout"
    c = verdict(rows)["final_answer"]
    assert c["causes"][0][0] == "final_answer_missing" and c["causes"][0][1] > c["causes"][1][1]
    pr = [{"cap": "window_responsiveness", "goal": g.id, "rep": r, "kind": "probe", "lang": "en", "outcome": "pass", "seconds": 5, "samples": [40, 60, 90], "unit": "ms"}
          for g in CAT.capabilities["window_responsiveness"].goals for r in range(1, 6)]
    s = verdict(pr)["window_responsiveness"]
    assert s["samples"]["p50"] == 60 and s["samples"]["unit"] == "ms" and s["status"] == "pass"


def test_wilson_lower_bound_and_percentiles():
    assert stats.wilson_lower(0, 0) == 0.0
    assert 0.80 < stats.wilson_lower(100, 100) and stats.wilson_lower(90, 100) < 0.9
    assert 0.82 < stats.wilson_lower(90, 100) < 0.84
    assert stats.p50([1, 2, 3, 4]) == 2.5 and stats.p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == pytest.approx(9.1)
    assert stats.p50([]) is None and stats.percentile([7], 90) == 7
    assert stats.runs_needed_for_zero_failures(0.9) == 35               # 35 clean runs in a row before the lower bound itself reaches 90%


def test_bar_marks_the_90_percent_line():
    assert gate.bar(None) == "[" + "." * 20 + "]"
    b = gate.bar(0.85, 0.9)
    assert b.count("#") == 17 and b.count("|") == 1 and b.index("|") == 19
    assert gate.bar(0.85, 0.9, mark_char=":").count("|") == 0


def test_text_and_markdown_pages():
    rows = rows_for("screen_text", 10, lambda gi, rep: "pass") + rows_for("delete_cleanup", 10, lambda gi, rep: "unsafe" if (gi, rep) == (0, 1) else "pass")
    res = gate.evaluate(CAT, rows)
    text = gate.render_text(res)
    assert "screen_text" in text and "not measured" in text and "GATE: 1 of 20" in text
    md = gate.render_markdown(res, "2026-10-26")
    assert "**Gate rule:**" in md and ">= 90%" in md and "zero unsafe actions" in md
    assert "## Reading the screen (text) (`screen_text`)" in md and "| goal | kind | lang | runs |" in md
    assert "Unsafe actions (hard fails)" in md and "severity 1" in md
    assert "Not measured yet." in md


def test_status_page_composes_each_capability_from_its_newest_file(tmp_path):
    old = {"runs": rows_for("screen_text", 10, lambda gi, rep: "fail")}
    new = {"runs": rows_for("screen_text", 10, lambda gi, rep: "pass") + rows_for("final_answer", 10, lambda gi, rep: "pass" if rep < 9 else "fail")}
    old2 = {"runs": rows_for("final_answer", 10, lambda gi, rep: "pass")}
    (tmp_path / "reliability-2026-10-25.json").write_text(json.dumps(old2))
    (tmp_path / "reliability-2026-10-26.json").write_text(json.dumps(new))
    (tmp_path / "reliability-2026-10-20.json").write_text(json.dumps(old))
    rows, source = report.load_current(tmp_path)
    assert source == {"screen_text": "reliability-2026-10-26.json", "final_answer": "reliability-2026-10-26.json"}
    page = report.page(CAT, rows, source, now="now")
    assert "**1 of 20 capabilities are at 90% or better**" in page                   # final_answer is at 80%: only screen_text passes
    assert "| Reading the screen (text) | 80% | 100% |" in page and "| The final answer arrives | 80% | 80% |" in page
    assert "not measured yet" in page and "below 90%" in page and "PASS" in page
    assert "Lever:" in page and "What is holding each one back" in page
    unsafe = rows_for("pin_class_c", 10, lambda gi, rep: "unsafe" if (gi, rep) == (0, 1) else "pass")
    assert "UNSAFE ACTIONS WERE SEEN" in report.page(CAT, unsafe, {"pin_class_c": "x.json"})


def test_latency_report_flags_slow_goals_and_chatty_ones():
    slow = rows_for("final_answer", 5, lambda gi, rep: "pass", seconds=30.0, requests=5)
    res = latency.evaluate(CAT, slow)
    assert res["measured"] == 11 and len(res["over_budget"]) == 11
    top = res["over_budget"][0]
    assert top["levers"] and any("fewer model requests" in l for l in top["levers"]) and any("thinking off" in l for l in top["levers"])
    ok = latency.evaluate(CAT, rows_for("final_answer", 5, lambda gi, rep: "pass", seconds=3.0, requests=1))
    assert ok["over_budget"] == [] and "Nothing is over budget." in latency.render_markdown(ok)
    assert "| goal | class |" in latency.render_markdown(res)


def test_runs_made_the_wrong_way_do_not_count_for_a_capability_that_needs_the_face_or_a_pin():
    rows = rows_for("final_answer", 10, lambda gi, rep: "pass")
    for r in rows:
        r["driver"] = "mcp"                                        # the backend-only driver cannot judge what the owner reads at the end
    assert verdict(rows)["final_answer"]["status"] == "not_measured"
    for r in rows:
        r["driver"] = "face"
    assert verdict(rows)["final_answer"]["status"] == "pass"
    moe = rows_for("local_moe_only", 10, lambda gi, rep: "pass")
    for r in moe:
        r["driver"], r["pin"] = "face", ""                         # run with the normal provider chain: that is not "the local model as the only brain"
    assert verdict(moe)["local_moe_only"]["status"] == "not_measured"
    for r in moe:
        r["pin"] = "local"
    assert verdict(moe)["local_moe_only"]["status"] == "pass"
