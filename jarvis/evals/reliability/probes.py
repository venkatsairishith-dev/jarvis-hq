"""Responsiveness probes: how long the face window takes to answer the owner, idle and under load (D59, owner: "the Windows widget is very non responsive").

Each probe goal is repeated like any other (N repeats). One repeat = one measurement set:
  input_latency      press a key N times in the chat box; each sample is event-to-next-frame in ms (drive/probe_ui.mjs); the repeat's value is its p90
  click_to_paint     press the pointer N times; same measure
  startup            stop the face, start it, and time (a) launch -> first input the page handled (`startup_interactive_s`) and (b) window shown ->
                     first input handled (`startup_input_ignored_s`: the seconds the owner can see a window that ignores him)
A repeat PASSES when its value is under the goal's limit (150 ms for latency, 3 s for startup interactive). The capability passes at >= 90% of repeats,
and the report also gives p50/p90 over all samples. LOADS make the measurement honest: the goal says what is running while the owner types
(a local model generating, an online stream, a PC task, a document read); if the load ended before the samples were taken the repeat is INVALID,
because a number measured while idle must not be credited to "while a model generates".

The measuring itself lives in two small Node scripts (drive/) and the Windows window poll (env.py); this module is the part that is testable
anywhere: loads, the repeat loop, the arithmetic, the thresholds. The `Sampler` is lent by the caller (NodeSampler on the laptop, a fake in tests).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from jarvis.plugins._shared import loopback_http

from . import stats
from .model import Goal

HERE = Path(__file__).resolve().parent
PROBE_UI = HERE / "drive" / "probe_ui.mjs"
PROBE_STARTUP = HERE / "drive" / "probe_startup.mjs"
GATEWAY = os.environ.get("JARVIS_GATEWAY_URL", "http://127.0.0.1:48950")


@dataclass
class ProbeResult:
    ok: bool
    value: float | None                  # this repeat's number (p90 ms, or seconds for startup)
    unit: str
    limit: float
    samples: list[float] = field(default_factory=list)
    invalid: str = ""                    # why the repeat does not count (load ended early, probe could not run)
    detail: str = ""


class Sampler(Protocol):
    def input_latency_ms(self, n: int, page: str) -> list[float]: ...
    def click_to_paint_ms(self, n: int, page: str) -> list[float]: ...
    def startup(self) -> dict: ...


# ------------------------------------------------------------------------------------------------------------------ loads
class Load:
    """Something heavy that runs while the probe takes its samples. `alive()` says whether it was still running when asked."""
    name = "idle"

    def start(self) -> None: ...
    def alive(self) -> bool:
        return True

    def wait_ready(self, timeout: float) -> bool:
        """True once the load is really producing work (the first streamed token arrived); a probe waits for it before sampling."""
        return True

    def stop(self) -> None: ...


class StreamLoad(Load):
    """A long streamed chat completion through the privacy gateway: the local model (default route for a plain request) or the online chain."""

    def __init__(self, name: str, online: bool = False, prompt: str = "Write a long, detailed story about a lighthouse keeper, at least 600 words.",
                 opener: Callable[..., Any] | None = None, url: str = GATEWAY):
        self.name, self.online, self.prompt, self._open, self.url = name, online, prompt, opener or loopback_http.urlopen, url
        self._stop, self._thread = threading.Event(), None
        self._done = threading.Event()
        self._ready = threading.Event()
        self.error = ""

    def _run(self) -> None:
        body = {"model": os.environ.get("JARVIS_AGENT_MODEL", "zai-org/GLM-5.3-Flash"), "stream": True, "max_tokens": 900, "temperature": 0.7,
                "messages": [{"role": "user", "content": self.prompt}]}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer jarvis-local"}
        if self.online:
            headers["X-Jarvis-Route"] = "online"
        req = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(body).encode(), headers=headers)
        try:
            with self._open(req, timeout=300) as r:
                for line in r:
                    if line.strip().startswith(b"data:") and b"[DONE]" not in line:
                        self._ready.set()
                    if self._stop.is_set():
                        break
        except Exception as e:  # noqa: BLE001 - a load that cannot start makes the repeat invalid, not the run crash
            self.error = type(e).__name__
        finally:
            self._done.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def alive(self) -> bool:
        return not self._done.is_set()

    def wait_ready(self, timeout: float) -> bool:
        return self._ready.wait(timeout)

    def stop(self) -> None:
        self._stop.set()


class DriverLoad(Load):
    """A goal given to JARVIS in the background (a PC task, a document read) through whichever driver the run uses."""

    def __init__(self, name: str, driver: Any, goal_text: str, timeout_s: float = 120.0):
        self.name, self.driver, self.goal_text, self.timeout_s = name, driver, goal_text, timeout_s
        self._done = threading.Event()
        self._thread = None

    def _run(self) -> None:
        try:
            self.driver.run(self.goal_text, self.timeout_s)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._done.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def alive(self) -> bool:
        return not self._done.is_set()


def make_load(kind: str, driver: Any = None, opener: Callable[..., Any] | None = None) -> Load:
    if kind in ("", "idle"):
        return Load()
    if kind == "local_model":
        return StreamLoad("local_model", online=False, opener=opener)
    if kind == "online_stream":
        return StreamLoad("online_stream", online=True, opener=opener)
    if kind == "pc_task" and driver is not None:
        return DriverLoad("pc_task", driver, "Open Notepad and type a short shopping list, then close it without saving.")
    if kind == "document_read" and driver is not None:
        return DriverLoad("document_read", driver, "What is on my screen right now? Read the text of the window in front.")
    raise ValueError(f"unknown load {kind!r}")


# ------------------------------------------------------------------------------------------------------------------ the Node sampler
class NodeSampler:
    """Samples through the Node scripts in drive/ (the face's debug port) and the Windows window poll for startup."""

    def __init__(self, node: str = "node", cfg: dict | None = None, env: Any = None, runner: Any = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.node, self.cfg, self.env, self._run, self.clock, self.sleep = node, cfg or {}, env, runner or subprocess.run, clock, sleep

    def _node_json(self, script: Path, *args: str, timeout: float = 120.0) -> dict:
        cwd = os.environ.get("JARVIS_FACE_DIR")
        proc = self._run([self.node, str(script), *args], capture_output=True, text=True, timeout=timeout, cwd=cwd, encoding="utf-8", errors="replace")
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("{")]
        if not lines:
            return {"harness_error": (proc.stderr or "no output")[:160]}
        try:
            return json.loads(lines[-1])
        except ValueError:
            return {"harness_error": "the probe printed something that is not JSON"}

    def input_latency_ms(self, n: int, page: str = "chat") -> list[float]:
        out = self._node_json(PROBE_UI, "input", str(n), page)
        if out.get("harness_error"):
            raise RuntimeError(out["harness_error"])
        return [float(x) for x in out.get("samples", [])]

    def click_to_paint_ms(self, n: int, page: str = "main") -> list[float]:
        out = self._node_json(PROBE_UI, "click", str(n), page)
        if out.get("harness_error"):
            raise RuntimeError(out["harness_error"])
        return [float(x) for x in out.get("samples", [])]

    def startup(self) -> dict:
        c = self.cfg.get("startup", {})
        if c.get("stop_cmd"):
            self._run(c["stop_cmd"], capture_output=True, text=True, timeout=60)
            self.sleep(float(c.get("after_stop_s", 3)))
        t_launch = self.clock()
        launcher = subprocess.Popen(c["launch_cmd"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) if c.get("launch_cmd") else None
        probe = subprocess.Popen([self.node, str(PROBE_STARTUP), str(c.get("timeout_s", 40)), c.get("page", "main")], stdout=subprocess.PIPE, text=True,
                                 cwd=os.environ.get("JARVIS_FACE_DIR"), encoding="utf-8", errors="replace")
        t_window = None
        title = (c.get("window_title") or "").lower()
        while probe.poll() is None:
            if t_window is None and self.env is not None and title:
                try:
                    if any(title in w.title.lower() for w in self.env.windows()):
                        t_window = self.clock()
                except Exception:  # noqa: BLE001
                    pass
            self.sleep(0.1)
        out_text, _ = probe.communicate(timeout=10)
        if launcher is not None:
            launcher.poll()
        lines = [ln for ln in (out_text or "").splitlines() if ln.strip().startswith("{")]
        row = json.loads(lines[-1]) if lines else {"harness_error": "no output"}
        if row.get("harness_error"):
            raise RuntimeError(row["harness_error"])
        handled = row["t_first_handled_ms"] / 1000.0
        res = {"interactive_s": round(handled - t_launch, 2), "page_s": round(row["t_page_ms"] / 1000.0 - t_launch, 2)}
        res["input_ignored_s"] = round(handled - t_window, 2) if t_window else None
        res["window_s"] = round(t_window - t_launch, 2) if t_window else None
        return res


# ------------------------------------------------------------------------------------------------------------------ one repeat
def run_probe(goal: Goal, sampler: Sampler, driver: Any = None, *, sleep: Callable[[float], None] = time.sleep, opener: Callable[..., Any] | None = None) -> ProbeResult:
    """One repeat of a probe goal. goal.probe = {probe, limit, unit, samples, load, page, warmup_s, metric}."""
    p = goal.probe
    kind, limit, unit = p["probe"], float(p["limit"]), p.get("unit", "ms")
    n = int(p.get("samples", 20))
    try:
        load = make_load(p.get("load", "idle"), driver, opener)
    except ValueError as e:
        return ProbeResult(False, None, unit, limit, invalid=str(e))
    load.start()
    try:
        if not load.wait_ready(float(p.get("ready_timeout_s", 90))):
            return ProbeResult(False, None, unit, limit, invalid=f"the {load.name} load never started producing" + (f" ({getattr(load, 'error', '')})" if getattr(load, "error", "") else ""))
        sleep(float(p.get("warmup_s", 1 if load.name != "idle" else 0)))
        if kind == "input_latency":
            samples = sampler.input_latency_ms(n, p.get("page", "chat"))
        elif kind == "click_to_paint":
            samples = sampler.click_to_paint_ms(n, p.get("page", "main"))
        elif kind == "startup":
            got = sampler.startup()
            metric = p.get("metric", "interactive_s")
            value = got.get(metric)
            if value is None:
                return ProbeResult(False, None, unit, limit, invalid=f"the startup probe could not read {metric}", detail=json.dumps(got)[:160])
            return ProbeResult(value < limit, float(value), unit, limit, [float(value)], detail=json.dumps(got)[:200])
        else:
            return ProbeResult(False, None, unit, limit, invalid=f"unknown probe {kind!r}")
        alive = load.alive()
    except Exception as e:  # noqa: BLE001
        return ProbeResult(False, None, unit, limit, invalid=f"the probe could not run ({type(e).__name__}: {str(e)[:100]})")
    finally:
        load.stop()
    if not samples:
        return ProbeResult(False, None, unit, limit, invalid="the probe returned no samples")
    if p.get("load", "idle") not in ("", "idle") and not alive:
        return ProbeResult(False, None, unit, limit, [float(x) for x in samples], invalid="the load ended before the samples were taken")
    q = stats.p90(samples)
    return ProbeResult(q < limit, round(q, 1), unit, limit, [float(x) for x in samples], detail=f"p50 {stats.p50(samples):.0f} {unit}, p90 {q:.0f} {unit}, worst {max(samples):.0f} {unit}")
