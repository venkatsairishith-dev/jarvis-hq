"""How a plugin asks the owner a question through the face's ask panel (jarvis_ask, D55) (D64, 2026-10-26).

The tool server (jarvis/bridge/face_tools.py) owns the AskBroker; a plugin that runs inside it (the desktop plugin's browser agent) cannot import the
broker without importing the bridge. The server calls `install(broker, loop)` once; a plugin calls `ask(spec)` from any thread and gets
{answered, button, values} or {answered: False, reason}. Without a panel (a test, a headless run) `ask` answers {answered: False, reason: "unavailable"}
at once, and the caller reports what blocked it in its own result so the chat model can show the form itself.

A form only collects choices. It never approves anything and never carries a PIN, password or code (D55 refuses such fields): a CAPTCHA, a 2FA code or a
payment is done by the owner on the page itself, and the form only waits for him to say "done".
"""
from __future__ import annotations

import asyncio
import threading

_STATE: dict = {"broker": None, "loop": None, "preview": None}
_LOCK = threading.Lock()


def install(broker, loop) -> None:
    with _LOCK:
        _STATE.update(broker=broker, loop=loop)


def uninstall() -> None:
    install(None, None)
    _STATE["preview"] = None


def install_preview(fn) -> None:
    """D76: the Preview panel's own queue (bridge/output_preview.OutputPreview.show_path): a task that has made a file shows it by itself."""
    _STATE["preview"] = fn


def show_file(path: str) -> bool:
    """Queue a file the task made for the Preview panel; True when the panel took it. Never raises; False without a panel (the chat model then says `jarvis_show`)."""
    fn = _STATE["preview"]
    if fn is None:
        return False
    try:
        return bool(fn(str(path)))
    except Exception:  # noqa: BLE001
        return False


def available() -> bool:
    return _STATE["broker"] is not None and _STATE["loop"] is not None


def ask(spec: dict, timeout_s: float = 120.0) -> dict:
    """Show the form and wait for the owner. Never raises."""
    with _LOCK:
        broker, loop = _STATE["broker"], _STATE["loop"]
    if broker is None or loop is None:
        return {"answered": False, "reason": "unavailable"}
    try:
        fut = asyncio.run_coroutine_threadsafe(broker.ask(dict(spec, timeout_s=min(float(timeout_s), 150.0)), min(float(timeout_s), 150.0)), loop)
        return fut.result(timeout=min(float(timeout_s), 150.0) + 10.0)
    except Exception as e:  # noqa: BLE001 - a broken panel is "no answer", not a crash of the task
        return {"answered": False, "reason": "error", "message": type(e).__name__}
