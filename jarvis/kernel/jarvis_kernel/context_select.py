"""Context selection for long tool output (2026-10-16): keep the lines that matter, verbatim and in order, instead of
cutting the text after its first N characters. OFF by default (JARVIS_CONTEXT_SELECT); "off" keeps the old head cut.

Modes
  off               the old behaviour: the first max_chars characters and a "[truncated: ...]" note.
  evidence          deterministic: the last and first lines, every line that carries evidence (errors, exceptions, stack
                    frames, exit codes, paths with line numbers, failures, denials) with its neighbours, and lines that
                    share words with the query, filled up to max_chars in that priority. No model.
  trim1-<point>     the vendored trim-1 line selector (jarvis/vendor/trim-1, a LightGBM model run by a small Go binary),
                    point = conservative | balanced | aggressive; then "evidence" if the result is still too long.

Rules every mode keeps (each has a test):
  - every kept line is a line of the input, byte for byte, in the original order; omissions become one marker line
    "[... N lines omitted ...]" and are listed exactly in `omitted` (0-based, inclusive ranges);
  - a text that fits is returned unchanged; so is anything that is not plain text the selector can split;
  - any error, timeout or unexpected output of trim-1 falls back to "evidence" (never to a lossy guess);
  - the result says which mode ran and carries the sha256 of the full input, so the full text can be matched/recovered;
  - selection is about what a model READS. It is never an authorization input: removed lines are not proof that they
    were irrelevant, and policy, approvals, verification and destructive decisions never look at the filtered text.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field

MODES = ("off", "evidence", "trim1-conservative", "trim1-balanced", "trim1-aggressive")
ENV_MODE = "JARVIS_CONTEXT_SELECT"
ENV_TRIM1 = "JARVIS_TRIM1_BIN"
HEAD_LINES, TAIL_LINES, NEIGHBOURS = 6, 30, 2
MARKER = "[... {n} lines omitted ...]"
_TRIM1_MARKER = re.compile(r"^… \d+ lines? …$")       # trim-1 writes "… 1 line …" for one line

EVIDENCE = re.compile(          # strong evidence: something went wrong, and where
    r"(?i)(traceback|exception|error\b|\berror|errno|fatal|panic|segfault|segmentation fault|abort|assert|"
    r"failed|failure|\bfail\b|denied|refused|forbidden|unauthori[sz]ed|not found|no such file|cannot|can't|couldn't|"
    r"timed? ?out|timeout|killed|oom|out of memory|exit (code|status)|exited with|returned non-zero|status [45]\d\d|"
    r"\b[45]\d\d (bad|not|internal|service|gateway|forbidden|unauthori)|quota|exceeded|crash|"
    r"^\s+at |\bat .+:\d+(:\d+)?\)?$|File \".+\", line \d+|\.(py|js|ts|tsx|go|rs|java|cs|cpp|c|h):\d+|"
    r"goroutine \d+|caused by|during handling of the above|the above exception|\w+(Error|Exception|Exit)\b)")
WEAK = re.compile(r"(?i)(warn|deprecat|retry|retrying|slow)")   # weak evidence: ranked after strong evidence and query hits
IDENT = re.compile(r"\b[A-Za-z][\w.]*[-_.][\w.-]*[A-Za-z0-9]\b")    # names like ledger-svc, payments_api, db.main
WORD = re.compile(r"[\w./\\:-]{3,}", re.UNICODE)


@dataclass
class Selection:
    text: str
    mode: str
    changed: bool
    total_lines: int
    kept_lines: int
    omitted: list = field(default_factory=list)      # [(first, last), ...] 0-based inclusive
    sha256: str = ""
    note: str = ""


def configured_mode() -> str:
    m = (os.environ.get(ENV_MODE) or "off").strip().lower()
    return m if m in MODES else "off"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _render(lines: list[str], keep: list[bool]) -> tuple[str, list]:
    out, omitted, i, n = [], [], 0, len(lines)
    while i < n:
        if keep[i]:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < n and not keep[j]:
            j += 1
        omitted.append((i, j - 1))
        out.append(MARKER.format(n=j - i))
        i = j
    return "\n".join(out), omitted


def _query_terms(query: str) -> set[str]:
    return {w.lower() for w in WORD.findall(query or "") if len(w) >= 3}


def evidence_select(text: str, query: str, max_chars: int) -> tuple[str, list, int]:
    """Deterministic selection; returns (text, omitted ranges, kept line count)."""
    lines = text.split("\n")
    n = len(lines)
    terms = _query_terms(query)
    evidence = [i for i, l in enumerate(lines) if EVIDENCE.search(l)]
    weak = [i for i, l in enumerate(lines) if not EVIDENCE.search(l) and WEAK.search(l)]
    hits = [i for i, l in enumerate(lines) if terms and any(t in l.lower() for t in terms)]
    # second hop: names that the errors mention (a crashed service, a failing module), followed to other lines
    names = []
    for i in evidence[::-1][:20]:
        names += [m.lower() for m in IDENT.findall(lines[i]) if len(m) >= 5 and not m[0].isdigit()]
    names = list(dict.fromkeys(names))[:12]
    hops = [i for i, l in enumerate(lines) if names and any(nm in l.lower() for nm in names)] if names else []
    order: list[int] = []
    order += list(range(max(0, n - TAIL_LINES), n))[::-1]          # the end first: results, final errors, exit codes
    order += list(range(min(HEAD_LINES, n)))                         # what was run
    rare = [i for i in evidence if len(evidence) <= 2 * TAIL_LINES or not WEAK.search(lines[i])]
    for i in rare[::-1] + [i for i in evidence[::-1] if i not in set(rare)]:   # evidence, latest first, with neighbours
        order += [i] + [k for d in range(1, NEIGHBOURS + 1) for k in (i - d, i + d) if 0 <= k < n]
    for i in hits:
        order += [i, i - 1, i + 1]
    order += hops[::-1]
    order += weak[::-1]
    seen, ranked = set(), []
    for i in order:
        if 0 <= i < n and i not in seen:
            seen.add(i)
            ranked.append(i)
    budget = max(0, max_chars)
    for _ in range(6):                                               # markers are paid for after the fact: re-plan
        keep, used = [False] * n, 0
        for i in ranked:
            cost = len(lines[i]) + 1
            if used + cost <= budget:
                keep[i] = True
                used += cost
        out, omitted = _render(lines, keep)
        if len(out) <= max_chars:
            return out, omitted, sum(keep)
        budget -= (len(out) - max_chars) + 64
    keep = [False] * n                                               # last resort: the tail only
    used = 0
    for i in range(n - 1, -1, -1):
        if used + len(lines[i]) + 1 + len(MARKER) + 8 > max_chars:
            break
        keep[i] = True
        used += len(lines[i]) + 1
    out, omitted = _render(lines, keep)
    return out, omitted, sum(keep)


def _trim1(text: str, query: str, point: str, timeout_s: float) -> tuple[str | None, str]:
    exe = os.environ.get(ENV_TRIM1, "")
    if not exe or not os.path.isfile(exe):
        return None, "trim-1 binary not configured"
    if not (query or "").strip():
        return None, "trim-1 needs a query"
    try:
        p = subprocess.run([exe, "--query", query[-8192:], "--point", point], input=text.encode("utf-8"),
                           capture_output=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"trim-1 did not run: {type(e).__name__}"
    if p.returncode != 0:
        return None, f"trim-1 exit {p.returncode}"
    try:
        return p.stdout.decode("utf-8"), "trim-1 ok"
    except UnicodeDecodeError:
        return None, "trim-1 returned non-UTF-8"


def _verify_subsequence(orig: list[str], out_lines: list[str], marker: re.Pattern) -> tuple[list[bool], bool]:
    """Map trim-1's output back onto the input: every non-marker line must be the next matching input line."""
    keep, j = [False] * len(orig), 0
    for line in out_lines:
        if marker.match(line):
            continue
        while j < len(orig) and orig[j] != line:
            j += 1
        if j == len(orig):
            return keep, False
        keep[j] = True
        j += 1
    return keep, True


