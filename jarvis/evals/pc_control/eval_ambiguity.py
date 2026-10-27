"""D74: the numbers of the ask-do-not-guess gate (plugins/_shared/ambiguity.py over config/ambiguity.json), no model, no PC, a second to run.

    python -m jarvis.evals.pc_control.eval_ambiguity [--verbose]

FALSE ASKS: clear requests that were asked about. The budget is zero. The set is every hand-written clear request in plugins/_shared/tests/ambiguity_cases.json, every
example of the simple-task catalogue (config/simple_tasks.json) and every act / act_or_ask / pin goal of the reliability gate (evals/reliability/goals.json).
TRUE ASKS: the ambiguous requests (English, Telugu and Hindi in English letters) that were asked about with the RIGHT kind of question.
Exit code 1 when any clear request is asked about or any ambiguous one is not.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jarvis.plugins._shared import ambiguity

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "plugins" / "_shared" / "tests" / "ambiguity_cases.json"


def clear_requests() -> list[tuple[str, str]]:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    excluded = set(data.get("clear_excluded") or {})
    out = [("hand", t) for t in data["clear"]]
    for t in json.loads((ROOT / "config" / "simple_tasks.json").read_text(encoding="utf-8"))["tasks"]:
        out += [("catalogue", e["say"]) for e in t.get("examples", []) if e["say"] not in excluded]
    goals = json.loads((ROOT / "evals" / "reliability" / "goals.json").read_text(encoding="utf-8"))
    for cap in goals["capabilities"].values():
        for g in cap["goals"]:
            if g.get("kind") in ("act", "act_or_ask", "pin"):
                out.append(("goal", g["text"].replace("{work}", "D:\\work").replace("{sep}", "\\").replace("{tok}", "ab12cd").replace("{num}", "12345")))
    return out


def run(verbose: bool = False) -> dict:
    ctx = ambiguity.Context(recent_files=[("a.txt", 10), ("b.pdf", 2048)], windows=[{"process": "notepad", "title": "x - Notepad"}, {"process": "calc", "title": "Calculator"}], people=["Ravi"])
    clear = clear_requests()
    false_asks = [(src, t, v.rule) for src, t in clear for v in [ambiguity.check(t, ctx)] if v]
    amb = json.loads(CASES.read_text(encoding="utf-8"))["ambiguous"]
    per: dict[str, list[int]] = {}
    wrong = []
    for c in amb:
        v = ambiguity.check(c["say"], ctx)
        ok = v is not None and v.kind == c["kind"]
        per.setdefault(c["lang"], [0, 0])
        per[c["lang"]][0] += 1 if ok else 0
        per[c["lang"]][1] += 1
        if not ok:
            wrong.append((c["lang"], c["say"], c["kind"], v and v.kind))
    res = {"clear": len(clear), "false_asks": len(false_asks), "ambiguous": len(amb), "asked_right": sum(v[0] for v in per.values()), "by_language": per,
           "false_ask_list": false_asks, "missed": wrong, "rules_from": ambiguity.load().source}
    print(f"rules from the {res['rules_from']}")
    print(f"clear requests: {res['clear']} ; asked about (false asks): {res['false_asks']}   (budget: 0)")
    print(f"ambiguous requests: {res['ambiguous']} ; asked with the right question: {res['asked_right']}   " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(per.items())))
    for row in false_asks:
        print("  FALSE ASK", row)
    for row in wrong:
        print("  NOT ASKED", row)
    if verbose:
        for c in amb:
            v = ambiguity.check(c["say"], ctx)
            print(f"  {c['lang']:>2} {c['say']!r:60} -> {v.kind if v else None}: {v.question if v else ''}")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verbose", action="store_true")
    res = run(ap.parse_args(argv).verbose)
    return 1 if res["false_asks"] or res["missed"] else 0


if __name__ == "__main__":
    sys.exit(main())
