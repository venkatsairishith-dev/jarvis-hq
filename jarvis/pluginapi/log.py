"""A logger with `{}` placeholders (`logger.warning("run {} failed: {}", rid, name)`), on the standard logging module."""
from __future__ import annotations

import logging as _logging


class BraceLogger:
    def __init__(self, name: str = "jarvis.plugin"):
        self._log = _logging.getLogger(name)

    def _emit(self, level: int, message: object, args: tuple, **kw) -> None:
        if not self._log.isEnabledFor(level):
            return
        text = str(message)
        if args:
            try:
                text = text.format(*args)
            except (IndexError, KeyError, ValueError):
                text = f"{text} {args}"
        self._log.log(level, text, **kw)

    def debug(self, message, *args): self._emit(_logging.DEBUG, message, args)
    def info(self, message, *args): self._emit(_logging.INFO, message, args)
    def warning(self, message, *args): self._emit(_logging.WARNING, message, args)
    def error(self, message, *args): self._emit(_logging.ERROR, message, args)
    def critical(self, message, *args): self._emit(_logging.CRITICAL, message, args)
    def exception(self, message, *args): self._emit(_logging.ERROR, message, args, exc_info=True)


DEFAULT = BraceLogger()
