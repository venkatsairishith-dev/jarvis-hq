"""Measure the language layer on eval_sentences.jsonl (D58). Run on the laptop with the real backend; `--backend oracle` only tests this runner.

    python jarvis/evals/translate/run_eval.py --backend real --decider local --roundtrip --out D:\\jarvis\\data\\evals\\translate-2026-10-27.json

What it measures, per language group (te, hi, te-Latn, hi-Latn, mixed) and overall:
  detection     did detect_language() give the labelled language (deterministic part: no model)
  chrF++        translate_to_english(text) against the reference English (0..100, jarvis/plugins/_shared/translate_task.chrf; no dependencies)
  seconds       per sentence: mean, median, p95 (the laptop's real number; the design assumes 3 s)
  intent        exact-intent agreement: the decider's tool sequence for the translated English equals the one for the reference English
                (--decider rules: the decider's deterministic fast path only, no model; --decider local: the real decider through llama-swap, one model load)
  roundtrip     (--roundtrip) English reference -> Telugu / Hindi -> English: chrF++ similarity of the two English texts and how many fall under
                config language.back_translation_min (those would be flagged to the owner)
  preserved     sentences with a path, quoted name or file name: did it come back exactly
  resources     peak RSS of this process in MB (psutil if installed) and the load seconds the backends report

NOTHING HERE WAS RUN AGAINST A REAL BACKEND (no model on the machine that wrote it). The sentences were written by Claude and are not native-checked.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.plugins._shared import translate as T            # noqa: E402
from jarvis.plugins._shared import translate_task as TT      # noqa: E402

DATA = HERE / "eval_sentences.jsonl"


def load(path: Path = DATA) -> list[dict]:
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith('{"_meta"'):
            rows.append(json.loads(ln))
    return rows


def _tools(plan) -> list[str] | None:
    try:
        return [str(s.get("tool")) for s in plan.steps] if plan is not None else None
    except Exception:                                      # noqa: BLE001
        return None


def intent_of(text: str, mode: str):
    """The decider's tool sequence for an English request, or None when it cannot be asked."""
    if mode == "none":
        return None
    from jarvis.plugins.desktop import decide
    try:
        plan = decide.fast_path(text, []) if mode == "rules" else decide.decide(text, [])
    except Exception:                                      # noqa: BLE001 - decider down
        return None
    return _tools(plan)


def oracle_translator(rows: list[dict]) -> T.Translator:
    """A translator whose backend returns the reference English for the exact input: only for testing the runner and the metrics."""
    sentences = {}
    for r in rows:
        m = T.mask(r["text"], [])
        ref = r["english"]
        # the oracle answers for the MASKED text, with the masked items put into the reference so unmasking restores them
        mr = T.mask(ref, [])
        sentences[m.text] = mr.text if len(mr.items) == len(m.items) else ref
    return T.Translator([T.FakeBackend(sentences=sentences)], {"keep_original": False, "budget_s": 30.0}, glossary=[])


def run(rows: list[dict], translator: T.Translator, decider: str = "none", roundtrip: bool = False) -> dict:
    per: dict[str, list[dict]] = {}
    for r in rows:
        det = T.detect_language(r["text"])
        want = "te" if r["lang"] == "mixed" else r["lang"]
        t0 = time.monotonic()
        res = translator.to_english(r["text"])
        secs = time.monotonic() - t0
        row = {"id": r["id"], "lang": r["lang"], "task": r["task"], "detected": det, "detect_ok": det == want, "english": res.english, "reference": r["english"],
               "translated": res.translated, "confidence": res.confidence, "backend": res.backend, "skipped": res.skipped_reason, "seconds": round(secs, 3),
               "chrf": TT.chrf(res.english, r["english"]) if res.translated else 0.0}
        ref_tools = intent_of(r["english"], decider)
        got_tools = intent_of(res.english, decider) if res.translated else None
        row["intent_ref"], row["intent_got"] = ref_tools, got_tools
        row["intent_agree"] = (ref_tools == got_tools) if ref_tools is not None and got_tools is not None else None
        if roundtrip:
            tgt = "hi" if r["lang"] in ("hi", "hi-Latn") else "te"
            bt = TT.back_translation(r["english"], tgt, translator=translator)
            row["roundtrip"] = {"target": tgt, "similarity": bt["similarity"], "ran": bt["ran"], "back": (bt.get("back") or {}).get("english"), "forward": (bt.get("forward") or {}).get("english")}
        per.setdefault(r["lang"], []).append(row)

    def summary(rs: list[dict]) -> dict:
        secs = sorted(x["seconds"] for x in rs)
        ag = [x["intent_agree"] for x in rs if x["intent_agree"] is not None]
        out = {"n": len(rs), "detection_accuracy": round(sum(x["detect_ok"] for x in rs) / len(rs), 3),
               "translated": sum(x["translated"] for x in rs), "chrf_mean": round(statistics.mean(x["chrf"] for x in rs), 2),
               "seconds_mean": round(statistics.mean(secs), 3), "seconds_median": round(statistics.median(secs), 3),
               "seconds_p95": round(secs[min(len(secs) - 1, int(0.95 * len(secs)))], 3),
               "intent_agreement": round(sum(ag) / len(ag), 3) if ag else None, "intent_asked": len(ag)}
        rt = [x["roundtrip"]["similarity"] for x in rs if x.get("roundtrip") and x["roundtrip"]["ran"]]
        if rt:
            floor = float(translator.cfg.get("back_translation_min", 0.5))
            out["roundtrip_similarity_mean"] = round(statistics.mean(rt), 3)
            out["roundtrip_flagged"] = sum(1 for s in rt if s < floor)
        return out

    allrows = [x for rs in per.values() for x in rs]
    res = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "backends": [b.name for b in translator.backends], "decider": decider,
           "overall": summary(allrows), "by_language": {k: summary(v) for k, v in per.items()}, "rows": allrows}
    try:
        import psutil
        res["peak_rss_mb"] = round(psutil.Process().memory_info().rss / 1048576)
    except Exception:                                      # noqa: BLE001
        res["peak_rss_mb"] = None
    res["load_seconds"] = {b.name: getattr(b, "load_seconds", {}) for b in translator.backends}
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backend", choices=("real", "oracle"), default="real")
    ap.add_argument("--decider", choices=("none", "rules", "local"), default="none")
    ap.add_argument("--roundtrip", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    rows = load()
    if a.limit:
        rows = rows[:a.limit]
    translator = oracle_translator(rows) if a.backend == "oracle" else T.get_translator()
    if not translator.available():
        print("No translation backend is available (neither the IndicTrans2 folder nor the llama-swap alias): nothing to measure.", file=sys.stderr)
        return 2
    res = run(rows, translator, a.decider, a.roundtrip)
    print(json.dumps({"overall": res["overall"], "by_language": res["by_language"], "peak_rss_mb": res["peak_rss_mb"], "load_seconds": res["load_seconds"]}, indent=2))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
