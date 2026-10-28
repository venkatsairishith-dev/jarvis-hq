"""D81: an EXTERNAL deadline around one goal's `driver.run(...)` call, for the hang the owner saw happen live ("it always gets stuck at some point").

Why this exists, in one line: `runner.run_one()` already passes `g.timeout_s` to the driver, but a driver (or something further down the face/kernel/node
chain it calls into) can block past that timeout anyway, because the thing actually stuck has NO internal timeout of its own (a win32 call, a streamed
model request that never finishes, a child process that never exits, a dialog nothing ever dismisses). When that happens today nothing advances, nothing
is logged, and a human has to notice and kill the harness by hand (`results/STATUS.md`'s own "stuck/timeout" notes). This module adds a second, OUTER
deadline that fires regardless of whether the driver's own timeout works, so the harness itself can never be the thing that hangs:

    run_with_recovery(driver, env, goal_text, timeout_s, attended, pre_hwnds=..., closable=..., ignore=..., grace_s=60.0, results_dir=..., goal_id=...)

`driver.run(...)` is called on a background thread; the caller `.join()`s it for `timeout_s + grace_s`. If it finishes in time, its own Observation is
returned unchanged (this module changes NOTHING about the ordinary case). If it does not, the still-running thread is abandoned (daemon, never joined
again: Python cannot kill a thread, and that is fine - nothing here waits on it a second time) and the harness:

  1. DIAGNOSES, read-only: the window list (content-free: process, window class, hung/cloaked/minimized/foreground flags - D65's richer `jarvis_node.windows`
     when it is importable, else the plain `env.windows()` every environment already has), a screen read of the one new window worth looking at (through
     the SAME `screen_cascade.read` cascade the desktop plugin uses - this module never reimplements OCR, it only lends the cascade a UIA rung from
     `env.window_text` and an OCR rung from `jarvis_node.vision` when that is importable), and the tail of whatever content-free logs are configured
     (the gateway's own `JARVIS_LATENCY_LOG` - already filtered to numbers/bools/short safe-charset strings by `gateway.write_latency_line` - plus any
     node/tool-server log the laptop names, read-only, skipped silently when no path is configured: this module never guesses one).
  2. CLASSIFIES the cause from that evidence alone, into one of `hung_window`, `dialog_stuck`, `no_window_model_silent`, `process_zombie`, `unknown`
     (`classify_stuck`): never a guess, `unknown` says so plainly.
  3. WRITES one stuck-report JSON under `results_dir/stuck/` (content-free: never the window's title text, never the screen reading's own text, only
     counts, flags and the classification - proven in tests/test_rel_watchdog.py).
  4. RECOVERS, narrowly: a `hung_window` or `dialog_stuck` window is closed ONLY when it is new (not in `pre_hwnds`) and matches the SAME
     `rules.cleanup.closable` spec `runner.py` already uses for ordinary cleanup (`checks._match`, reused, never a broader rule); `no_window_model_silent`
     or `process_zombie` call `driver.cancel_all()` defensively and, only if the driver exposes a live child process it is still tracking
     (`driver.current_proc`, an optional port: no driver on this branch populates it yet - see `01_RESEARCH_AUDIT.md` / D81's follow-up), terminate
     JUST that one process after a short grace period. `unknown` recovers nothing and is reported as exactly that: "stuck, cause unknown".
  5. RETURNS an `Observation(status="harness_error", harness_error="stuck (<cause>): <detail>", ...)`, the SAME shape `driver.run()` always returns, so
     `run_one`'s existing code (verify skipped, `classify.classify` -> INVALID, `cleanup` in the `finally`) needs no change at all.

Standard library only; every external read (window lister, screen reader, log paths) is an injectable port with a safe default, so every branch here is
tested with fakes (tests/test_rel_watchdog.py) without ever needing a real hang on a real PC.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import checks
from .records import Observation

DEFAULT_GRACE_S = 60.0
LOG_TAIL_LINES = 50
STUCK_SUBDIR = "stuck"
DIALOG_CLASS = "#32770"          # the classic Win32 dialog window class (diag.py's WINDOW_TEXT and ladder.py both key off it the same way)

HUNG_WINDOW, DIALOG_STUCK, NO_WINDOW_MODEL_SILENT, PROCESS_ZOMBIE, UNKNOWN = (
    "hung_window", "dialog_stuck", "no_window_model_silent", "process_zombie", "unknown")
CAUSES = (HUNG_WINDOW, DIALOG_STUCK, NO_WINDOW_MODEL_SILENT, PROCESS_ZOMBIE, UNKNOWN)

_SAFE_STR = re.compile(r"[A-Za-z0-9_.:\-/ ]*")
_SAFE_STR_MAX = 40
_ID = re.compile(r"[^A-Za-z0-9_-]")           # no dots either: ".." must never survive into a filename, even with no path separator left to traverse with


@dataclass
class StuckCause:
    kind: str
    detail: str = ""                      # a short, closed-vocabulary phrase; never screen text, never the owner's words
    window: dict | None = None            # the offending window, already redacted to content-free fields (see `_normalize_window`)


@dataclass
class Diagnosis:
    windows: list[dict] = field(default_factory=list)       # every window at the moment of the hang (content-free)
    new_windows: list[dict] = field(default_factory=list)    # the ones not in `pre_hwnds`
    screen_read: dict = field(default_factory=dict)          # {source, confidence, chars} - never the text itself
    logs: dict[str, list[dict]] = field(default_factory=dict)
    process_alive: bool = False


# ------------------------------------------------------------------------------------------------------------------------ content-free filtering
def _safe_value(v: Any) -> bool:
    return isinstance(v, bool) or isinstance(v, (int, float)) or (isinstance(v, str) and len(v) <= _SAFE_STR_MAX and _SAFE_STR.fullmatch(v) is not None)


def _safe_row(row: Any) -> dict:
    """A log row kept ONLY if every surviving field passes the gateway's own content-free rule (`write_latency_line`): numbers, bools, or a short
    safe-charset string. Anything else (an unparseable line, a field that fails the test) becomes a bare count, so nothing it held can leak into the
    stuck-report. Applied even to the gateway's own log: a double filter is cheap and this module must never be the leak."""
    if not isinstance(row, dict):
        return {"raw_redacted": True}
    return {k: v for k, v in row.items() if _safe_value(v)}


