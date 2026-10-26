"""D54 (2026-10-26): the catalogue of everyday simple PC tasks, read from jarvis/config/simple_tasks.json.

One data file says what each task is called, how the owner may phrase it, its effect class (A ordinary, B only with his own words, C always the PIN,
ask = explained and not done), how it is done (a HANDLER named here plus its parameters) and how it is verified. More tasks are added to the file with an
existing handler, no code. This module has no I/O of its own beyond reading that file: every effect goes through the `Env` seams (kernel jobs, the
scheduler, the vault inbox), which the plugin fills in and the tests fake.

    match(request)      -> the ONE entry the whole request means, with its arguments (None when none or when two fit equally)
    plan_for(request)   -> what the decider does with it: a step (tool "simple_task"), legacy steps (window, press_key, open_file ...), a question, or a refusal
    run(step, env)      -> the result {"ok", "facts", "unverified", "expected_missing", ...}; the coordinator's verify() reads only these

Nothing here grants anything. The kernel computes the grant from the owner's words (the goal rides in every job the Pc sends, D54), so a task whose class
is B or C is still held there; the catalogue's class is documentation and the order in which a conflict is settled (C beats ask beats B beats A).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from pathlib import Path

from . import composite

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CLASSES = ("A", "B", "C", "ask")
RANK = {"C": 3, "ask": 2, "B": 1, "A": 0}                # a conflict between two matching tasks is settled by the more careful class first
Q = r"\"[^\"]{1,160}\"|'[^']{1,160}'|“[^”]{1,160}”"
KIND_RX = {
    "item": rf"(?:{Q}|[\w][\w .,'()&+\-]{{0,100}}?)",
    "file": rf"(?:{Q}|[\w][\w.()&+\-]{{0,78}})",
    "folder": r"(?:the |my )?[A-Za-z][\w\-/\\]*(?: [A-Za-z][\w\-/\\]*){0,2}?(?: folder| directory)?",
    "text": r".+?",
    "app": r"[A-Za-z][\w.+\-]*(?: [A-Za-z][\w.+\-]*){0,2}?",
    "n": r"\d{1,5}(?:\.\d+)?",
    "site": r"(?![\w\-.]+\.(?:txt|md|docx?|xlsx?|pptx?|pdf|png|jpe?g|gif|bmp|zip|csv|json|py|js|html?|css|log|exe|msi|mp[34]|wav|mkv|avi)(?:\s|$))(?:https?://)?(?:[A-Za-z0-9\-]+\.)+[A-Za-z]{2,}(?:/\S*)?|[A-Za-z][A-Za-z0-9]{1,30}(?: [a-z]{3,10})?",
    "when": r"(?:tomorrow |today |tonight )?(?:at )?\d{1,2}(?::\d{2})?\s?(?:am|pm)?|noon|midnight",
}
PH = re.compile(r"\{(\w+):(\w+)\}")
LEAD = r"(?i)^\s*(?:(?:hey |ok |okay )?jarvis[, ]+)?(?:(?:please|can you|could you|would you|kindly|now|just)[, ]+)*"
TAIL = r"(?: please| for me| now| thanks| thank you)?[\s.!?]*$"
FREE_TEXT = ("text",)                                     # the arguments that are the owner's own words (a note, text to type): "and" inside them is just a word
COMPOUND = re.compile(r"(?i)\b(?:and|then)\s+(?:then\s+)?(?:move|copy|delete|remove|rename|open|type|write|save|send|play|close|create|make|zip|unzip|run|click|turn|set|find|show|take|go|put|email|post)\b")
FOLDER_RESOLVER = None                                    # phrase -> a known folder's name or None (set by decide.py to the shared decision engine, D54b)
HANDLERS: dict = {}                                       # filled at the end of the file: handler name -> function(entry, args, env, step)
VAGUE_ITEM = re.compile(r"(?i)^(?:it|that|this|them|those|these|stuff|things?|junk|the rest|everything|anything|something|some|all|every\w*|any\w*|"
                        r"rest|others?|(?:all|some|the)? ?(?:old|big|large|useless|unused|random|other|extra) (?:stuff|things?|files?|folders?|junk|items?)|"
                        r"(?:all|some|my|the) ?(?:files?|folders?|items?|stuff|things?|documents?))$")
SPECIAL_RX = {"n": re.compile(r"^\d")}


OUTSIDE = re.compile(r"(?i)^\W*(?:(?:please|can you|could you|just)[, ]+)*(?:delete|remove|erase|trash|move|copy|rename|zip|unzip|extract|wipe)\b.*"
                     r"(?:\b[a-z]:[\\/]|\b[a-z]:\s*$|\\\\|\.\.[\\/]|\s[\\/][\w.]|~[\\/])")


class Unavailable(Exception):
    """An Env seam the caller did not provide."""


class Env:
    """The seams a handler uses. Anything not given raises Unavailable(name), which becomes an honest "cannot" result and never a guess.
    pc(body, timeout=90, keep_local=False) -> dict      one kernel job as the owner (the goal rides with it); {"ok", "state", ...node facts...}
    agent(tool, step) -> dict                           another step runner of the plugin (window, drive, ...)
    process(app) -> str | None                          the process name of an open window that fits the app the owner named
    front() -> str | None                               the process of the window in front (never JARVIS's own)
    window_text(app) -> str                             the text of one window, read locally (never sent anywhere)
    now() -> datetime (aware)                           the clock
    scheduler_create(definition) -> (status, dict)      the kernel's automations
    scheduler_list() -> list
    vault_dir() -> str                                  the memory vault's folder (its inbox/ is written; nothing else)
    scrub(text) -> str                                  the secret scrubber"""

    def __init__(self, **seams):
        self.__dict__.update(seams)

    def __getattr__(self, name):
        raise Unavailable(name)


# ---------------------------------------------------------------------------------------------------------------- loading
_CACHE: dict = {"sig": None, "tasks": [], "problems": []}


def catalogue_path() -> Path:
    return Path(os.environ.get("JARVIS_SIMPLE_TASKS_FILE") or CONFIG_DIR / "simple_tasks.json")


def _compile(phrase: str) -> "re.Pattern":
    seen: set = set()

    def sub(m):
        kind, name = m.groups()
        if kind not in KIND_RX:
            raise ValueError(f"unknown placeholder kind {kind}")
        if name in seen:
            return f"(?P={name})"
        seen.add(name)
        return f"(?P<{name}>{KIND_RX[kind]})"
    return re.compile(LEAD + PH.sub(sub, phrase) + TAIL)


def _literal(phrase: str) -> int:
    """How much of the phrase is fixed words: the more specific phrase wins when two fit ("... for good" beats a bare "delete X")."""
    return len(re.sub(r"\{\w+:\w+\}|\(\?P<\w+>[^()]*(?:\([^()]*\)[^()]*)*\)|[\\^$|?*+()\[\]{}]|\(\?:", "", phrase))


def problems_in(data) -> list[str]:
    """What is wrong with a catalogue file, one line each (also what load() skips). A bad entry never stops the others."""
    out = []
    tasks = (data or {}).get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        return ["the file has no 'tasks' list"]
    ids = set()
    for i, t in enumerate(tasks):
        tid = t.get("id") if isinstance(t, dict) else None
        if not isinstance(t, dict) or not isinstance(tid, str) or not re.fullmatch(r"[a-z0-9_]{2,40}", tid):
            out.append(f"entry {i}: it has no usable id")
            continue
        if tid in ids:
            out.append(f"{tid}: the id is used twice")
        ids.add(tid)
        if t.get("class") not in CLASSES:
            out.append(f"{tid}: class must be one of {', '.join(CLASSES)}")
        how = t.get("how") if isinstance(t.get("how"), dict) else {}
        if how.get("handler") not in HANDLERS:
            out.append(f"{tid}: no handler named {how.get('handler')!r}")
        if not t.get("phrases") or not isinstance(t.get("phrases"), list):
            out.append(f"{tid}: it has no phrases")
        else:
            for p in t["phrases"]:
                try:
                    _compile(p)
                except (re.error, ValueError, TypeError) as e:
                    out.append(f"{tid}: a phrase does not compile ({e})")
        if not t.get("examples"):
            out.append(f"{tid}: it has no examples")
        if not isinstance(t.get("verify"), str) or not t.get("verify"):
            out.append(f"{tid}: it says nothing about how it is verified")
        if not isinstance(t.get("needs_laptop"), bool):
            out.append(f"{tid}: needs_laptop must be true or false")
    return out


def load(path: Path | None = None) -> list[dict]:
    """The catalogue's entries, each with its compiled phrases ('_rx') and fixed-word counts; cached until the file changes. A bad entry is skipped
    and listed by problems()."""
    p = Path(path) if path else catalogue_path()
    try:
        st = p.stat()
        sig = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        _CACHE.update(sig=None, tasks=[], problems=[f"the catalogue {p.name} could not be read"])
        return []
    if _CACHE["sig"] == sig:
        return _CACHE["tasks"]
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        _CACHE.update(sig=sig, tasks=[], problems=[f"the catalogue {p.name} is not valid JSON"])
        return []
    bad = problems_in(data)
    tasks, seen = [], set()
    for t in data.get("tasks") or []:
        if not isinstance(t, dict) or not isinstance(t.get("id"), str) or t["id"] in seen or problems_in({"tasks": [t]}):
            continue                                       # one bad (or repeated) entry is skipped and reported; the others still work
        try:
            t = dict(t, _rx=[(_compile(ph), _literal(ph)) for ph in t["phrases"]])
        except (re.error, ValueError, TypeError, KeyError):
            continue
        seen.add(t["id"])
        tasks.append(t)
    _CACHE.update(sig=sig, tasks=tasks, problems=bad)
    return tasks


def problems() -> list[str]:
    load()
    return list(_CACHE["problems"])


def get(task_id: str) -> dict | None:
    return next((t for t in load() if t["id"] == task_id), None)


# ---------------------------------------------------------------------------------------------------------------- reading a request
def _strip(v: str) -> str:
    v = " ".join(str(v or "").split())
    if len(v) >= 2 and ((v[0] == v[-1] and v[0] in "\"'") or (v[0] == "“" and v[-1] == "”")):
        v = v[1:-1]
    return v.strip().strip(".,;!?").strip()


def _app(v: str) -> str:
    v = _strip(v).lower()
    v = re.sub(r"^(?:the|my)\s+", "", v)
    return re.sub(r"\s+(?:app|window|application)$", "", v).strip()


def _folder_word(v: str) -> str:
    return re.sub(r"(?i)\s+(?:folder|directory)$", "", _strip(v))


def canonical(args: dict) -> dict:
    """The arguments as the rest of the system wants them: folders as their known names, apps in lower case, quotes and end marks gone, empty ones left out."""
    out = {}
    for k, v in args.items():
        if v is None or str(v).strip() == "":
            continue
        if k in ("target", "dest"):
            w = _folder_word(v)
            spec, why = composite.folder_spec(w)
            out[k] = spec if not why else w
        elif k in ("app", "app2"):
            out[k] = _app(v)
        elif k in ("path", "newdir"):
            out[k] = _strip(v)
        elif k == "text":
            out[k] = _strip(v)
        elif k in ("unit", "size", "age", "from", "to", "when", "n", "site"):
            out[k] = _strip(v).lower() if k != "site" else _strip(v)
        else:
            out[k] = _strip(v)
    return out


class Hit(dict):
    """A task the request means: id, the entry, its arguments."""


def match(request: str, path: Path | None = None) -> Hit | None:
    """The one entry the WHOLE request means. Several can fit ("delete old.txt for good" fits the bin and the permanent delete): the more careful class
    wins (C, then ask, then B), then the phrase with more fixed words; two equal fits mean no match (the decider then asks or lets the model plan)."""
    req = " ".join(str(request or "").split())
    if not req or len(req) > 1200:
        return None
    found = []
    for t in load(path):
        best = None
        for rx, lit in t["_rx"]:
            m = rx.match(req)
            if m and (best is None or lit > best[0]):
                best = (lit, m)
        if best:
            found.append((RANK[t["class"]], best[0], t, best[1]))
    if not found:
        return None
    beaten = {b for f in found for b in f[2].get("beats") or []}       # an entry can say which others it overrides when both fit
    found = [f for f in found if f[2]["id"] not in beaten] or found
    found.sort(key=lambda f: (f[0], f[1]), reverse=True)
    if len(found) > 1 and found[0][:2] == found[1][:2] and found[0][2]["how"].get("handler") != found[1][2]["how"].get("handler"):
        return None
    _, _, t, m = found[0]
    args = canonical(m.groupdict())
    if any(COMPOUND.search(str(v)) for k, v in args.items() if k not in FREE_TEXT):        # "rename a to b and move it ...": two tasks in one sentence are planned, not matched
        return None
    return Hit(id=t["id"], entry=t, args=args)


# ---------------------------------------------------------------------------------------------------------------- what the decider does with a match
def _legacy(entry: dict, args: dict) -> list[dict] | None:
    """Tasks the plugin already had a step for (window, media key, open file/site) are planned as that step: the same agent, the same verification."""
    how, h = entry["how"], entry["how"]["handler"]
    if h == "window":
        return [{"tool": "window", "action": how["action"], "app": args.get("app", "")}]
    if h == "media_key":
        return [{"tool": "press_key", "key": how["key"], "text": how.get("intent") or how["key"]}]
    if h == "open_file":
        return [{"tool": "open_file", "name": args.get("path", ""), "folder": args.get("target", "")}]
    if h == "open_site" and how.get("search"):
        from urllib.parse import quote_plus
        return [{"tool": "open_website", "site": "https://www.google.com/search?q=" + quote_plus(fill(how["search"], args)), "account": ""}]
    if h == "open_site":
        return [{"tool": "open_website", "site": args.get("site", ""), "account": ""}]
    return None


def plan_for(request: str, path: Path | None = None) -> dict | None:
    """None when the catalogue does not know the request. Otherwise one of
       {"status": "ok", "steps": [...], "task": id, "class": c}      steps for validate()/the coordinator
       {"status": "clarify", "question": ...}                          a missing or vague part, or a task that is explained and not done
       {"status": "fallback", "problems": [...]}                       a task the catalogue lists honestly as not built"""
    hit = match(request, path)
    if hit is None:
        if OUTSIDE.search(str(request or "")):               # a place that is not a known folder is asked about, never guessed or handed to the model
            return {"status": "clarify", "question": composite.folder_question(), "task": "", "class": "ask"}
        return None
    e, a, h = hit["entry"], hit["args"], hit["entry"]["how"]["handler"]
    base = {"task": e["id"], "class": e["class"]}
    if h == "ask":
        return dict(base, status="clarify", question=e["how"].get("say") or "I would rather you decide that yourself.")
    if h == "unavailable":
        return dict(base, status="fallback", problems=[e["how"].get("say") or "That is not built yet."])
    for k in ("path", "newdir"):
        if k in a and VAGUE_ITEM.match(a[k]):
            return dict(base, status="clarify", question="Which one do you mean? Name the file or folder, and the folder it is in if you can.")
    if h in ("file_op", "open_file") and "path" in a and re.search(r"[\\/:*?\"<>|]|\.\.", a["path"]):
        return dict(base, status="clarify", question="I can only work on one item inside " + composite.ROOTS_TEXT + ". Tell me its name and which of those folders it is in.")
    for k in ("target", "dest"):
        if k in a:
            spec, why = composite.folder_spec(a[k])
            if why and not (k == "dest" and re.fullmatch(r"[\w .\-]{1,60}", a[k])):
                guess = FOLDER_RESOLVER(a[k]) if (FOLDER_RESOLVER and why == "outside" and re.fullmatch(r"[\w .\-]{1,60}", a[k])) else None
                if guess:                                   # D54b: the shared decision engine named a known folder for a vague phrase, confidently
                    a[k] = guess
                    continue
                return dict(base, status="clarify", question=composite.folder_question(a[k]))
    if h in ("setting", "file_op") and e["id"] == "brightness" and a.get("n") and not 0 <= int(float(a["n"])) <= 100:
        return dict(base, status="clarify", question="Brightness is a number from 0 to 100. Which one?")
    legacy = _legacy(e, a)
    if legacy:
        return dict(base, status="ok", steps=legacy, legacy=True)
    if e["class"] == "C" or h == "held":                   # always the owner's PIN: the loop does the preparing, the kernel holds the step that matters
        return dict(base, status="ok", steps=[{"tool": "drive", "app": "", "query": " ".join(str(request).split())[:600]}], held=True)
    return dict(base, status="ok", steps=[{"tool": "simple_task", "task": e["id"], "args": a, "query": " ".join(str(request).split())[:600]}])


# ---------------------------------------------------------------------------------------------------------------- results
def out(ok: bool, facts=(), unverified=(), missing=(), **kw) -> dict:
    return {"ok": bool(ok), "facts": [str(f) for f in facts], "unverified": [str(u) for u in unverified], "expected_missing": [str(m) for m in missing], **kw}


def held(r: dict) -> dict | None:
    """What the kernel holding (or refusing) a job looks like to the caller: the coordinator turns it into 'waiting for your PIN'."""
    if r.get("state") in ("waiting_for_permission", "denied"):
        return {"ok": False, "state": r["state"], "approval_id": r.get("approval_id"), "how": r.get("how"), "error": str(r.get("error") or r["state"])[:200],
                "facts": [], "unverified": [], "expected_missing": []}
    return None


def _mb(n) -> str:
    n = float(n or 0)
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.1f} MB" if n >= 1024 ** 2 else f"{n / 1024:.0f} KB"


def _in(label) -> str:
    return f" in {label}" if label else ""


def _left(sk) -> str:
    """What a sort left alone, in words: files still downloading or just changed, hidden ones, and kinds with no folder."""
    sk = sk if isinstance(sk, dict) else {}
    words = [f"{sk[k]} {w}" for k, w in (("recent", "changed in the last 2 minutes (maybe still downloading)"), ("other", "of a kind with no folder"), ("hidden", "hidden or half-downloaded")) if sk.get(k)]
    return ("; left alone: " + ", ".join(words)) if words else ""


FACT = {
    "mkdir": lambda r: ([f"the folder {r.get('created')} exists{_in(r.get('folder'))}" + (" (that name was taken, so it got a number)" if r.get("renamed") else "")], []),
    "create_file": lambda r: ([f"the file {r.get('created')} is on disk{_in(r.get('folder'))}" + (" (that name was taken, so it got a number)" if r.get("renamed") else "") +
                               (" and its text was read back" if r.get("content_ok") else "")], [] if r.get("content_ok") else ["its text was not compared"]),
    "rename": lambda r: ([f"it is now called {r.get('renamed_to')}{_in(r.get('folder'))}" + (" (the new name was taken, so it got a number)" if r.get("numbered") else "")], []),
    "move": lambda r: ([f"{r.get('moved')} moved from {r.get('from')} to {r.get('to')}" + (" (a number was added: nothing was replaced)" if r.get("numbered") else "")], []),
    "copy": lambda r: ([f"{r.get('copied')} copied from {r.get('from')} to {r.get('to')}" + (" (a number was added: nothing was replaced)" if r.get("numbered") else "")], []),
    "zip": lambda r: ([f"the archive {r.get('archive')}{_in(r.get('folder'))} holds {r.get('files')} file(s)"], []),
    "unzip": lambda r: ([f"{r.get('files')} file(s) were unpacked into the new folder {r.get('unpacked_to')}{_in(r.get('folder'))}"], []),
    "sort_downloads": lambda r: ([f"{r.get('moved')} file(s) moved in Downloads: " + (", ".join(f"{k} {v}" for k, v in (r.get('by_type') or {}).items()) or "nothing to sort") +
                                  f"; an undo list is kept (say \"undo the last sort\")" + _left(r.get("skipped"))], []),
    "undo": lambda r: ([f"{r.get('undone')} move(s) put back"], []),
    "reveal": lambda r: ([], [f"Explorer was asked to show {r.get('shown')}; whether its window opened is not checked"]),
    "trash": lambda r: ([f"{r.get('trashed')} is gone from {r.get('folder')} and is in the Recycle Bin (you can restore it)"], []),
    "delete_permanent": lambda r: ([f"{r.get('deleted')} was deleted for good from {r.get('folder')} ({_mb(r.get('bytes'))}); it is not in the Recycle Bin"], []),
    "empty_bin": lambda r: ([f"the Recycle Bin is empty ({r.get('items_before')} item(s) were in it)"], []),
}


def _cleanup_facts(r: dict) -> tuple[list, list]:
    done = ", ".join(f"{x['label']} {_mb(x['freed_bytes'])}" for x in r.get("results") or [] if x.get("freed_bytes"))
    facts = [f"freed {_mb(r.get('freed_bytes'))} from the safe list" + (f" ({done})" if done else "")]
    sug = r.get("suggestions") or []
    loose = []
    if sug:
        facts.append("not deleted, for you to decide: " + "; ".join(f"{s.get('kind')} {s.get('name')} ({_mb(s.get('bytes'))})" for s in sug[:6]) + (f" and {len(sug) - 6} more" if len(sug) > 6 else ""))
    return facts, loose


# ---------------------------------------------------------------------------------------------------------------- handler: file_op
TPL = re.compile(r"\$(\w+)(?:\.(\w+))?(?:\|([^|$/]*))?")


def fill(tpl, args: dict, result: dict | None = None) -> str:
    def sub(m):
        name, attr, default = m.groups()
        v = (result or {}).get(attr) if name == "result" and attr else args.get(name)
        return str(v) if v not in (None, "") else (default or "")
    return TPL.sub(sub, str(tpl))


def _locate(env: Env, name: str) -> tuple[dict | None, str]:
    """An item the owner named without a folder: found in the known folders by its exact name. One hit -> (hit, ""); none or several -> (None, why)."""
    r = env.pc({"op": "file", "action": "find", "target": "all", "path": name, "text": ""}, 60)
    if not r.get("ok"):
        return None, str(r.get("error") or "the search failed")[:160]
    want = name.lower()
    hits = [h for h in r.get("matches") or [] if isinstance(h, dict) and str(h.get("name", "")).lower() in (want, os.path.splitext(want)[0] if "." in want else want)]
    exact = [h for h in hits if str(h.get("name", "")).lower() == want] or hits
    if not exact:
        return None, f"no item called {name} is in {composite.ROOTS_TEXT}"
    if len(exact) > 1:
        return None, f"more than one item is called {name} (in " + ", ".join(sorted({str(h.get('folder')) for h in exact})) + "): which folder is it in?"
    return exact[0], ""


def filters_from(args: dict) -> str:
    """"bigger than 100 mb" + "older than 90 days" -> the node's filter text "size>100 mb;older:90d" (a fixed little language, never the owner's words)."""
    parts = []
    m = re.fullmatch(r"(?:bigger|larger) than (\d{1,4}) ?(kb|mb|gb)", str(args.get("size", "")))
    if m:
        parts.append(f"size>{m.group(1)} {m.group(2)}")
    m = re.fullmatch(r"(?:in the )?last (\d{1,3}) days", str(args.get("age", "")))
    if m:
        parts.append(f"newer:{m.group(1)}d")
    m = re.fullmatch(r"older than (\d{1,4}) days", str(args.get("age", "")))
    if m:
        parts.append(f"older:{m.group(1)}d")
    return ";".join(parts)


def h_file_op(entry, args, env, step):
    how, result, facts, loose, missing = entry["how"], {}, [], [], []
    args = dict(args, filters=filters_from(args))
    tgt_default = args.get("target") or "Documents"
    for s in how.get("steps") or []:
        op = s["op"]
        body = {"op": "file", "action": op, "target": fill(s.get("target", ""), args, result), "path": fill(s.get("path", ""), args, result),
                "dest": fill(s.get("dest", ""), args, result), "text": fill(s.get("text", ""), args, result)}
        if op in ("move", "copy") and body["dest"] and not composite.folder_spec(body["dest"])[0]:
            body["dest"] = f"{tgt_default}/{body['dest']}"                 # "into backup": a folder inside the one the item is in
        if op in ("trash", "delete_permanent", "reveal", "rename", "move", "copy", "zip", "unzip") and not body["target"]:
            hit, why = _locate(env, body["path"])
            if hit is None:
                return out(False, error=why, expected_missing=[f"the item {body['path']}"])
            body["target"], body["path"] = str(hit.get("folder")), str(hit.get("path_rel") or hit.get("name"))
        r = env.pc(body, 180)
        h = held(r)
        if h:
            return h
        if not r.get("ok"):
            return out(False, facts, loose, [f"{op} {body['path']}".strip()], error=str(r.get("error") or r.get("state") or "it did not work")[:240])
        result = r
        if op == "find":
            ms = r.get("matches") or []
            if how.get("reveal_first") and len(ms) == 1:
                rr = env.pc({"op": "file", "action": "reveal", "target": str(ms[0].get("folder")), "path": str(ms[0].get("path_rel") or ms[0].get("name")), "text": ""}, 60)
                hh = held(rr)
                if hh:
                    return hh
                f2, l2 = FACT["reveal"](rr) if rr.get("ok") else ([], ["Explorer could not be asked to show it"])
                facts += [f"found {ms[0].get('name')} in {ms[0].get('folder')}"] + f2
                loose += l2
            elif ms:
                facts.append(f"{len(ms)} match(es): " + "; ".join(f"{m.get('name')} in {m.get('folder')} ({_mb(m.get('bytes'))}, {m.get('modified')})" for m in ms[:8]) +
                             (f" and {len(ms) - 8} more" if len(ms) > 8 else ""))
                if how.get("reveal_first") and len(ms) > 1:
                    facts.append("several items fit, so none was opened in Explorer: tell me which one")
            else:
                if how.get("reveal_first"):
                    return out(False, error="no item with that name is in the known folders", expected_missing=["a matching item"])
                facts.append("nothing with that name was found in the known folders")
            continue
        if op == "cleanup":
            f2, l2 = _cleanup_facts(r)
        else:
            f2, l2 = FACT[op](r)
        facts += f2
        loose += [x for x in l2 if x]
        if r.get("verified") is False and op not in ("reveal",):
            missing.append(op)
    return out(not missing, facts, loose, missing, result={k: v for k, v in result.items() if isinstance(v, (str, int, bool))})


# ---------------------------------------------------------------------------------------------------------------- handler: setting, keys, windows
def h_setting(entry, args, env, step):
    how = entry["how"]
    value = fill(how.get("value", ""), args)
    r = env.pc({"op": "setting", "action": how["op"], "text": value}, 90)
    h = held(r)
    if h:
        return h
    if not r.get("ok"):
        return out(False, error=str(r.get("error") or r.get("state") or "it did not work")[:240], expected_missing=[f"{how['op']} to take effect"])
    name = str(r.get("setting") or how["op"])
    if r.get("read_back") is True:
        return out(True, [f"{name}: Windows' own value was read back"])
    if r.get("verified") is False:
        return out(True, [], [f"{name}: {r.get('note') or 'requested, but Windows gives no read-back'}"], partial=bool(r.get("partial")))
    return out(True, [f"{name} done"])


def _key(env: Env, keys: str) -> dict:
    return env.pc({"op": "ui", "action": "key", "target": "desktop|", "text": keys}, 30)


def h_keys(entry, args, env, step):
    r = _key(env, entry["how"]["keys"])
    h = held(r)
    if h:
        return h
    if not r.get("ok"):
        return out(False, error=str(r.get("error") or "the key was not sent")[:200], expected_missing=[entry["name"]])
    return out(True, [], [f"{entry['how']['keys']} was sent to the desktop; its effect is not read back"])


def _focus(env: Env, app: str) -> dict:
    return env.agent("window", {"tool": "window", "action": "focus", "app": app})


def h_snap(entry, args, env, step):
    app = args.get("app", "")
    f = _focus(env, app)
    if held(f):
        return held(f)
    if not f.get("ok"):
        return out(False, error=str(f.get("error") or f"no open window for {app}")[:200], expected_missing=[f"the {app} window in front"])
    r = _key(env, "win+" + entry["how"]["side"])
    if held(r):
        return held(r)
    if not r.get("ok"):
        return out(False, [f"the {app} window was brought forward"], [], ["the snap"], error=str(r.get("error") or "")[:200])
    return out(True, [f"the {app} window was brought forward"], [f"Win+{entry['how']['side'].title()} was sent; the final position is not read back"])


def h_arrange(entry, args, env, step):
    apps = [a for a in (args.get("app"), args.get("app2")) if a]
    facts = []
    for app, side in zip(apps, ("left", "right")):
        f = _focus(env, app)
        if held(f):
            return held(f)
        if not f.get("ok"):
            return out(False, facts, [], [f"the {app} window"], error=str(f.get("error") or f"no open window for {app}")[:200])
        r = _key(env, "win+" + side)
        if held(r):
            return held(r)
        if not r.get("ok"):
            return out(False, facts, [], [f"the {app} snap"], error=str(r.get("error") or "")[:200])
        facts.append(f"{app} was brought forward and snapped {side}")
    return out(True, facts, ["the final positions are not read back"])


def h_close_app(entry, args, env, step):
    app = args.get("app", "")
    proc = env.process(app)
    if not proc:
        return out(False, error=f"no open window for {app}", expected_missing=[f"the {app} window to be found"])
    r = env.pc({"op": "ui", "action": "close", "target": f"{proc}|", "text": ""}, 60)
    h = held(r)
    if h:
        return h
    if not r.get("ok"):
        return out(False, error=str(r.get("error") or "it did not close")[:200], expected_missing=[f"the {app} window closed"])
    if r.get("closed") is True:
        return out(True, [f"the {app} window closed"])
    return out(False, error=f"the {app} window is still open: it probably asks whether to save. I left it open; that question is yours", expected_missing=[f"the {app} window closed"])


# ---------------------------------------------------------------------------------------------------------------- handler: screen, clipboard, typing
def h_screenshot(entry, args, env, step):
    r = env.pc({"op": "screenshot"}, 60)
    h = held(r)
    if h:
        return h
    if not r.get("ok"):
        return out(False, error=str(r.get("error") or "the screenshot failed")[:200], expected_missing=["a picture file"])
    loose = ["the whole screen was taken: a single window is not cut out yet"] if args.get("app") else []
    return out(True, [f"a screenshot was saved as {r.get('saved')} in {r.get('folder')}"], loose)


def h_copy_screen_text(entry, args, env, step):
    app = args.get("app", "")
    text = env.window_text(app)
    if not text.strip():
        return out(False, error=f"I could not read any text in the {app} window", expected_missing=[f"text from {app}"])
    text = env.scrub(text)[:4000]
    r = env.pc({"op": "clipboard", "action": "set", "text": text}, 60)
    h = held(r)
    if h:
        return h
    if not r.get("ok") or r.get("read_back") is not True:
        return out(False, error=str(r.get("error") or "the clipboard did not hold the text when read back")[:200], expected_missing=["the clipboard holding the text"])
    return out(True, [f"{len(text)} characters from the {app} window are on the clipboard (read back); the text stayed on this PC"])


def h_clipboard_get(entry, args, env, step):
    r = env.pc({"op": "clipboard", "action": "get"}, 60)
    h = held(r)
    if h:
        return h
    if not r.get("ok"):
        return out(False, error=str(r.get("error") or "the clipboard could not be read")[:200], expected_missing=["the clipboard text"])
    n = int(r.get("chars") or 0)
    return out(True, [(f"the clipboard holds {n} character(s); its text stayed on this PC (ask me to paste it somewhere if you want it shown)" if n else "the clipboard is empty")])


def h_paste(entry, args, env, step):
    app = args.get("app")
    if app:
        f = _focus(env, app)
        if held(f):
            return held(f)
        if not f.get("ok"):
            return out(False, error=str(f.get("error") or f"no open window for {app}")[:200], expected_missing=[f"the {app} window in front"])
    proc = env.process(app) if app else env.front()
    if not proc:
        return out(False, error="there is no window to paste into", expected_missing=["a window"])
    r = env.pc({"op": "ui", "action": "key", "target": f"{proc}|", "text": "ctrl+v"}, 30)
    h = held(r)
    if h:
        return h
    if not r.get("ok"):
        return out(False, error=str(r.get("error") or "the paste was not sent")[:200], expected_missing=["the paste"])
    return out(True, [], [f"Ctrl+V was sent to {proc}; what it pasted is not read back"])


def h_type_active(entry, args, env, step):
    text = args.get("text", "")
    proc = env.front()
    if not proc:
        return out(False, error="there is no window in front to type into", expected_missing=["a window"])
    seen, done = True, 0
    for piece in composite.chunks(text):
        r = env.pc({"op": "ui", "action": "type_focused", "target": f"{proc}|", "text": piece}, 90)
        h = held(r)
        if h:
            return h
        if not r.get("ok"):
            return out(False, [f"{done} of {len(text)} characters were typed"] if done else [], [], ["the rest of the text"], error=str(r.get("error") or "typing failed")[:200])
        done += len(piece)
        seen = seen and bool(r.get("observed"))
    return out(True, [f"{done} characters were typed into {proc} and read back"] if seen else [], [] if seen else [f"{done} characters were typed into {proc}; the field cannot be read back"])


# ---------------------------------------------------------------------------------------------------------------- handler: reminder, notes, lists, status
UNIT_S = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def parse_when(text: str, now: dt.datetime) -> dt.datetime | None:
    """"17:30", "5pm", "tomorrow at 7", "noon": the next time that is. Naive input is read in the PC's own time zone."""
    t = str(text or "").strip().lower()
    base = now.replace(second=0, microsecond=0)
    if t in ("noon", "midnight"):
        c = base.replace(hour=12 if t == "noon" else 0, minute=0)
        return c if c > now else c + dt.timedelta(days=1)
    m = re.fullmatch(r"(?:(tomorrow|today|tonight) )?(?:at )?(\d{1,2})(?::(\d{2}))?\s?(am|pm)?", t)
    if not m:
        return None
    day, hh, mm, ap = m.group(1), int(m.group(2)), int(m.group(3) or 0), m.group(4)
    if ap == "pm" and hh < 12:
        hh += 12
    if ap == "am" and hh == 12:
        hh = 0
    if day == "tonight" and not ap and hh < 12:
        hh += 12
    if hh > 23 or mm > 59:
        return None
    c = base.replace(hour=hh, minute=mm)
    if day == "tomorrow":
        c += dt.timedelta(days=1)
    elif c <= now and day != "today":
        c += dt.timedelta(days=1)
    return c if c > now else None


