"""D74 (2026-10-27): ASK, DO NOT GUESS. The deterministic gate that stops a request which must be asked about before anything is planned or done.

Measured on the real laptop (Phase 7): 0 of 15 ambiguous requests were asked about; the model opened Notepad on "notepad or wordpad" and acted on "the file I
was working on yesterday". The fix is rules first, kept as DATA (jarvis/config/ambiguity.json, console-only), no model, no network:

    check(text, ctx) -> Verdict | None

A Verdict is ONE short question with the candidates JARVIS can really find (apps the owner named, his open windows, the newest files by name and size, the known
folders, people it knows). It is shown as a jarvis_ask form (a choice, plus a 'Something else' text box; voice and typing both work, D63) INSTEAD of acting. When the
owner answers, `resolve` puts the answer into the ORIGINAL request and the request goes on. Kinds:

    either_or    "open notepad or wordpad", "Notepad leda Wordpad teruvu"      two things joined by or / ya / leda
    destructive  "delete the old file", "delete everything", "delete this"       a delete with no named object (a named file, a path, a folder)
    close        "close the window", "close this", "close everything"          never JARVIS's own window; ask which of the owner's windows
    reference    "open it", "that file", "the file I worked on yesterday"       a pronoun or a vague reference that nothing in the request or the last task settles
    slot         "send it" (to whom), "move it" (which folder), "make a pdf" (about what), "remind me" (when), "play something", "rename it"

What it never does: act, call a model, read a file's content, or ask about a request that names what it works on ("open notepad", "delete test.txt in Downloads").
A request is NOT ambiguous when the words it needs are in it: a named app, a file with an extension, a path, a known folder, a site, a quoted text. A pronoun is settled by
a named thing in the same request, by a thing the same request makes ("make a deck and show it"), or, for "open it" only, by what the last task verifiably produced.
A destructive request is never settled by the last task: it needs a name.

Privacy: the form that goes to the chat model (`form`) holds the question and generic labels (app names the owner said, folder names); the names of his files and the titles of
his windows go only to his own panel (`spec`).
Standard library only. Every function is total: an odd input returns None or a plain Verdict, never an exception.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import ask_form

CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "ambiguity.json"
OWN_FILE = Path(__file__).resolve().parents[2] / "config" / "own_windows.json"
MAX_TEXT = 1000

_L = r"(?<![\wऀ-ॿఀ-౿])"        # a word starts here (Latin, Devanagari, Telugu letters; \w alone misses the combining marks)
_R = r"(?![\wऀ-ॿఀ-౿])"

# What is used when the file is missing or a key is broken: small, and on the careful side. The file replaces it key by key.
FLOOR: dict[str, Any] = {
    "enabled": True, "ask_timeout_s": 90, "max_rounds": 3, "max_candidates": 5,
    "fillers": ["hey jarvis", "jarvis", "please", "can you", "could you", "would you", "will you"],
    "or_words": ["or", "ya", "leda"],
    "question_starts": ["is", "are", "do", "does", "did", "can", "could", "will", "would", "what", "which", "who", "how", "why", "when", "where"],
    "content_verbs": ["type", "write", "say", "tell", "search", "google", "ask", "reply", "answer", "translate", "whether"],
    "generic_stop": ["yes", "no", "on", "off", "up", "down", "left", "right", "both", "all", "none"],
    "determiners": ["the", "a", "an", "my", "your", "this", "that", "some", "any"],
    "verbs": {"open": ["open", "launch", "start"], "close": ["close", "quit", "exit"], "delete": ["delete", "remove", "erase", "trash"],
              "send": ["send"], "move": ["move"], "copy": ["copy"], "rename": ["rename"], "save": ["save"], "play": ["play"]},
    "act_groups": ["open", "close", "delete", "send", "move", "copy", "rename", "save"],
    "harmless_groups": [], "destructive_groups": ["delete"], "clause_start_groups": [],
    "vague_np_groups": ["open", "delete", "move", "copy", "rename", "save", "edit", "read", "show", "upload"],
    "pronouns": {"adjacent": ["it", "that", "this", "those", "these", "them"], "free": []},
    "pronoun_safe": [], "vague_nouns": ["file", "files", "folder", "document", "picture", "photo", "thing", "stuff"], "name_follow_stop": ["in", "on", "from", "to", "and", "then"],
    "temporal": ["yesterday", "last week", "last night"], "past_reference": ["i was working on", "i mentioned", "the usual"],
    "temporal_acts": ["open", "close", "delete", "send", "move", "copy", "rename", "save"],
    "antecedent_verbs": ["make", "create", "write", "draw", "take", "type"],
    "close": {"generic_objects": ["it", "this", "that", "window", "the window", "this window", "app", "everything"], "all_words": ["everything", "all"], "label_max": 5},
    "known_folders": ["downloads", "documents", "desktop", "pictures", "music", "videos", "recycle bin"],
    "file_extensions": ["txt", "pdf", "docx", "xlsx", "pptx", "png", "jpg", "md", "csv", "zip"],
    "extra_apps": ["notepad", "wordpad", "calculator", "paint", "chrome", "edge", "excel", "word"], "extra_sites": ["youtube", "gmail", "google"],
    "vague_media": ["something", "anything", "music"], "slots": [], "superlatives": ["oldest", "newest", "latest", "biggest", "largest", "smallest", "last", "first", "most recent"],
    "window_ignore": ["explorer", "textinputhost", "searchhost", "shellexperiencehost", "startmenuexperiencehost", "applicationframehost", "msedgewebview2", "systemsettings"],
    "questions": {
        "either_or": {"title": "Which one?", "question": "Which one: {a} or {b}?", "reason": "You named two things; I will not pick one for you."},
        "destructive": {"title": "Which one?", "question": "Which one should I delete?", "reason": "A delete needs a named item. I will not guess."},
        "close_one": {"title": "Which window?", "question": "Which window should I close?", "reason": "I do not close my own windows, and I will not guess which of yours."},
        "close_all": {"title": "Which windows?", "question": "Which windows should I close?", "reason": "Closing everything can lose unsaved work."},
        "reference_file": {"title": "Which file?", "question": "Which file do you mean?", "reason": "I cannot tell which one you mean from what you said."},
        "reference": {"title": "Which one?", "question": "Which one do you mean?", "reason": "I cannot tell which one you mean from what you said."},
        "free": {"title": "JARVIS asks", "question": "Could you say which one you mean?", "reason": "I do not want to guess."}},
}


def _alt(words: Iterable[str]) -> str:
    """An alternation of whole words / phrases, longest first; a space in a phrase is any run of white space."""
    ws = sorted({str(w).strip().lower() for w in words if str(w).strip()}, key=lambda s: (-len(s), s))
    return "(?:" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in ws) + ")" if ws else r"(?!)"


def _word_rx(words: Iterable[str]) -> re.Pattern:
    return re.compile(_L + _alt(words) + _R, re.I)


# ----------------------------------------------------------------------------------------------------------------------------------- rules
class Rules:
    """config/ambiguity.json compiled. Each key falls back to the built-in FLOOR when it is missing or the wrong type."""

    def __init__(self, data: dict | None = None, source: str = "floor"):
        data = data if isinstance(data, dict) else {}
        self.source = source
        self.raw = data

        def get(key: str, kind: type) -> Any:
            v = data.get(key)
            return v if isinstance(v, kind) and not (kind is int and isinstance(v, bool)) else FLOOR[key]
        self.enabled = bool(data["enabled"]) if isinstance(data.get("enabled"), bool) else True
        self.ask_timeout_s = float(min(max(get("ask_timeout_s", (int, float)) if isinstance(data.get("ask_timeout_s"), (int, float)) else FLOOR["ask_timeout_s"], 5), 150))
        self.max_rounds = int(min(max(data.get("max_rounds") if isinstance(data.get("max_rounds"), int) else FLOOR["max_rounds"], 1), 6))
        self.max_candidates = int(min(max(data.get("max_candidates") if isinstance(data.get("max_candidates"), int) else FLOOR["max_candidates"], 2), 8))
        lists = {k: [str(x) for x in get(k, list) if isinstance(x, str)] for k in (
            "fillers", "or_words", "question_starts", "content_verbs", "generic_stop", "determiners", "act_groups", "harmless_groups", "destructive_groups",
            "clause_start_groups", "vague_np_groups", "pronoun_safe", "vague_nouns", "name_follow_stop", "temporal", "past_reference", "temporal_acts", "antecedent_verbs",
            "known_folders", "file_extensions", "extra_apps", "extra_sites", "vague_media", "window_ignore")}
        self.lists = lists
        verbs = get("verbs", dict)
        self.verbs = {g: [str(x) for x in forms if isinstance(x, str)] for g, forms in verbs.items() if isinstance(forms, list)}
        pr = get("pronouns", dict)
        self.pron_adjacent = [str(x) for x in pr.get("adjacent", []) if isinstance(x, str)] or FLOOR["pronouns"]["adjacent"]
        self.pron_free = [str(x) for x in pr.get("free", []) if isinstance(x, str)]
        cl = get("close", dict)
        self.close_generic = [str(x) for x in cl.get("generic_objects", []) if isinstance(x, str)]
        self.close_all = [str(x) for x in cl.get("all_words", []) if isinstance(x, str)]
        self.label_max = int(cl.get("label_max", 5)) if isinstance(cl.get("label_max", 5), int) else 5
        q = get("questions", dict)
        self.questions = {k: {**FLOOR["questions"].get(k, {}), **(v if isinstance(v, dict) else {})} for k, v in {**FLOOR["questions"], **q}.items()}
        # compiled
        self.filler_rx = re.compile(r"^\W*" + _alt(lists["fillers"]) + r"(?=[\s,:;.!-]|$)[\s,:;.!-]*", re.I) if lists["fillers"] else None
        self.or_rx = re.compile(_L + "(" + _alt(lists["or_words"]) + ")" + _R, re.I)
        self.verb_rx = {g: re.compile(_L + _alt(forms) + _R, re.I) for g, forms in self.verbs.items() if forms}
        self.content_rx = _word_rx(lists["content_verbs"])
        self.adjacent_rx = re.compile(_alt(self.pron_adjacent) + _R, re.I)
        self.free_rx = _word_rx(self.pron_free) if self.pron_free else None
        self.safe_rx = []
        for rx in lists["pronoun_safe"]:
            try:
                self.safe_rx.append(re.compile(rx, re.I))
            except re.error:
                continue
        nouns = _alt(lists["vague_nouns"])
        self.vague_np = re.compile(_L + r"(?:the|that|this|those|these|a|an|my|some|any)\s+(?:(?:old|older|oldest|new|newest|latest|last|other|same|first|second|previous|recent|big|large|small)\s+)*(" + nouns + r")" + _R, re.I)
        self.bare_noun = re.compile(_L + "(" + nouns + ")" + _R, re.I)
        self.temporal_rx = _word_rx(lists["temporal"])
        self.past_rx = _word_rx(lists["past_reference"])
        self.possessive_time = re.compile(_L + _alt([w for w in lists["temporal"] if " " not in w]) + r"(?:'s|s)?\s+(?:" + nouns + r")" + _R, re.I)
        self.folder_rx = _word_rx(lists["known_folders"])
        self.app_rx = _word_rx(lists["extra_apps"])
        self.site_rx = _word_rx(lists["extra_sites"])
        ext = "|".join(re.escape(e.lower().lstrip(".")) for e in lists["file_extensions"]) or "txt"
        self.file_rx = re.compile(r"(?<![\w])[^\s\"'<>|*?]*[\w)\]-]\.(?:" + ext + r")" + _R, re.I)
        self.ext_only_rx = re.compile(r"(?<![\w])\.(?:" + ext + r")" + _R, re.I)
        self.antecedent_rx = _word_rx(lists["antecedent_verbs"])
        self.vague_media_rx = re.compile(_L + _alt(lists["vague_media"]) + _R, re.I)
        sup = data.get("superlatives")
        self.superlatives = [str(x).lower() for x in (sup if isinstance(sup, list) else FLOOR["superlatives"]) if isinstance(x, str)]
        self.question_starts = {w.lower() for w in lists["question_starts"]}
        self.generic_stop = {w.lower() for w in lists["generic_stop"]}
        self.determiners = {w.lower() for w in lists["determiners"]}
        self.name_stop = {w.lower() for w in lists["name_follow_stop"]} | self.determiners
        self.window_ignore = {w.lower() for w in lists["window_ignore"]}
        self.slots = []
        for s in data.get("slots") if isinstance(data.get("slots"), list) else []:
            if not isinstance(s, dict) or not s.get("id") or not isinstance(s.get("verbs"), list):
                continue
            pres = []
            for rx in s.get("present_any") if isinstance(s.get("present_any"), list) else []:
                try:
                    pres.append(re.compile(str(rx).replace("{NOUNS}", _alt(lists["vague_nouns"])).replace("{PRON}", _alt(self.pron_adjacent + self.pron_free)), re.I))
                except re.error:
                    continue
            ia = None
            if s.get("insert_after"):
                try:
                    ia = re.compile(s["insert_after"], re.I)
                except re.error:
                    ia = None
            noun = _word_rx(s["needs_noun"]) if isinstance(s.get("needs_noun"), list) and s["needs_noun"] else None
            self.slots.append({**s, "_present": pres, "_insert": ia, "_noun": noun})


_CACHE: dict = {"sig": None, "rules": None}


def load(path: Path | str | None = None) -> Rules:
    """config/ambiguity.json compiled (cached by modification time); a missing or broken file gives the built-in floor, never 'no rules'."""
    p = Path(path) if path else CONFIG_FILE
    try:
        st = p.stat()
        sig = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return Rules(FLOOR, "floor")
    if _CACHE["sig"] == sig and _CACHE["rules"] is not None:
        return _CACHE["rules"]
    try:
        rules = Rules(json.loads(p.read_text(encoding="utf-8-sig")), "file")
    except (OSError, ValueError, re.error, TypeError, AttributeError):
        rules = Rules(FLOOR, "floor")
    _CACHE.update(sig=sig, rules=rules)
    return rules


# ---------------------------------------------------------------------------------------------------------------------------------- context
@dataclass
class Context:
    """What the caller knows that the words alone do not say. Every part is optional; a missing part means 'not known', which never makes the gate LESS careful."""
    windows: list[dict] | None = None        # [{process, title, foreground, minimized}] front to back, as the node lists them
    launched: Iterable[str] = ()             # process names of windows JARVIS opened and that are still open (the journal)
    last: dict | None = None                 # coordinator.previous(): what the last task verifiably produced {file | url | app}
    apps: Iterable[str] = ()                 # the app names the plugin knows (decide.APPS)
    sites: Iterable[str] = ()                # the site names the plugin knows (decide.SITES)
    folders: Iterable[str] = ()              # extra folder names (projects)
    people: Iterable[str] | None = None      # people JARVIS knows (the owner's own names store); shown on his panel only
    recent_files: list[tuple[str, int]] | None = None    # (name, size) of the newest files; None = look in the known folders
    home: Path | None = None
    now: float | None = None


@dataclass
class Option:
    id: str
    label: str                  # what the owner reads
    value: str                  # what goes into the request when he picks it
    detail: str = ""
    private: bool = False       # a file name or a window title: shown on his panel, never given to the chat model


@dataclass
class Verdict:
    kind: str                   # either_or | destructive | close | reference | slot | clarify
    rule: str                   # which rule fired: a fixed word (safe to log)
    title: str
    question: str
    reason: str
    original: str
    options: list[Option] = field(default_factory=list)
    source: str = "none"        # files | apps | windows | folders | people | none
    multiple: bool = False
    file_filter: dict | None = None
    text_label: str = "Something else"
    text_required: bool = False
    span: tuple[int, int] | None = None      # the part of `original` the answer replaces
    fill: str = "replace"       # replace | append | insert_after | replace_vague_media | whole
    template: str = "{answer}"
    insert_after: str = ""
    auto_text: str = ""         # set: nothing needs asking, the request simply means this (a window JARVIS opened)
    slot: str = ""
    timeout_s: float = 100.0


# ----------------------------------------------------------------------------------------------------------------------------------- text
_WS = re.compile(r"\s+")
_QUOTE = re.compile(r"\"[^\"]{1,400}\"|“[^”]{1,400}”|(?<![\w])'[^']{1,400}'(?![\w])")
_SENT = re.compile(r"(?<=[.!?;])\s+|\n+|\s+(?:and then|then)\s+(?=\w)", re.I)
_CLAUSE = re.compile(r"\s*,\s*|\s+(?:and then|then|and|aur|mariyu|chesi|karke|phir)\s+|\s*;\s*|\s*&\s*", re.I)
_PATH = re.compile(r"[A-Za-z]:[\\/]|[\w.-]+[\\/][\w.-]+|~[\\/]|%\w+%|\\\\\w")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|org|net|io|in|co|app|dev|ai|gov|edu|info|tv|me)\b", re.I)
_NAMED_AS = re.compile(_L + r"(?:named|called|titled|name)\s+\S", re.I)


def _clean(text: Any) -> str:
    return _WS.sub(" ", str(text or "")).strip()[:MAX_TEXT]


def _mask(text: str) -> str:
    """The same text with every quoted piece blanked out (same length, so positions agree): typed text is content, never scanned for words."""
    return _QUOTE.sub(lambda m: "█" * len(m.group(0)), text)


def _strip_fillers(text: str, r: Rules) -> tuple[str, int]:
    off = 0
    while r.filler_rx is not None:
        m = r.filler_rx.match(text[off:])
        if not m or m.end() == 0:
            break
        off += m.end()
    return text[off:], off


def _first_word(s: str) -> str:
    m = re.match(r"\W*([\wऀ-ॿఀ-౿']+)", s)
    return m.group(1).lower() if m else ""


@dataclass
class _Named:
    files: list[str] = field(default_factory=list)
    paths: bool = False
    folders: list[str] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)
    quoted: bool = False
    ext_only: bool = False
    file_word: bool = False        # "the file budget", "folder Old"
    as_named: bool = False         # "named X", "called X"
    ant_object: bool = False       # a file / folder / quoted text a pronoun can stand for (not one that is only the place or the tool: "to b.txt", "in Notepad")
    ant_any: bool = False          # the same, an app or a site included

    @property
    def object_named(self) -> bool:
        """Something a delete / move / rename could be aimed at is named: a file, a path, a folder, a quoted name."""
        return bool(self.files or self.paths or self.folders or self.quoted or self.file_word or self.as_named)

    @property
    def any_named(self) -> bool:
        return bool(self.object_named or self.apps or self.sites)


_DEST_PREP = {"to", "into", "onto", "in", "on", "under", "inside", "from", "with", "using", "via", "at", "by", "over", "as", "within", "through"}
_DEST_POST = {"mein", "me", "lo", "par", "pe", "se", "tho", "thoo", "ga", "gaa"}


def _dest_use(masked: str, start: int, end: int) -> bool:
    """Is the name at masked[start:end] only the place or the tool of the action ("to b.txt", "in Notepad", "from Downloads"), not the thing acted on?"""
    before = re.findall(r"[\w']+", masked[max(0, start - 14):start].lower())
    after = re.findall(r"[\w']+", masked[end:end + 14].lower())
    while before and before[-1] in {"the", "a", "an", "my", "your", "our", "this", "that", "new"}:
        before.pop()                                                  # "to the desktop", "into a new folder"
    return bool(before and before[-1] in _DEST_PREP) or bool(after and after[0] in _DEST_POST)


def _named(masked: str, r: Rules, ctx: Context) -> _Named:
    n = _Named()
    low = masked.lower()
    spans: list[tuple[str, int, int]] = []          # (kind, start, end)
    n.quoted = "\u2588" in masked
    for m in r.file_rx.finditer(masked):
        n.files.append(m.group(0))
        spans.append(("file", m.start(), m.end()))
    n.ext_only = bool(r.ext_only_rx.search(masked))
    pm = _PATH.search(masked)
    n.paths = bool(pm)
    if pm and not n.files:
        spans.append(("file", pm.start(), pm.end()))
    for m in r.folder_rx.finditer(masked):
        n.folders.append(m.group(0))
        spans.append(("folder", m.start(), m.end()))
    for extra in ctx.folders or ():
        for m in re.finditer(_L + re.escape(str(extra).lower()) + _R, low) if extra else ():
            n.folders.append(str(extra))
            spans.append(("folder", m.start(), m.end()))
    for m in r.app_rx.finditer(masked):
        n.apps.append(m.group(0))
        spans.append(("app", m.start(), m.end()))
    for extra in ctx.apps or ():
        for m in re.finditer(_L + re.escape(str(extra).lower()) + _R, low) if extra else ():
            n.apps.append(str(extra))
            spans.append(("app", m.start(), m.end()))
    for m in list(r.site_rx.finditer(masked)) + list(_URL.finditer(masked)):
        n.sites.append(m.group(0))
        spans.append(("site", m.start(), m.end()))
    for extra in ctx.sites or ():
        for m in re.finditer(_L + re.escape(str(extra).lower()) + _R, low) if extra else ():
            n.sites.append(str(extra))
            spans.append(("site", m.start(), m.end()))
    nm = _NAMED_AS.search(masked)
    n.as_named = bool(nm)
    # "the file budget", "folder Old Notes": a file / folder word followed by a name (not a preposition, a determiner, a verb or the end)
    for m in re.finditer(_L + r"(?:file|folder|document|doc|directory|deck|report|pdf|picture|photo|image|spreadsheet)\s+([\w\-'.]+)", masked, re.I):
        nxt = m.group(1).lower()
        if nxt not in r.name_stop and nxt not in r.generic_stop and not r.bare_noun.fullmatch(nxt) and not _is_verb_word(nxt, r):
            n.file_word = True
            spans.append(("file", m.start(1), m.end(1)))
    n.ant_object = n.quoted or n.as_named or any(k in ("file", "folder") and not _dest_use(masked, a0, b0) for k, a0, b0 in spans)
    n.ant_any = n.ant_object or any(k in ("app", "site") and not _dest_use(masked, a0, b0) for k, a0, b0 in spans)
    return n


# ----------------------------------------------------------------------------------------------------------------------------------- detectors
def _is_question(sent: str, r: Rules) -> bool:
    w = _first_word(sent)
    return bool(w) and w in r.question_starts


def _verbs_in(sent: str, r: Rules) -> list[tuple[str, int, int]]:
    """(group, start, end) of every verb in the sentence. A weak group (email, message, share: also nouns) counts only at the start of a clause."""
    out = []
    starts = {0}
    for m in _CLAUSE.finditer(sent):
        starts.add(m.end())
    for g, rx in r.verb_rx.items():
        weak = g in r.lists["clause_start_groups"]
        for m in rx.finditer(sent):
            if weak and not any(abs(m.start() - s) <= 1 or sent[s:m.start()].strip(" ,") == "" for s in starts):
                continue
            out.append((g, m.start(), m.end()))
    return sorted(out, key=lambda t: t[1])


def _option(i: int, label: str, value: str | None = None, detail: str = "", private: bool = False) -> Option:
    return Option(f"o{i}", label[:80], (value if value is not None else label)[:300], detail[:120], private)


def _display_app(word: str) -> str:
    w = _WS.sub(" ", word.strip())
    table = {"notepad": "Notepad", "wordpad": "WordPad", "calc": "Calculator", "calculator": "Calculator", "paint": "Paint", "ms paint": "Paint", "vscode": "VS Code",
             "vs code": "VS Code", "visual studio code": "VS Code", "msedge": "Edge", "edge": "Edge", "chrome": "Chrome", "google chrome": "Chrome", "excel": "Excel",
             "word": "Word", "powerpoint": "PowerPoint", "explorer": "File Explorer", "file explorer": "File Explorer", "youtube": "YouTube", "gmail": "Gmail",
             "whatsapp": "WhatsApp", "spotify": "Spotify", "vlc": "VLC", "outlook": "Outlook", "teams": "Teams", "discord": "Discord", "settings": "Settings",
             "task manager": "Task Manager", "terminal": "Terminal", "downloads": "Downloads", "documents": "Documents", "desktop": "Desktop", "pictures": "Pictures",
             "calculatorapp": "Calculator", "winword": "Word", "powerpnt": "PowerPoint", "mspaint": "Paint", "code": "VS Code", "wt": "Terminal", "taskmgr": "Task Manager",
             "systemsettings": "Settings", "wordpad": "WordPad", "snippingtool": "Snipping Tool", "onenote": "OneNote", "notepad++": "Notepad++"}
    return table.get(w.lower(), w[:1].upper() + w[1:])


def _tokens(s: str) -> list[tuple[str, int, int]]:
    """(word, start, end) with the punctuation at the edges of a word cut off ('wordpad.' is 'wordpad')."""
    out = []
    for m in re.finditer(r"[^\s,;!?()]+", s):
        w = m.group(0)
        lead = len(w) - len(w.lstrip(".:'\"-"))
        w2 = w.strip(".:'\"-")
        if w2:
            out.append((w2, m.start() + lead, m.start() + lead + len(w2)))
    return out


def _edge_phrase(toks: list[tuple[str, int, int]], r: Rules, right_side: bool) -> list[tuple[str, int, int]]:
    """Up to three words next to the 'or': one determiner next to it is skipped, a verb or a polite word ends the phrase."""
    out: list[tuple[str, int, int]] = []
    seq = toks if right_side else list(reversed(toks))
    for t in seq:
        w = t[0].lower()
        if w in r.determiners and not out:
            continue
        if w in r.determiners or _is_verb_word(t[0], r) or w in {"please", "now", "me", "for"}:
            break
        out.append(t)
        if len(out) >= 3:
            break
    return out if right_side else list(reversed(out))


def _either_or(sent: str, base: int, full: str, r: Rules, nm: _Named, ctx: Context) -> Verdict | None:
    if _is_question(sent, r):
        return None
    acts = _verbs_in(sent, r)
    for m in r.or_rx.finditer(sent):
        left, right = sent[:m.start()], sent[m.end():]
        if "\u2588" in sent[max(0, m.start() - 2):m.start()] or "\u2588" in sent[m.end():m.end() + 2]:
            continue                                            # the "or" sits next to a quoted text
        if r.content_rx.search(left):
            continue                                            # "search for cats or dogs", "type a or b": the or belongs to the content
        lcut = max((c.end() for c in _CLAUSE.finditer(left)), default=0)
        rm = _CLAUSE.search(right)
        rcut = rm.start() if rm else len(right)
        lw, rw = left[lcut:], right[:rcut]
        ltoks, rtoks = _tokens(lw), _tokens(rw)
        # an action on each side ("open or close", "delete or move the file"): the choice is the action
        vl, vr = _verbs_in(lw, r), _verbs_in(rw, r)
        if vl and vr and vl[-1][0] != vr[0][0]:
            la, ra = lw[vl[-1][1]:vl[-1][2]].strip(), rw[vr[0][1]:vr[0][2]].strip()
            a_start = base + lcut + vl[-1][1]
            b_end = base + m.end() + vr[0][2]
            opts = [_option(1, la[:1].upper() + la[1:], la), _option(2, ra[:1].upper() + ra[1:], ra)]
            return _mk("either_or", "or_verbs", r, full, a=opts[0].label, b=opts[1].label, options=opts, span=(a_start, b_end))
        lp, rp = _edge_phrase(ltoks, r, False), _edge_phrase(rtoks, r, True)
        if not lp or not rp:
            continue
        a, b = " ".join(t[0] for t in lp), " ".join(t[0] for t in rp)
        if a.lower() in r.generic_stop or b.lower() in r.generic_stop or a.lower() == b.lower():
            continue
        ka, kb = _kind_of(a, r, ctx), _kind_of(b, r, ctx)
        has_cmd = any(g in r.lists["act_groups"] or g in ("play", "make") for g, _s, _e in acts)
        generic = (not ka and not kb) and has_cmd and all(len(t[0]) >= 3 for t in lp + rp) and len(lp) <= 2 and len(rp) <= 2
        if not ((ka and kb) or generic):
            continue
        a_start = base + lcut + lp[0][1]
        b_end = base + m.end() + rp[-1][2]
        la = _display_app(a) if ka in ("app", "site", "folder") else a
        lb = _display_app(b) if kb in ("app", "site", "folder") else b
        src = "apps" if ka == kb == "app" else "folders" if ka == kb == "folder" else "none"
        return _mk("either_or", "or_" + (ka or "words"), r, full, a=la, b=lb, options=[_option(1, la), _option(2, lb)], source=src, span=(a_start, b_end))
    return None


def _is_verb_word(w: str, r: Rules) -> bool:
    return any(rx.fullmatch(w) for rx in r.verb_rx.values())


def _kind_of(phrase: str, r: Rules, ctx: Context) -> str:
    p = phrase.strip().lower()
    if r.file_rx.fullmatch(p) or _PATH.search(p):
        return "file"
    if re.fullmatch(_alt(r.lists["known_folders"]), p, re.I) or p in {str(f).lower() for f in ctx.folders or ()}:
        return "folder"
    if re.fullmatch(_alt(r.lists["extra_apps"]), p, re.I) or p in {str(a).lower() for a in ctx.apps or ()}:
        return "app"
    if re.fullmatch(_alt(r.lists["extra_sites"]), p, re.I) or _URL.fullmatch(p) or p in {str(a).lower() for a in ctx.sites or ()}:
        return "site"
    return ""


def _mk(rule_kind: str, rule: str, r: Rules, original: str, *, options: list[Option] | None = None, source: str = "none", span=None, fill: str = "replace",
        qkey: str | None = None, template: str = "{answer}", **fmt) -> Verdict:
    q = r.questions.get(qkey or rule_kind, r.questions["free"])
    question = str(q.get("question") or r.questions["free"]["question"])
    try:
        question = question.format(**fmt)
    except (KeyError, IndexError, ValueError):
        pass
    return Verdict(kind=rule_kind, rule=rule, title=str(q.get("title") or "Which one?"), question=question, reason=str(q.get("reason") or ""), original=original,
                   options=options or [], source=source, span=span, fill=fill, template=template, timeout_s=r.ask_timeout_s)


def _windows_candidates(ctx: Context, r: Rules) -> list[dict]:
    """The owner's windows a 'close it' could mean: visible, not minimised, with a title, not a shell process, and NEVER JARVIS's own."""
    out, seen = [], set()
    for w in ctx.windows or []:
        if not isinstance(w, dict) or w.get("minimized"):
            continue
        proc = re.sub(r"\.exe$", "", str(w.get("process") or "").strip().lower())
        title = _clean(w.get("title"))[:80]
        if not proc or not title or proc in r.window_ignore and proc != "applicationframehost":
            continue
        if own_window(proc, title):
            continue
        if proc == "applicationframehost":                       # a modern app: named by its title
            proc = re.sub(r"[^a-z0-9 ._-]", "", title.lower())[:30] or proc
        if proc in seen:
            continue
        seen.add(proc)
        out.append({"proc": proc, "title": title, "foreground": bool(w.get("foreground"))})
    return out


def _close(sent: str, base: int, full: str, r: Rules, nm: _Named, ctx: Context) -> Verdict | None:
    verbs = [v for v in _verbs_in(sent, r) if v[0] == "close"]
    if not verbs or nm.any_named:
        return None
    low = sent.lower()
    all_rx = _word_rx(r.close_all)
    gen_rx = _word_rx(r.close_generic)
    want_all = bool(all_rx.search(sent))
    generic = bool(gen_rx.search(sent)) or want_all
    after = sent[verbs[0][2]:].strip(" .!?")
    bare = not re.sub(r"(?i)\b(?:now|please|up|down|out)\b", "", after).strip()      # "close" alone
    if not (generic or bare):
        return None
    wins = _windows_candidates(ctx, r)
    launched = {str(p).lower() for p in ctx.launched or ()}
    if want_all:
        opts = [_option(i + 1, _display_app(w["proc"]), _display_app(w["proc"]), w["title"], True) for i, w in enumerate(wins[:8])]
        v = _mk("close", "close_all", r, full, options=opts, source="windows", qkey="close_all", fill="whole", template="Close {answer}")
        v.multiple = True
        v.text_required = not opts
        return v
    mine = [w for w in wins if w["proc"] in launched or any(l and (l in w["proc"] or l in w["title"].lower()) for l in launched)]
    if len(mine) == 1 and len(wins) >= 1 and not _word_rx(["everything"]).search(low):
        v = _mk("close", "close_opened", r, full, qkey="close_one", fill="whole", template="Close {answer}")
        v.auto_text = "Close " + _display_app(mine[0]["proc"])
        return v
    opts = [_option(i + 1, _display_app(w["proc"]), _display_app(w["proc"]), w["title"], True) for i, w in enumerate(wins[:r.label_max])]
    v = _mk("close", "close_which", r, full, options=opts, source="windows", qkey="close_one", fill="whole", template="Close {answer}")
    v.text_required = not opts
    return v


def _pronoun_hits(sent: str, r: Rules, acts: list[tuple[str, int, int]], antecedent_end: int | None) -> tuple[tuple[int, int] | None, str]:
    """(span, why) of a pronoun the verbs take as their object, or (None, '')."""
    for g, s, e in acts:
        if g not in r.lists["act_groups"]:
            continue
        m = re.compile(r"\W+(?:[\w']+\W+){0,2}?(" + _alt(r.pron_adjacent) + r")" + _R, re.I).match(sent, e)
        if m:
            if antecedent_end is not None and antecedent_end < s and g not in r.lists["destructive_groups"]:
                continue                                         # "make a deck and show it": the deck is what 'it' is
            return (m.start(1), m.end(1)), g
    if r.free_rx is not None:
        m = r.free_rx.search(sent)
        if m and any(g in r.lists["act_groups"] for g, _s, _e in acts):
            g = next(g for g, _s, _e in acts if g in r.lists["act_groups"])
            if not (antecedent_end is not None and g not in r.lists["destructive_groups"] and any(antecedent_end < s for _g, s, _e in acts)):
                return (m.start(), m.end()), g
    return None, ""


def _recent(ctx: Context, r: Rules, flt: dict | None = None) -> list[tuple[str, int]]:
    if ctx.recent_files is not None:
        return list(ctx.recent_files)[:12]
    return recent_files(ctx.home, flt, now=ctx.now)


def _reference(sent: str, base: int, full: str, r: Rules, nm: _Named, ctx: Context, all_verbs: list[tuple[str, int, int]], whole: str) -> Verdict | None:
    acts = [v for v in all_verbs if v[0] in r.lists["act_groups"]]
    if not acts:
        return None
    destructive = any(v[0] in r.lists["destructive_groups"] for v in acts)
    body = sent.strip(" .!?")
    if any(rx.fullmatch(whole.strip(" .!?")) for rx in r.safe_rx) or any(rx.fullmatch(body) for rx in r.safe_rx):
        return None
    anc = None
    if not destructive:
        for m in r.antecedent_rx.finditer(whole):
            anc = m.end() if anc is None else anc
    span, why = _pronoun_hits(sent, r, acts, anc)
    temporal = r.temporal_rx.search(sent) or r.possessive_time.search(sent)
    past = r.past_rx.search(sent)
    vague = None
    for g, _s, e in acts:
        if g not in r.lists["vague_np_groups"]:
            continue
        cand = r.vague_np.search(sent, e)
        if cand is not None and cand.start() - e <= 14:
            vague = cand
            break
    first = acts[0]
    if vague is None:                                               # "open file", "delete files": the bare noun is the whole object
        tail = sent[first[2]:].strip(" .!?")
        bm = re.fullmatch(r"(?i)(?:(?:old|older|oldest|new|newest|latest|last|other|same|previous|recent)\s+)*(" + _alt(r.lists["vague_nouns"]) + r")", tail)
        if bm:
            vague = bm
            vague_start = sent.rfind(bm.group(0))
        else:
            vague_start = -1
    else:
        vague_start = vague.start()
    in_vague_groups = any(g in r.lists["vague_np_groups"] for g, _s, _e in acts)
    t_ok = bool(temporal) and any(g in r.lists["temporal_acts"] for g, _s, _e in acts)
    ref_phrase = bool(past) or t_ok
    if span is None and not ref_phrase and (vague is None or not in_vague_groups):
        return None
    if (nm.ant_object if destructive else nm.ant_any):
        return None                                                  # the request itself names what it works on
    if span is None and nm.object_named and all(g in r.lists["harmless_groups"] for g, _s, _e in acts):
        return None                                                  # "show me the files in documents": a listing inside a named place is not a guess
    if vague is not None and (nm.folders or nm.paths) and span is None and re.search(r"(?i)\b(?:" + "|".join(re.escape(x) for x in r.superlatives) + r")\b", vague.group(0)):
        return None                                                  # "the oldest file in <folder>": a definite choice inside a named place
    # settled by the last task (only "open it" / "show it" style, never a destructive or a sending request)
    last = ctx.last or {}
    if not destructive and why in ("open", "show", "read") and (last.get("file") or last.get("url") or last.get("app")) and span is not None and not (temporal or past):
        return None
    fl = None
    tw = temporal.group(0).lower() if temporal else ""
    if re.match(r"(?:yesterday|ninna|ninnati|kal)\b", tw):
        fl = {"day": "yesterday"}
    elif "last week" in tw:
        fl = {"day": "last_week"}
    kinds = r"(?i)files?|documents?|docs?|pdfs?|reports?|spreadsheets?|sheets?|decks?|presentations?|slides|invoices?|receipts?|letters?|notes?|pictures?|photos?|images?|things?|stuff|items?|ones?"
    use_files = span is not None or ref_phrase or bool(vague is not None and re.fullmatch(kinds, vague.group(1)))
    files = (_recent(ctx, r, fl) or (_recent(ctx, r) if fl else [])) if use_files else []      # nothing of that day: the newest files (the panel says so)
    sp = (base + span[0], base + span[1]) if span else ((base + vague_start, base + vague_start + len(vague.group(0))) if vague is not None and vague_start >= 0 else None)
    if ref_phrase and span is None:                                  # "the file I was working on yesterday": the whole description is what the answer replaces
        marks = [m for m in (vague if vague is not None and vague_start >= 0 else None, past, temporal, r.possessive_time.search(sent)) if m is not None]
        if marks:
            lo = min((vague_start if m is vague else m.start()) for m in marks)
            hi = max(((vague_start + len(vague.group(0))) if m is vague else m.end()) for m in marks)
            sp = (base + lo, base + hi)
    key = "destructive" if destructive else ("reference_file" if files else "reference")
    v = _mk("destructive" if destructive else "reference", "destructive_vague" if destructive else ("reference_time" if ref_phrase else "reference_pronoun"), r, full,
            options=[], source="files" if files else "none", span=sp, fill="replace", qkey=key)
    v.file_filter = fl
    v.text_required = not files
    return v


def _destructive_without_object(sent: str, base: int, full: str, r: Rules, nm: _Named, ctx: Context, acts: list[tuple[str, int, int]]) -> Verdict | None:
    dels = [a for a in acts if a[0] in r.lists["destructive_groups"]]
    if not dels or nm.object_named:
        return None
    after = sent[dels[0][2]:]
    # a delete in the middle of typed content or a cleanup flow is not this rule: those have their own names ("clean up my PC", "empty the recycle bin")
    if re.search(r"(?i)\b(?:my pc|the pc|my computer|junk)\b", after) and re.search(r"(?i)\bclean", sent):
        return None
    fl = {"name_like": ["old"]} if re.search(r"(?i)\bold(?:er|est)?\b|purani|pata\b", after) else None
    files = _recent(ctx, r, fl) or (_recent(ctx, r) if fl else [])
    v = _mk("destructive", "destructive_vague", r, full, source="files" if files else "none", span=(base + dels[0][2], base + len(sent.rstrip(" .!?"))), fill="replace", qkey="destructive")
    v.file_filter = fl
    v.text_required = not files
    return v


def _slot(sent: str, base: int, full: str, r: Rules, nm: _Named, ctx: Context, acts: list[tuple[str, int, int]]) -> Verdict | None:
    groups = {a[0] for a in acts}
    for s in r.slots:
        if not groups & set(s["verbs"]):
            continue
        if s.get("_noun") is not None and not s["_noun"].search(sent):
            continue
        if s.get("bare_only") and not _bare_request(sent, s, r):
            continue                                              # "research solar panels and make slides": the topic is in the other clause
        if s.get("vague_only"):
            m = r.vague_media_rx.search(sent)
            rest = sent[m.end():].strip(" .!?") if m else ""
            rest_words = [w for w in re.findall(r"[\w\u0900-\u097f\u0c00-\u0c7f']+", rest) if not _is_verb_word(w, r) and not r.vague_media_rx.fullmatch(w) and w.lower() not in ("karo", "cheyyi", "cheyandi", "kar", "do", "please")]
            if not m or (rest_words and not re.match(r"(?i)(?:on|in)\b", rest)):
                continue
            if _named_media(sent, m, r) or nm.apps or nm.sites:      # "play music on spotify" names where; "play a relaxing song" names what
                continue
            if len(sent[:m.start()].split()) > 3:
                continue
        else:
            if any(p.search(sent) for p in s["_present"]):
                continue
            if s["id"] in ("recipient",) and nm.sites and not re.search(r"(?i)\b(?:email|mail|send|share|forward)\b", sent):
                continue
        cands: list[Option] = []
        src = "none"
        kind = s.get("candidates")
        if kind == "folders":
            cands = [_option(i + 1, f, f) for i, f in enumerate(("Documents", "Desktop", "Downloads", "Pictures"))]
            src = "folders"
        elif kind == "people":
            ppl = [str(p) for p in (ctx.people if ctx.people is not None else _known_people()) if str(p).strip()][:r.max_candidates]
            cands = [_option(i + 1, p, p, "", True) for i, p in enumerate(ppl)]
            src = "people" if cands else "none"
        v = Verdict(kind="slot", rule="slot_" + str(s["id"]), title=str(s.get("title") or "Which one?"), question=str(s.get("question") or "Could you say more?"),
                    reason=str(s.get("reason") or ""), original=full, options=cands, source=src, text_label=str(s.get("label") or "Your answer"),
                    fill=str(s.get("fill") or "append"), template=str(s.get("template") or " {answer}"), insert_after=str(s.get("insert_after") or ""),
                    slot=str(s["id"]), timeout_s=r.ask_timeout_s)
        v.text_required = not cands
        return v
    return None


def _bare_request(sent: str, slot: dict, r: Rules) -> bool:
    """Is the sentence only the verb and the thing it makes ('make a pdf', 'make a 3-slide deck', 'create a report'), with nothing else that could be the topic?"""
    t = slot["_noun"].sub(" ", sent) if slot.get("_noun") is not None else sent
    for g in slot["verbs"]:
        if g in r.verb_rx:
            t = r.verb_rx[g].sub(" ", t)
    words = [w.lower() for w in re.findall(r"[\w\u0900-\u097f\u0c00-\u0c7f']+", t)]
    skip = r.determiners | {"please", "me", "now", "new", "quick", "short", "small", "big", "one", "page", "pages", "slide", "slides", "karo", "cheyyi", "cheyandi", "do", "oka", "ek", "for", "of"}
    return not [w for w in words if w not in skip and not re.fullmatch(r"\d+(?:-\w+)?", w)]


def _named_media(sent: str, m: re.Match, r: Rules) -> bool:
    """'play despacito' / 'play a relaxing song on YouTube' name what to play: more than the vague word, or an adjective before it, or a site."""
    before = sent[:m.start()].split()
    mid = [w.lower() for w in before[1:]]                          # words between the verb and the vague word
    return any(w not in r.determiners and w not in {"some", "any", "a", "the", "something", "anything"} for w in mid)


def _known_people() -> list[str]:
    try:
        from . import names
        st = names.get_store()
        order = {"family": 0, "friend": 1, "other": 2}
        ents = [e for e in st.entries() if e.get("kind") in order and float(e.get("confidence") or 0) >= 0.6]
        ents.sort(key=lambda e: (order[e["kind"]], -int(e.get("count") or 0)))
        return [str(e.get("canonical") or "") for e in ents if e.get("canonical")]
    except Exception:  # noqa: BLE001 - no names store here: the question is a text box
        return []


# ---------------------------------------------------------------------------------------------------------------------------------- own windows
_OWN = {"sig": None, "procs": {"jarvis", "jarvisface", "jarvis-face", "jarvis_face", "jarvis.face", "electron"},
        "browsers": {"chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc"}, "titles": [re.compile(r"^jarvis(?:\s*[-–|:]\s*|\s+(?:console|dashboard|editor|chat)\b|$)", re.I)]}


def _own_cfg() -> dict:
    try:
        st = OWN_FILE.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return _OWN
    if _OWN["sig"] == sig:
        return _OWN
    try:
        data = json.loads(OWN_FILE.read_text(encoding="utf-8-sig"))
        for key, slot in (("processes", "procs"), ("browser_processes", "browsers")):
            if isinstance(data.get(key), list):
                _OWN[slot] |= {re.sub(r"\.exe$", "", str(x).strip().lower()) for x in data[key] if isinstance(x, str) and x.strip()}
        for rx in data.get("title_regex") if isinstance(data.get("title_regex"), list) else []:
            try:
                _OWN["titles"].append(re.compile(str(rx), re.I))
            except re.error:
                continue
    except (OSError, ValueError, AttributeError):
        pass
    _OWN["sig"] = sig
    return _OWN


def own_window(process: Any, title: Any = "") -> str:
    """Why this window is JARVIS's own ("" when it is not): the same floor and file as the kernel's policy.own_window and the node's ownwin.py."""
    cfg = _own_cfg()
    proc = re.sub(r"\.exe$", "", str(process or "").strip().lower())
    if proc in cfg["procs"]:
        return "JARVIS's own window"
    if title and (not proc or proc in cfg["browsers"]) and any(rx.search(str(title).strip()) for rx in cfg["titles"]):
        return "JARVIS's own console window"
    return ""