def tail_log(path: str, n: int = LOG_TAIL_LINES) -> list[dict]:
    """The last `n` lines of a log file as content-free rows, or `[]` when `path` is empty or unreadable (never raises, never guesses a path)."""
    if not path:
        return []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    for raw in text.splitlines()[-n:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            out.append({"raw_redacted": True, "chars": len(raw)})
            continue
        out.append(_safe_row(row))
    return out


def _gateway_log_path() -> str:
    """The gateway's content-free timing log (D60's `JARVIS_LATENCY_LOG`, read through the existing report tool's own path rule, never gateway.py
    itself: that file is reserved, and the path formula is a one-line, already-published convention)."""
    try:
        from jarvis.tools.latency_report import default_log
        return str(default_log())
    except Exception:  # noqa: BLE001 - no path is better than a guessed one
        return os.environ.get("JARVIS_LATENCY_LOG", "")


def log_tails(*, gateway: str | None = None, node: str | None = None, tool_server: str | None = None) -> dict[str, list[dict]]:
    """Each named log's tail, or left out entirely when its path is not configured and cannot be found by an existing convention (`node` and
    `tool_server` have no standing file-log convention in this repo today - see `01_RESEARCH_AUDIT.md` - so they are skipped unless the caller names
    a path; `gateway` defaults to the one path this codebase already publishes)."""
    paths = {"gateway": gateway if gateway is not None else _gateway_log_path(),
              "node": node if node is not None else os.environ.get("JARVIS_NODE_LOG", ""),
              "tool_server": tool_server if tool_server is not None else os.environ.get("JARVIS_TOOLSERVER_LOG", "")}
    return {name: tail_log(p) for name, p in paths.items() if p}


# ------------------------------------------------------------------------------------------------------------------------ window diagnosis
def _default_window_lister(env: Any) -> list[dict]:
    """D65's richer listing (process, window class, hung/cloaked/minimized/foreground) when `jarvis_node.windows` is importable and this is Windows;
    else the plain `{hwnd, pid, process, title}` every `env.windows()` already gives. Never raises."""
    try:
        from jarvis.node.jarvis_node import windows as node_windows
        if node_windows.ON_WINDOWS:
            return list(node_windows.list_windows())
    except Exception:  # noqa: BLE001
        pass
    try:
        return [{"hwnd": w.hwnd, "pid": w.pid, "process": w.process, "title": w.title} for w in (env.windows() or [])]
    except Exception:  # noqa: BLE001
        return []


def _normalize_window(w: dict) -> dict:
    """A window row, kept to exactly what is safe to write into the stuck-report AND enough to classify by: never the title text itself, only
    whether there is one (`has_title`)."""
    return {"hwnd": w.get("hwnd"), "process": str(w.get("process") or "")[:40], "class": str(w.get("class") or "")[:40],
            "has_title": bool(w.get("title")), "hung": bool(w.get("hung")), "cloaked": bool(w.get("cloaked")),
            "minimized": bool(w.get("minimized")), "foreground": bool(w.get("foreground"))}


def list_windows(env: Any, window_lister: Callable[[Any], list[dict]] | None = None) -> list[dict]:
    rows = (window_lister or _default_window_lister)(env)
    return [_normalize_window(w) for w in rows if isinstance(w, dict)]


def _find_env_win(env: Any, hwnd: int):
    """The real `env.Win` object for `hwnd` (needed for `checks._match` and `env.close_window`, which both expect the attribute-based `Win` the
    harness's own `env.windows()` returns - never the richer node dict, which has a different shape)."""
    try:
        for w in env.windows() or []:
            if getattr(w, "hwnd", None) == hwnd:
                return w
    except Exception:  # noqa: BLE001
        return None
    return None


# ------------------------------------------------------------------------------------------------------------------------ screen diagnosis
def default_screen_reader(env: Any, win: Any) -> dict:
    """One reading of the window the hang left behind, through `screen_cascade.read` (D59 lever d / D65's own cascade): a UIA rung from
    `env.window_text` (wired on every environment already) plus an OCR rung from `jarvis_node.vision`'s OWN `capture`/`ocr` functions when that module
    is importable - this never reimplements OCR, it only lends the cascade a second opinion. Returns counts only: `{source, confidence, chars}`,
    never the text itself (consistent with D61/D73: the window's content stays local and is not written anywhere by this module)."""
    rungs: dict[str, Callable[[], str]] = {"uia": lambda: env.window_text(win)}
    try:
        from jarvis.node.jarvis_node import vision as node_vision
    except Exception:  # noqa: BLE001
        node_vision = None
    proc = getattr(win, "process", "") or (win.get("process") if isinstance(win, dict) else "")
    if node_vision is not None and proc:
        def _ocr() -> str:
            png, size = node_vision.capture(proc)
            try:
                lines = node_vision.ocr(png, size.get("scale", 1))
            finally:
                try:
                    os.remove(png)
                except OSError:
                    pass
            return "\n".join(str(ln.get("text", "")) for ln in lines)
        rungs["ocr"] = _ocr
    from jarvis.plugins._shared import screen_cascade
    reading = screen_cascade.read(rungs, cross_check=False)
    return {"source": reading.source, "confidence": reading.confidence, "chars": len(reading.text)}


def read_screen(env: Any, new_windows: list[dict], screen_reader: Callable[[Any, Any], dict] | None = None) -> dict:
    """The foreground new window, else the first new window, else nothing (no window worth reading is not a failure). Never raises."""
    if not new_windows:
        return {}
    target = next((w for w in new_windows if w.get("foreground")), new_windows[0])
    hwnd = target.get("hwnd")
    if hwnd is None:
        return {}
    reader = screen_reader or default_screen_reader
    try:
        return reader(env, _WinRef(hwnd, target.get("process", ""))) or {}
    except Exception as e:  # noqa: BLE001 - a failed read is evidence too ("could not read it"), never a crash
        return {"error": type(e).__name__}


class _WinRef:
    """The minimal stand-in `default_screen_reader` needs (`.process`, used to pick the OCR target); `env.window_text` takes it as `win` and most
    `env` implementations only read `.hwnd`/`.process` off it, exactly like the real `env.Win`."""

    def __init__(self, hwnd: int, process: str):
        self.hwnd, self.process = hwnd, process


# ------------------------------------------------------------------------------------------------------------------------ classification
def classify_stuck(new_windows: list[dict], *, log_quiet: bool, process_alive: bool) -> StuckCause:
    """The cause, from the evidence ALONE - never a guess. Order matters: a window that is actually flagged hung or looks like an unanswered dialog
    is reported as that even when a tracked process also happens to be alive (closing/cancelling the window is still the right first move)."""
    hung = [w for w in new_windows if w.get("hung")]
    if hung:
        return StuckCause(HUNG_WINDOW, "a new window is flagged not-responding by Windows", window=hung[0])
    dialogish = [w for w in new_windows if w.get("class") == DIALOG_CLASS]
    if dialogish:
        return StuckCause(DIALOG_STUCK, "a new dialog window appeared with nothing to click it", window=dialogish[0])
    if not new_windows:
        if process_alive:
            return StuckCause(PROCESS_ZOMBIE, "no new window; a child process from this run is still alive")
        if log_quiet:
            return StuckCause(NO_WINDOW_MODEL_SILENT, "no new window; the gateway log shows no recent activity")
    return StuckCause(UNKNOWN, "stuck, cause unknown: the evidence did not match a known pattern")


def _log_quiet(logs: dict[str, list[dict]], since_wall: float) -> bool:
    """True when nothing in the known logs was written at or after the goal started (a hint that the model/network side, not a window, is stuck)."""
    rows = [r for name in ("gateway", "node", "tool_server") for r in logs.get(name) or []]
    return not any(isinstance(r.get("t"), (int, float)) and r["t"] >= since_wall - 1 for r in rows)


def _process_alive(proc: Any) -> bool:
    try:
        return proc is not None and proc.poll() is None
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------------------------------------------------------------ the stuck-report file
def write_stuck_report(results_dir: Path | str | None, goal_id: str, rep: int, cause: StuckCause, diag: Diagnosis, *, clock: Callable[[], float] = time.time) -> str:
    """One JSON file under `<results_dir>/stuck/`, or "" when `results_dir` is not given. Content-free by construction: `diag.windows` /
    `diag.new_windows` are already normalized (no titles), `diag.screen_read` never holds text, `diag.logs` is already filtered."""
    if not results_dir:
        return ""
    folder = Path(results_dir) / STUCK_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    safe_id = _ID.sub("_", str(goal_id or "goal"))[:60]
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))
    path = folder / f"{safe_id}-rep{int(rep)}-{ts}.json"
    payload = {"schema": 1, "goal": safe_id, "rep": int(rep), "ts": round(clock(), 3), "cause": cause.kind, "detail": cause.detail,
               "window": cause.window, "windows": diag.windows, "new_windows": diag.new_windows, "screen_read": diag.screen_read, "logs": diag.logs}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