def h_reminder(entry, args, env, step):
    now = env.now()
    if args.get("n"):
        unit = re.sub(r"s$", "", args.get("unit", "minute"))
        due = now + dt.timedelta(seconds=float(args["n"]) * UNIT_S.get(unit, 60))
    else:
        due = parse_when(args.get("when", ""), now)
        if due is None:
            return out(False, error="I could not read that time. Say it like 17:30, 5pm or tomorrow at 7", expected_missing=["a time"])
    what = args.get("text") or ("your timer is up" if args.get("n") else "your alarm")
    iso = due.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    d = {"name": ("Reminder: " + what)[:60], "action_kind": "command", "action": {"text": f"Remind the owner now: {what}"}, "trigger_kind": "once", "schedule": iso}
    status, body = env.scheduler_create(d)
    if status not in (200, 201) or not isinstance(body, dict) or not body.get("id"):
        return out(False, error=str((body or {}).get("error") or f"the scheduler refused it ({status})")[:200], expected_missing=["the reminder in the scheduler"])
    listed = [a for a in env.scheduler_list() if isinstance(a, dict) and a.get("id") == body["id"]]
    when = due.astimezone().strftime("%a %d %b %H:%M")
    soon = args.get("n") and float(args["n"]) * UNIT_S.get(re.sub(r"s$", "", args.get("unit", "minute")), 60) < 120
    if not listed:
        return out(True, [], [f"the scheduler accepted the reminder for {when} but it was not found when listed"])
    return out(True, [f"the reminder \"{what}\" is set for {when} (listed in the scheduler as {body['id']})" +
                      ("; it fires within about a minute of that time, the scheduler checks once a minute" if soon else "")])


