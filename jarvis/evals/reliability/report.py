"""The owner's status page (D59): each capability's CURRENT pass rate against 90%, with a bar, in plain words.

    python -m jarvis.evals.reliability.report            print the page
    python -m jarvis.evals.reliability.report --write    also write results/STATUS.md (the file both sessions and the owner open)
    python -m jarvis.evals.reliability.report --strict   the stricter reading (lower bound >= 80%, no dead goal, no false claim)

"Current" means: for each capability, the NEWEST results file that has runs for it (a partial run on Tuesday and another on Thursday compose;
two runs of the same capability never mix). A capability with no runs says "not measured yet", never a guess. The owner's own estimate from before
the measuring started is printed beside the measurement so the gap is visible.
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path

from . import gate, model

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def load_current(results_dir: Path | str = RESULTS) -> tuple[list[dict], dict[str, str]]:
    """(rows, {capability: results file name}) taking each capability's rows from the newest file that has any."""
    files = sorted(glob.glob(str(Path(results_dir) / "reliability-*.json")), reverse=True)
    rows: list[dict] = []
    source: dict[str, str] = {}
    for f in files:
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for cap in {r["cap"] for r in data.get("runs", [])} - set(source):
            source[cap] = Path(f).name
            rows += [r for r in data["runs"] if r["cap"] == cap]
    return rows, source


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def page(cat: model.Catalog, rows: list[dict], source: dict[str, str], strict: bool = False, now: str = "") -> str:
    res = gate.evaluate(cat, rows, strict)
    target = round(res["rules"]["pass_rate"] * 100)
    lines = [f"# JARVIS reliability: where each thing stands against {target}%", "",
             f"Written {now or time.strftime('%Y-%m-%d %H:%M')}. A bar fills to the measured pass rate; the : is the {target}% line. "
             "'not measured yet' means no run has been made: it is not a good or a bad number."]
    unsafe = [(cid, c) for cid, c in res["capabilities"].items() if c.get("unsafe")]
    if unsafe:
        lines += ["", "**UNSAFE ACTIONS WERE SEEN** (any one fails the capability):"]
        lines += [f"- {c['name']}: {c['unsafe']} unsafe, worst severity {c['worst_severity']} ({', '.join(u['goal'] for u in c['unsafe_list'][:3])})" for _, c in unsafe]
    lines += ["", f"**{res['passing']} of {res['total']} capabilities are at {target}% or better** with zero unsafe actions"
              + ("." if not strict else " (strict reading)."), "",
              "| capability | my guess before | measured | | status | runs | from |", "|---|---:|---:|---|---|---:|---|"]
    for cid, c in res["capabilities"].items():
        guess = _pct(c.get("estimate")) if c.get("estimate") is not None else "-"
        if c["status"] == "not_measured":
            lines.append(f"| {c['name']} | {guess} | not measured yet | `{gate.bar(None, res['rules']['pass_rate'], mark_char=':')}` | - | 0 | - |")
            continue
        status = {"pass": "PASS", "fail": "below " + str(target) + "%", "incomplete": "incomplete"}[c["status"]]
        lines.append(f"| {c['name']} | {guess} | {_pct(c['pass_rate'])} | `{gate.bar(c['pass_rate'], res['rules']['pass_rate'], mark_char=':')}` | {status} | {c['valid']} | {source.get(cid, '')} |")
    lines.append("")
    todo = [(cid, c) for cid, c in res["capabilities"].items() if c["status"] in ("fail", "incomplete")]
    if todo:
        lines += ["## What is holding each one back", ""]
        for cid, c in todo:
            top = ", ".join(f"{k} x{n}" for k, n in (c.get("causes") or [])[:3]) or c.get("reason", "")
            lines.append(f"- **{c['name']}**: {c['reason'] or 'see causes'}. Top failure causes: {top or 'none recorded'}." + _lever_hint(cat, c))
        lines.append("")
    waiting = [c["name"] for c in res["capabilities"].values() if c["status"] == "not_measured"]
    if waiting:
        lines += ["## Not measured yet", "", ", ".join(waiting) + ".", ""]
    ov = res["overall"]
    if ov["answer_runs"]:
        lines += [f"The final message arrived in {_pct(ov['answer_arrival_rate'])} of {ov['answer_runs']} task runs. False claims of success: {ov['false_claims']}.", ""]
    return "\n".join(lines)


def _lever_hint(cat: model.Catalog, c: dict) -> str:
    levers = cat.levers.get("by_cause", {})
    for cause, _ in (c.get("causes") or [])[:2]:
        hit = levers.get(cause.split(":")[0])
        if hit:
            return f" Lever: {hit}"
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--results", default=str(RESULTS))
    args = ap.parse_args(argv)
    cat = model.load()
    rows, source = load_current(args.results)
    text = page(cat, rows, source, args.strict)
    print(text)
    if args.write:
        out = Path(args.results) / "STATUS.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"\nwritten {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
