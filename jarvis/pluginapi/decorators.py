"""Markers a plugin puts on its class and methods. They only record what the plugin declares; the host (host.py) reads it.

    @jarvis_plugin
    class MyPlugin(PluginBase):
        @lifecycle(id="startup")
        async def startup(self, **_): ...

        @timer_interval(id="tick", seconds=30)
        async def tick(self, **_): ...

        @plugin_entry(id="do_it", name="Do it", description="...", input_schema={...}, timeout=60, llm_result_fields=["message"])
        async def do_it(self, thing: str = "", **_): ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])
C = TypeVar("C", bound=type)

ENTRY_META_ATTR = "__jarvis_entry_meta__"
PLUGIN_ATTR = "__jarvis_plugin__"
UI_ATTR = "__jarvis_ui__"


@dataclass(slots=True)
class EntryMeta:
    kind: str                                   # "entry", "lifecycle" or "timer"
    id: str
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] | None = None
    timeout: float | None = None
    llm_result_fields: list[str] | None = None  # which fields of the result the chat model sees; None means all
    seconds: float | None = None                # timers
    auto_start: bool = True                     # timers
    extra: dict[str, Any] = field(default_factory=dict)

    # the plugin tests read these two names
    @property
    def event_type(self) -> str:
        return "plugin_entry" if self.kind == "entry" else self.kind


def jarvis_plugin(cls: C) -> C:
    """Marks a class as a plugin."""
    setattr(cls, PLUGIN_ATTR, True)
    return cls


def _mark(fn: F, meta: EntryMeta) -> F:
    setattr(fn, ENTRY_META_ATTR, meta)
    return fn


def plugin_entry(*, id: str | None = None, name: object = "", description: object = "", input_schema: dict[str, Any] | None = None,
                 timeout: float | None = None, llm_result_fields: list[str] | None = None, **extra: Any) -> Callable[[F], F]:
    """An action the plugin offers: other plugins and the face's chat model can call it."""
    def deco(fn: F) -> F:
        return _mark(fn, EntryMeta("entry", id or fn.__name__, str(name or ""), str(description or ""), input_schema, timeout,
                                   list(llm_result_fields) if llm_result_fields else None, extra=extra))
    return deco


def lifecycle(*, id: str, **extra: Any) -> Callable[[F], F]:
    """`startup` runs once when the host starts the plugin, `shutdown` when it stops."""
    if id not in ("startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"):
        raise ValueError(f"unknown lifecycle id {id!r}")

    def deco(fn: F) -> F:
        return _mark(fn, EntryMeta("lifecycle", id, extra=extra))
    return deco


def timer_interval(*, id: str, seconds: float, auto_start: bool = True, **extra: Any) -> Callable[[F], F]:
    """Runs the method every `seconds` while the plugin runs (never twice at once)."""
    def deco(fn: F) -> F:
        return _mark(fn, EntryMeta("timer", id, seconds=float(seconds), auto_start=auto_start, extra=extra))
    return deco


class _Ui:
    """`@ui.action` and `@ui.context` describe buttons and panels. The face has no plugin panel, so they are only recorded."""

    @staticmethod
    def action(**info: Any) -> Callable[[F], F]:
        def deco(fn: F) -> F:
            setattr(fn, UI_ATTR, {"kind": "action", **{k: str(v) if not isinstance(v, (bool, int, float)) else v for k, v in info.items()}})
            return fn
        return deco

    @staticmethod
    def context(**info: Any) -> Callable[[F], F]:
        def deco(fn: F) -> F:
            setattr(fn, UI_ATTR, {"kind": "context", **{k: str(v) for k, v in info.items()}})
            return fn
        return deco


ui = _Ui()


def tr(key: str, default: str | None = None, **_: Any) -> str:
    """The text for `key`. The interface is English only, so this is the default text."""
    return default if default is not None else key
