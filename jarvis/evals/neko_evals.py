"""Neko's measured-improvement loop: score the small models and pipelines on fixed labelled sets, save the scores, and
compare them with the previous run so a change that makes anything worse is caught (exit 1 on a regression).

    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe jarvis\\evals\\neko_evals.py [--skip pii,deliverables]

Windows, this laptop; local only (no public-model calls). Needs the running stack (llama-swap, OpenJev, plugin runtime).
Results: D:\\jarvis\\outputs\\neko\\evals\\<time>.json. Every case below is made up or a public fact; none is private.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)
OUT = os.environ.get("NEKO_EVALS_DIR", r"D:\jarvis\outputs\neko\evals")
PRIVACY_PY = r"D:\jarvis\tools\privacy-venv\Scripts\python.exe"

ROUTING = [  # (request, specialist) — the D20 tuning set plus fresh items
    ("My router admin password = X. Write a two-line note reminding me to change it every 3 months.", "general"),
    ("write a polite email to my landlord about the leaking tap", "general"),
    ("summarize the plot of Hamlet in three lines", "general"),
    ("my bill was split between three friends, who owes what if I paid 900", "math"),
    ("if a train leaves at 3pm going 60 km/h when does it cover 150 km", "math"),
    ("why does my laptop get hot when gaming", "general"),
    ("make my website's button turn red on hover", "code"),
    ("the build fails with 'module not found' after npm install", "code"),
    ("plan a 3 day trip to Goa on a budget", "general"),
    ("convert 72 fahrenheit to celsius", "math"),
    ("translate 'good morning' to Telugu", "general"),
    ("automate renaming all photos in a folder by date", "code"),
    ("write a cover letter for a junior data analyst job", "general"),
    ("my python script prints None instead of the total", "code"),
    ("how much is 18% GST on 2,450 rupees", "math"),
    ("suggest a name for my bakery", "general"),
]
DEPTH = [  # (task, needs the public adviser?)
    ("how many days are in a leap year", False), ("fix the typo in this sentence: 'I recieved the parcel'", False),
    ("convert 5 miles to kilometres", False), ("write a haiku about monsoon rain", False),
    ("what does HTTP 404 mean", False), ("give me a regex that matches an Indian PIN code", False),
    ("architect an offline-first mobile app that syncs with a server and resolves conflicts", True),
    ("work out a debt repayment strategy for three loans with different interest rates and show the schedule", True),
    ("evaluate whether my small shop should switch from cash to UPI only, considering costs, risks and customers", True),
    ("debug why my React app re-renders endlessly after I added a context provider and explain the root cause", True),
    ("create a step by step plan to migrate a 2 TB MySQL database to PostgreSQL with no downtime", True),
    ("derive the formula for compound interest and explain when monthly compounding beats yearly", True),
]
CLAIMS = [  # (passage, claim, supported?)
    ("The OWASP Top 10 is a standard awareness document for developers and web application security. It represents a broad consensus about the most critical security risks to web applications.",
     "The OWASP Top 10 is an awareness document about critical web application security risks.", True),
    ("The OWASP Top 10 is a standard awareness document for developers and web application security.", "The OWASP Top 10 is a legally binding regulation.", False),
    ("Python 3.12 was released on October 2, 2023. It adds improved error messages.", "Python 3.12 was released in October 2023.", True),
    ("Python 3.12 was released on October 2, 2023. It adds improved error messages.", "Python 3.12 was released in 2021.", False),
    ("The Web Security Academy is a free online training center from PortSwigger. It includes interactive labs.", "PortSwigger's Web Security Academy is free.", True),
    ("The Web Security Academy is a free online training center from PortSwigger. It includes interactive labs.", "The academy costs 500 dollars per year.", False),
    ("Mount Everest's summit is 8,849 metres above sea level according to a 2020 survey by Nepal and China.", "Everest is 8,849 metres high.", True),
    ("Mount Everest's summit is 8,849 metres above sea level according to a 2020 survey by Nepal and China.", "Everest is 9,200 metres high.", False),
]


def score_routing() -> dict:
    from plugin.plugins.local_team import core
    t0, right, misses, how = time.time(), 0, [], {}
    for text, want in ROUTING:
        got, by = core.route(text)
        right += got == want
        how[by.split(" ")[0]] = how.get(by.split(" ")[0], 0) + 1
        if got != want:
            misses.append(f"{text[:40]} -> {got}")
    return {"accuracy": round(right / len(ROUTING), 3), "n": len(ROUTING), "decided_by": how, "misses": misses,
            "seconds_each": round((time.time() - t0) / len(ROUTING), 2)}


def score_depth() -> dict:
    from plugin.plugins.local_team import core
    right, misses = 0, []
    for text, want in DEPTH:
        got, why = core.needs_adviser(text)
        right += got == want
        if got != want:
            misses.append(f"{text[:40]} -> {'adviser' if got else 'local'} ({why})")
    return {"accuracy": round(right / len(DEPTH), 3), "n": len(DEPTH), "misses": misses}


def score_claims() -> dict:
    from plugin.plugins.research import verify
    if not verify.available():
        return {"skipped": "NLI model not installed"}
    right = sum(verify.supported(c, [p])[0] == want for p, c, want in CLAIMS)
    return {"accuracy": round(right / len(CLAIMS), 3), "n": len(CLAIMS)}


def score_pii() -> dict:
    r = subprocess.run([PRIVACY_PY, os.path.join(REPO, "jarvis", "privacy", "eval_pii.py")], capture_output=True, text=True,
                       timeout=900, cwd=os.path.join(REPO, "jarvis", "privacy"))
    m = re.search(r"patterns \+ GLiNER \+ Jev\s+hidden (\d+)/(\d+)\s+ordinary flagged (\d+)/(\d+)", r.stdout)
    if not m:
        return {"error": f"eval_pii exit {r.returncode}"}
    h, n, f, c = map(int, m.groups())
    return {"secrets_hidden": round(h / n, 3), "ordinary_flagged": round(f / c, 3), "n": n + c}


def score_deliverables() -> dict:
    import shutil
    from plugin.plugins.deliverables import core as deliver
    fix = os.path.join(REPO, "plugin", "plugins", "deliverables", "tests", "fixtures")
    out = {}
    for name, fn in (("deck", deliver.make_presentation), ("report", deliver.make_report)):
        r = fn(json.load(open(os.path.join(fix, f"demo_{name}.json"), encoding="utf-8")))
        out[f"{name}_passed"] = 1.0 if r["qa"]["passed"] else 0.0
        out[f"{name}_seconds"] = r["seconds"]
        shutil.rmtree(r["folder"], ignore_errors=True)
    return out


# higher is better unless listed here
LOWER_IS_BETTER = {"ordinary_flagged", "seconds_each", "deck_seconds", "report_seconds"}
TOLERANCE = {"seconds_each": 0.5, "deck_seconds": 5.0, "report_seconds": 5.0}


def compare(new: dict, old: dict) -> list[str]:
    worse = []
    for area, vals in new.items():
        for k, v in (vals or {}).items():
            ov = (old.get(area) or {}).get(k)
            if not isinstance(v, (int, float)) or not isinstance(ov, (int, float)) or k == "n":
                continue
            tol = TOLERANCE.get(k, 0.0)
            if (k in LOWER_IS_BETTER and v > ov + tol) or (k not in LOWER_IS_BETTER and v < ov - tol):
                worse.append(f"{area}.{k}: {ov} -> {v}")
    return worse


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", default="", help="comma list of areas to skip: routing,depth,claims,pii,deliverables")
    a = ap.parse_args()
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    areas = {"routing": score_routing, "depth": score_depth, "claims": score_claims, "pii": score_pii, "deliverables": score_deliverables}
    res = {}
    for name, fn in areas.items():
        if name in skip:
            continue
        try:
            res[name] = fn()
        except Exception as e:                                   # noqa: BLE001 - one broken area must not hide the others
            res[name] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        print(f"{name:13s} {json.dumps(res[name])}", flush=True)
    os.makedirs(OUT, exist_ok=True)
    previous = sorted(glob.glob(os.path.join(OUT, "*.json")))
    path = os.path.join(OUT, time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "results": res}, f, indent=2)
    print("saved", path)
    if previous:
        old = json.load(open(previous[-1], encoding="utf-8"))["results"]
        worse = compare(res, old)
        print("compared with", os.path.basename(previous[-1]) + ":", "no regressions" if not worse else "WORSE: " + "; ".join(worse))
        return 1 if worse else 0
    print("first run: nothing to compare with yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