def write_inbox_note(vault: str, text: str, now: dt.datetime, prefix: str = "note") -> dict:
    """A new .md file in <vault>/inbox, never over an existing one (the vault's rule: inbox only, Markdown only). Read back."""
    inbox = os.path.join(vault, "inbox")
    os.makedirs(inbox, exist_ok=True)
    stem = now.strftime("%Y-%m-%d-%H%M") + f"-{prefix}"
    for n in range(1, 100):
        name = f"{stem}.md" if n == 1 else f"{stem}-{n}.md"
        try:
            with open(os.path.join(inbox, name), "x", encoding="utf-8") as f:
                f.write(text.rstrip() + "\n")
            break
        except FileExistsError:
            continue
    else:
        return {"ok": False, "error": "too many notes with the same minute"}
    with open(os.path.join(inbox, name), encoding="utf-8") as f:
        back = f.read()
    return {"ok": back.strip() == text.strip(), "name": name, "folder": "the vault's inbox"}


def h_vault_note(entry, args, env, step):
    text = env.scrub(args.get("text", "")).strip()
    if not text:
        return out(False, error="there was no text for the note", expected_missing=["a note"])
    r = write_inbox_note(env.vault_dir(), "# Note\n\n" + text, env.now())
    if not r.get("ok"):
        return out(False, error=str(r.get("error") or "the note's text did not match when read back")[:200], expected_missing=["the note on disk"])
    return out(True, [f"the note {r['name']} is in {r['folder']} (read back)"])


