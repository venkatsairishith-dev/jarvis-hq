"""Deliverables logic without LibreOffice or OfficeCLI: spec checks, theme choice, text fitting, deck build, HTML safety,
and the rule that only made files can be opened. The full pipeline is run by run_demo.py and live_check.py."""
import json
import zipfile
from pathlib import Path

import pytest

from plugin.plugins.deliverables import animate, deck, report, spec, themes, tools

FIX = Path(__file__).parent / "fixtures"


def demo(name):
    return json.loads((FIX / f"demo_{name}.json").read_text(encoding="utf-8"))


def test_demo_specs_are_valid():
    d = spec.deck(demo("deck"))
    r = spec.report(demo("report"))
    assert len(d["slides"]) == 10 and len(d["sources"]) == 5
    assert len(r["sections"]) == 4


@pytest.mark.parametrize("bad,msg", [
    ({"title": "x", "slides": []}, "at least one slide"),
    ({"title": "x", "slides": [{"layout": "nope", "title": "a"}]}, "layout must be"),
    ({"title": "x", "slides": [{"title": "a", "bullets": ["see [S9]"]}]}, "not listed"),
    ({"title": "x", "sources": [{"id": "S1", "title": "t", "url": "javascript:alert(1)"}], "slides": [{"title": "a"}]}, "http"),
    ({"title": "x", "slides": [{"layout": "chart", "title": "c", "chart": {"labels": ["a"], "series": [{"values": [1, 2]}]}}]}, "values for"),
    ({"title": "x" * 500, "slides": [{"title": "a"}]}, "too long"),
])
def test_bad_specs_are_refused(bad, msg):
    with pytest.raises(spec.SpecError, match=msg):
        spec.deck(bad)


def test_theme_choice():
    assert themes.choose_theme("Cloud security for engineers").name == "midnight"
    assert themes.choose_theme("Annual business report").name == "paper"
    assert themes.choose_theme("Kids music workshop").name == "vivid"
    assert themes.choose_theme("something", "vivid").name == "vivid"
    assert themes.choose_theme("plain words").name == themes.DEFAULT_THEME


def test_fit_size_shrinks_long_text_and_reports_overflow():
    short, ok = deck.fit_size([("Short line", 0)], "Segoe UI", 10, 2, 24, 14)
    assert short == 24 and ok
    long_paras = [("word " * 80, 0)] * 6
    size, ok = deck.fit_size(long_paras, "Segoe UI", 10, 3, 24, 14)
    assert size == 14 and not ok


def test_text_is_still_measured_to_scale_when_the_font_file_is_missing(monkeypatch):
    monkeypatch.setattr(deck, "font_path", lambda family, bold=False: None)    # no theme font, no Segoe UI either
    w12, w24 = deck._width_pt("word " * 10, "Segoe UI", 12), deck._width_pt("word " * 10, "Segoe UI", 24)
    assert w12 > 50 and 1.8 < w24 / w12 < 2.2                              # was ~25 pt at every size
    size, ok = deck.fit_size([("word " * 80, 0)] * 6, "Segoe UI", 10, 3, 24, 14)
    assert size == 14 and not ok                                            # overflow is still reported


def test_deck_build_without_external_tools(tmp_path):
    s = spec.deck(demo("deck"))
    out = tmp_path / "d.pptx"
    info = deck.build(s, str(out))
    assert info["slides"] == 11                               # 10 + one Sources slide before the closing slide
    assert all(f["fits"] for f in info["fit"])
    with zipfile.ZipFile(out) as z:
        charts = [n for n in z.namelist() if n.startswith("ppt/charts/chart")]
        xml = z.read(charts[0]).decode()
    import re
    ids = [int(v) for v in re.findall(r'<c:(?:axId|crossAx) val="(-?\d+)"', xml)]
    assert ids and all(0 <= v <= 0x7FFFFFFF for v in ids)     # schema-valid axis ids
    assert not any(a["kind"] == "table" for a in info["animations"])


def test_animation_commands_shape():
    cmds = animate.commands(3, "push", [{"slide": 2, "kind": "shape", "id": 5, "effect": "fade"},
                                        {"slide": 2, "kind": "chart", "id": 7, "effect": "wipe"}], {1})
    assert cmds[0]["props"]["transition"] == "fade" and cmds[1]["props"]["transition"] == "push"
    adds = [c for c in cmds if c["command"] == "add"]
    assert adds[0]["parent"] == "/slide[2]/shape[@id=5]" and adds[1]["parent"] == "/slide[2]/chart[@id=7]"
    assert adds[0]["props"]["delay"] == "250" and adds[1]["props"]["delay"] == "120"


def test_report_html_escapes_untrusted_text():
    s = spec.report({"title": "<script>alert(1)</script>", "sources": [], "sections": [
        {"heading": "H <img src=x onerror=alert(1)>", "paragraphs": ["[click](javascript:alert(1)) and <b>raw</b>"]}]})
    html, _ = report.render_html(s)
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html
    assert 'href="javascript' not in html                    # left as plain text, never a link
    assert "&lt;b&gt;raw&lt;/b&gt;" in html


def test_report_citations_link_to_references():
    html, _ = report.render_html(spec.report(demo("report")))
    assert 'href="#src-S1"' in html and 'id="src-S1"' in html


def test_only_made_files_can_be_opened(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "OUT_ROOT", tmp_path)
    made = tmp_path / "job" / "a.pdf"
    made.parent.mkdir()
    made.write_bytes(b"%PDF")
    assert tools.inside_outputs(str(made)) == made.resolve()
    outside = tmp_path.parent / "elsewhere.pdf"
    outside.write_bytes(b"%PDF")
    with pytest.raises(tools.ToolError):
        tools.inside_outputs(str(outside))
    with pytest.raises(tools.ToolError):
        tools.inside_outputs(str(tmp_path / "job" / "missing.pdf"))
    exe = tmp_path / "job" / "x.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(tools.ToolError):
        tools.inside_outputs(str(exe))


def test_new_job_dir_never_reuses(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "OUT_ROOT", tmp_path)
    a, b = tools.new_job_dir("Same Title"), tools.new_job_dir("Same Title")
    assert a != b and a.is_dir() and b.is_dir()


def test_a_stats_slide_with_more_than_four_items_is_refused_not_cut():
    s = {"title": "x", "slides": [{"layout": "stats", "title": "s", "items": [{"value": str(i), "label": f"l{i}"} for i in range(6)]}]}
    with pytest.raises(spec.SpecError, match="at most 4"):
        spec.deck(s)
    four = {"title": "x", "slides": [{"layout": "stats", "title": "s", "items": [{"value": str(i), "label": f"l{i}"} for i in range(4)]}]}
    assert len(spec.deck(four)["slides"][0]["items"]) == 4


def test_missing_edge_is_reported_plainly(monkeypatch):
    import playwright.sync_api as psa

    class NoEdge:
        def __enter__(self):
            class C:
                def launch(self, **kw):
                    raise RuntimeError("Chromium distribution 'msedge' is not found. Run \"npx playwright install msedge\"")
            return type("P", (), {"chromium": C()})()

        def __exit__(self, *e):
            return False
    monkeypatch.setattr(psa, "sync_playwright", NoEdge)
    with pytest.raises(tools.ToolError, match="Microsoft Edge could not be started") as e:
        report.print_pdf("<p>x</p>", "x.pdf", "t")
    assert "npx" not in str(e.value)
