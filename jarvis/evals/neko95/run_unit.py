"""Neko 9.5: run the engine test files and turn named tests into scorecard rows. Windows, neko-venv.
    python jarvis\\evals\\neko95\\run_unit.py
Engine tests use fake agents with the real store (DBOS/SQLite, a real killed-and-restarted process); they prove the
engine's logic, not a live mission. The live case (M-research-deck-open-explain) is never derived from them.
Results: jarvis/evals/neko95/results/unit-<time>.json
"""
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MAP = {  # (capability, id): tests that must all pass
    ("C6_missions", "M-replan-after-failure"): ["test_replan_after_a_failed_check_then_success", "test_replans_are_bounded"],
    ("C6_missions", "M-resume-after-restart"): ["test_resume_after_restart_skips_done_steps"],
    ("C6_missions", "M-no-duplicate-side-effects"): ["test_a_done_effect_is_never_repeated",
                                                     "test_an_interrupted_unsafe_step_is_not_repeated_but_a_safe_one_is",
                                                     "test_resume_after_restart_skips_done_steps"],
    ("C6_missions", "M-loop-detection"): ["test_loop_is_detected"],
    ("C6_missions", "M-cancel-propagates"): ["test_cancel_stops_before_the_next_step_and_stops_the_running_job"],
    ("C6_missions", "M-budgets"): ["test_time_budget", "test_replans_are_bounded"],
    ("C6_missions", "M-engine-explain"): ["test_research_deck_open_explain_completes_and_explains"],
    ("C13_self_improvement", "SI-skill-registry-versioned"): ["test_versions_are_immutable_and_hash_checked",
                                                              "test_a_proposal_cannot_expand_privileges_or_replace_built_ins"],
    ("C13_self_improvement", "SI-regression-gate"): ["test_gate_blocks_a_regression_and_promotion_needs_a_passed_gate"],
    ("C13_self_improvement", "SI-rollback"): ["test_rollback_restores_the_previous_version_and_the_decider_follows"],
    ("C13_self_improvement", "SI-no-self-promotion"): ["test_no_plugin_entry_can_promote_or_roll_back",
                                                       "test_a_proposal_cannot_expand_privileges_or_replace_built_ins"],
    ("C10_proactivity", "PR-rules"): ["test_crashes_after_the_bookmark_only_and_once_each", "test_disk_floor_with_hysteresis",
                                      "test_service_down_needs_two_checks_and_a_previous_up", "test_stuck_task_after_budget_plus_grace"],
}
FILES = ["plugin/plugins/desktop/tests/test_missions.py", "plugin/plugins/desktop/tests/test_monitor.py",
         "plugin/plugins/desktop/tests/test_skills.py"]


def main():
    xml = HERE / "results" / "_unit.xml"
    xml.parent.mkdir(exist_ok=True)
    p = subprocess.run([sys.executable, "-m", "pytest", *FILES, "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"],
                       cwd=ROOT, capture_output=True, text=True)
    status = {}
    for tc in ET.parse(xml).getroot().iter("testcase"):
        bad = any(ch.tag in ("failure", "error", "skipped") for ch in tc)
        name = tc.get("name").split("[")[0]                 # parametrized cases count under their test: all must pass
        status[name] = "fail" if bad or status.get(name) == "fail" else "pass"
    xml.unlink()
    rows = []
    for (cap, cid), tests in MAP.items():
        got = [status.get(t, "not_run") for t in tests]
        st = "pass" if all(g == "pass" for g in got) else ("not_run" if all(g == "not_run" for g in got) else "fail")
        rows.append({"cap": cap, "id": cid, "status": st, "detail": f"engine tests (fake agents, real store): {dict(zip(tests, got))}"})
        print(f"{cid:32} {st}")
    out = HERE / "results" / f"unit-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "pytest_exit": p.returncode,
                               "pytest_tail": p.stdout[-400:], "rows": rows}, indent=1), encoding="utf-8")
    print("pytest exit", p.returncode, "| saved", out)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
