"""Generated UI = declarative forms only (D55). The broker that holds the one form the face is showing.

A form is JSON (see ask_spec.py for the language, ui_patterns.py for the ready-made shapes). The face's AskPanel is one fixed, trusted
component that draws it. What a form can and cannot do:

  - It only collects choices. Nothing in a spec is run, fetched or followed; all text is plain text and the face shows it as text.
  - File and gallery inputs list only what the backend finds in the known folders; the answer is the path of the item the owner clicked.
  - It never grants an approval. Approvals stay in the JARVIS console with the PIN (D27): this module does not import the console client or
    the kernel, a field that asks for a PIN, password or code is refused, and an answer is data for the task, not an approval.
  - A form that is not answered says so: {answered: false, reason: timeout | closed | replaced}. An answer is never invented.
  - D63: every form can also be used by voice (face: use-jarvis-ask-voice.ts; backend help: ask_voice.py). The owner's own words in a text field
    stay as he said them in the panel (Telugu, Hindi or English); the answer the MODEL gets has them in English (D58), the original is kept in
    the owner's translation record on this PC.

The model's tool is `jarvis_ask` (waits for the answer). The panel's own calls are `jarvis_ask_wanted` (a long poll: it returns within
milliseconds of a change, so the panel appears at once without fast polling), `jarvis_ask_submit` and `jarvis_ask_close`
(bridge/face_tools.py). A form with a `handler` (the cleaner, a running job) stays open after a click: the handler may replace the form
(progress, result) with `update`.
"""
from __future__ import annotations

import asyncio
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from jarvis.bridge.ask_spec import AskError, clean_spec, default_values, for_panel, paths_of, plain, validate_answer  # noqa: F401 - re-exported

NOTE = "The owner's choice is data for the task. It is not an approval; approvals happen in the JARVIS console with the PIN."


def english_answer(spec: dict, result: dict) -> dict:
    """D63 + D58: a text the owner typed or dictated in Telugu or Hindi reaches the model in English; his original is kept in his record on this PC
    (translate.keep_record). A translation that could not be done leaves the original and says so. Only `text` inputs are looked at."""
    values = result.get("values")
    if not result.get("answered") or not isinstance(values, dict):
        return result
    try:
        from jarvis.plugins._shared import translate
    except Exception:                                             # noqa: BLE001 - no translation layer: the answer goes on as it is
        return result
    texts = {n["id"] for n in iter_text_inputs(spec)}
    changed: dict[str, str] = {}
    skipped: list[str] = []
    out = dict(values)
    for nid in texts:
        raw = values.get(nid)
        if not isinstance(raw, str) or not raw.strip():
            continue
        info = translate.english_info(raw, source="form")
        lang = str(info.get("source_lang") or "en")
        if lang == "en":
            continue
        if info.get("translated"):
            out[nid] = str(info["english"])[:2000]
            changed[nid] = lang.split("-")[0]
        else:
            skipped.append(nid)
    if not changed and not skipped:
        return result
    note = []
    if changed:
        note.append("These text answers were in " + ", ".join(sorted({translate.LANG_NAMES.get(v, v) for v in changed.values()})) + " and are given in English: "
                    + ", ".join(sorted(changed)) + ". The owner's own words are kept on his PC.")
    if skipped:
        note.append("These could not be translated on this PC and are in the owner's own words: " + ", ".join(sorted(skipped)) + ".")
    return {**result, "values": out, "translated_fields": {k: v for k, v in sorted(changed.items())}, "note_for_model_language": " ".join(note)}


def iter_text_inputs(spec: dict):
    from jarvis.bridge.ask_spec import iter_inputs
    return (n for n in iter_inputs(spec["fields"]) if n.get("type") == "text")


class _Form:
    def __init__(self, form_id: str, spec: dict, handler: Callable | None, token: str, waiter: bool, key: str):
        self.id, self.spec, self.handler, self.token, self.key = form_id, spec, handler, token, key
        self.event = threading.Event() if waiter else None
        self.result: dict | None = None
        self.busy = False
        self.status = ""
        self.progress: dict | None = None
        self.clicked = ""                                    # the last button the owner pressed on a form nobody waits for (a job's Cancel)


