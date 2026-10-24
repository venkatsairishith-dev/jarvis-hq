"""Propose-Probe-Compose for the planner's prompt (the method of Harness², google-research/harness2, Apache-2.0, adapted).

    python jarvis/evals/quality95/bench/harness_probe.py [--model zai-org/GLM-5.3-Flash] [--repeat 2] [--base http://127.0.0.1:48950/v1]

Harness² improves the "harness" around a fixed model (system prompt, guardrails, tool guidance, memory, skills...) by running the agent,
PROPOSING edits, PROBING each edit on the same tasks and COMPOSING a refined harness from what the contrasts show, with no ground-truth
labels needed from the model's side. Here the harness is the planner's prompt (`planner.planner_messages(..., hints=...)` already takes extra
guidance), the tasks are the 15 labelled requests of hosted_model_compare.py, and:

  propose  deterministic edits chosen from the failures of the base run (no model decides what to try); one edit = one short rule
  probe    every candidate runs on all cases, `repeat` times (so a regression elsewhere is visible)
  compose  edits join the composite greedily, best first, only if no case that was right gets worse; the composite is probed again
  adopt    only if the composite beats the base AND regresses nothing. Even then this PROPOSES: nothing is written to the planner.
           Promotion is the owner's (SELF_IMPROVEMENT_2026-10-22.md: propose, test, owner promotes, one-step rollback).

Budget (as in Harness²'s ledger): rollouts = model calls. Results: results/harness-probe-<time>.json with the lessons (an edit and the cases
it helped; an edit that hurt is recorded too, so it is not proposed again). Uses the privacy gateway and the placeholder key (no key typed).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("hosted_model_compare", HERE / "hosted_model_compare.py")
H = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("hosted_model_compare", H)
_spec.loader.exec_module(H)
planner = H.planner

# Edit library: a cue found in the base run's failure reasons -> one invariant rule (general) or domain rule (about one kind of request).
EDITS = [
    {"id": "answer-json-only", "family": "invariant", "cue": re.compile(r"(?i)json|not a plan|expected|empty|parse"),
     "text": "Reply with the JSON object only: no markdown fence, no text before or after it."},
    {"id": "args-keep-user-words", "family": "invariant", "cue": re.compile(r"(?i)argument lacks"),
     "text": "In a plugin step, copy the user's own key words (names, places, dates, titles, topics) into the args, unchanged."},
    {"id": "cannot-when-not-listed", "family": "invariant", "cue": re.compile(r"(?i)impossible|planned something"),
     "text": "Ordering, buying, booking, paying and sending messages are not in the catalogue. For those return {\"summary\": \"cannot\", \"steps\": []}."},
    {"id": "outside-facts-use-search", "family": "domain", "cue": re.compile(r"(?i)first step think|first step jarvis"),
     "text": "Facts about the outside world (news, weather, places, people, prices) need web_search or research; \"think\" cannot know them."},
    {"id": "notes-use-vault", "family": "domain", "cue": re.compile(r"(?i)vault"),
     "text": "\"Save\", \"write down\" or \"note\" means one vault:save_note step with a short title and the text."},
    {"id": "code-and-files-use-jarvis", "family": "domain", "cue": re.compile(r"(?i)first step (web_search|research|think)|first step plugin"),
     "text": "Work on the owner's own files or code folder is one \"jarvis\" step, not a search."},
    {"id": "translate-is-think", "family": "domain", "cue": re.compile(r"(?i)first step (web_search|research|jarvis)"),
     "text": "Translating, rewriting and short jokes are one \"think\" step with no outside access."},
]


def run(ask: Callable, base: str, model: str, hints: str, repeat: int, max_tokens: int, rollouts: list[int]) -> dict[str, dict]:
    """{request: {'right': n, 'of': repeat, 'why': [..]}} for one harness (hints text)."""
    out = {}
    for req, expect, word in H.CASES:
        messages = planner.planner_messages(req, H.CAPS, 4, "work", hints=hints)
        right, why = 0, []
        for _ in range(repeat):
            rollouts[0] += 1
            text, _used, _secs, finish = ask(base, model, messages, max_tokens)
            ok, correct, reason = H.score(text, expect, word) if text else (False, False, finish or "empty answer")
            right += bool(correct)
            if not correct:
                why.append(reason)
        out[req] = {"right": right, "of": repeat, "why": why}
    return out


def good(row: dict) -> bool:
    return row["right"] * 2 >= row["of"] and row["right"] > 0          # right at least half the time


def total(res: dict) -> int:
    return sum(r["right"] for r in res.values())


def contrast(base: dict, cand: dict) -> tuple[list[str], list[str]]:
    """(fixed, regressed) request lists, by the majority rule."""
    fixed = [q for q in base if not good(base[q]) and good(cand[q])]
    regressed = [q for q in base if good(base[q]) and not good(cand[q])]
    return fixed, regressed


def propose(base_res: dict, tried: set[str] | None = None) -> list[dict]:
    reasons = " | ".join(w for r in base_res.values() for w in r["why"])
    return [e for e in EDITS if e["id"] not in (tried or set()) and e["cue"].search(reasons)]


def hints_of(edits: list[dict]) -> str:
    return "Extra rules learned from earlier runs:\n" + "\n".join(f"- {e['text']}" for e in edits) if edits else ""


def improve(ask: Callable, base: str, model: str, repeat: int = 2, max_tokens: int = 6000, existing_hints: str = "") -> dict:
    rollouts = [0]
    base_res = run(ask, base, model, existing_hints, repeat, max_tokens, rollouts)
    cands = propose(base_res)
    probes, lessons = [], []
    for e in cands:
        res = run(ask, base, model, (existing_hints + "\n" + hints_of([e])).strip(), repeat, max_tokens, rollouts)
        fixed, regressed = contrast(base_res, res)
        probes.append({"edit": e["id"], "family": e["family"], "fixed": fixed, "regressed": regressed, "delta": total(res) - total(base_res), "_res": res})
        lessons.append({"edit": e["id"], "text": e["text"], "helped": fixed, "hurt": regressed})
    # compose: best first, keep an edit only if nothing that was right gets worse with it
    chosen = []
    for p in sorted(probes, key=lambda p: (-len(p["fixed"]), len(p["regressed"]), p["edit"])):
        if p["fixed"] and not p["regressed"]:
            chosen.append(next(e for e in cands if e["id"] == p["edit"]))
    final, adopt, why = base_res, False, "no edit fixed a case without hurting another"
    if chosen:
        final = run(ask, base, model, (existing_hints + "\n" + hints_of(chosen)).strip(), repeat, max_tokens, rollouts)
        fixed, regressed = contrast(base_res, final)
        adopt = bool(fixed) and not regressed and total(final) > total(base_res)
        why = (f"composite of {len(chosen)} edit(s) fixes {len(fixed)} case(s)" if adopt else
               "the composite did not beat the base without regressions" + (f" (regressed: {regressed})" if regressed else ""))
    for p in probes:
        p.pop("_res")
    return {"model": model, "repeat": repeat, "rollouts": rollouts[0], "base_correct": total(base_res), "of": repeat * len(H.CASES),
            "final_correct": total(final), "candidates": [p["edit"] for p in probes], "probes": probes, "chosen": [e["id"] for e in chosen],
            "adopt": adopt, "reason": why, "hints": hints_of(chosen) if adopt else "", "lessons": lessons}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:48950/v1")
    ap.add_argument("--model", default="zai-org/GLM-5.3-Flash")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=6000)
    a = ap.parse_args()
    rep = improve(H.ask, a.base, a.model, a.repeat, a.max_tokens)
    print(f"base {rep['base_correct']}/{rep['of']} -> composite {rep['final_correct']}/{rep['of']}  rollouts {rep['rollouts']}  adopt={rep['adopt']}")
    print(rep["reason"])
    for p in rep["probes"]:
        print(f"  {p['edit']:28} {p['family']:10} fixed {len(p['fixed'])} hurt {len(p['regressed'])} delta {p['delta']:+d}")
    if rep["adopt"]:
        print("\nPROPOSED hints for planner_messages(hints=...) (not applied; the owner promotes):\n" + rep["hints"])
    out = HERE.parent / "results" / f"harness-probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), **rep}, indent=1), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
