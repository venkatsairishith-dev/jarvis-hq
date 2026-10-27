"""D77: reading a problem statement file into PROBLEMS and SAMPLE CASES. Pure functions, no I/O beyond one small data file (config/problemset.json).

    split_problems(text, rules)      -> Split(problems=[Problem(number, title, text)], common, rule)   numbering rules are DATA
    extract_samples(text, rules)     -> [Sample(input, output)]                                       verbatim, for testing
    detect_language(text, rules)     -> language id or ""                                             what the problem says
    slug(title)                      -> ascii file-name part

The text is UNTRUSTED data (it came from a file, a scan or a screen): nothing here runs it, follows it or lets it change the task. Hindi and Telugu headers are
kept as they are (they are only matched). A rule that is broken in the data file is skipped.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILE = Path(os.environ.get("JARVIS_PROBLEMSET_FILE") or Path(__file__).resolve().parents[2] / "config" / "problemset.json")

_DIGITS = {ord(c): str(i) for base in (0x0966, 0x0C66) for i, c in enumerate(chr(base + k) for k in range(10))}      # Devanagari and Telugu digits

FLOOR_NUMBERING = [
    {"id": "word_number", "single": True,
     "rx": r"^\s*(?:#{1,4}\s*)?(?:problem|prob|question|exercise|task|program|practical|experiment|assignment|lab)\s*(?:no\.?|number|#)?\s*(?P<num>\d{1,3})\b\s*[:.)\-–—]*\s*(?P<title>.*)$"},
    {"id": "q_short", "single": True, "rx": r"^\s*(?:#{1,4}\s*)?Q\s?\.?\s?(?P<num>\d{1,3})(?:\s*[:.)\-–—]\s*|\s+)(?P<title>\S.*)?$"},
    {"id": "numbered", "single": False, "rx": r"^\s*\(?(?P<num>\d{1,3})\s*[.)]\s+(?P<title>\S.*)$"},
]
FLOOR_MARKERS = {
    "section": r"^\s*(?:#{1,4}\s*)?(?:sample|example|test\s*case|testcase)\b.*$",
    "input": r"^\s*(?:#{1,4}\s*)?(?:(?:sample|example|test\s*case)\s*)?(?:input|inp)\s*(?:#?\s*\d+)?\s*[:=\-]?\s*(?P<rest>.*)$",
    "output": r"^\s*(?:#{1,4}\s*)?(?:(?:sample|example|test\s*case)\s*)?(?:expected\s+)?(?:output|out)\s*(?:#?\s*\d+)?\s*[:=\-]?\s*(?P<rest>.*)$",
    "not_a_sample": r"(?:format|description|constraints?|specification|explanation|note)\b",
    "stop": r"^\s*(?:#{1,4}\s*)?(?:explanation|note|notes|constraints?|follow[- ]?up|hint|solution|approach)\b",
}


@dataclass
class Problem:
    number: int
    title: str
    text: str                      # the statement as written (header line included), capped


@dataclass
class Split:
    problems: list[Problem]
    common: str = ""               # instructions before the first problem ("Write all programs in C"), when short
    rule: str = ""                 # the numbering rule that matched, "" = the whole file is one problem


@dataclass
class Sample:
    input: str
    output: str


@dataclass
class Rules:
    raw: dict
    numbering: list[tuple[str, "re.Pattern[str]", bool]]
    markers: dict[str, "re.Pattern[str]"]
    languages: list[tuple[str, "re.Pattern[str]"]]
    max_statement: int = 6000
    max_common: int = 800
    max_samples: int = 8
    problems: list[str] = field(default_factory=list)       # entries of the data file that were skipped, with why


def _compile(rx: Any, flags: int = re.I) -> "re.Pattern[str] | None":
    if not isinstance(rx, str):
        return None
    try:
        return re.compile(rx, flags)
    except re.error:
        return None


def load_config(path: Path | str | None = None) -> dict:
    p = Path(path) if path else CONFIG_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_rules(cfg: dict | None = None) -> Rules:
    cfg = load_config() if cfg is None else cfg
    bad: list[str] = []
    numbering = []
    for r in (cfg.get("numbering") if isinstance(cfg.get("numbering"), list) else None) or FLOOR_NUMBERING:
        pat = _compile(r.get("rx") if isinstance(r, dict) else None, re.I | re.M)
        if pat is None or "num" not in pat.groupindex:
            bad.append(f"numbering rule {r.get('id') if isinstance(r, dict) else '?'} skipped")
            continue
        numbering.append((str(r.get("id") or "rule"), pat, bool(r.get("single", False))))
    if not numbering:
        numbering = [(r["id"], re.compile(r["rx"], re.I | re.M), r["single"]) for r in FLOOR_NUMBERING]
    marks_cfg = cfg.get("sample_markers") if isinstance(cfg.get("sample_markers"), dict) else {}
    markers = {}
    for k, floor in FLOOR_MARKERS.items():
        pat = _compile(marks_cfg.get(k), re.I)
        if pat is None:
            if k in marks_cfg:
                bad.append(f"sample marker {k} skipped")
            pat = re.compile(floor, re.I)
        markers[k] = pat
    langs = []
    for r in cfg.get("language_in_statement") if isinstance(cfg.get("language_in_statement"), list) else []:
        pat = _compile(r.get("rx") if isinstance(r, dict) else None, re.I)
        if pat is not None and isinstance(r.get("language"), str):
            langs.append((r["language"], pat))
    if not langs:
        langs = [("cpp", re.compile(r"\bc\+\+|\bcpp\b", re.I)), ("java", re.compile(r"\bjava\b", re.I)), ("python", re.compile(r"\bpython\b", re.I))]

    def num(key: str, default: int) -> int:
        v = cfg.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else default
    return Rules(cfg, numbering, markers, langs, num("max_statement_chars", 6000), num("max_common_chars", 800), num("max_samples", 8), bad)


# ------------------------------------------------------------------------------------------------------------- splitting
def _num(s: str) -> int:
    try:
        return int(str(s).translate(_DIGITS))
    except ValueError:
        return 0


def _strip(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or "")).replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def split_problems(text: str, rules: Rules | None = None) -> Split:
    """The problems of one file. The EXPLICIT rules (marked "single" in the data file: "Problem 2", "Q1.", a Hindi or Telugu header) are applied together; one header is
    enough for them. Otherwise the plain numbered-list rules, which need two headers with rising numbers. Nothing matched: the whole file is one problem. A short
    preamble is kept as `common`."""
    rules = rules or load_rules()
    t = _strip(text).strip("\n")
    if not t.strip():
        return Split([])
    lines = t.split("\n")

    def heads_of(pats: list["re.Pattern[str]"]) -> list[tuple[int, int, str]]:
        heads: list[tuple[int, int, str]] = []
        for i, line in enumerate(lines):
            if len(line) > 400:
                continue
            for pat in pats:
                m = pat.match(line)
                if m:
                    n = _num(m.group("num"))
                    if n > 0:
                        heads.append((i, n, (m.groupdict().get("title") or "").strip()))
                        break
        rising = [h for k, h in enumerate(heads) if k == 0 or h[1] > heads[k - 1][1]]
        return heads if len(rising) == len(heads) else _longest_rising(heads)
    explicit = [(rid, pat) for rid, pat, single in rules.numbering if single]
    if explicit:                                            # "Problem 1", "Q2.", a Hindi or a Telugu header: all the explicit rules together (a file may mix them)
        heads = heads_of([p for _, p in explicit])
        if len(heads) >= 1:
            used = {rid for rid, pat in explicit if any(pat.match(lines[h[0]]) for h in heads)}
            return _build(lines, heads, "+".join(sorted(used)) or explicit[0][0], rules)
    for rid, pat, single in rules.numbering:
        if single:
            continue
        heads = heads_of([pat])
        if len(heads) >= 2 and heads[0][1] <= 5 and all(len(h[2]) >= 3 for h in heads):
            return _build(lines, heads, rid, rules)
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    return Split([Problem(1, first[:80], t[:rules.max_statement])], "", "")


def _longest_rising(heads: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    best: list[tuple[int, int, str]] = []
    for start in range(len(heads)):
        chain = [heads[start]]
        for h in heads[start + 1:]:
            if h[1] > chain[-1][1] and h[1] - chain[-1][1] <= 3:
                chain.append(h)
        if len(chain) > len(best):
            best = chain
    return best


def _build(lines: list[str], heads: list[tuple[int, int, str]], rid: str, rules: Rules) -> Split:
    problems = []
    pre = "\n".join(lines[:heads[0][0]]).strip()
    for k, (i, n, title) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body = "\n".join(lines[i:end]).strip()
        if not title:
            title = next((ln.strip() for ln in lines[i + 1:end] if ln.strip()), "")
        problems.append(Problem(n, " ".join(title.split())[:80], body[:rules.max_statement]))
    common = pre if 0 < len(pre) <= rules.max_common else ""
    return Split(problems, common, rid)


# ------------------------------------------------------------------------------------------------------------- samples
_WORD = re.compile(r"[A-Za-z]{4,}")
_DATA_WORDS = {"true", "false", "none", "null", "nums", "target", "string", "list", "array", "yes", "nan", "inf"}


def _prose(s: str) -> bool:
    """A description of the input ("The first line contains an integer n"), not an input: more than three ordinary words."""
    return len([w for w in _WORD.findall(s) if w.lower() not in _DATA_WORDS]) > 1


def _clean_block(lines: list[str]) -> str:
    out = [ln.rstrip() for ln in lines if not ln.strip().startswith("```")]
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)[:4000]


def extract_samples(text: str, rules: Rules | None = None) -> list[Sample]:
    """Sample cases as the statement shows them, VERBATIM (trailing spaces and fences removed): "Sample Input / Sample Output", "Example 1: Input: ... Output: ...",
    "Test case 2" pairs and an Input | Output table. A generic "Input:" / "Output:" pair only counts when it sits under a sample / example / test-case heading or
    its input looks like data, not like a description of the input format."""
    rules = rules or load_rules()
    m_in, m_out, m_sec, m_not, m_stop = (rules.markers[k] for k in ("input", "output", "section", "not_a_sample", "stop"))
    lines = _strip(text).split("\n")
    samples: list[Sample] = []
    seen: set[tuple[str, str]] = set()

    labelled_samples: list[Sample] = []
    loose_samples: list[Sample] = []

    def add(i: str, o: str, labelled: bool = True):
        key = (i.strip(), o.strip())
        if o.strip() and key not in seen:
            seen.add(key)
            (labelled_samples if labelled else loose_samples).append(Sample(i, o))

    # 1. tables: a header row with "input" and "output" cells, then rows with the same number of cells
    for k, line in enumerate(lines):
        cells = [c.strip() for c in re.split(r"\||\t", line.strip().strip("|")) if c.strip() != ""] if ("|" in line or "\t" in line) else []
        low = [c.lower() for c in cells]
        if len(cells) >= 2 and any(re.fullmatch(r"(?:sample\s+)?input", c) for c in low) and any(re.fullmatch(r"(?:sample\s+|expected\s+)?output", c) for c in low):
            ii = next(j for j, c in enumerate(low) if "input" in c)
            oi = next(j for j, c in enumerate(low) if "output" in c)
            for row in lines[k + 1:k + 40]:
                if not row.strip() or re.fullmatch(r"[\s|:\-]+", row):
                    if not row.strip():
                        break
                    continue
                rc = [c.strip() for c in re.split(r"\||\t", row.strip().strip("|"))]
                if len(rc) != len(cells):
                    break
                add(rc[ii], rc[oi])
    # 2. blocks
    i, n = 0, len(lines)
    in_section = False
    while i < n:
        line = lines[i]
        if m_sec.match(line) and not m_in.match(line):
            in_section = True
        mi = m_in.match(line)
        if mi and not _fmt_heading(line, m_not):
            rest = (mi.group("rest") or "").strip()
            inp = [rest] if rest else []
            j = i + 1
            found_out = None
            while j < n:
                mo = m_out.match(lines[j])
                if mo and not _fmt_heading(lines[j], m_not):
                    found_out = j
                    break
                if m_in.match(lines[j]) and not _fmt_heading(lines[j], m_not) and (rest or inp):
                    break
                if m_stop.match(lines[j]) or (j - i) > 400:
                    break
                inp.append(lines[j])
                j += 1
            if found_out is None:
                i += 1
                continue
            ro = (m_out.match(lines[found_out]).group("rest") or "").strip()
            out = [ro] if ro else []
            k = found_out + 1
            while k < n:
                if (m_in.match(lines[k]) and not _fmt_heading(lines[k], m_not)) or m_stop.match(lines[k]) or m_sec.match(lines[k]) or (m_out.match(lines[k]) and not _fmt_heading(lines[k], m_not)):
                    break
                if not lines[k].strip() and out:
                    break
                if k - found_out > 200:
                    break
                out.append(lines[k])
                k += 1
            block_in, block_out = _clean_block(inp), _clean_block(out)
            labelled = in_section or bool(re.match(r"(?i)^\s*(?:#{1,4}\s*)?(?:sample|example|test\s*case)", line))
            if block_out and (labelled or not (_prose(block_in) or _prose(block_out))):
                add(block_in, block_out, labelled)
            i = k
            continue
        i += 1
    samples = (labelled_samples or loose_samples)[:rules.max_samples]       # a labelled sample wins over "Input:" lines that only describe the format
    return samples


def _fmt_heading(line: str, m_not: "re.Pattern[str]") -> bool:
    """"Input Format:", "Output description", "Constraints" etc.: a heading of the statement, not a sample's label."""
    head = re.sub(r"(?i)^\s*(?:#{1,4}\s*)?(?:input|output|inp|out|i/p|o/p)\b", "", line, count=1).strip()
    return bool(m_not.match(head))


# ------------------------------------------------------------------------------------------------------------- language, names
def detect_language(text: str, rules: Rules | None = None) -> str:
    """The language the statement names ("Write a C++ program", "in Java", "using Python"), the first named by position; "" when it names none."""
    rules = rules or load_rules()
    best, pos = "", 10**9
    t = _strip(text)[:4000]
    for lang, pat in rules.languages:
        m = pat.search(t)
        if m and m.start() < pos:
            best, pos = lang, m.start()
    return best


def slug(title: str, limit: int = 40) -> str:
    """An ASCII file-name part from a title: letters and digits joined by '-', lower case; '' when nothing ASCII is left (a Hindi or Telugu title)."""
    t = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"\[[A-Za-z_]+_?\d*\]", " ", t)                          # a gateway placeholder is never part of a name
    words = [w for w in re.split(r"[^a-z0-9]+", t) if w]
    out = "-".join(words)[:limit].strip("-")
    return out
