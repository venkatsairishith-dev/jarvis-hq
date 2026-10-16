"""Neko 9.5, C8 research and documents, live through the REAL plugins (2026-10-16). Windows, neko-venv; plugin runtime up.
Public questions only. Two runs: a PDF report, and a deck opened through run_pc_task (research -> deck -> opened window).
    python jarvis\\tests\\neko_research_live.py
R-citations-verified      the claim checker ran; every kept cited sentence was supported by its cited passages; and an
                          independent lexical check: each kept cited sentence shares content words with its cited passages
R-no-fabricated-sources   every [n] in the text is a listed source; every listed source was really fetched (has passages)
R-pdf-open-searchable     the report PDF passed its QA (opens, text layer with headings, bookmarks, source links, fonts
                          embedded, tagged, nothing off the page), re-checked here with pypdf
R-deck-render-qa          the deck passed its QA (schema valid, text fits, rendered page count, nothing off the slide)
R-e2e-research-to-deck-open  one spoken-style request -> research -> deck -> a window showing it appeared (verified)
Results: jarvis/evals/neko95/results/research-<time>.json
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
from live_check import run_entry  # noqa: E402

Q_REPORT = "How do heat pumps work and how efficient are they compared with gas boilers?"
Q_DECK = "research how lithium iron phosphate batteries differ from NMC batteries, make slides and open them"
STOP = set("the a an and or of to in on for with is are was were be by as at from that this it its their than more most "
           "can may also which such into over per about between not".split())
CLOSE = r"""
Add-Type -AssemblyName UIAutomationClient
$AE=[System.Windows.Automation.AutomationElement]; $n=0
foreach ($w in $AE::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
  if ($env:STEM -and $w.Current.Name.ToLower().Contains($env:STEM)) { $wp=$null
    if ($w.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern, [ref]$wp)) { $wp.Close(); $n++ } } }
$n
"""


def words(t):
    return {w for w in re.findall(r"[a-z0-9%]+", t.lower()) if w not in STOP and len(w) > 2}


def row(rows, cid, ok, detail):
    rows.append({"cap": "C8_research_documents", "id": cid, "status": "pass" if ok else "fail", "detail": str(detail)[:400]})
    print(("PASS " if ok else "FAIL ") + cid, str(detail)[:300], flush=True)


def main():
    rows = []
    t0 = time.time()
    r = run_entry("research", "research_topic", {"question": Q_REPORT, "deliverable": "report"}, wait=900)
    data = (r or {}).get("data") or {}
    rec_file = data.get("record_file")
    if not rec_file or not Path(rec_file).exists():
        for cid in ("R-citations-verified", "R-no-fabricated-sources", "R-pdf-open-searchable"):
            row(rows, cid, False, f"research run failed: {str(r)[:200]}")
    else:
        rec = json.loads(Path(rec_file).read_text(encoding="utf-8"))
        spec, stats = rec.get("spec") or {}, rec.get("stats") or {}
        passages = {}
        for p in rec.get("passages") or []:
            passages.setdefault(p["sid"], []).append(p["text"])
        listed = {str(s["id"]): s for s in spec.get("sources") or []}
        text_parts = [spec.get("summary", "")] + [x for s in spec.get("sections") or [] if s.get("kind") != "uncertain"
                                                  for x in (s.get("paragraphs") or []) + (s.get("bullets") or [])]
        cited = []
        for part in text_parts:
            for sent in re.split(r"(?<=[.!?\]])\s+", part):
                ids = re.findall(r"\[(\w+)\]", sent)
                if ids:
                    cited.append((re.sub(r"\s*\[\w+\]", "", sent), ids))
        unknown = sorted({i for _, ids in cited for i in ids if i not in listed})
        unfetched = sorted(i for i in listed if i not in passages)
        row(rows, "R-no-fabricated-sources", not unknown and not unfetched and listed,
            f"{len(listed)} sources listed, cited ids not listed: {unknown}, listed but never fetched: {unfetched}")
        weak = [s for s, ids in cited if len(words(s) & set().union(*(words(" ".join(passages.get(i, []))) for i in ids))) < 3]
        supported = stats.get("supported", 0)
        ok = bool(stats.get("checked")) and cited and not weak and stats.get("unsupported", 0) <= stats.get("cited", 0) * 0.5
        row(rows, "R-citations-verified", ok,
            f"checker ran: {stats.get('checked')}; {len(cited)} kept cited sentences; checker supported {supported} of "
            f"{stats.get('cited')} cited (unsupported ones moved to the unconfirmed section: {stats.get('unsupported')}); "
            f"kept sentences sharing < 3 content words with their sources: {len(weak)}")
        qa = data.get("qa") or {}
        pdf = data.get("file")
        indep = False
        if pdf and Path(pdf).exists():
            from pypdf import PdfReader
            rd = PdfReader(pdf)
            txt = " ".join((p.extract_text() or "") for p in rd.pages)
            indep = len(rd.pages) > 0 and spec.get("title", "")[:30].lower() in re.sub(r"\s+", " ", txt).lower() and bool(rd.outline)
        row(rows, "R-pdf-open-searchable", bool(data.get("qa_passed")) and indep,
            f"plugin QA passed: {data.get('qa_passed')}; independent pypdf check (pages, title in text layer, bookmarks): {indep}; {pdf}")
    # the deck, end to end through the coordinator (research -> deck -> opened window)
    r2 = run_entry("desktop", "run_pc_task", {"request": Q_DECK}, wait=1200)
    d2 = (r2 or {}).get("data") or {}
    steps = d2.get("steps") or []
    res = next((s for s in steps if s.get("tool") == "research"), {})
    checked = str(res.get("checked") or "")
    row(rows, "R-e2e-research-to-deck-open", d2.get("status") == "completed" and res.get("passed") and "opened" in checked,
        f"status {d2.get('status')}; {checked}; {d2.get('summary', '')[:160]}")
    m = re.search(r"presentation (.+?\.pptx)", checked)
    deck = m.group(1) if m else None
    qa_ok = None
    if deck:
        qa_file = Path(deck).parent / "qa.json"
        try:
            qa_ok = (json.loads(qa_file.read_text(encoding="utf-8")).get("qa") or {}).get("passed") if qa_file.exists() else None
        except ValueError:
            qa_ok = None
    row(rows, "R-deck-render-qa", res.get("passed") and "failed its quality checks" not in checked and qa_ok is not False,
        f"research step passed (it fails when the deck's QA fails): {res.get('passed')}; qa.json passed: {qa_ok}; {deck}")
    if deck:
        import os
        subprocess.run(["powershell", "-NoProfile", "-Command", CLOSE], capture_output=True, text=True,
                       env={**os.environ, "STEM": Path(deck).stem.lower()[:25]})
    out = HERE.parent / "evals" / "neko95" / "results" / f"research-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.time() - t0), "rows": rows}, indent=1),
                   encoding="utf-8")
    print("saved", out)
    return 0 if all(r["status"] == "pass" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
