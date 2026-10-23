"""The plugin host: loads plugins, runs their lifecycle and timers, routes calls between them, and offers their entries as tools.

Before 2026-10 this was N.E.K.O's plugin server. JARVIS now runs its plugins in the same process as the face's tool server
(jarvis/bridge/face_tools.py), so a plugin entry is an MCP tool the face's model can call, and the kernel still decides every
action a plugin takes (plugins act through the console and the kernel, never on their own authority).

Plugins live in jarvis/plugins/<id>/ with a plugin.toml:

    [plugin]
    id = "desktop"
    entry = "jarvis.plugins.desktop:DesktopPlugin"
    [plugin_runtime]
    enabled = true
"""
from __future__ import annotations

import asyncio
import contextvars
import importlib
import inspect
import time
import tomllib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Callable

from .base import PluginBase
from .decorators import ENTRY_META_ATTR, PLUGIN_ATTR, EntryMeta
from .log import BraceLogger
from .paths import data_dir
from .result import Err, Ok, SdkError

PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"
MAX_CALL_DEPTH = 4
DEFAULT_TIMEOUT = 60.0
OUTBOX_LIMIT = 200
_depth: contextvars.ContextVar[int] = contextvars.ContextVar("jarvis_plugin_call_depth", default=0)


@dataclass
class Message:
    id: int
    ts: float
    source: str
    text: str
    priority: int = 0
    ai_behavior: str = "respond"
    metadata: dict[str, Any] = field(default_factory=dict)
    coalesce_key: str | None = None
    read: bool = False

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.ts)), "from": self.source,
                "text": self.text, "priority": self.priority}


class Outbox:
    """What plugins say on their own initiative. The face's model reads it with the `jarvis_messages` tool."""

    def __init__(self, limit: int = OUTBOX_LIMIT, clock: Callable[[], float] = time.time):
        self._items: deque[Message] = deque(maxlen=limit)
        self._next = 1
        self._clock = clock

    def push(self, **kw: Any) -> Message:
        text = " ".join(str(p.get("text", "")) for p in (kw.get("parts") or []) if isinstance(p, dict) and p.get("type", "text") == "text").strip()
        key = kw.get("coalesce_key")
        if key:
            for old in self._items:
                if old.coalesce_key == key and not old.read:
                    old.read = True                   # the newer message replaces an unread one with the same key
        msg = Message(self._next, self._clock(), str(kw.get("source") or "plugin"), text, int(kw.get("priority") or 0),
                      str(kw.get("ai_behavior") or "respond"), dict(kw.get("metadata") or {}), str(key) if key else None)
        self._next += 1
        self._items.append(msg)
        return msg

    def unread(self) -> list[Message]:
        return [m for m in self._items if not m.read]

    def take(self, mark_read: bool = True) -> list[dict[str, Any]]:
        out = self.unread()
        if mark_read:
            for m in out:
                m.read = True
        return [m.public() for m in sorted(out, key=lambda m: (-m.priority, m.id))]


class Context:
    """What a plugin receives as `ctx`."""

    def __init__(self, plugin_id: str, plugins: "Plugins", outbox: Outbox):
        self.plugin_id = plugin_id
        self.plugins = plugins
        self.logger = BraceLogger(f"jarvis.plugin.{plugin_id}")
        self.data_dir = data_dir()
        self._outbox = outbox

    def push_message(self, **message: Any) -> Message:
        message.setdefault("source", self.plugin_id)
        return self._outbox.push(**message)


@dataclass
class Loaded:
    id: str
    name: str
    description: str
    cls: type
    instance: PluginBase | None = None
    entries: dict[str, tuple[EntryMeta, str]] = field(default_factory=dict)       # entry id -> (meta, method name)
    lifecycle: dict[str, str] = field(default_factory=dict)
    timers: list[tuple[EntryMeta, str]] = field(default_factory=list)
    status: str = "loaded"                                                        # loaded, running, failed, stopped
    error: str = ""


def read_manifest(folder: Path) -> dict[str, Any] | None:
    manifest = folder / "plugin.toml"
    if not manifest.is_file():
        return None
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    plugin = data.get("plugin") or {}
    runtime = data.get("plugin_runtime") or {}
    if not plugin.get("id") or not plugin.get("entry") or runtime.get("enabled") is False:
        return None
    return {"id": str(plugin["id"]), "name": str(plugin.get("name") or plugin["id"]),
            "description": str(plugin.get("description") or ""), "entry": str(plugin["entry"])}