def _items(text: str) -> list[str]:
    parts = re.split(r"\s*(?:,|;|\n|\band\b)\s*", text)
    return [p.strip(" .") for p in parts if p.strip(" .")][:60]


def h_quick_list(entry, args, env, step):
    items = _items(args.get("text", ""))
    if not items:
        return out(False, error="the list has no items", expected_missing=["list items"])
    name = (args.get("path") or "List " + env.now().strftime("%Y-%m-%d %H%M")).strip()
    name, why = composite.file_name(name, ".md")
    if why:
        return out(False, error="that is not a name I can use for the list file", expected_missing=["a list file"])
    body = "\n".join(f"- {i}" for i in items)
    r = env.pc({"op": "file", "action": "create_file", "target": "Documents", "path": name, "dest": "", "text": body}, 90)
    h = held(r)
    if h:
        return h
    if not r.get("ok"):
        return out(False, error=str(r.get("error") or "the list was not written")[:200], expected_missing=["the list file"])
    f, l = FACT["create_file"](r)
    return out(True, [f"{len(items)} item(s) written; " + f[0]], l)


def h_status(entry, args, env, step):
    what = entry["how"]["what"]
    if what == "time":
        n = env.now().astimezone()
        return out(True, [f"it is {n.strftime('%H:%M')} on {n.strftime('%A %d %B %Y')} (this PC's clock)"])
    r = env.pc({"op": "diag", "check": what, "target": ""}, 60)
    h = held(r)
    if h:
        return h
    if not r.get("ok"):
        return out(False, error=str(r.get("error") or f"the {what} check failed")[:200], expected_missing=[f"the {what} reading"])
    rows = [x for x in r.get("rows") or [] if isinstance(x, dict)]
    if what == "battery":
        if not rows:
            return out(True, ["this PC reports no battery (a desktop or a plugged-in machine)"])
        b = rows[0]
        left = f", about {b['minutes_left']} minutes left" if b.get("minutes_left") else ""
        return out(True, [f"the battery is at {b.get('percent')}%, " + ("charging" if b.get("charging") else "not charging") + left])
    if what == "disk":
        return out(bool(rows), ["; ".join(f"drive {d.get('drive')}: {d.get('free_gb')} GB free of {d.get('total_gb')} GB" for d in rows)] if rows else [],
                   [] if rows else ["the disk check returned nothing"], [] if rows else ["a disk reading"])
    if what == "network":
        d = rows[0] if rows else r
        up = d.get("adapters_up", 0)
        state = "connected to the internet" if d.get("internet_tcp_ok") else "an adapter is up but the internet did not answer" if up else "no network adapter is up"
        return out(True, [f"{state} ({up} adapter(s) up; name lookup {'works' if d.get('dns_ok') else 'failed'})"])
    return out(True, [f"the {what} check ran"])


