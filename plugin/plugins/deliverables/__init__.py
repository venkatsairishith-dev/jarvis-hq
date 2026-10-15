"""Deliverables: polished PowerPoint decks and PDF reports from a structured spec, checked before they are handed over.

Decks: python-pptx (native text, charts, tables, notes, source footnotes) + OfficeCLI transitions and entrance animations
+ LibreOffice render for checks and the slideshow. Reports: HTML/CSS printed by Microsoft Edge (tagged PDF, bookmarks,
links, page numbers). Logic in core.py; it only opens files it made itself.
"""

from __future__ import annotations

import asyncio

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr

from . import core, tools
from .spec import SpecError

_SLIDE_HELP = ("Slides: list of {layout, title, subtitle, bullets, notes, sources}. layout: title, section, bullets, "
               "two_column (left_title, left, right_title, right), chart (chart: {type: column|bar|line|pie, title, labels, "
               "series: [{name, values}], caption, unit}), table (table: {columns, rows, caption}), quote (quote, attribution), "
               "timeline (items: [{label, text}]), stats (items: [{label: '42%', text}]), closing. Cite with [S1] markers "
               "that match sources: [{id: 'S1', title, url, date, publisher}].")


def _err(exc: Exception):
    return Err(SdkError(str(exc)))


@neko_plugin
class DeliverablesPlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready", **tools.status()})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="make_presentation",
        name=tr("entry.make_presentation.name", default="Make a presentation"),
        description=tr("entry.make_presentation.description", default=(
            "Create a polished, animated PowerPoint deck (.pptx) from a structured outline, check it (text fit, schema, "
            "rendering) and optionally open it or start the slideshow. " + _SLIDE_HELP)),
        input_schema={"type": "object", "properties": {
            "spec": {"type": "object", "description": "title, subtitle, author, topic, theme (auto if empty: midnight, paper, vivid), animate, sources, slides."},
            "open_after": {"type": "boolean", "default": False},
            "start_show": {"type": "boolean", "default": False}},
            "required": ["spec"]},
        timeout=600,
        llm_result_fields=["file", "slides", "qa"],
    )
    async def make_presentation(self, spec: dict | None = None, open_after: bool = False, start_show: bool = False, **_):
        try:
            return Ok(await asyncio.to_thread(core.make_presentation, spec or {}, bool(open_after), bool(start_show)))
        except (SpecError, tools.ToolError) as exc:
            return _err(exc)

    @plugin_entry(
        id="make_report",
        name=tr("entry.make_report.name", default="Make a PDF report"),
        description=tr("entry.make_report.description", default=(
            "Create a professional PDF report (cover, contents, sections, tables, charts, references, page numbers, "
            "clickable links), check it and optionally open it. Sections: [{heading, level 2|3, kind: fact|opinion|"
            "uncertain|recommendation, paragraphs, bullets, callout, table, chart}]. Cite with [S1] markers matching sources.")),
        input_schema={"type": "object", "properties": {
            "spec": {"type": "object", "description": "title, subtitle, author, topic, theme, summary, sources, sections."},
            "open_after": {"type": "boolean", "default": False}},
            "required": ["spec"]},
        timeout=600,
        llm_result_fields=["file", "qa"],
    )
    async def make_report(self, spec: dict | None = None, open_after: bool = False, **_):
        try:
            return Ok(await asyncio.to_thread(core.make_report, spec or {}, bool(open_after)))
        except (SpecError, tools.ToolError) as exc:
            return _err(exc)

    @plugin_entry(id="open_output", name=tr("entry.open_output.name", default="Open a made file"),
                  description=tr("entry.open_output.description", default="Open a deck, report or preview this tool made."),
                  input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                  timeout=30, llm_result_fields=["opened"])
    async def open_output(self, path: str = "", **_):
        try:
            p = tools.inside_outputs(path)
            await asyncio.to_thread(tools.open_file, p)
            return Ok({"opened": str(p)})
        except tools.ToolError as exc:
            return _err(exc)

    @plugin_entry(id="show_in_folder", name=tr("entry.show_in_folder.name", default="Show in folder"),
                  description=tr("entry.show_in_folder.description", default="Show a made file selected in File Explorer."),
                  input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                  timeout=30, llm_result_fields=["shown"])
    async def show_in_folder(self, path: str = "", **_):
        try:
            p = tools.inside_outputs(path)
            await asyncio.to_thread(tools.show_in_folder, p)
            return Ok({"shown": str(p)})
        except tools.ToolError as exc:
            return _err(exc)

    @plugin_entry(id="start_slideshow", name=tr("entry.start_slideshow.name", default="Present a deck"),
                  description=tr("entry.start_slideshow.description", default="Start the slideshow of a deck this tool made (LibreOffice Impress, full screen)."),
                  input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                  timeout=30, llm_result_fields=["presenting"])
    async def start_slideshow(self, path: str = "", **_):
        try:
            p = tools.inside_outputs(path)
            if p.suffix.lower() != ".pptx":
                raise tools.ToolError("only a .pptx deck can be presented")
            await asyncio.to_thread(tools.start_slideshow, p)
            return Ok({"presenting": str(p)})
        except tools.ToolError as exc:
            return _err(exc)

    @plugin_entry(id="regenerate", name=tr("entry.regenerate.name", default="Make it again"),
                  description=tr("entry.regenerate.description", default="Build a deliverable again from its saved spec (into a new folder)."),
                  input_schema={"type": "object", "properties": {"folder": {"type": "string"}, "open_after": {"type": "boolean", "default": False}},
                                "required": ["folder"]},
                  timeout=600, llm_result_fields=["file", "qa"])
    async def regenerate(self, folder: str = "", open_after: bool = False, **_):
        try:
            return Ok(await asyncio.to_thread(core.regenerate, folder, bool(open_after)))
        except (SpecError, tools.ToolError) as exc:
            return _err(exc)

    @plugin_entry(id="list_deliverables", name=tr("entry.list_deliverables.name", default="List made decks and reports"),
                  description=tr("entry.list_deliverables.description", default="The newest decks and reports, with whether their checks passed."),
                  timeout=20, llm_result_fields=["items", "tools"])
    async def list_deliverables(self, **_):
        return Ok({"items": await asyncio.to_thread(core.list_deliverables), "tools": tools.status()})
