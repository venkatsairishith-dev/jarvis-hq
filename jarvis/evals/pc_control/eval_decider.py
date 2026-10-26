"""Score the shared decision engine (D54b, jarvis/plugins/_shared/decider.py) on labelled cases, per kind, for each local model. Windows, agent-venv;
llama-swap on 127.0.0.1:8080 (one model at a time swaps in; the first call of each model is cold).

    python jarvis\\evals\\pc_control\\eval_decider.py --models decider,decider-qwen3-1.7b [--apply] [--rules-only] [--out results\\decider-<time>.json]

For every model and every case (decider_cases.json + the examples of the catalogue config/simple_tasks.json as intent=simple_task) it measures TWO things:
  * engine   rules first, then the model, then the backstops (what JARVIS really does): accuracy per kind, how often it asked, and wrong answers it was SURE about;
  * model    the model alone (rules skipped): accuracy per kind and latency p50/p95, so the 1.7B can be compared with the 4B kind by kind.
It also checks the raise-only rule (a dialog class lower than the rule's floor is counted as a violation; it must be 0) and suggests, per kind, the smallest
confidence threshold at which the answered cases are at least 95% right with at least 60% of the cases answered. `--apply` writes those thresholds (and the
cheapest model per kind that holds within 2 points of the best) into jarvis/config/decider.json; without it nothing is changed.
`--rules-only` needs no model at all (CI, or a first look): only the engine column is filled.
Results: jarvis/evals/pc_control/results/decider-<time>.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from jarvis.plugins._shared import decider  # noqa: E402
from jarvis.plugins.desktop import decide as _desktop_decide  # noqa: E402,F401  (registers the catalogue matcher with the engine)

CASES = HERE / "decider_cases.json"
CATALOGUE = Path(decider.JARVIS) / "config" / "simple_tasks.json"
TARGET_ACCURACY, MIN_COVERAGE = 0.95, 0.60
RULE_FLOOR = {"D04": "B", "D05": "B", "D07": "C", "D08": "C", "D09": "C", "D10": "C", "D11": "C", "D12": "C", "D13": "C", "D14": "C", "D15": "B"}


def load_cases(path: Path = CASES, catalogue: Path = CATALOGUE) -> list[dict]:
    cases = list(json.loads(path.read_text(encoding="utf-8"))["cases"])
    try:
        tasks = json.loads(catalogue.read_text(encoding="utf-8-sig"))["tasks"]
    except (OSError, ValueError, KeyError):
        tasks = []
    for t in tasks:
        if t.get("class") in ("A", "B") and t.get("how", {}).get("handler") not in ("window", "media_key", "open_site", "open_file") and t.get("examples"):
            cases.append({"id": "T-" + t["id"], "kind": "intent", "text": t["examples"][0]["say"], "expect": "simple_task", "tags": ["catalogue"]})
    return cases


def options_of(case: dict) -> list:
    if case["kind"] == "intent":
        return list(decider.INTENTS)
    if case["kind"] == "dialog_class":
        return list(decider.CLASSES)
    if case["kind"] == "specialist":
        return ["reason", "ui_decide", "screen_read", "screen_locate", "doc_read", "chat_simple"]
    return list(case.get("candidates") or [])


def run_case(case: dict, model: str | None, rules: bool, post=None, cfg: dict | None = None) -> dict:
    decider.clear()
    cfg = dict(cfg or decider.config())
    if model:
        cfg = dict(cfg, models={k: model for k in decider.KINDS}, cache_s=0)
    t0 = time.monotonic()
    d = decider.decide(case["kind"], case["text"], options_of(case), post=post, use_model=bool(model) or post is not None, skip_rules=not rules, cfg=cfg,
                       timeout=60.0 if model else None)
    ms = (time.monotonic() - t0) * 1000
    got = "ask" if d.ask or d.choice is None else d.choice
    row = {"id": case["id"], "kind": case["kind"], "expect": case["expect"], "got": got, "confidence": round(d.confidence, 3), "source": d.source, "ms": round(ms),
           "ok": got == case["expect"] or got in (case.get("accept") or []), "tags": case.get("tags") or []}
    if case["kind"] == "dialog_class" and case["id"] in RULE_FLOOR and d.choice:
        row["lowered"] = decider.RISK[d.choice] < decider.RISK[RULE_FLOOR[case["id"]]]
    return row


def summarize(rows: list[dict]) -> dict:
    out = {}
    for kind in sorted({r["kind"] for r in rows}):
        rs = [r for r in rows if r["kind"] == kind]
        sure_wrong = [r["id"] for r in rs if not r["ok"] and r["got"] != "ask" and r["confidence"] >= 0.8]
        lat = sorted(r["ms"] for r in rs)
        out[kind] = {"n": len(rs), "accuracy": round(sum(r["ok"] for r in rs) / len(rs), 3), "asked": sum(r["got"] == "ask" for r in rs),
                     "sure_but_wrong": sure_wrong, "p50_ms": int(statistics.median(lat)), "p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))],
                     "lowered_class": [r["id"] for r in rs if r.get("lowered")], "failed": [r["id"] for r in rs if not r["ok"]]}
        tags = {}
        for tag in ("hindi", "telugu", "mixed", "injection", "vague"):
            ts = [r for r in rs if tag in r["tags"]]
            if ts:
                tags[tag] = round(sum(r["ok"] for r in ts) / len(ts), 3)
        if tags:
            out[kind]["by_tag"] = tags
    return out


def suggest_thresholds(rows: list[dict]) -> dict:
    """Per kind: the smallest confidence at which the cases the engine ANSWERED (confidence at or above it) are >= 95% right and at least 60% of the cases are
    answered. A kind with no such threshold keeps a cautious 0.9. dialog_class stays 0: its class is never withheld (raise-only)."""
    out = {}
    for kind in sorted({r["kind"] for r in rows}):
        if kind == "dialog_class":
            out[kind] = 0.0
            continue
        rs = [r for r in rows if r["kind"] == kind]
        choice = 0.9
        for t in [x / 100 for x in range(40, 100, 5)]:
            answered = [r for r in rs if r["got"] != "ask" and r["confidence"] >= t]
            if rs and len(answered) / len(rs) >= MIN_COVERAGE and sum(r["ok"] for r in answered) / len(answered) >= TARGET_ACCURACY:
                choice = t
                break
        out[kind] = choice
    return out


def apply_config(thresholds: dict, models: dict, path: Path = decider.CONFIG_FILE) -> None:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    data["thresholds"] = {**data.get("thresholds", {}), **{k: v for k, v in thresholds.items() if k in decider.KINDS}}
    data["models"] = {k: v for k, v in models.items() if k in decider.KINDS}
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def pick_models(per_model: dict, default: str) -> dict:
    """Per kind the cheapest (smaller / faster) model within 2 points of the best model-only accuracy; the 1.7B is preferred when it holds."""
    best = {}
    for kind in {k for m in per_model.values() for k in m["model"]}:
        scores = {name: m["model"][kind]["accuracy"] for name, m in per_model.items() if kind in m["model"]}
        top = max(scores.values())
        ok = [n for n, s in scores.items() if s >= top - 0.02]
        best[kind] = sorted(ok, key=lambda n: (0 if "1.7b" in n else 1 if n != default else 2, n))[0]
    return {k: v for k, v in best.items() if v != default}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="decider,decider-qwen3-1.7b")
    ap.add_argument("--rules-only", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    cases = load_cases()
    result = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "cases": len(cases), "kinds": sorted({c["kind"] for c in cases}), "models": {}}
    engine_rows_for_thresholds = []
    names = [] if a.rules_only else [m.strip() for m in a.models.split(",") if m.strip()]
    if a.rules_only:
        rows = [run_case(c, None, True) for c in cases]
        result["rules_only"] = {"engine": summarize(rows)}
        engine_rows_for_thresholds = rows
    for name in names:
        print(f"== {name}", flush=True)
        run_case({"id": "warm", "kind": "intent", "text": "hello", "expect": "chat"}, name, True)           # the load is not part of the latency
        eng = [run_case(c, name, True) for c in cases]
        mod = [run_case(c, name, False) for c in cases]
        result["models"][name] = {"engine": summarize(eng), "model": summarize(mod)}
        engine_rows_for_thresholds = engine_rows_for_thresholds or eng
        for kind, s in result["models"][name]["engine"].items():
            print(f"  {kind:13} engine {s['accuracy']:.2f}  model-alone {result['models'][name]['model'][kind]['accuracy']:.2f}  asked {s['asked']}  sure-but-wrong {s['sure_but_wrong']}  "
                  f"p50 {result['models'][name]['model'][kind]['p50_ms']} ms", flush=True)
    result["suggested_thresholds"] = suggest_thresholds(engine_rows_for_thresholds)
    if names:
        result["suggested_models"] = pick_models(result["models"], "decider")
    out = Path(a.out) if a.out else HERE / "results" / f"decider-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print("results:", out)
    print("suggested thresholds:", result["suggested_thresholds"], "models:", result.get("suggested_models", {}))
    if a.apply and names:
        apply_config(result["suggested_thresholds"], result.get("suggested_models", {}))
        print("written to", decider.CONFIG_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
