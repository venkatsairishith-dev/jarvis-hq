"""Make a presentation or a report end to end: check the spec, build, animate, check the result, record what happened.

Each deliverable gets its own new folder under the output root with the file, the spec it came from, qa.json and page
previews, so it can be inspected, regenerated or compared later. Nothing is overwritten.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import animate, deck, qa, report, spec as specmod, tools


def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def make_presentation(raw_spec: dict, open_after: bool = False, start_show: bool = False) -> dict:
    t0 = time.time()
    s = specmod.deck(raw_spec)
    work = tools.new_job_dir(s["title"])
    _save_json(work / "spec.json", s)
    path = work / f"{tools.slug(s['title'])}.pptx"
    info = deck.build(s, str(path))
    title_slides = {1} | {i + 1 for i, sl in enumerate(s["slides"]) if sl["layout"] in ("title", "section", "closing")}
    anim = animate.apply(path, info["slides"], info["transition"], info["animations"], title_slides) if s["animate"] else None
    check = qa.check_deck(path, info, s, work)
    result = {"file": str(path), "folder": str(work), "kind": "pptx", "theme": info["theme"], "slides": info["slides"],
              "animation": anim, "qa": check, "seconds": round(time.time() - t0, 1)}
    _save_json(work / "qa.json", result)
    if start_show:
        tools.start_slideshow(path)
    elif open_after:
        tools.open_file(path)
    return result


def make_report(raw_spec: dict, open_after: bool = False) -> dict:
    t0 = time.time()
    s = specmod.report(raw_spec)
    work = tools.new_job_dir(s["title"])
    _save_json(work / "spec.json", s)
    path = work / f"{tools.slug(s['title'])}.pdf"
    info = report.build(s, str(path), str(work / "report.html"))
    check = qa.check_report(path, s, work)
    result = {"file": str(path), "folder": str(work), "kind": "pdf", "theme": info["theme"], "qa": check,
              "seconds": round(time.time() - t0, 1)}
    _save_json(work / "qa.json", result)
    if open_after:
        tools.open_file(path)
    return result


def regenerate(folder: str, open_after: bool = False) -> dict:
    """Build the same deliverable again from the spec saved next to it (a new folder; the old one stays)."""
    d = Path(folder).resolve()
    d = d.parent if d.is_file() else d
    root = tools.OUT_ROOT.resolve()
    spec_file = d / "spec.json"
    if root not in d.parents or not spec_file.is_file():
        raise tools.ToolError("regenerate needs a deliverable folder made by this tool")
    data = json.loads(spec_file.read_text(encoding="utf-8"))
    return make_presentation(data, open_after) if "slides" in data else make_report(data, open_after)


def list_deliverables(limit: int = 20) -> list[dict]:
    if not tools.OUT_ROOT.is_dir():
        return []
    out = []
    for d in sorted((p for p in tools.OUT_ROOT.iterdir() if p.is_dir()), reverse=True)[:limit]:
        q = d / "qa.json"
        if q.is_file():
            try:
                r = json.loads(q.read_text(encoding="utf-8"))
                out.append({"file": r["file"], "kind": r["kind"], "passed": r["qa"]["passed"], "folder": str(d)})
            except (ValueError, KeyError):
                continue
    return out