def h_today_folder(entry, args, env, step):
    n = env.now().astimezone()
    names = [n.strftime("%Y-%m-%d"), n.strftime("%d-%m-%Y"), n.strftime("%Y.%m.%d"), n.strftime("%Y%m%d"), n.strftime("%d %b %Y")]
    for nm in names:
        r = env.pc({"op": "file", "action": "find", "target": "all", "path": nm, "text": ""}, 60)
        h = held(r)
        if h:
            return h
        dirs = [m for m in r.get("matches") or [] if isinstance(m, dict) and str(m.get("kind", "")).lower() in ("dir", "folder", "directory")]
        if len(dirs) == 1:
            d = dirs[0]
            rr = env.pc({"op": "file", "action": "reveal", "target": str(d.get("folder")), "path": str(d.get("path_rel") or d.get("name")), "text": ""}, 60)
            if held(rr):
                return held(rr)
            if rr.get("ok"):
                return out(True, [f"found the folder {d.get('name')} in {d.get('folder')}"], [f"Explorer was asked to show it; whether its window opened is not checked"])
        if len(dirs) > 1:
            return out(False, error="more than one folder is named with today's date: " + ", ".join(f"{m.get('name')} in {m.get('folder')}" for m in dirs[:4]), expected_missing=["one folder for today"])
    return out(False, error="no folder named with today's date was found in the known folders. I do not guess one", expected_missing=["today's folder"])


