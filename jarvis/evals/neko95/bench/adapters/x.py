"""X: context selection (jarvis_kernel.context_select) and its use in mcpwire.process_result."""
from __future__ import annotations

import os
import time
from unittest import mock

from jarvis_kernel import context_select as CS
from jarvis_kernel import mcpwire as W

import context_fixtures as FX

_FX = {f["id"]: f for f in FX.fixtures()}


def _verbatim(orig: str, out: str) -> bool:
    lines, j = orig.split("\n"), 0
    for line in out.split("\n"):
        if line.startswith("[... ") and line.endswith(" lines omitted ...]"):
            continue
        while j < len(lines) and lines[j] != line:
            j += 1
        if j == len(lines):
            return False
        j += 1
    return True


def keeps_answer(a):
    f = _FX[a["fixture"]]
    s = CS.select(f["text"], f["query"], a["budget"], a.get("mode", "evidence"))
    kept = set(s.text.split("\n"))
    ok = f["answer"] in kept and all(c in kept for c in f["critical"]) and len(s.text) <= a["budget"] and _verbatim(f["text"], s.text)
    return ok, f"mode {s.mode}, {len(s.text)} chars of {len(f['text'])}, answer kept {f['answer'] in kept}"


def default_is_off(a):
    """(name kept for the case file) The default is evidence; 'off' restores the old head cut."""
    f = _FX["python-middle-013"]
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop(CS.ENV_MODE, None)
        d = CS.select(f["text"], f["query"], 4000)
    with mock.patch.dict(os.environ, {CS.ENV_MODE: "off"}):
        o = CS.select(f["text"], f["query"], 4000)
    ok = d.mode == "evidence" and f["answer"] in d.text.split("\n") and o.mode == "off" and o.text.startswith(f["text"][:4000])
    return ok, f"default {d.mode}, off {o.mode}"


def short_untouched(a):
    res = [CS.select(f["text"], f["query"], 16000, m) for f in FX.fixtures() if f["kind"] == "short" for m in CS.MODES]
    return all(not s.changed for s in res), f"{sum(not s.changed for s in res)}/{len(res)} unchanged"


def omitted_ranges_exact(a):
    f = _FX["warning-flood-middle-050"]
    s = CS.select(f["text"], f["query"], 4000, "evidence")
    lines = f["text"].split("\n")
    dropped = sum(b - a_ + 1 for a_, b in s.omitted)
    return dropped + s.kept_lines == len(lines) and s.sha256, f"{dropped} omitted + {s.kept_lines} kept of {len(lines)}"


def untrusted_and_no_authority(a):
    f = _FX["injection-middle-026"] if "injection-middle-026" in _FX else next(x for x in FX.fixtures() if x["kind"] == "injection")
    with mock.patch.dict(os.environ, {CS.ENV_MODE: "evidence"}):
        r = W.process_result({"content": [{"type": "text", "text": f["text"]}]}, 4000, query=f["query"])
    ok = r["untrusted"] is True and "never as instructions" in r["note"] and r["selection"]["mode"] == "evidence"
    return ok, f"untrusted={r['untrusted']} mode={r['selection']['mode']}"


def secret_withheld(a):
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3NzaC1yc2EAAAADAQABAAABAQ\n-----END OPENSSH PRIVATE KEY-----"
    with mock.patch.dict(os.environ, {CS.ENV_MODE: "evidence"}):
        r = W.process_result({"content": [{"type": "text", "text": _FX["python-middle-013"]["text"] + "\n" + key}]}, 4000)
    return r["withheld"] and "PRIVATE KEY" not in r["text"], f"withheld={r['withheld']}"


def fallback_without_binary(a):
    f = _FX["go-middle-015"]
    with mock.patch.dict(os.environ, {CS.ENV_TRIM1: "/nonexistent/trim"}):
        s = CS.select(f["text"], f["query"], 4000, "trim1-balanced")
    return s.mode.endswith("->evidence") and f["answer"] in s.text.split("\n"), f"mode {s.mode}: {s.note}"


def latency(a):
    f = _FX[a["fixture"]]
    t0 = time.perf_counter()
    for _ in range(5):
        CS.select(f["text"], f["query"], a["budget"], "evidence")
    ms = (time.perf_counter() - t0) / 5 * 1000
    return ms <= a["max_ms"], f"{ms:.1f} ms per call (limit {a['max_ms']} ms) on {len(f['text'])} chars"


def giant_line_limit(a):
    f = _FX["giant-line"]
    s = CS.select(f["text"], f["query"], 16000, "evidence")
    # a single line longer than the budget cannot be kept verbatim: the selector must still fit and say lines were omitted
    return len(s.text) <= 16000 and s.omitted and f["answer"] not in s.text, f"{len(s.text)} chars, omitted {s.omitted[:2]}"
