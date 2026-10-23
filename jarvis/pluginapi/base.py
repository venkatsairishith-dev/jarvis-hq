"""The base class of every plugin."""
from __future__ import annotations

from typing import Any

from .log import DEFAULT, BraceLogger


class PluginBase:
    """A plugin gets a context from the host (`ctx`): `ctx.plugins` (the router for calling other plugins' entries),
    `ctx.logger`, `ctx.push_message(...)` and `ctx.data_dir`. Tests may create a plugin with `object.__new__` and set what they
    need, so every attribute read here has a working default at class level."""

    logger: Any = DEFAULT                       # `logger.warning("run {} failed", rid)`; a plugin or a test may replace it
    ctx: Any = None

    def __init__(self, ctx: Any = None):
        self.ctx = ctx
        if ctx is not None and getattr(ctx, "logger", None) is not None:
            self.logger = ctx.logger

    @property
    def plugins(self) -> Any:
        """The router for calling other plugins' entries: `await self.plugins.call_entry_json("research:research_topic", {...})`."""
        override = self.__dict__.get("_plugins_override")
        return override if override is not None else getattr(self.ctx, "plugins", None)

    @plugins.setter
    def plugins(self, router: Any) -> None:
        """Tests and embedders may point a plugin at another router."""
        self.__dict__["_plugins_override"] = router

    def push_message(self, **message: Any) -> Any:
        """Says something to the owner in the face's chat on the plugin's own initiative (a result is ready, a question)."""
        push = getattr(self.ctx, "push_message", None)
        if push is None:
            self.logger.warning("a message could not be delivered: no host")
            return None
        return push(**message)

    def enable_file_logging(self, log_level: str = "INFO") -> BraceLogger:
        """The host writes the log of every plugin to its own file; the plugin only gets its logger back."""
        return self.logger if isinstance(self.logger, BraceLogger) else DEFAULT


__all__ = ["PluginBase"]