# ---------------------------------------------------------------------------------------------------------------- handler: units, calculator, held
UNITS = {  # kind -> {name: factor to the base unit}
    "length": {"mm": 0.001, "millimeter": 0.001, "millimetre": 0.001, "cm": 0.01, "centimeter": 0.01, "centimetre": 0.01, "m": 1, "meter": 1, "metre": 1,
               "km": 1000, "kilometer": 1000, "kilometre": 1000, "in": 0.0254, "inch": 0.0254, "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
               "yd": 0.9144, "yard": 0.9144, "mile": 1609.344, "mi": 1609.344},
    "mass": {"mg": 1e-6, "g": 0.001, "gram": 0.001, "kg": 1, "kilogram": 1, "kilo": 1, "t": 1000, "tonne": 1000, "oz": 0.028349523125, "ounce": 0.028349523125,
             "lb": 0.45359237, "pound": 0.45359237},
    "volume": {"ml": 0.001, "milliliter": 0.001, "millilitre": 0.001, "l": 1, "liter": 1, "litre": 1, "gal": 3.785411784, "gallon": 3.785411784,
               "qt": 0.946352946, "quart": 0.946352946, "pt": 0.473176473, "pint": 0.473176473, "cup": 0.2365882365, "floz": 0.0295735295625},
    "speed": {"kmh": 1 / 3.6, "kph": 1 / 3.6, "mph": 0.44704, "mps": 1, "knot": 0.514444},
    "data": {"b": 1, "byte": 1, "kb": 1024, "kilobyte": 1024, "mb": 1024 ** 2, "megabyte": 1024 ** 2, "gb": 1024 ** 3, "gigabyte": 1024 ** 3, "tb": 1024 ** 4, "terabyte": 1024 ** 4},
    "time": {"s": 1, "sec": 1, "second": 1, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600, "day": 86400, "week": 604800},
}
TEMP = {"c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}


def _unit(u: str) -> str:
    u = re.sub(r"\s+", "", str(u).lower().strip("° ").replace("per", "p").replace("/", "p"))
    u = {"kmph": "kmh", "kmperhour": "kmh", "kilometersperhour": "kmh", "milesperhour": "mph", "metrespersecond": "mps", "meterspersecond": "mps", "ms": "mps", "mps": "mps",
         "fluidounce": "floz", "fluidounces": "floz", "kilos": "kg", "kms": "km", "miles": "mile", "inches": "inch", "pounds": "pound", "ounces": "ounce", "yards": "yard",
         "gallons": "gallon", "cups": "cup", "knots": "knot", "hours": "hour", "minutes": "minute", "seconds": "second", "days": "day", "weeks": "week", "litres": "litre", "liters": "liter",
         "meters": "meter", "metres": "metre", "grams": "gram", "bytes": "byte", "kilometers": "kilometer", "kilometres": "kilometre", "centimeters": "centimeter", "centimetres": "centimetre"}.get(u, u)
    return u


def convert(n: float, src: str, dst: str) -> tuple[float, str] | None:
    s, d = _unit(src), _unit(dst)
    if s in TEMP and d in TEMP:
        a, b = TEMP[s], TEMP[d]
        c = n if a == "c" else (n - 32) * 5 / 9 if a == "f" else n - 273.15
        return (c if b == "c" else c * 9 / 5 + 32 if b == "f" else c + 273.15), "temperature"
    for kind, table in UNITS.items():
        if s in table and d in table:
            return n * table[s] / table[d], kind
    return None


def _fmt(x: float) -> str:
    return f"{x:.6g}" if abs(x) < 1e15 else f"{x:.3e}"


def h_convert(entry, args, env, step):
    try:
        n = float(args.get("n", ""))
    except ValueError:
        return out(False, error="I could not read the number", expected_missing=["a number"])
    r = convert(n, args.get("from", ""), args.get("to", ""))
    if r is None:
        return out(False, error=f"I cannot convert {args.get('from')} to {args.get('to')}: they are not units of the same kind that I know", expected_missing=["a known unit pair"])
    return out(True, [f"{_fmt(n)} {args.get('from')} = {_fmt(r[0])} {args.get('to')} (worked out here from a fixed table)"])


WORDS_OP = [(r"\s*(?:divided by|over|/|÷)\s*", "/"), (r"\s*(?:times|multiplied by|x|\*|×)\s*", "*"), (r"\s*(?:plus|and|\+)\s*", "+"), (r"\s*(?:minus|less|-)\s*", "-")]


def arithmetic(text: str) -> float | None:
    """"12 times 7", "15 plus 27", "100 divided by 4": one operation on two numbers. None when it is anything else (the Calculator is still driven, the answer unchecked)."""
    t = str(text or "").lower().replace(",", "").strip(" ?=.")
    for rx, op in WORDS_OP:
        m = re.fullmatch(rf"(-?\d+(?:\.\d+)?){rx}(-?\d+(?:\.\d+)?)", t)
        if m:
            a, b = float(m.group(1)), float(m.group(2))
            if op == "/":
                return a / b if b else None
            return {"*": a * b, "+": a + b, "-": a - b}[op]
    return None


def h_drive(entry, args, env, step):
    """A task done by looking at the screen (drive.py). A calculation is also worked out here and compared with what the Calculator window shows."""
    app = entry["how"].get("app") or args.get("app", "")
    query = step.get("query") or ""
    expect = arithmetic(args.get("text", "")) if entry["id"] == "calculate" else None
    r = env.agent("drive", {"tool": "drive", "app": app, "query": query})
    if held(r):
        return held(r)
    r = dict(r)
    r.setdefault("facts", [])
    r.setdefault("unverified", [])
    r.setdefault("expected_missing", [])
    if expect is not None and r.get("ok"):
        want = str(int(expect)) if float(expect).is_integer() else _fmt(expect)
        shown = re.sub(r"[,\s]", "", env.window_text(app or "calculator") or "")
        if want in shown:
            r["facts"] = list(r["facts"]) + [f"the Calculator window shows {want}, the answer worked out here"]
        else:
            r["expected_missing"] = list(r["expected_missing"]) + [f"the Calculator showing {want}"]
            r["ok"] = False
            r["error"] = f"the Calculator does not show {want}, which is what {args.get('text')} comes to"
    return r


HANDLERS.update(file_op=h_file_op, setting=h_setting, keys=h_keys, snap=h_snap, arrange=h_arrange, close_app=h_close_app, screenshot=h_screenshot,
                copy_screen_text=h_copy_screen_text, clipboard_get=h_clipboard_get, paste=h_paste, type_active=h_type_active, reminder=h_reminder,
                vault_note=h_vault_note, quick_list=h_quick_list, status=h_status, today_folder=h_today_folder, convert=h_convert, drive=h_drive,
                # these four are planned as the plugin's older steps (window, press_key, open_file, open_website) and these entries are never run here
                window=lambda e, a, env, s: out(False, error="planned as a window step"), media_key=lambda e, a, env, s: out(False, error="planned as a key step"),
                open_file=lambda e, a, env, s: out(False, error="planned as an open_file step"), open_site=lambda e, a, env, s: out(False, error="planned as an open_website step"),
                # explained and not done / always the PIN: decided before anything runs
                ask=lambda e, a, env, s: out(False, error=e["how"].get("say", "")), unavailable=lambda e, a, env, s: out(False, error=e["how"].get("say", "")),
                held=lambda e, a, env, s: env.agent("drive", {"tool": "drive", "app": "", "query": s.get("query", "")}))


# ---------------------------------------------------------------------------------------------------------------- the entry the plugin's agent calls
def run(step: dict, env: Env) -> dict:
    entry = get(str(step.get("task", "")))
    if entry is None:
        return out(False, error="that task is not in the catalogue", expected_missing=["a known task"])
    fn = HANDLERS.get(entry["how"]["handler"])
    try:
        return fn(entry, dict(step.get("args") or {}), env, step)
    except Unavailable as e:
        return out(False, error=f"this PC link cannot do that yet ({e})", expected_missing=[entry["name"]])
