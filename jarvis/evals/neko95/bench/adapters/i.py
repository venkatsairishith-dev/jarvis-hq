"""I: documents and presentations, against plugin/plugins/deliverables (spec checks, python-pptx deck build, HTML report
template, QA checks, output-path rules). Report PDFs: the laptop prints with Microsoft Edge; here the SAME HTML is printed
with headless Google Chrome (the same Chromium engine) as a stand-in, and the Edge print itself is a deferred case."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
FIX = ROOT / "plugin/plugins/deliverables/tests/fixtures"
os.environ.setdefault("DELIVERABLES_OUT_DIR", tempfile.mkdtemp(prefix="neko-deliverables-"))
from plugin.plugins.deliverables import deck, qa, report, spec, tools  # noqa: E402


def _demo(name):
    return json.loads((FIX / f"demo_{name}.json").read_text(encoding="utf-8"))


def spec_ok(a):
    d, r = spec.deck(_demo("deck")), spec.report(_demo("report"))
    return len(d["slides"]) == 10 and len(r["sections"]) == 4, f"{len(d['slides'])} slides, {len(r['sections'])} sections"


def spec_refused(a):
    try:
        (spec.report if a.get("kind") == "report" else spec.deck)(a["spec"])
    except spec.SpecError as e:
        return a["msg"].lower() in str(e).lower(), f"refused: {e}"
    return False, "accepted"


def deck_builds(a):
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "d.pptx"
        t0 = time.perf_counter()
        info = deck.build(spec.deck(_demo("deck")), str(out))
        secs = time.perf_counter() - t0
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            bad = z.testzip()
        charts = [n for n in names if n.startswith("ppt/charts/chart")]
        ok = info["slides"] == 11 and all(f["fits"] for f in info["fit"]) and bad is None and charts and secs < a.get("max_s", 10)
        return ok, f"{info['slides']} slides, all fit {all(f['fits'] for f in info['fit'])}, charts {len(charts)}, zip ok {bad is None}, {secs:.2f} s"


def deck_links_and_notes(a):
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "d.pptx"
        deck.build(spec.deck(_demo("deck")), str(out))
        with zipfile.ZipFile(out) as z:
            rels = " ".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".rels"))
            notes = [n for n in z.namelist() if n.startswith("ppt/notesSlides/")]
        urls = [s["url"] for s in _demo("deck").get("sources", [])]
        linked = [u for u in urls if u.replace("&", "&amp;") in rels]
        return len(linked) == len(urls) and bool(notes), f"{len(linked)}/{len(urls)} source links clickable; {len(notes)} notes parts"


def overflow_reported(a):
    s = copy.deepcopy(_demo("deck"))
    s["slides"] = [{"layout": "bullets", "title": "Too much", "bullets": [("word " * 60).strip() for _ in range(a.get("n", 12))]}]
    try:
        sp = spec.deck(s)
    except spec.SpecError as e:
        return True, f"refused before building: {e}"
    with tempfile.TemporaryDirectory() as t:
        info = deck.build(sp, str(Path(t) / "o.pptx"))
    unfit = [f for f in info["fit"] if not f["fits"]]
    return bool(unfit), f"{len(unfit)} text boxes reported as not fitting (of {len(info['fit'])})"


def html_escapes(a):
    s = copy.deepcopy(_demo("report"))
    s["sections"][0]["paragraphs"] = ["<script>alert(1)</script><img src=x onerror=alert(2)> plain"]
    html, _t = report.render_html(spec.report(s))
    return "<script>alert(1)" not in html and "onerror=alert" not in html.replace("&lt;", "<").replace("onerror=alert(2)&gt;", "") \
        and "&lt;script&gt;" in html, "script and handler escaped"


def html_citations_link(a):
    html, _t = report.render_html(spec.report(_demo("report")))
    return 'href="#src-1"' in html or "#src" in html or "href=\"#S1" in html, "citation markers link to the source list"


def _print_pdf(html: str, path: Path):
    """report.print_pdf exactly as the laptop runs it (JS off, network blocked, tagged PDF, bookmarks, footer), with only
    the browser channel swapped: msedge -> chrome."""
    import playwright.sync_api as PSA
    real = PSA.sync_playwright

    class _Swap:
        def __init__(self):
            self._cm = real()

        def __enter__(self):
            pw = self._cm.__enter__()
            launch = pw.chromium.launch

            def chrome_launch(**kw):
                if kw.get("channel") == "msedge":
                    kw["channel"] = "chrome"
                return launch(**kw)
            pw.chromium.launch = chrome_launch
            return pw

        def __exit__(self, *e):
            return self._cm.__exit__(*e)
    PSA.sync_playwright = _Swap
    try:
        report.print_pdf(html, str(path), "Bench report")
    finally:
        PSA.sync_playwright = real


def report_pdf_qa(a):
    sp = spec.report(_demo("report"))
    html, _t = report.render_html(sp)
    with tempfile.TemporaryDirectory() as t:
        pdf = Path(t) / "r.pdf"
        _print_pdf(html, pdf)
        off = qa.words_off_page(pdf)
        res = qa.check_report(pdf, sp, Path(t))
        failed = [k for k, v in res["checks"].items() if not v]
        return not off and res.get("passed"), f"words off page {len(off)}; QA passed {res.get('passed')}; failed checks {failed}; warnings {res['warnings'][:2]}"


def outputs_only(a):
    root = Path(os.environ["DELIVERABLES_OUT_DIR"])
    tools.OUT_ROOT = root
    good = tools.new_job_dir("Quarterly Report: Q3/2026 <draft>")
    f = good / "x.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    bad = []
    for path in (str(root / ".." / "etc" / "passwd"), "/etc/hostname", str(good / "x.exe"), str(good / ".." / ".." / "x.pdf")):
        try:
            tools.inside_outputs(path)
            bad.append(path)
        except tools.ToolError:
            pass
    ok_open = tools.inside_outputs(str(f)) == f.resolve()
    return not bad and ok_open and good.parent == root, f"accepted outside paths {bad}; own file ok {ok_open}; folder {good.name}"


def unique_job_dirs(a):
    tools.OUT_ROOT = Path(os.environ["DELIVERABLES_OUT_DIR"])
    ds = [tools.new_job_dir("Same title") for _ in range(3)]
    return len({d for d in ds}) == 3, f"{[d.name for d in ds]}"


def missing_edge_honest(a):
    import plugin.plugins.deliverables.report as R
    try:
        R.print_pdf("<p>x</p>", os.path.join(tempfile.mkdtemp(), "x.pdf"), "x")
    except tools.ToolError as e:
        return "Edge" in str(e) and "install" not in str(e).lower(), f"ToolError: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"raw {type(e).__name__}: {str(e)[:120]}"
    return False, "printed (Edge present?)"


def missing_officecli_honest(a):
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "d.pptx"
        deck.build(spec.deck(_demo("deck")), str(out))
        old = tools.OFFICECLI
        tools.OFFICECLI = "/nonexistent/officecli.exe"
        try:
            from plugin.plugins.deliverables import animate
            animate.apply(out, 11, "fade", [], {1})
            return False, "animation claimed success without OfficeCLI"
        except tools.ToolError as e:
            return True, f"ToolError: {str(e)[:120]}"
        except Exception as e:  # noqa: BLE001
            return False, f"raw {type(e).__name__}: {str(e)[:120]}"
        finally:
            tools.OFFICECLI = old
