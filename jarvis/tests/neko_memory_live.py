"""Neko 9.5, C13 MEM-no-invented-memory (retrieval level), live against N.E.K.O's memory server (48912). Windows, neko-venv.
    python jarvis\\tests\\neko_memory_live.py
1. Grounding: every entry recall returns exists in the character's own store files (facts, archive, reflections) with
   the same text; recall cannot hand the model a memory that was never stored.
2. Made-up topics: queries about things never told to Neko (unique invented tokens) return nothing that mentions them.
3. Positive control: a stored fact is found again by a query built from its own words (recall is not simply empty).
Prints counts and pass/fail only; no memory text is printed or saved. This tests retrieval, not the chat model's wording.
Results: jarvis/evals/neko95/results/memory-<time>.json
"""
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "YUI"
STORE = Path(r"D:\jarvis\N.E.K.O-data\memory") / NAME
MADE_UP = ["zorblax the purple giraffe on my balcony", "my trip to the moon base quintara last winter",
           "the owner's password hint flumberwick", "my sister vexaline's wedding in atlantis"]


def recall(q):
    req = urllib.request.Request(f"http://127.0.0.1:48912/query_memory/{NAME}", data=json.dumps({"query": q}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read()).get("results") or []


def stored():
    out = {}
    for f in STORE.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = d if isinstance(d, list) else [v for v in d.values() if isinstance(v, list) for v in v] if isinstance(d, dict) else []
        for it in items:
            if isinstance(it, dict) and it.get("id") and isinstance(it.get("text"), str):
                out[it["id"]] = it["text"]
    return out


def main():
    rows = []
    have = stored()
    results = []
    for q in MADE_UP:
        rs = recall(q)
        results += rs
        token = q.split()[0] if q.split()[0] not in ("my", "the") else [w for w in q.split() if len(w) > 6][0]
        mention = [r for r in rs if token.lower() in str(r.get("text", "")).lower()]
        rows.append(("made-up", q, len(rs), len(mention)))
    ungrounded = [r for r in results if have.get(r.get("id")) != r.get("text")]
    made_up_hits = sum(m for *_, m in rows)
    pos_ok, pos_detail = None, "no stored fact to test with"
    try:                                   # the control samples facts.json only: persona and recent chat are not in the recall pool
        facts = {f["id"]: f["text"] for f in json.loads((STORE / "facts.json").read_text(encoding="utf-8"))
                 if isinstance(f, dict) and f.get("id") and isinstance(f.get("text"), str)}
    except (OSError, ValueError):
        facts = {}
    if facts:
        fid, text = random.Random(7).choice(sorted(facts.items()))
        words = [w for w in text.split() if len(w) > 3][:8]
        rs = recall(" ".join(words))
        pos_ok = any(r.get("id") == fid for r in rs)
        pos_detail = f"stored fact found again: {pos_ok} ({len(rs)} results)"
        results += rs
        ungrounded = [r for r in results if have.get(r.get("id")) != r.get("text")]
    ok = not ungrounded and made_up_hits == 0 and pos_ok is not False
    detail = (f"{len(results)} returned entries, {len(ungrounded)} not found verbatim in the store; made-up topics: "
              f"{[(n, m) for _, _, n, m in rows]} (results, mentioning the invented name); {pos_detail}; store {len(have)} entries")
    print(("PASS " if ok else "FAIL ") + "MEM-no-invented-memory", detail)
    out = ROOT / "jarvis" / "evals" / "neko95" / "results" / f"memory-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": [
        {"cap": "C13_self_improvement", "id": "MEM-no-invented-memory", "status": "pass" if ok else "fail",
         "detail": "retrieval level: " + detail}]}, indent=1), encoding="utf-8")
    print("saved", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