def inspect_class(cls: type) -> tuple[dict[str, tuple[EntryMeta, str]], dict[str, str], list[tuple[EntryMeta, str]]]:
    entries: dict[str, tuple[EntryMeta, str]] = {}
    lifecycle: dict[str, str] = {}
    timers: list[tuple[EntryMeta, str]] = []
    for name in dir(cls):
        meta = getattr(getattr(cls, name, None), ENTRY_META_ATTR, None)
        if not isinstance(meta, EntryMeta):
            continue
        fn = getattr(cls, name)
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"{cls.__name__}.{name} must be 'async def' (it is a plugin {meta.kind})")
        if meta.kind == "entry":
            entries[meta.id] = (meta, name)
        elif meta.kind == "lifecycle":
            lifecycle[meta.id] = name
        elif meta.kind == "timer":
            timers.append((meta, name))
    return entries, lifecycle, timers


class Plugins:
    """The router plugins use to call each other: `await self.plugins.call_entry_json("research:research_topic", {...})`."""

    def __init__(self, host: "PluginHost"):
        self._host = host

    async def call_entry(self, ref: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Ok | Err:
        return await self._host.invoke(ref, params or {}, timeout)

    async def call_entry_json(self, ref: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Ok | Err:
        res = await self._host.invoke(ref, params or {}, timeout)
        if isinstance(res, Err) or res.value is None or isinstance(res.value, dict):
            return res
        return Err(SdkError(f"entry {ref!r} returned something that is not an object"))

    async def list_ids(self, enabled: bool | None = None) -> Ok:
        return Ok([p.id for p in self._host.loaded.values() if enabled is None or (p.status == "running") == enabled])

    def list_plugins(self) -> list[dict[str, Any]]:
        """Running plugins and their entries, as the planner's catalogue wants them."""
        return self._host.describe()


class PluginHost:
    def __init__(self, plugins_dir: Path | None = None, outbox: Outbox | None = None, only: set[str] | None = None, exclude: set[str] | None = None):
        self.plugins_dir = plugins_dir or PLUGINS_DIR
        self.outbox = outbox or Outbox()
        self.loaded: dict[str, Loaded] = {}
        self.router = Plugins(self)
        self.log = BraceLogger("jarvis.plugin.host")
        self._timers: list[asyncio.Task] = []
        self._only, self._exclude = only, exclude or set()

    # ---- loading -------------------------------------------------------------------------------------------------------------
    def load(self) -> list[str]:
        """Imports every enabled plugin under plugins_dir. A plugin that cannot be loaded is recorded as failed and skipped."""
        for folder in sorted(p for p in self.plugins_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
            manifest = read_manifest(folder)
            if manifest is None or manifest["id"] in self._exclude or (self._only is not None and manifest["id"] not in self._only):
                continue
            item = Loaded(manifest["id"], manifest["name"], manifest["description"], PluginBase)
            self.loaded[item.id] = item
            try:
                module_name, _, class_name = manifest["entry"].partition(":")
                cls = getattr(importlib.import_module(module_name), class_name)
                if not getattr(cls, PLUGIN_ATTR, False):
                    raise TypeError(f"{manifest['entry']} is not marked with @jarvis_plugin")
                item.cls = cls
                item.entries, item.lifecycle, item.timers = inspect_class(cls)
            except Exception as exc:      # noqa: BLE001 - one broken plugin must not stop the others
                item.status, item.error = "failed", f"{type(exc).__name__}: {exc}"[:300]
                self.log.warning("plugin {} could not be loaded: {}", item.id, item.error)
        return list(self.loaded)

    async def start(self) -> None:
        for item in self.loaded.values():
            if item.status == "failed":
                continue
            try:
                item.instance = item.cls(Context(item.id, self.router, self.outbox))
                if "startup" in item.lifecycle:
                    res = await getattr(item.instance, item.lifecycle["startup"])()
                    if isinstance(res, Err):
                        raise RuntimeError(str(res.error))
                item.status = "running"
                for meta, method in item.timers:
                    if meta.auto_start:
                        self._timers.append(asyncio.create_task(self._run_timer(item, meta, method), name=f"{item.id}:{meta.id}"))
            except Exception as exc:      # noqa: BLE001
                item.status, item.error = "failed", f"{type(exc).__name__}: {exc}"[:300]
                self.log.warning("plugin {} did not start: {}", item.id, item.error)

    async def stop(self) -> None:
        for task in self._timers:
            task.cancel()
        await asyncio.gather(*self._timers, return_exceptions=True)
        self._timers.clear()
        for item in reversed(list(self.loaded.values())):
            if item.status == "running" and item.instance is not None and "shutdown" in item.lifecycle:
                try:
                    await getattr(item.instance, item.lifecycle["shutdown"])()
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("plugin {} did not stop cleanly: {}", item.id, type(exc).__name__)
            if item.status == "running":
                item.status = "stopped"

    async def _run_timer(self, item: Loaded, meta: EntryMeta, method: str) -> None:
        busy = False
        while True:
            await asyncio.sleep(meta.seconds or 60.0)
            if busy:
                continue
            busy = True
            try:
                await getattr(item.instance, method)()
            except asyncio.CancelledError:
                raise
            except Exception as exc:      # noqa: BLE001
                self.log.warning("timer {}:{} failed: {}", item.id, meta.id, type(exc).__name__)
            finally:
                busy = False

    # ---- calling -------------------------------------------------------------------------------------------------------------
    async def invoke(self, ref: str, params: dict[str, Any], timeout: float | None = None) -> Ok | Err:
        pid, sep, eid = str(ref).partition(":")
        item = self.loaded.get(pid)
        if not sep or item is None or item.status != "running" or eid not in item.entries:
            return Err(SdkError(f"there is no running entry {ref!r}", code="not_found"))
        if _depth.get() >= MAX_CALL_DEPTH:
            return Err(SdkError(f"{ref}: plugins called each other too deeply", code="too_deep"))
        meta, method = item.entries[eid]
        missing = [k for k in ((meta.input_schema or {}).get("required") or []) if k not in params]
        if missing:
            return Err(SdkError(f"{ref}: missing {', '.join(missing)}", code="invalid"))
        limit = timeout or meta.timeout or DEFAULT_TIMEOUT
        token = _depth.set(_depth.get() + 1)
        try:
            res = await asyncio.wait_for(getattr(item.instance, method)(**params), limit)
        except asyncio.TimeoutError:
            return Err(SdkError(f"{ref} did not answer within {limit:g} s", code="timeout"))
        except Exception as exc:          # noqa: BLE001 - only the type leaves: provider errors can echo request details
            item.instance.logger.warning("entry {} failed: {}", eid, type(exc).__name__)
            return Err(SdkError(f"{ref} failed ({type(exc).__name__})", code="plugin_error"))
        finally:
            _depth.reset(token)
        return res if isinstance(res, (Ok, Err)) else Ok(res)

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for p in self.loaded.values():
            out.append({"id": p.id, "name": p.name, "description": p.description, "status": p.status,
                        "entries": [{"id": eid, "name": m.name, "description": m.description, "input_schema": m.input_schema}
                                    for eid, (m, _) in sorted(p.entries.items())] if p.status == "running" else []})
        return out


# ---- offering entries as MCP tools -------------------------------------------------------------------------------------------
_JSON_TYPES: dict[str, Any] = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list[Any], "object": dict[str, Any]}


def for_model(meta: EntryMeta, value: Any) -> Any:
    """Only the fields the plugin allows its chat model to see (`llm_result_fields`); everything when it names none."""
    if meta.llm_result_fields and isinstance(value, dict):
        data = value.get("data", value) if isinstance(value.get("data"), dict) else value
        return {k: data[k] for k in meta.llm_result_fields if k in data}
    return value


def tool_function(host: PluginHost, pid: str, eid: str, meta: EntryMeta, clean: Callable[[Any], Any] | None = None):
    from pydantic import Field

    schema = meta.input_schema or {}
    required = set(schema.get("required") or [])
    params = []
    for name, prop in (schema.get("properties") or {}).items():
        if not str(name).isidentifier():
            continue
        base = _JSON_TYPES.get((prop or {}).get("type") if isinstance(prop, dict) else None, Any)
        desc = str((prop or {}).get("description") or "") if isinstance(prop, dict) else ""
        if name in required:
            ann = Annotated[base, Field(description=desc)] if desc else base
            params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=ann))
        else:
            ann = Annotated[base | None, Field(description=desc)] if desc else base | None
            params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=ann))

    async def call(**kwargs: Any) -> dict[str, Any]:
        args = {k: v for k, v in kwargs.items() if v is not None}
        res = await host.invoke(f"{pid}:{eid}", args)
        if isinstance(res, Err):
            out = {"ok": False, "error": str(getattr(res.error, "message", res.error))[:400]}
        else:
            out = {"ok": True, "result": for_model(meta, res.value)}
        return clean(out) if clean else out

    call.__signature__ = inspect.Signature(params)           # type: ignore[attr-defined]
    call.__annotations__ = {p.name: p.annotation for p in params}
    call.__name__ = f"{pid}__{eid}"
    return call


def register_tools(server: Any, host: PluginHost, exclude: set[str] = frozenset(), clean: Callable[[Any], Any] | None = None) -> list[str]:
    """Adds one MCP tool per entry of every running plugin: `<plugin>__<entry>`. `clean` is applied to every result before it is returned."""
    names = []
    for pid, item in host.loaded.items():
        if item.status != "running" or pid in exclude:
            continue
        for eid, (meta, _method) in sorted(item.entries.items()):
            name = f"{pid}__{eid}"
            server.add_tool(tool_function(host, pid, eid, meta, clean), name=name, description=(meta.description or meta.name or name)[:1000])
            names.append(name)
    return names
