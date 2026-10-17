"""Retrieval quality of the kernel's memory resolver (Memories.resolve) on a fixed fixture: hit@1, MRR, and how often it
claims a unique match that is wrong (that one must stay 0: forget-by-content acts on a unique match).

    python jarvis/evals/memory_retrieval.py [--kernel DIR] [--fixture FILE]

--kernel is the folder that holds the jarvis_kernel package (default: this repo's jarvis/kernel). To measure an older
resolver, point it at a copy of that version (git show HEAD:jarvis/kernel/jarvis_kernel/memory.py > copy/...).
The fixture (jarvis/kernel/tests/fixtures/memory_retrieval.json) was written before any resolver was measured on it.
No model and no network are used.
"""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def measure(kernel_dir: str, fixture: str) -> dict:
    sys.path.insert(0, kernel_dir)
    from jarvis_kernel.memory import Memories
    from jarvis_kernel.store import Kernel
    with open(fixture, encoding="utf-8") as f:
        fx = json.load(f)
    with tempfile.TemporaryDirectory() as d:
        k = Kernel(d)
        m = Memories(k)
        ids = [m.add("eval", t, "fact")["id"] for t in fx["memories"]]
        hits, rr, unique_right, unique_wrong, misses = 0, 0.0, 0, 0, []
        for case in fx["queries"]:
            r = m.resolve("eval", case["q"])
            got = [c["id"] for c in r["candidates"]]
            want = ids[case["want"]]
            if got[:1] == [want]:
                hits += 1
            else:
                misses.append(case["q"])
            rr += 1.0 / (got.index(want) + 1) if want in got else 0.0
            if r["unique"]:
                unique_right += got[:1] == [want]
                unique_wrong += got[:1] != [want]
        k.db.close()
    n = len(fx["queries"])
    return {"queries": n, "memories": len(fx["memories"]), "hit@1": round(hits / n, 3), "mrr": round(rr / n, 3),
            "unique_right": unique_right, "unique_wrong": unique_wrong, "misses": misses}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", default=os.path.join(REPO, "jarvis", "kernel"))
    ap.add_argument("--fixture", default=os.path.join(REPO, "jarvis", "kernel", "tests", "fixtures", "memory_retrieval.json"))
    a = ap.parse_args()
    print(json.dumps(measure(os.path.abspath(a.kernel), a.fixture), indent=1))


if __name__ == "__main__":
    main()