# ------------------------------------------------------------------------------------------------------------------------------ recent files
SECRET_NAME = re.compile(r"(?i)pass(?:word|wd)?|secret|token|credential|private|\.pem$|\.key$|\.kdbx$|\.env$|id_rsa|wallet|seed|cookies?|login data")
KNOWN = ("Downloads", "Documents", "Desktop")


def recent_files(home: Path | None = None, flt: dict | None = None, limit: int = 12, now: float | None = None) -> list[tuple[str, int]]:
    """(name, size) of the newest plain files in Downloads, Documents and Desktop (never a hidden or secret-looking name, never a link): what a 'which file' form can offer.
    The same places and rules as the ask panel's own list (bridge/ask_spec.file_options), so the form and the answer agree. Names and sizes only."""
    home = Path(home) if home else Path.home()
    rows: list[tuple[float, str, int]] = []
    for folder in KNOWN:
        try:
            names = os.listdir(home / folder)
        except OSError:
            continue
        for name in names:
            if name.startswith(".") or SECRET_NAME.search(name):
                continue
            try:
                st = os.lstat(home / folder / name)
            except OSError:
                continue
            import stat as _stat
            if not _stat.S_ISREG(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
                continue
            rows.append((st.st_mtime, name, st.st_size))
    if flt:
        words = [w.lower() for w in flt.get("name_like") or []]
        if words:
            rows = [r for r in rows if any(w in r[1].lower() for w in words)]
        if flt.get("day"):
            span = _day_span(str(flt["day"]), now)
            if span:
                rows = [r for r in rows if span[0] <= r[0] < span[1]]
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(n, s) for _t, n, s in rows[:limit]]


