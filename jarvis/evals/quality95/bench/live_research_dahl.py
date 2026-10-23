"""Live research (jarvis/plugins/research/core.run) on PUBLIC questions: real web search (the web_search plugin's DuckDuckGo
code), real page fetching (research/fetch.py), the real local claim checker (NLI ONNX model), and Dahl's public lane
through the real kernel proxy as query planner and writer (the laptop uses its local model for planning).

    JARVIS_SECRETS=<dir> RESEARCH_NLI_DIR=<nli model dir> python jarvis/evals/quality95/bench/live_research_dahl.py

Questions are public facts with one expected answer word, so correctness can be checked mechanically. No private data.
Results: results/live-research-dahl-<time>.json (key "cases"). Owner-authorised 2026-10-16.

Pass rule (MEM-M10, tightened 2026-10-18; see judge()): the answer word must appear in a CONFIRMED part of the report (the
summary or a "fact" section, never the "could not be confirmed" or "sources disagree" sections) in a sentence that cites a source
of the report, the claim checker must have been available and have supported at least one cited sentence, and no source may be
invented. The supported share (supported / cited sentences) is reported for every case and overall. The earlier rule passed
when the answer word appeared anywhere in the report, including the unconfirmed section. Needs the network and a key: it cannot
run in the offline test suites; judge() is unit-tested with canned reports (tests/test_live_research_judge.py).
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

QUESTIONS = [
    ("R-eiffel", "When was the Eiffel Tower completed and how tall is it?", r"1889"),
    ("R-python", "Who created the Python programming language and when was it first released?", r"Guido|Rossum"),
    ("R-canberra", "What is the capital city of Australia and why was it chosen?", r"Canberra"),
    ("R-seasons", "What causes the seasons on Earth?", r"tilt|axial|axis"),
    ("R-boiling", "At what temperature does water boil at sea level in degrees Fahrenheit?", r"212"),
]


CITE = re.compile(r"\[(S\d{1,3})\]")
# a sentence ends at . ! ? or at a closing citation marker, as the report maker writes "Sentence. [S1] Next sentence. [S2]"
SPLIT = re.compile(r"(?<=[.!?\]])\s+(?=[A-Z0-9\"'(])")
UNCONFIRMED_HEADINGS = ("could not be confirmed", "sources disagree")


def confirmed_sentences(spec: dict) -> list[str]:
    """The sentences the report stands behind: the summary and the "fact" sections. A "fact" section holds only sentences the
    claim checker supported; "uncertain" sections (statements that could not be confirmed, sources that disagree), opinions and
    recommendations are not confirmed, and without a claim checker no section is labelled "fact" at all."""
    items = [spec.get("summary") or ""]
    for sec in spec.get("sections") or []:
        heading = str(sec.get("heading") or "").lower()
        if sec.get("kind") != "fact" or any(h in heading for h in UNCONFIRMED_HEADINGS):
            continue
        items += list(sec.get("paragraphs") or []) + list(sec.get("bullets") or [])
    return [x.strip() for it in items for x in SPLIT.split(it) if x.strip()]


def judge(spec: dict, stats: dict, read_urls, want: str) -> dict:
    """Pass or fail one research report (pure; no network). The answer (regex `want`) must stand in a confirmed sentence that
    cites a source listed in the report, with the claim checker available and at least one supported citation; every source
    must be a page that was really read."""
    ids = {s["id"] for s in spec.get("sources") or []}
    fabricated = [s["url"] for s in spec.get("sources") or [] if s["url"] not in set(read_urls)]
    whole = (spec.get("summary") or "") + " " + " ".join(p for s in spec.get("sections") or [] for p in (s.get("paragraphs") or []) + (s.get("bullets") or []))
    anywhere = bool(re.search(want, whole, re.I))                                 # the old rule, kept for the record
    hits = [x for x in confirmed_sentences(spec) if re.search(want, x, re.I) and any(c in ids for c in CITE.findall(x))]
    cited, supported = int(stats.get("cited") or 0), int(stats.get("supported") or 0)
    checked = bool(stats.get("checked"))
    reasons = []
    if not hits:
        reasons.append("the answer is not in a confirmed, cited sentence" + (" (it appears only outside the confirmed parts)" if anywhere else ""))
    if not checked:
        reasons.append("the claim checker was not available: nothing was checked against a source")
    elif supported < 1:
        reasons.append("no cited sentence was supported by its source")
    if cited < 1 or not ids:
        reasons.append("no cited sources")
    if fabricated:
        reasons.append(f"{len(fabricated)} source(s) that were never read")
    return {"ok": not reasons, "reasons": reasons, "answer_anywhere": anywhere, "confirmed_hits": len(hits), "cited": cited,
            "supported": supported, "supported_share": round(supported / cited, 3) if cited else None, "fabricated": fabricated}


def main():
    import httpx
    from jarvis_kernel import gateway
    from jarvis_kernel.llm_proxy import make_handler
    from jarvis_kernel.store import Kernel
    from jarvis.plugins import web_search as WS
    from jarvis.plugins.research import core, fetch, verify
    from jarvis.plugins.research import search as searchmod
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
            j = judge(spec, st, {s["url"] for s in out["record"]["sources"]}, want)
            ok = j["ok"]
            rows.append({"id": cid, "area": "H", "title": q, "risk": "high", "dims": ["research"], "status": "pass" if ok else "fail",
                         "observed": f"answer in a confirmed cited sentence {j['confirmed_hits'] > 0} (anywhere in the report: "
                                     f"{j['answer_anywhere']}); cited {st.get('cited')} supported {st.get('supported')} "
                                     f"(share {j['supported_share']}); sources {len(spec['sources'])} (fabricated {len(j['fabricated'])}); "
                                     f"pages read {cnt['pages_read']} failed {cnt['pages_failed']}; {out['seconds']} s; "
                                     f"{'; '.join(j['reasons']) + '; ' if j['reasons'] else ''}notes {out['notes'][:2]}",
                         "cited": st.get("cited"), "supported": st.get("supported"), "supported_share": j["supported_share"],
                         "answer_anywhere": j["answer_anywhere"], "recited": st.get("recited", 0), "seconds": out["seconds"]})
            print(("PASS " if ok else "FAIL ") + cid, rows[-1]["observed"][:220], flush=True)
    finally:
        srv.shutdown()
        k.db.close()
        tmp.cleanup()
    cited = sum(r.get("cited") or 0 for r in rows)
    sup = sum(r.get("supported") or 0 for r in rows)
    rec = sum(r.get("recited") or 0 for r in rows)
    summary = {"pass": sum(r["status"] == "pass" for r in rows), "n": len(rows), "cited": cited, "supported": sup,
               "supported_share": round(sup / cited, 3) if cited else None, "recited": rec,
               "supported_share_without_repair": round((sup - rec) / cited, 3) if cited else None}
    print(json.dumps(summary))
    out = HERE.parent / "results" / f"live-research-dahl-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "summary": summary, "cases": rows}, indent=1), encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    main()
