"""Live research (plugin/plugins/research/core.run) on PUBLIC questions: real web search (the web_search plugin's DuckDuckGo
code), real page fetching (research/fetch.py), the real local claim checker (NLI ONNX model), and Dahl's public lane
through the real kernel proxy as query planner and writer (the laptop uses its local model for planning).

    JARVIS_SECRETS=<dir> RESEARCH_NLI_DIR=<nli model dir> python jarvis/evals/neko95/bench/live_research_dahl.py

Questions are public facts with one expected answer word, so correctness can be checked mechanically. No private data.
Results: results/live-research-dahl-<time>.json (key "cases"). Owner-authorised 2026-10-16.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
for p in (ROOT, ROOT / "jarvis" / "kernel"):
    sys.path.insert(0, str(p))
import httpx  # noqa: E402
from jarvis_kernel import gateway  # noqa: E402
from jarvis_kernel.llm_proxy import make_handler  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402
from plugin.plugins import web_search as WS  # noqa: E402
from plugin.plugins.research import core, fetch, verify  # noqa: E402
from plugin.plugins.research import search as searchmod  # noqa: E402

QUESTIONS = [
    ("R-eiffel", "When was the Eiffel Tower completed and how tall is it?", r"1889"),
    ("R-python", "Who created the Python programming language and when was it first released?", r"Guido|Rossum"),
    ("R-canberra", "What is the capital city of Australia and why was it chosen?", r"Canberra"),
    ("R-seasons", "What causes the seasons on Earth?", r"tilt|axial|axis"),
    ("R-boiling", "At what temperature does water boil at sea level in degrees Fahrenheit?", r"212"),
]


def main():
    tmp = tempfile.TemporaryDirectory()
    k = Kernel(os.path.join(tmp.name, "d"))
    provs = [p for p in gateway.load(str(ROOT / "jarvis/config/providers.json")) if p.name.startswith("dahl") and p.enabled]
    H, tokens = make_handler(k, provs)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url, tok = f"http://127.0.0.1:{srv.server_address[1]}", tokens.issue("bench-research", "public")

    def llm(system, user, max_tokens=3500):
        body = {"model": "x", "max_tokens": max_tokens, "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        r = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        with urllib.request.urlopen(r, timeout=420) as resp:
            out = json.loads(resp.read())
        return str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "")

    def ddg(q, n):
        async def go():
            async with httpx.AsyncClient(follow_redirects=True) as c:
                try:
                    return await WS._search_ddg_html(c, q, n)
                except Exception:  # noqa: BLE001 - the plugin itself falls back to the lite page
                    return await WS._search_ddg_lite(c, q, n)
        return [r for r in asyncio.run(go()) if r.get("url")]

    rows = []
    try:
        print("claim checker available:", verify.available(), flush=True)
        for cid, q, want in QUESTIONS:
            notes = []
            t0 = time.time()
            try:
                out = core.run(q, depth="quick", search=lambda qq, n: searchmod.merged_search(qq, n, ddg, None, notes), fetch=fetch.fetch,
                               plan_llm=lambda s, u: llm(s, u, 600), write_llm=llm, verifier=verify.supported if verify.available() else None)
            except Exception as e:  # noqa: BLE001
                rows.append({"id": cid, "area": "H", "title": q, "risk": "high", "dims": ["research"], "status": "fail",
                             "observed": f"{type(e).__name__}: {str(e)[:200]}; notes {notes[:3]}", "seconds": round(time.time() - t0, 1)})
                print("FAIL", cid, str(e)[:160], flush=True)
                continue
            spec, st, cnt = out["spec"], out["stats"], out["counts"]
            text = spec["summary"] + " " + " ".join(p for s in spec["sections"] for p in s["paragraphs"] + s["bullets"])
            read_urls = {s["url"] for s in out["record"]["sources"]}
            fabricated = [s["url"] for s in spec["sources"] if s["url"] not in read_urls]
            correct = bool(re.search(want, text, re.I))
            ok = correct and not fabricated and st.get("cited", 0) > 0 and len(spec["sources"]) > 0
            rows.append({"id": cid, "area": "H", "title": q, "risk": "high", "dims": ["research"], "status": "pass" if ok else "fail",
                         "observed": f"answer word found {correct}; cited {st.get('cited')} supported {st.get('supported')}; "
                                     f"sources {len(spec['sources'])} (fabricated {len(fabricated)}); pages read {cnt['pages_read']} "
                                     f"failed {cnt['pages_failed']}; {out['seconds']} s; notes {out['notes'][:2]}",
                         "cited": st.get("cited"), "supported": st.get("supported"), "seconds": out["seconds"]})
            print(("PASS " if ok else "FAIL ") + cid, rows[-1]["observed"][:220], flush=True)
    finally:
        srv.shutdown()
        k.db.close()
        tmp.cleanup()
    cited = sum(r.get("cited") or 0 for r in rows)
    sup = sum(r.get("supported") or 0 for r in rows)
    summary = {"pass": sum(r["status"] == "pass" for r in rows), "n": len(rows), "cited": cited, "supported": sup,
               "supported_share": round(sup / cited, 3) if cited else None}
    print(json.dumps(summary))
    out = HERE.parent / "results" / f"live-research-dahl-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "summary": summary, "cases": rows}, indent=1), encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    main()