def _day_span(day: str, now: float | None = None) -> tuple[float, float] | None:
    import datetime as dt
    today = dt.datetime.fromtimestamp(now if now is not None else time.time()).replace(hour=0, minute=0, second=0, microsecond=0)
    if day == "today":
        a, b = today, today + dt.timedelta(days=1)
    elif day == "yesterday":
        a, b = today - dt.timedelta(days=1), today
    elif day == "last_week":
        a, b = today - dt.timedelta(days=7), today
    else:
        return None
    return a.timestamp(), b.timestamp()


# ------------------------------------------------------------------------------------------------------------------------------------ check
def needs(text: str, rules: Rules | None = None) -> set[str]:
    """Which context the caller should fetch for `check` (a pure, cheap scan): 'windows' for a close request, 'last' for a pronoun object. Nothing else costs anything."""
    r = rules or load()
    out: set[str] = set()
    if not r.enabled:
        return out
    t = _mask(_clean(text))
    if r.verb_rx.get("close") and r.verb_rx["close"].search(t):
        out.add("windows")
        out.add("launched")
    if r.adjacent_rx.search(t) or (r.free_rx is not None and r.free_rx.search(t)):
        out.add("last")
    return out


def check(text: str, ctx: Context | None = None, rules: Rules | None = None) -> Verdict | None:
    """The question this request must be answered with before anything is done, or None when it can go on. Pure: no I/O unless `ctx.recent_files` is None and a file list is needed."""
    try:
        return _check(text, ctx or Context(), rules or load())
    except Exception:  # noqa: BLE001 - a bug here must not stop an ordinary request; the kernel and the planner still have their own rules
        return None