class AskBroker:
    """Holds the one form that is open. `ask` (the model's call) waits for the answer; `show` opens a form that nobody waits for."""

    def __init__(self, home: Path | None = None, clock: Callable[[], float] = time.monotonic):
        self.home, self.clock = home, clock
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.version = 0
        self.form: _Form | None = None

    # -- opening -----------------------------------------------------------------------------------------------------------------------
    def _bump(self) -> None:
        self.version += 1
        self.cond.notify_all()

    def _open(self, spec: dict, handler: Callable | None, waiter: bool, key: str = "") -> _Form:
        with self.lock:
            self._finish(self.form, {"answered": False, "reason": "replaced"})
            self.form = _Form("q" + secrets.token_hex(5), spec, handler, secrets.token_hex(8), waiter, key)
            self._bump()
            return self.form

    def _finish(self, form: _Form | None, result: dict) -> None:
        if form is None or form.result is not None:
            return
        form.result = result
        if form.event is not None:
            form.event.set()
        if self.form is form:
            self.form = None
            self._bump()

    def show(self, spec: Any, handler: Callable | None = None, key: str = "") -> str:
        """Open a form the panel owns (no one waits). With the same `key` as the open form, that form is updated in place. Returns the form id."""
        clean = clean_spec(spec, self.home)
        with self.lock:
            if key and self.form is not None and self.form.key == key and self.form.result is None:
                self.update(self.form.id, clean, busy=False, clear_progress=True)
                return self.form.id
        return self._open(clean, handler, False, key).id

    def current(self, key: str = "") -> _Form | None:
        with self.lock:
            return self.form if self.form is not None and (not key or self.form.key == key) else None

    def update(self, form_id: str, spec: Any | None = None, busy: bool | None = None, status: str | None = None,
               progress: dict | None = None, clear_progress: bool = False) -> bool:
        """Replace what the open form shows (progress, result). `spec` is cleaned like a new one. False when that form is no longer open."""
        with self.lock:
            f = self.form
            if f is None or f.id != form_id:
                return False
            if spec is not None:
                f.spec = clean_spec(spec, self.home)
            if busy is not None:
                f.busy = bool(busy)
            if status is not None:
                f.status = plain(status, 120)
            if clear_progress:
                f.progress = None
            if progress is not None:
                f.progress = {k: (plain(v, 80) if isinstance(v, str) else int(max(0, min(float(v or 0), 1e15)))) for k, v in progress.items()}
            self._bump()
            return True

    def withdraw(self, form_id: str) -> None:
        with self.lock:
            if self.form is not None and self.form.id == form_id:
                self._finish(self.form, {"answered": False, "reason": "closed"})

    async def ask(self, spec: Any, timeout_s: float | None = None) -> dict:
        """Show a form and wait for the owner. {answered: true, button, values} or {answered: false, reason: timeout | closed | replaced}."""
        clean = clean_spec(spec, self.home)
        form = self._open(clean, None, True)
        wait = float(timeout_s if timeout_s is not None else clean["timeout_s"])
        got = await asyncio.to_thread(form.event.wait, wait)
        with self.lock:
            if not got:
                self._finish(form, {"answered": False, "reason": "timeout"})
            result = dict(form.result or {"answered": False, "reason": "closed"})
        if clean.get("notes") and result.get("answered"):
            result["note_for_model"] = "Some parts of the form were simplified: " + " ".join(clean["notes"])
        return await asyncio.to_thread(english_answer, clean, result)

    # -- the panel's side ----------------------------------------------------------------------------------------------------------------
    def wanted(self, seen: int = 0, wait_s: float = 0.0) -> dict:
        """What the panel asks for. With `wait_s` it is a long poll: it returns as soon as something is newer than the panel's `seen`
        (a form opened, changed or closed), or after `wait_s` seconds with `unchanged`."""
        seen = int(seen or 0)
        with self.lock:
            if wait_s > 0 and seen >= self.version:
                self.cond.wait_for(lambda: self.version > seen, timeout=min(float(wait_s), 25.0))
            f = self.form
            if f is None:
                return {"version": self.version, "form": None}
            if seen >= self.version:
                return {"version": self.version, "unchanged": True}
            return {"version": self.version, "form": {"id": f.id, "token": f.token, "busy": f.busy, "status": f.status, "progress": f.progress,
                                                     "values": default_values(f.spec), **for_panel(f.spec)}}

    def submit(self, form_id: str, token: str, button: str, values: Any) -> dict:
        with self.lock:
            f = self.form
            if f is None or f.id != str(form_id) or not secrets.compare_digest(f.token, str(token or "")):
                return {"ok": False, "message": "That form is no longer open."}
            if f.busy:
                return {"ok": False, "message": "JARVIS is still working on the last click."}
            try:
                answer = validate_answer(f.spec, str(button), values)
            except AskError as exc:
                return {"ok": False, "message": str(exc)}
            paths = paths_of(f.spec)
            picked = {k: _to_path(v, paths[k]) if k in paths else v for k, v in answer.items()}
            handler = f.handler
            if handler is None and f.event is not None:
                self._finish(f, {"answered": True, "button": str(button), "values": picked, "note": NOTE})
                return {"ok": True, "done": True}
            if handler is None:                                       # a form nobody waits for and nothing handles: remember the click
                f.clicked = str(button)
                self._bump()
                return {"ok": True, "done": False}
        try:                                                          # a handler form: run it outside the lock (it may call update())
            reply = handler(str(button), answer, f.id) or {}
        except Exception as exc:  # noqa: BLE001 - the panel gets a sentence, not a stack
            return {"ok": False, "message": f"JARVIS could not do that: {type(exc).__name__}"}
        if reply.get("close"):
            self.withdraw(f.id)
            return {"ok": True, "done": True}
        return {"ok": True, "done": False}

    def peek(self, form_id: str, token: str) -> dict | None:
        """The open form's cleaned spec (a copy), for the voice helper (ask_voice.py), only for the form id and its one-time token. None otherwise."""
        with self.lock:
            f = self.form
            if f is None or f.id != str(form_id) or not secrets.compare_digest(f.token, str(token or "")):
                return None
            return {"spec": f.spec, "busy": f.busy}

    def close(self, form_id: str, token: str) -> dict:
        with self.lock:
            f = self.form
            if f is None or f.id != str(form_id) or not secrets.compare_digest(f.token, str(token or "")):
                return {"ok": False, "message": "That form is no longer open."}
            self._finish(f, {"answered": False, "reason": "closed"})
            return {"ok": True}


def _to_path(value: Any, mapping: dict) -> Any:
    if isinstance(value, list):
        return [mapping.get(v, v) for v in value]
    return mapping.get(value, value)
