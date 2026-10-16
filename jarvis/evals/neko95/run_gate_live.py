"""Neko 9.5, C13: the self-improvement regression gate with the REAL local decider (2026-10-16). Windows, neko-venv,
llama-swap on 8080. Stages one proposal in the owner's registry (staging changes nothing; only the owner can promote),
then runs dev + holdout + plan cases with the staged content and with the active one, through skills.gate().
    python jarvis\\evals\\neko95\\run_gate_live.py
Pass: the gate ran both suites, saved its report, and its verdict matches its own numbers (no case that passes on the
active version fails on the staged one). Results: jarvis/evals/neko95/results/gate-<time>.json
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE.parent / "pc_control"))
from plugin.plugins.desktop import core, decide, skills  # noqa: E402
import eval_decision  # noqa: E402

PROPOSAL = {"sites": {"hacker news": "https://news.ycombinator.com/"},
            "examples": [{"request": "show me hacker news", "plan": {"steps": [{"tool": "open_website", "site": "hacker news"}]}}]}


def cases():
    pc = HERE.parent / "pc_control"
    out = []
    for f in (pc / "decision_cases.json", pc / "decision_cases_holdout.json", HERE / "plan_cases.json"):
        for i, c in enumerate(json.loads(f.read_text(encoding="utf-8"))):
            out.append((f"{c['id']}#{i}", c))
    out.append(("GATE-new-site#0", {"req": "open hacker news", "expect": {"tool": "open_website", "host": "ycombinator.com"}}))
    return out


def main():
    profiles = core.profiles()
    all_cases = cases()
    calls = {"n": 0}

    def run_suite(content):
        decide.FORCED = content
        try:
            res = {}
            for cid, c in all_cases:
                p = decide.decide(c["req"], profiles, timeout=120, context=c.get("context", ""))
                res[cid] = eval_decision.score(p, c["expect"])[0]
                calls["n"] += 1
            return res
        finally:
            decide.FORCED = None

    t0 = time.time()
    ver = skills.propose(PROPOSAL, "gate test: a site the owner may name (hacker news)")
    rep = skills.gate(ver, run_suite)
    consistent = rep["passed"] == (not rep["regressed"] and rep["staged_pass"] >= rep["baseline_pass"])
    reg = json.loads((skills.ROOT / "registry.json").read_text(encoding="utf-8"))
    untouched = reg.get("active") != ver                     # staging must not activate anything
    ok = consistent and untouched and (skills.ROOT / "gates" / f"{ver}.json").exists()
    detail = (f"{ver}: gate {'passed' if rep['passed'] else 'FAILED'} ({rep['staged_pass']}/{rep['cases']} staged vs "
              f"{rep['baseline_pass']} active; regressed {rep['regressed']}); active unchanged: {untouched}; "
              f"{calls['n']} real decider calls in {time.time() - t0:.0f} s")
    print(("PASS " if ok else "FAIL ") + "SI-regression-gate-live", detail)
    out = HERE / "results" / f"gate-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "report": rep, "rows": [
        {"cap": "C13_self_improvement", "id": "SI-regression-gate-live", "status": "pass" if ok else "fail", "detail": detail}]},
        indent=1), encoding="utf-8")
    print("saved", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