def _check(text: str, ctx: Context, r: Rules) -> Verdict | None:
    if not r.enabled:
        return None
    full = _clean(text)
    if not full:
        return None
    body, off = _strip_fillers(full, r)
    if not body.strip():
        return None
    masked = _mask(body)
    nm = _named(masked, r, ctx)
    sents, pos = [], 0
    for m in list(_SENT.finditer(masked)) + [None]:
        end = m.start() if m else len(masked)
        seg = masked[pos:end]
        if seg.strip():
            sents.append((pos, seg))
        pos = m.end() if m else end
    for start, seg in sents:
        s2, extra = _strip_fillers(seg, r)
        start += extra
        if _is_question(s2, r) or not s2.strip():
            continue
        base = start + off
        acts = _verbs_in(s2, r)
        v = _either_or(s2, base, full, r, nm, ctx)
        if v is None:
            v = _destructive_without_object(s2, base, full, r, nm, ctx, acts) if not r.safe_rx_match(s2) else None
        if v is None:
            v = _close(s2, base, full, r, nm, ctx)
        if v is None:
            v = _reference(s2, base, full, r, nm, ctx, acts, masked)
        if v is None:
            v = _slot(s2, base, full, r, nm, ctx, acts)
        if v is not None:
            v.original = full
            v.timeout_s = r.ask_timeout_s
            return v
    return None


