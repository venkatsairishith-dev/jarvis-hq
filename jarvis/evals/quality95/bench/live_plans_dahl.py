"""The PC-task decision pipeline (jarvis/plugins/desktop/decide.py: fast path, model plan, validate, rule completion) with a
SUBSTITUTE model: Dahl's public-lane model through the real kernel proxy instead of the laptop's local decider.

    JARVIS_SECRETS=<dir with providers/dahl.key> python jarvis/evals/quality95/bench/live_plans_dahl.py [--repeat 1] [--sets ...]

What it shows: whether the deterministic layers around the model turn a capable model's plans into correct, complete
plans for the labelled cases. What it does NOT show: the production decider's accuracy or latency (that model runs on
the laptop's GPU); v1's C3 score is not touched. Names in the requests are replaced by made-up ones (Dahl never gets
private data) with a matching synthetic profile list. Owner-authorised 2026-10-16.
Results: results/live-plans-dahl-<time>.json (key "cases").
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
for p in (ROOT, ROOT / "jarvis" / "kernel", ROOT / "jarvis" / "evals" / "pc_control"):
    sys.path.insert(0, str(p))
from jarvis_kernel import gateway  # noqa: E402
from jarvis_kernel.llm_proxy import make_handler  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402
from jarvis.plugins.desktop import decide  # noqa: E402
import eval_decision  # noqa: E402

SUBST = [(re.compile(r"VenkatAMV", re.I), "RiverMoon"), (re.compile(r"\bvenkat\b", re.I), "robin"),
         (re.compile(r"\bsatish\b", re.I), "Kiran")]
PROFILES = [  # synthetic, same shape as the owner's: Default = the default account; "robin" is ambiguous on purpose
    {"profile": "Default", "name": "Robin", "account": "robin.main@example.com", "default": True, "say": ["robin", "robinmain"]},
    {"profile": "Profile 1", "name": "RiverMoon", "account": "rivermoon@example.com", "default": False, "say": ["rivermoon", "robin"]},
    {"profile": "Profile 2", "name": "Kiran", "account": "kiran@example.com", "default": False, "say": ["kiran"]},
]
SETS = {"plan_cases": HERE.parent / "plan_cases.json", "dev": ROOT / "jarvis/evals/pc_control/decision_cases.json",
        "holdout": ROOT / "jarvis/evals/pc_control/decision_cases_holdout.json",
        "holdout2": ROOT / "jarvis/evals/pc_control/decision_cases_holdout2.json"}


def sanitize(s: str) -> str:
    for rx, rep in SUBST:
        s = rx.sub(rep, s)
    return s


def proxy():
    tmp = tempfile.TemporaryDirectory()
    k = Kernel(os.path.join(tmp.name, "d"))
    provs = [p for p in gateway.load(str(ROOT / "jarvis/config/providers.json")) if p.name.startswith("dahl") and p.enabled]
    H, tokens = make_handler(k, provs)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return tmp, k, srv, f"http://127.0.0.1:{srv.server_address[1]}", tokens.issue("bench-decider", "public")


def make_post(url, tok, usage):
    def post(body):
        body = dict(body)
        body["max_tokens"] = 1500             # a reasoning model needs room before the JSON
        # Dahl's structured-output mode returns steps with the tool name only ({"tool":"open_app"}, measured 2026-10-16),
        # unlike llama.cpp's grammar on the laptop; the stand-in model gets the same prompt without it and its JSON is parsed
        body.pop("response_format", None)
        body.pop("chat_template_kwargs", None)
        body["messages"] = [dict(body["messages"][0], content=body["messages"][0]["content"]
                                 + "\nAnswer with the JSON object only: {\"steps\": [...]}, each step with its arguments.")] + body["messages"][1:]
        r = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                out = json.loads(resp.read())
        except (urllib.error.URLError, ValueError) as e:
            raise decide.DecisionError(f"proxy: {type(e).__name__}") from None
        usage.append((out.get("usage") or {}).get("total_tokens", 0))
        text = str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        text = re.sub(r"(?s)<think>.*?</think>", "", text).strip()       # reasoning models: keep the answer only
        m = re.search(r"(?s)\{.*\}", text)
        return m.group(0) if m else text
    return post


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--sets", default="plan_cases,dev,holdout,holdout2")
    a = ap.parse_args(argv)
    cases = []
    for s in a.sets.split(","):
        for c in json.loads(SETS[s].read_text(encoding="utf-8")):
            cases.append({**c, "set": s, "req": sanitize(c["req"]), "context": sanitize(c.get("context", ""))})
    tmp, k, srv, url, tok = proxy()
    usage, lat, rows = [], [], []
    by = defaultdict(list)
    try:
        post = make_post(url, tok, usage)
        for rep in range(a.repeat):
            for c in cases:
                t0 = time.perf_counter()
                p = decide.decide(c["req"], PROFILES, model="dahl-substitute", post=post, timeout=300, context=c["context"])
                dt = time.perf_counter() - t0
                ok, why = eval_decision.score(p, c["expect"])
                if p.model != "rule":
                    lat.append(dt)
                by[(c["set"], c["id"])].append((ok, why, p.model, [s.get("tool") for s in p.steps], p.status))
        for (st, cid), runs in by.items():
            ok = all(r[0] for r in runs)
            rows.append({"id": f"{st}:{cid}", "set": st, "status": "pass" if ok else "fail", "runs": len(runs),
                         "rule_path": runs[0][2] == "rule", "observed": f"{runs[0][1]} | tools {runs[0][3]} status {runs[0][4]}"[:300]})
    finally:
        srv.shutdown()
        k.db.close()
        tmp.cleanup()
    summary = {}
    for st in a.sets.split(","):
        rs = [r for r in rows if r["set"] == st]
        summary[st] = {"pass": sum(r["status"] == "pass" for r in rs), "n": len(rs), "rule_path": sum(r["rule_path"] for r in rs)}
    print(json.dumps(summary))
    for r in rows:
        if r["status"] == "fail":
            print("FAIL", r["id"], r["observed"][:170])
    if lat:
        print(f"model-path latency through Dahl: p50 {statistics.median(lat):.2f} s, max {max(lat):.2f} s; tokens/call p50 {statistics.median(usage) if usage else 0}")
    out = HERE.parent / "results" / f"live-plans-dahl-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "model": "Dahl public lane (substitute for the local decider)",
                               "repeat": a.repeat, "summary": summary, "latency_p50_s": statistics.median(lat) if lat else None,
                               "tokens_total": sum(usage), "cases": rows}, indent=1), encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    main()