def select(text: str, query: str = "", max_chars: int = 16000, mode: str | None = None,
           timeout_s: float = 2.0) -> Selection:
    mode = mode or configured_mode()
    if mode not in MODES:
        mode = "off"
    if not isinstance(text, str):
        return Selection(text="", mode=mode, changed=False, total_lines=0, kept_lines=0, note="not text")
    digest, lines = _sha(text), text.split("\n")
    n = len(lines)
    if len(text) <= max_chars:
        return Selection(text, mode, False, n, n, [], digest, "fits")
    if mode == "off":
        cut = text[:max_chars]
        return Selection(cut + f"\n[truncated: {len(text) - max_chars} more characters]", "off", True, n,
                         cut.count("\n") + 1, [], digest, "head cut")
    if "\x00" in text:
        cut = text[:max_chars]
        return Selection(cut + f"\n[truncated: {len(text) - max_chars} more characters]", "off", True, n,
                         cut.count("\n") + 1, [], digest, "binary-looking text: head cut")
    note = ""
    if mode.startswith("trim1-"):
        point = mode.split("-", 1)[1]
        out, note = _trim1(text.replace("\r\n", "\n"), query, point, timeout_s)
        if out is not None:
            out_lines = out.rstrip("\n").split("\n") if out.endswith("\n") and not text.endswith("\n") else out.split("\n")
            keep, ok = _verify_subsequence(text.replace("\r\n", "\n").split("\n"), out_lines, _TRIM1_MARKER)
            if not ok:
                note = "trim-1 output was not a subsequence of the input: ignored"
            else:
                kept_idx = [i for i, k in enumerate(keep) if k]
                sub = "\n".join(lines[i] for i in kept_idx)
                if len(sub) + 64 <= max_chars or not kept_idx:
                    rendered, omitted = _render(lines, keep)
                    if len(rendered) <= max_chars:
                        return Selection(rendered, mode, True, n, sum(keep), omitted, digest, note)
                # still too long: evidence selection over trim-1's choice, then mapped back to input positions
                sel_text, sel_omitted, _k = evidence_select(sub, query, max_chars)
                keep2 = [False] * n
                kept_set = set(range(len(kept_idx)))
                for a, b in sel_omitted:
                    kept_set -= set(range(a, b + 1))
                for s in kept_set:
                    keep2[kept_idx[s]] = True
                rendered, omitted = _render(lines, keep2)
                if len(rendered) <= max_chars:
                    return Selection(rendered, mode, True, n, sum(keep2), omitted, digest, note + "; then evidence")
                note += "; still too long"
        mode_used = "evidence"
    else:
        mode_used = "evidence"
    out, omitted, kept = evidence_select(text, query, max_chars)
    return Selection(out, mode_used if mode == "evidence" else f"{mode}->evidence", True, n, kept, omitted, digest,
                     note or "evidence")