def _safe_rx_match(self: Rules, s: str) -> bool:
    body = s.strip(" .!?")
    return any(rx.fullmatch(body) for rx in self.safe_rx)


Rules.safe_rx_match = _safe_rx_match      # type: ignore[attr-defined]


# ------------------------------------------------------------------------------------------------------------------------------- the forms
def form(v: Verdict) -> dict:
    """The ask form (ask_form.py, kind jarvis_ask) for the chat model and the reliability harness: the question and GENERIC labels only (app names the owner said, folders, a
    count). A file name, a window title or a person's name stays on the owner's own panel (`spec`)."""
    labels = [o.label for o in v.options if not o.private]
    f = ask_form.build_form(v.question, labels, reason=v.reason, source="ambiguity", allow_other=True)
    f["options"] = [{"id": o["id"], "label": o["label"]} for o in f["options"]]          # no 'value' key: the plugin result is cleaned by key
    f["rule"] = v.rule
    f["options_on_panel"] = len(v.options)
    return f


def spec(v: Verdict) -> dict:
    """The panel form (bridge/ask_spec.py language): one choice (a radio, a tick list, or the newest files by name and size), a 'Something else' text box, Continue and Cancel.
    It collects a choice; it approves nothing (D55)."""
    fields: list[dict] = []
    if v.source == "files":
        node: dict[str, Any] = {"id": "choice", "type": "file_choice", "source": "recent", "label": "Newest files (name and size)"}
        if v.file_filter:
            node["filter"] = v.file_filter
        fields.append(node)
    elif v.options:
        opts = [{"id": o.id, "label": o.label, **({"detail": o.detail} if o.detail else {})} for o in v.options]
        if v.multiple:
            fields.append({"id": "choice", "type": "checkbox_group", "label": "Tick the ones you mean", "options": opts})
        else:
            short = len(opts) <= 4 and all(len(o["label"]) <= 20 and "detail" not in o for o in opts)
            fields.append({"id": "choice", "type": "radio", "variant": "segmented" if short else "list", "label": "Choose one", "options": opts})
    fields.append({"id": "other", "type": "text", "label": v.text_label if v.options or v.source == "files" else (v.text_label if v.text_label != "Something else" else "Your answer"),
                   "max_len": 200, "required": bool(v.text_required and not v.options and v.source != "files")})
    return {"title": v.title, "intro": v.question, "timeout_s": int(v.timeout_s), "fields": fields,
            "buttons": [{"id": "ok", "label": "Continue", "style": "primary"}, {"id": "cancel", "label": "Cancel", "style": "secondary"}]}


