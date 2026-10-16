"""Build a PDF report from a checked spec: HTML/CSS template, printed by the installed Microsoft Edge through Playwright.

The text is untrusted (a model or the web wrote it): HTML in it is escaped, only http(s) links survive, the page runs with
JavaScript off and every network request blocked, and charts are drawn locally with matplotlib as SVG (text stays
searchable). Edge writes a tagged PDF with an outline (bookmarks) and page numbers in the footer.
"""
from __future__ import annotations

import datetime as _dt
import io
import re

import jinja2
from markdown_it import MarkdownIt
from markupsafe import Markup, escape

from .spec import CITE
from .themes import Theme, choose_theme

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": True})
KIND_LABEL = {"fact": "Verified", "opinion": "Opinion", "uncertain": "Uncertain", "recommendation": "Recommendation"}

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{{ s.title }}</title>
<style>
  :root { --bg:#{{ t.bg }}; --surface:#{{ t.surface }}; --text:#{{ t.text }}; --muted:#{{ t.muted }};
          --accent:#{{ t.accent }}; --accent2:#{{ t.accent2 }}; }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; }
  body { font-family: "{{ t.body_font }}", "Segoe UI", "Nirmala UI", Arial, sans-serif; font-size: 10.5pt; line-height: 1.55;
         color: #1F2937; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  h1, h2, h3 { font-family: "{{ t.heading_font }}", "Segoe UI", Georgia, serif; color: #111827; line-height: 1.25; }
  .cover { height: 250mm; display: flex; flex-direction: column; justify-content: flex-end; padding: 18mm 14mm;
           background: linear-gradient(150deg, #{{ t.bg }} 0%, #{{ t.surface }} 100%); color: #{{ t.text }};
           border-radius: 6mm; position: relative; overflow: hidden; page-break-after: always; }
  .cover .orb1 { position:absolute; width:120mm; height:120mm; border-radius:50%; right:-35mm; top:-30mm; background:#{{ t.accent }}; opacity:.22; }
  .cover .orb2 { position:absolute; width:80mm; height:80mm; border-radius:50%; right:20mm; top:70mm; background:#{{ t.accent2 }}; opacity:.18; }
  .cover .bar { width: 22mm; height: 2.2mm; background: #{{ t.accent }}; margin-bottom: 8mm; }
  .cover h1 { font-size: 34pt; margin: 0 0 5mm 0; color: #{{ t.text }}; max-width: 150mm; }
  .cover .sub { font-size: 15pt; color: #{{ t.muted }}; margin: 0 0 14mm 0; max-width: 150mm; }
  .cover .meta { font-size: 10pt; color: #{{ t.muted }}; }
  .toc { page-break-after: always; }
  .toc h2 { border: none; }
  .toc ol { padding-left: 6mm; } .toc li { margin: 2.5mm 0; } .toc a { color: #111827; text-decoration: none; }
  .summary { background: #F8FAFC; border-left: 1.4mm solid #{{ t.accent }}; padding: 5mm 6mm; margin: 0 0 8mm 0; border-radius: 2mm; }
  .summary h2 { margin-top: 0; border: none; }
  h2 { font-size: 17pt; margin: 9mm 0 3mm 0; padding-bottom: 1.5mm; border-bottom: 0.4mm solid #E5E7EB; break-after: avoid; }
  h3 { font-size: 13pt; margin: 6mm 0 2mm 0; break-after: avoid; }
  p { margin: 0 0 3mm 0; text-align: left; orphans: 3; widows: 3; }
  ul { margin: 0 0 3mm 0; padding-left: 6mm; } li { margin: 1mm 0; }
  ul ul { margin: 0; }
  .badge { display:inline-block; font-size: 7.5pt; font-weight: 600; letter-spacing: .04em; text-transform: uppercase;
           padding: .5mm 2mm; border-radius: 3mm; margin-left: 2mm; vertical-align: middle; font-family: "Segoe UI", sans-serif; }
  .k-fact { background:#DCFCE7; color:#166534; } .k-opinion { background:#E0E7FF; color:#3730A3; }
  .k-uncertain { background:#FEF3C7; color:#92400E; } .k-recommendation { background:#FCE7F3; color:#9D174D; }
  .callout { background:#FFFBEB; border:0.3mm solid #FDE68A; border-radius:2mm; padding:3mm 4mm; margin: 3mm 0 4mm 0; break-inside: avoid; }
  table { width:100%; border-collapse: collapse; margin: 3mm 0 1mm 0; font-size: 9.5pt; break-inside: auto; }
  thead { display: table-header-group; }
  th { background:#{{ t.accent }}; color:#fff; text-align:left; padding: 2mm 2.5mm; font-weight:600; }
  td { padding: 1.8mm 2.5mm; border-bottom: 0.2mm solid #E5E7EB; vertical-align: top; }
  tr:nth-child(even) td { background: #F9FAFB; }
  tr { break-inside: avoid; }
  figure { margin: 4mm 0 5mm 0; break-inside: avoid; } figure svg { width: 100%; height: auto; }
  figcaption, .tcap { font-size: 8.8pt; color: #6B7280; margin-top: 1.5mm; font-style: italic; }
  sup.cite { font-size: 7pt; line-height: 0; } sup.cite a { color: #{{ t.accent }}; text-decoration: none; }
  a { color: #{{ t.accent2 }}; }
  .refs { page-break-before: always; } .refs ol { padding-left: 7mm; font-size: 9.2pt; } .refs li { margin: 2mm 0; word-break: break-word; }
  .refs .meta { color:#6B7280; }
</style></head>
<body>
<section class="cover">
  <div class="orb1"></div><div class="orb2"></div>
  <div class="bar"></div>
  <h1>{{ s.title }}</h1>
  {% if s.subtitle %}<p class="sub">{{ s.subtitle }}</p>{% endif %}
  <p class="meta">{{ meta }}</p>
</section>
<nav class="toc"><h2>Contents</h2><ol>
  {% if s.summary %}<li><a href="#summary">Summary</a></li>{% endif %}
  {% for sec in sections if sec.level == 2 %}<li><a href="#{{ sec.anchor }}">{{ sec.heading }}</a></li>{% endfor %}
  {% if s.sources %}<li><a href="#references">References</a></li>{% endif %}
</ol></nav>
{% if s.summary %}<section class="summary" id="summary"><h2>Summary</h2>{{ summary }}</section>{% endif %}
{% for sec in sections %}
<section>
  {% if sec.level == 2 %}<h2 id="{{ sec.anchor }}">{{ sec.heading }}{% if sec.kind %}<span class="badge k-{{ sec.kind }}">{{ sec.kind_label }}</span>{% endif %}</h2>
  {% else %}<h3 id="{{ sec.anchor }}">{{ sec.heading }}{% if sec.kind %}<span class="badge k-{{ sec.kind }}">{{ sec.kind_label }}</span>{% endif %}</h3>{% endif %}
  {% for p in sec.paragraphs %}<p>{{ p }}</p>{% endfor %}
  {% if sec.callout %}<div class="callout">{{ sec.callout }}</div>{% endif %}
  {% if sec.chart %}<figure>{{ sec.chart.svg }}{% if sec.chart.caption %}<figcaption>Figure {{ sec.chart.n }}. {{ sec.chart.caption }}</figcaption>{% endif %}</figure>{% endif %}
  {% if sec.table %}<table><thead><tr>{% for c in sec.table.columns %}<th>{{ c }}</th>{% endfor %}</tr></thead><tbody>
    {% for r in sec.table.rows %}<tr>{% for c in r %}<td>{{ c }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>
    {% if sec.table.caption %}<p class="tcap">Table {{ sec.table.n }}. {{ sec.table.caption }}</p>{% endif %}{% endif %}
  {% if sec.bullets %}{{ sec.bullets }}{% endif %}
</section>
{% endfor %}
{% if s.sources %}<section class="refs" id="references"><h2>References</h2><ol>
  {% for x in s.sources %}<li id="src-{{ x.id }}">{{ x.title }}{% if x.publisher or x.date %} <span class="meta">— {{ x.publisher }}{% if x.publisher and x.date %}, {% endif %}{{ x.date }}</span>{% endif %}
    {% if x.url %}<br><a href="{{ x.url }}">{{ x.url }}</a>{% endif %}</li>{% endfor %}
</ol></section>{% endif %}
</body></html>"""

FOOTER = ('<div style="width:100%;font-size:7.5pt;color:#6B7280;padding:0 16mm;display:flex;justify-content:space-between;'
          'font-family:Segoe UI,Arial,sans-serif;"><span>{title}</span><span><span class="pageNumber"></span> / '
          '<span class="totalPages"></span></span></div>')


def _inline(text: str, src_num: dict) -> Markup:
    html = _MD.renderInline(text or "")                      # escapes any HTML in the text; blocks javascript: links
    html = re.sub(r'href="(?!https?://|#)[^"]*"', 'href="#"', html)
    return Markup(CITE.sub(lambda m: f'<sup class="cite"><a href="#src-{m.group(1)}">[{src_num.get(m.group(1), "?")}]</a></sup>', html))


def _bullets(items: list[dict], src_num: dict) -> Markup:
    out, depth = [], -1
    for b in items:
        while depth < b["level"]:
            out.append("<ul>")
            depth += 1
        while depth > b["level"]:
            out.append("</ul>")
            depth -= 1
        out.append(f"<li>{_inline(b['text'], src_num)}</li>")
    out += ["</ul>"] * (depth + 1)
    return Markup("".join(out))


def chart_svg(c: dict, t: Theme) -> Markup:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "svg.fonttype": "none", "font.size": 9})
    fig, ax = plt.subplots(figsize=(6.6, 3.1))
    colors = ["#" + x for x in t.chart]
    labels = c["labels"]
    if c["type"] == "pie":
        ax.pie(c["series"][0]["values"], labels=labels, colors=colors[: len(labels)], autopct="%1.0f%%", startangle=90,
               wedgeprops={"linewidth": 1, "edgecolor": "white"}, textprops={"fontsize": 8.5})
        ax.axis("equal")
    else:
        n = len(c["series"])
        width = 0.8 / n
        for i, s in enumerate(c["series"]):
            xs = [k + (i - (n - 1) / 2) * width for k in range(len(labels))]
            if c["type"] == "line":
                ax.plot(range(len(labels)), s["values"], marker="o", linewidth=2.2, color=colors[i % len(colors)], label=s["name"])
            elif c["type"] == "bar":
                ax.barh(xs, s["values"], height=width, color=colors[i % len(colors)], label=s["name"])
            else:
                ax.bar(xs, s["values"], width=width, color=colors[i % len(colors)], label=s["name"])
        if c["type"] == "bar":
            ax.set_yticks(range(len(labels)), labels)
            ax.invert_yaxis()
        else:
            ax.set_xticks(range(len(labels)), labels, rotation=0 if max(len(x) for x in labels) < 12 else 25, ha="right" if max(len(x) for x in labels) >= 12 else "center")
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.grid(axis="x" if c["type"] == "bar" else "y", color="#E5E7EB", linewidth=0.8)
        ax.set_axisbelow(True)
        if c["unit"]:
            (ax.set_xlabel if c["type"] == "bar" else ax.set_ylabel)(c["unit"])
        if n > 1:
            ax.legend(frameon=False, fontsize=8.5)
    if c["title"]:
        ax.set_title(c["title"], fontsize=10.5, fontweight="bold", loc="left")
    fig.tight_layout()
    buf = io.StringIO()
    fig.savefig(buf, format="svg")
    plt.close(fig)
    svg = buf.getvalue()
    return Markup(svg[svg.find("<svg"):])


def render_html(spec: dict) -> tuple[str, Theme]:
    t = choose_theme(" ".join([spec["title"], spec.get("subtitle", ""), spec.get("topic", "")]), spec.get("theme"))
    src_num = {s["id"]: i + 1 for i, s in enumerate(spec["sources"])}
    sections, tn, fn = [], 0, 0
    for i, se in enumerate(spec["sections"]):
        sec = {"heading": se["heading"], "level": se["level"], "anchor": f"sec-{i + 1}", "kind": se["kind"],
               "kind_label": KIND_LABEL.get(se["kind"], ""), "paragraphs": [_inline(p, src_num) for p in se["paragraphs"]],
               "bullets": _bullets(se["bullets"], src_num) if se["bullets"] else "",
               "callout": _inline(se["callout"], src_num) if se["callout"] else "", "table": None, "chart": None}
        if se["table"]:
            tn += 1
            sec["table"] = {"columns": se["table"]["columns"], "rows": [[_inline(c, src_num) for c in r] for r in se["table"]["rows"]],
                            "caption": se["table"]["caption"], "n": tn}
        if se["chart"]:
            fn += 1
            sec["chart"] = {"svg": chart_svg(se["chart"], t), "caption": se["chart"]["caption"], "n": fn}
        sections.append(sec)
    meta = "  ·  ".join(x for x in (spec["author"], _dt.date.today().strftime("%d %B %Y")) if x)
    env = jinja2.Environment(autoescape=True)
    html = env.from_string(TEMPLATE).render(s=spec, t=t, sections=sections, meta=meta,
                                            summary=Markup("".join(f"<p>{_inline(p, src_num)}</p>" for p in spec["summary"].split("\n\n") if p.strip())))
    return html, t


def print_pdf(html: str, path: str, title: str) -> None:
    from playwright.sync_api import sync_playwright

    from .tools import ToolError
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception as e:  # noqa: BLE001 - Playwright's own text suggests an install command; say what is missing
            raise ToolError(f"Microsoft Edge could not be started to print the PDF ({type(e).__name__}); "
                            "it is needed for reports") from None
        try:
            ctx = browser.new_context(java_script_enabled=False)
            ctx.route("**/*", lambda route: route.continue_() if route.request.url.startswith(("data:", "about:")) else route.abort())
            page = ctx.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(path=path, format="A4", print_background=True, display_header_footer=True,
                     header_template="<span></span>", footer_template=FOOTER.format(title=escape(title[:90])),
                     margin={"top": "16mm", "bottom": "18mm", "left": "16mm", "right": "16mm"}, tagged=True, outline=True)
        finally:
            browser.close()


def build(spec: dict, pdf_path: str, html_path: str | None = None) -> dict:
    html, t = render_html(spec)
    if html_path:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
    print_pdf(html, pdf_path, spec["title"])
    return {"theme": t.name, "sections": len(spec["sections"]), "sources": len(spec["sources"])}
