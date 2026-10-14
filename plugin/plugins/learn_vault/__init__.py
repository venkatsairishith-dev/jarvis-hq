"""Learn Vault: a study vault in Obsidian format for N.E.K.O's Learn mode.

Read, search and save notes, and track progress per topic, inside one bound folder. Logic and safety rules are in core.py.
"""

from __future__ import annotations

import asyncio

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr

from . import core
from .core import VaultError


def _wrap(fn, *args, **kwargs):
    async def run():
        try:
            return Ok(await asyncio.to_thread(fn, *args, **kwargs))
        except VaultError as exc:
            return Err(SdkError(str(exc)))
    return run()


@neko_plugin
class LearnVaultPlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready", "vault": str(core.vault_root())})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    @plugin_entry(id="vault_status", name=tr("entry.vault_status.name", default="Study vault status"),
                  description=tr("entry.vault_status.description", default="Show whether the study vault exists, how many notes and topics it has."),
                  timeout=20, llm_result_fields=["exists", "notes", "topics"])
    async def vault_status(self, **_):
        return await _wrap(lambda: core.status())

    @plugin_entry(
        id="list_notes", name=tr("entry.list_notes.name", default="List study notes"),
        description=tr("entry.list_notes.description", default="List the Markdown notes in the study vault, optionally inside one folder."),
        input_schema={"type": "object", "properties": {"folder": {"type": "string", "description": "Folder inside the vault (optional)."}}},
        timeout=20, llm_result_fields=["notes"])
    async def list_notes(self, folder: str = "", **_):
        try:
            return Ok({"notes": await asyncio.to_thread(core.list_notes, folder)})
        except VaultError as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="read_note", name=tr("entry.read_note.name", default="Read a study note"),
        description=tr("entry.read_note.description", default="Read one note from the study vault by its path, for example notes/photosynthesis.md."),
        input_schema={"type": "object", "properties": {"path": {"type": "string", "description": "Path inside the vault."}}, "required": ["path"]},
        timeout=20, llm_result_fields=["path", "text"])
    async def read_note(self, path: str = "", **_):
        return await _wrap(core.read_note, path)

    @plugin_entry(
        id="search_notes", name=tr("entry.search_notes.name", default="Search study notes"),
        description=tr("entry.search_notes.description", default="Search the study vault for a few words; returns the best matching notes with a snippet."),
        input_schema={"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 10}}, "required": ["query"]},
        timeout=30, llm_result_fields=["hits"])
    async def search_notes(self, query: str = "", limit: int = 10, **_):
        try:
            return Ok({"hits": await asyncio.to_thread(core.search_notes, query, limit)})
        except VaultError as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="save_note", name=tr("entry.save_note.name", default="Save a study note"),
        description=tr("entry.save_note.description", default=(
            "Save Markdown text as a note in the study vault (summaries, explanations, exercises). Creates a new note; "
            "mode=append adds to an existing one. Never overwrites. Use folders such as notes/, summaries/ or exercises/.")),
        input_schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path inside the vault, e.g. exercises/cell-biology.md"},
            "content": {"type": "string", "description": "Markdown text."},
            "mode": {"type": "string", "enum": ["create", "append"], "default": "create"}}, "required": ["path", "content"]},
        timeout=30, llm_result_fields=["path", "mode", "bytes"])
    async def save_note(self, path: str = "", content: str = "", mode: str = "create", **_):
        return await _wrap(core.save_note, path, content, mode)

    @plugin_entry(
        id="record_progress", name=tr("entry.record_progress.name", default="Record learning progress"),
        description=tr("entry.record_progress.description", default="Record the user's progress on a topic: not_started, learning, review or mastered."),
        input_schema={"type": "object", "properties": {
            "topic": {"type": "string"}, "status": {"type": "string", "enum": list(core.STATUSES)}, "note": {"type": "string"}},
            "required": ["topic", "status"]},
        timeout=20, llm_result_fields=["topic", "status"])
    async def record_progress(self, topic: str = "", status: str = "", note: str = "", **_):
        return await _wrap(core.record_progress, topic, status, note)

    @plugin_entry(id="get_progress", name=tr("entry.get_progress.name", default="Show learning progress"),
                  description=tr("entry.get_progress.description", default="Show the latest status of every topic the user is learning."),
                  timeout=20, llm_result_fields=["topics"])
    async def get_progress(self, **_):
        try:
            return Ok({"topics": await asyncio.to_thread(core.progress)})
        except VaultError as exc:
            return Err(SdkError(str(exc)))