def _path_text(p: str) -> str:
    p = str(p)
    return f'"{p}"' if re.search(r"\s", p) and not p.startswith('"') else p


def resolve(v: Verdict, answer: Any) -> list[str]:
    """The request with the owner's answer put in: a list (several only for 'close these windows'); [] when he cancelled, closed the form or gave no usable answer. `answer` is
    what the ask broker returns: {answered, button, values: {choice, other}}."""
    if not isinstance(answer, dict) or answer.get("answered") is not True or str(answer.get("button") or "ok") == "cancel":
        return []
    vals = answer.get("values") if isinstance(answer.get("values"), dict) else {}
    typed = _WS.sub(" ", str(vals.get("other") or "")).strip()[:200]
    chosen = vals.get("choice")
    answers: list[str] = []
    if typed:
        answers = [typed]
    elif isinstance(chosen, list):
        byid = {o.id: o for o in v.options}
        answers = [byid[c].value for c in chosen if c in byid][:8]
    elif isinstance(chosen, str) and chosen:
        byid = {o.id: o for o in v.options}
        if chosen in byid:
            answers = [byid[chosen].value]
        elif v.source == "files" and re.search(r"[\\/]", chosen):
            answers = [_path_text(chosen)]
    if not answers:
        return []
    out = []
    for a in answers:
        out.append(_apply(v, a))
    return [o for o in out if o]