# ------------------------------------------------------------------------------------------------------------------------ recovery
CANCEL_DEADLINE_S = 15.0


def bounded(fn: Callable[[], Any], seconds: float = CANCEL_DEADLINE_S) -> bool:
    """Runs `fn()` on a daemon thread and waits at most `seconds`: True when it finished without raising, False when it raised or was still running (and is abandoned).
    For the calls the runner makes AFTER a hang, to the same peer that hung (`driver.cancel_all()` asks the tool server what is still running): a server that
    keeps sending a byte every few seconds defeats a per-read socket timeout, so that one call hung the whole suite right after the watchdog had recovered the
    goal (laptop 2026-10-28, a local server that trickled keep-alive bytes: stuck report written at +185 s, runner still blocked 5 minutes later)."""
    ok: list[bool] = []

    def _run() -> None:
        try:
            fn()
            ok.append(True)
        except Exception:  # noqa: BLE001 - the caller treats a failed call like a skipped one
            pass

    th = threading.Thread(target=_run, name="reliability-bounded-call", daemon=True)
    th.start()
    th.join(seconds)
    return bool(ok)


def recover(env: Any, driver: Any, cause: StuckCause, *, closable: list[dict], ignore: tuple[str, ...] = (), pre_hwnds: set[int] = frozenset(),
            process_grace_s: float = 5.0, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Narrow, reversible recovery: closes at most ONE window (new, and matching the SAME closable rule `runner.py`'s own cleanup uses), or cancels
    the driver call and, only if it is still tracking a live child process after a short grace period, ends JUST that process. Never raises."""
    closed_hwnd = None
    if cause.kind in (HUNG_WINDOW, DIALOG_STUCK) and cause.window:
        hwnd = cause.window.get("hwnd")
        if isinstance(hwnd, int) and hwnd not in pre_hwnds:
            win = _find_env_win(env, hwnd)
            if win is not None and win.process.lower() not in {p.lower() for p in ignore} and any(checks._match(win, spec) for spec in closable):
                try:
                    env.close_window(win)
                    closed_hwnd = hwnd
                except Exception:  # noqa: BLE001 - a failed close is not fatal to the suite
                    pass
    cancelled = terminated = False
    if cause.kind in (NO_WINDOW_MODEL_SILENT, PROCESS_ZOMBIE):
        cancelled = bounded(driver.cancel_all)       # a peer that hung once can hang the cancel too: never wait for it past a short deadline
        proc = getattr(driver, "current_proc", None)
        if proc is not None:
            sleep(process_grace_s)
            try:
                if proc.poll() is None:
                    proc.kill()
                    terminated = True
            except Exception:  # noqa: BLE001
                pass
    return {"closed_hwnd": closed_hwnd, "cancelled": cancelled, "terminated_process": terminated}


# ------------------------------------------------------------------------------------------------------------------------ the external deadline itself
def run_with_recovery(driver: Any, env: Any, goal_text: str, timeout_s: float, attended: bool, *, pre_hwnds: set[int], closable: list[dict],
                       ignore: tuple[str, ...] = (), grace_s: float = DEFAULT_GRACE_S, results_dir: Path | str | None = None, goal_id: str = "",
                       rep: int = 1, window_lister: Callable[[Any], list[dict]] | None = None, screen_reader: Callable[[Any, Any], dict] | None = None,
                       log_paths: dict | None = None, clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                       sleep: Callable[[float], None] = time.sleep, process_grace_s: float = 5.0) -> Observation:
    """Wraps `driver.run(goal_text, timeout_s, attended)` with a HARD external deadline of `timeout_s + grace_s`, on a background thread. The normal
    case (the driver returns within its own timeout) is untouched: its Observation comes back exactly as `driver.run()` made it. A hang past the
    external deadline is diagnosed, classified, reported, and recovered from (see the module docstring); the thread itself is abandoned (it is a
    daemon thread and is never joined a second time - Python cannot kill a thread, and nothing here needs to: the recovery acts on the PC, not on the
    thread), and an `Observation(status="harness_error", ...)` is returned so `run_one`'s existing code treats it exactly like any other harness
    trouble (INVALID, `harness=True`, the goal's own cleanup still runs, the suite moves on to the next goal)."""
    result: dict[str, Any] = {}

    def _target() -> None:
        try:
            result["obs"] = driver.run(goal_text, timeout_s, attended)
        except Exception as e:  # noqa: BLE001 - the thread must never crash silently; the caller turns this into an Observation
            result["exc"] = e

    th = threading.Thread(target=_target, name="reliability-goal-driver", daemon=True)
    t0, started_wall = clock(), wall()
    th.start()
    th.join(timeout_s + grace_s)
    if not th.is_alive():
        if "exc" in result:
            exc = result["exc"]
            return Observation(status="harness_error", harness_error=f"{type(exc).__name__}: {str(exc)[:140]}", seconds=round(clock() - t0, 2))
        obs = result.get("obs")
        if obs is not None:
            return obs
        return Observation(status="harness_error", harness_error="the driver returned nothing", seconds=round(clock() - t0, 2))

    # the external deadline fired: driver.run() ignored (or outran) its own internal timeout. Diagnose, classify, report, recover, move on.
    windows = list_windows(env, window_lister)
    new_windows = [w for w in windows if w.get("hwnd") not in pre_hwnds]
    screen_read = read_screen(env, new_windows, screen_reader)
    logs = log_tails(**(log_paths or {}))
    process_alive = _process_alive(getattr(driver, "current_proc", None))
    diag = Diagnosis(windows=windows, new_windows=new_windows, screen_read=screen_read, logs=logs, process_alive=process_alive)
    cause = classify_stuck(new_windows, log_quiet=_log_quiet(logs, started_wall), process_alive=process_alive)
    report_path = write_stuck_report(results_dir, goal_id, rep, cause, diag, clock=wall)
    recover(env, driver, cause, closable=closable, ignore=ignore, pre_hwnds=pre_hwnds, process_grace_s=process_grace_s, sleep=sleep)
    detail = cause.detail or "stuck, cause unknown"
    return Observation(status="harness_error", harness_error=(f"stuck ({cause.kind}): {detail}")[:220], seconds=round(clock() - t0, 2),
                       raw={"stuck_report": report_path, "stuck_cause": cause.kind})
