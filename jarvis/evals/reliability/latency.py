"""Per-goal latency budget report (D59, lever e): which goals are slower than they should be, and which speed lever applies.

    python -m jarvis.evals.reliability.latency [--results-dir DIR]

A budget is a p50 and a p90 in seconds per goal CLASS (goals.json `latency`): a question 8/15 s, a PC action 15/30 s, a deck 60/120 s ...
The report lists every goal whose measured p50 or p90 (over valid runs, successful or not: a slow failure is still slow) is over its budget,
the model requests it made, and the levers that fit what the numbers show:

  thinking off for decisions      jarvis/plugins/_shared/speed.decision_body        (local models: enable_thinking=false; goal.pick_args already does)
  prompt cache of the static prompt  speed.static_first + speed.with_prompt_cache   (PrefixWatch shows if the "static" prompt changes every turn)
  no second request               D52: tool/reply cards off; more than `_requests_per_goal_max` requests per goal is flagged here
  independent local steps in parallel  speed.run_dag                                (a web lookup beside a UI action)
Wiring the levers into the face's request path and the desktop plugin is laptop work (D59 lists the files); this report is how the laptop session
sees whether a lever moved a number.
"""
from __future__ import annotations

import argparse

from . import classify, model, report, stats


def budget_for(cat: model.Catalog, goal: model.Goal) -> dict:
    cls = goal.latency_class or cat.capabilities[goal.cap].latency_class
    b = cat.latency.get(cls) or {}
    return {"class": cls, "p50_s": b.get("p50_s"), "p90_s": b.get("p90_s")}


def evaluate(cat: model.Catalog, rows: list[dict]) -> dict:
    max_req = int(cat.latency.get("_requests_per_goal_max", 3))
    out: list[dict] = []
    for g in cat.goals():
        gr = [r for r in rows if r["goal"] == g.id and r["outcome"] != classify.INVALID and r.get("seconds") is not None]
        if not gr or g.kind == "probe":
            continue
        secs = [r["seconds"] for r in gr]
        reqs = [r["requests"] for r in gr if r.get("requests")]
        b = budget_for(cat, g)
        p50, p90 = stats.p50(secs), stats.p90(secs)
        over50 = b["p50_s"] is not None and p50 > b["p50_s"]
        over90 = b["p90_s"] is not None and p90 > b["p90_s"]
        many = bool(reqs) and stats.p50(reqs) > max_req
        levers = []
        if over50 or over90:
            levers.append("thinking off for decisions (speed.decision_body)")
            if reqs and max(reqs) > 1:
                levers.append("prompt cache of the static prompt (speed.static_first, with_prompt_cache)")
        if many:
            levers.append(f"fewer model requests ({stats.p50(reqs):.0f} per goal, budget {max_req}): D52 no second request beside a turn")
        if (over50 or over90) and g.cap in ("open_type_save_click", "final_answer", "screen_text") and "Two things" in g.text:
            levers.append("run the independent parts in parallel (speed.run_dag)")
        out.append({"goal": g.id, "cap": g.cap, "class": b["class"], "runs": len(gr), "p50": p50, "p90": p90, "budget_p50": b["p50_s"], "budget_p90": b["p90_s"],
                    "over": over50 or over90, "requests_p50": stats.p50(reqs) if reqs else None, "many_requests": many, "levers": levers})
    over = sorted((x for x in out if x["over"] or x["many_requests"]), key=lambda x: -(x["p90"] / x["budget_p90"] if x["budget_p90"] else 0))
    return {"goals": out, "over_budget": over, "within": len(out) - len([x for x in out if x["over"]]), "measured": len(out)}


def render_markdown(res: dict) -> str:
    lines = ["# Latency budget report", "", f"{res['within']} of {res['measured']} measured goals are within their p50 and p90 budgets.", ""]
    if not res["over_budget"]:
        return "\n".join(lines + ["Nothing is over budget."])
    lines += ["| goal | class | runs | p50 | budget | p90 | budget | requests (p50) | levers |", "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for x in res["over_budget"]:
        rq = "-" if x["requests_p50"] is None else f"{x['requests_p50']:.0f}"
        lines.append(f"| {x['goal']} | {x['class']} | {x['runs']} | {x['p50']:.1f}s | {x['budget_p50']}s | {x['p90']:.1f}s | {x['budget_p90']}s | {rq} | {'; '.join(x['levers'])} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-dir", default=str(report.RESULTS))
    args = ap.parse_args(argv)
    cat = model.load()
    rows, _ = report.load_current(args.results_dir)
    print(render_markdown(evaluate(cat, rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