def _tidy(text: str) -> str:
    """One line, no space before a full stop or comma ('Open Notepad .' is 'Open Notepad.')."""
    return re.sub(r"\s+([.,;:!?])", r"\1", _WS.sub(" ", text)).strip()


def _apply(v: Verdict, answer: str) -> str:
    o = v.original
    new_piece = v.template.format(answer=answer) if "{answer}" in v.template else answer
    if v.fill == "whole":
        return _WS.sub(" ", new_piece).strip()
    if v.fill == "append":
        return (o.rstrip(" .!?") + new_piece).strip()
    if v.fill == "insert_after" and v.insert_after:
        try:
            m = re.search(v.insert_after, o, re.I)
        except re.error:
            m = None
        if m:
            return (o[:m.end()] + new_piece + o[m.end():]).strip()
        return (o.rstrip(" .!?") + new_piece).strip()
    if v.fill == "replace_vague_media":
        m = load().vague_media_rx.search(o)
        if m:
            return (o[:m.start()] + answer + o[m.end():]).strip()
        return (o.rstrip(" .!?") + " " + answer).strip()
    if v.span and 0 <= v.span[0] <= v.span[1] <= len(o):
        s, e = v.span
        piece = answer if v.kind != "destructive" or e > s else answer
        left = o[:s].rstrip()
        right = o[e:].lstrip()
        return _tidy(f"{left} {piece} {right}")
    return (o.rstrip(" .!?") + " " + answer).strip()


