# Deliverables

Polished PowerPoint decks and PDF reports from a structured outline, checked before they are handed over. Windows.

| Entry | What it does |
|---|---|
| `make_presentation` | python-pptx deck: themed layouts (title, section, bullets, two columns, native chart, table, quote, timeline, stats, closing), speaker notes, source footnotes and a Sources slide. OfficeCLI then adds slide transitions and entrance animations and validates the file against the OpenXML schema. LibreOffice renders it for checks. |
| `make_report` | HTML/CSS template printed by Microsoft Edge: cover, contents, summary, sections labelled fact / opinion / uncertain / recommendation, tables, charts (matplotlib SVG, searchable text), numbered references with links, page numbers, bookmarks, tagged PDF. |
| `open_output`, `show_in_folder`, `start_slideshow`, `regenerate`, `list_deliverables` | Act only on files this plugin made under `D:\jarvis\outputs\neko\deliverables`. |

**Checks recorded in `qa.json` for each file:**

| Deliverable | Checks |
|---|---|
| Deck | reopens, slide count, speaker notes, text fits its box (measured with the real font), transitions and animations present, schema-valid, renders, rendered page count, no text outside the slide. Page PNGs and a contact sheet go in `previews/` for a visual look. |
| Report | opens, searchable text with every heading, bookmarks, every source URL is a live link, fonts embedded, tagged, no text outside the page. Page PNGs and a contact sheet. A PDF the parsers cannot read (cut short, damaged) is "not passed" with the reason, never a raw parser error. |

**Themes:** midnight (technology, security), paper (reports, business) and vivid (learning, creative). One is chosen from the topic unless `theme` is set.

**Specs are untrusted** (a model or the research plugin writes them), so `spec.py` checks them before anything is built:
- A wrongly typed field (layout, chart type, section kind, bullet level, a chart series that is not an object) gives a
  `SpecError` that names the field, not a crash.
- Chart values must be finite numbers: `nan`, `inf` and `1e309` are refused.
- Control characters other than tab and newline are replaced by a space, so slides never show codes like `_x000B_`.
- Links must be http(s), and every `[S#]` marker must name a listed source.

**The report page loads nothing remote.** Edge prints it with JavaScript off and every network request blocked. HTML in
the text is escaped, and a markdown image becomes a plain link, so the `report.html` kept next to the PDF does not load
from any address when it is opened.

**Each job gets its own folder** under the outputs folder. The folder is created atomically, so two deliverables started
at the same moment with the same title never share one.

**Limits, stated plainly:**
- Checks render with LibreOffice, not PowerPoint.
- Some effects look simpler in LibreOffice; morph plays only in PowerPoint 2019/365.
- A static render cannot prove that animations play, so watch one run.
- Tables are not animated, because OfficeCLI animates shapes and charts only.
- Telugu/Hindi text uses Windows fonts (Nirmala UI) and has not been checked yet.

**Tools:**
- OfficeCLI v1.0.152 (Apache-2.0, SHA256SUMS verified, auto-update off) in `D:\jarvis\tools\officecli`.
- LibreOffice 26.2.6 (MPL-2.0, sha256 and GPG verified, administrative install, no system registration) in `D:\jarvis\tools\libreoffice`.
- python-pptx 1.0.2 and pdfplumber 0.11.10 in `neko-venv`.

**Tests:**
- `pytest plugin/plugins/deliverables/tests`: no external tools needed.
- `python -m plugin.plugins.deliverables.tests.run_demo`: the full pipeline.