# ----------------------------------------------------------------------------------------------------------- the decider's / planner's own question
_CHOICE_Q = re.compile(r"(?is)^\s*which one\s*[:?]\s*(.{1,60}?)\s+or\s+(.{1,60}?)\s*\??\s*$")
_FILE_Q = re.compile(r"(?i)^\s*which (file|document|pdf|one)\b")


def clarify_verdict(question: str, original: str = "", ctx: Context | None = None, rules: Rules | None = None) -> Verdict:
    """The decider's 'ask' outcome (confidence below its threshold) or the planner's clarify question, as the SAME kind of form: 'Which one: A or B?' becomes a two-way choice,
    'Which file?' lists the newest files by name and size, anything else is a text box. Never an empty answer."""
    r = rules or load()
    ctx = ctx or Context()
    q = _clean(question)[:300] or "Could you say a bit more?"
    m = _CHOICE_Q.match(q)
    if m:
        a, b = _display_app(m.group(1).strip(" ?.")), _display_app(m.group(2).strip(" ?."))
        v = Verdict(kind="clarify", rule="clarify_choice", title="Which one?", question=q, reason="", original=_clean(original), options=[_option(1, a, a), _option(2, b, b)],
                    fill="append", template="\n(The owner's clarification: {answer})", timeout_s=r.ask_timeout_s)
        return v
    if _FILE_Q.match(q):
        files = _recent(ctx, r)
        return Verdict(kind="clarify", rule="clarify_file", title="Which file?", question=q, reason="", original=_clean(original), source="files" if files else "none",
                       fill="append", template="\n(The owner's clarification: {answer})", text_required=not files, timeout_s=r.ask_timeout_s)
    return Verdict(kind="clarify", rule="clarify_text", title="JARVIS asks", question=q, reason="", original=_clean(original), text_required=True, text_label="Your answer",
                   fill="append", template="\n(The owner's clarification: {answer})", timeout_s=r.ask_timeout_s)
