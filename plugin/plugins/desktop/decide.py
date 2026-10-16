"""The local decision model for PC tasks (2026-10-16): request -> structured plan, then deterministic resolution.

Split of work (measured choice, see jarvis/docs/neko/PC_CONTROL_BENCHMARK.md):
  * the small local model only EXTRACTS: what kind of task, which steps, and the phrases the owner used (account, app,
    site, query, file). Its output is forced into a JSON schema by llama.cpp's grammar, so it is always parseable;
  * deterministic code RESOLVES those phrases against facts: the Chrome profile registry (ambiguous -> ask), known
    sites, the Start-menu app list; and checks every step against the tool list and its argument rules;
  * nothing here authorizes anything: the plan is only a proposal. The JARVIS kernel decides each action when it runs.
If the model is down, slow, or its plan does not validate, the result says so ("fallback") instead of guessing.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

LOCAL = "http://127.0.0.1:8080/v1/chat/completions"
DEFAULT_MODEL = "decider"          # a llama-swap alias (see jarvis/local_models/llama-swap.yaml)

TOOLS = {
    "open_website": "open a website or web address in Chrome (args: site or url, account)",
    "play_media": "find and play a song or video on YouTube (args: query, account)",
    "browser": "do something on the open web page: read, find, click, type, press, tabs, whoami (args: action, target, text, account)",
    "open_app": "start a Windows app by name (args: app)",
    "window": "focus, minimize, maximize or restore an app window, or read what is in it (args: action, app)",
    "type_text": "type text into an app (args: app, target, text)",
    "press_key": "press a key or shortcut in an app, or a media key (args: app, key)",
    "open_file": "find and open a file or folder (args: name, folder)",
    "check_pc": "read-only checks: disk, network, processes, crashes, an app's version or state (args: check, app)",
    "troubleshoot": "find out why an app shows an error, and fix it safely (args: app)",
    "research": "research a topic on the web with sources, optionally as a report or slides (args: query, deliverable)",
    "answer": "answer from general knowledge, no PC action",
    "clarify": "the request is missing something essential: ask one short question (args: question)",
    "stop": "stop or cancel the current task",
}
ACTIONS = {"browser": ["read", "find", "click", "type", "press", "tabs", "whoami"],
           "window": ["focus", "minimize", "maximize", "restore", "inspect"],
           "check_pc": ["disk", "network", "processes", "crashes", "app_info", "services", "media"]}
SITES = {"youtube": "https://www.youtube.com/", "gmail": "https://mail.google.com/", "google": "https://www.google.com/",
         "github": "https://github.com/", "wikipedia": "https://www.wikipedia.org/", "whatsapp": "https://web.whatsapp.com/",
         "chatgpt": "https://chatgpt.com/", "amazon": "https://www.amazon.in/", "netflix": "https://www.netflix.com/",
         "drive": "https://drive.google.com/", "google drive": "https://drive.google.com/", "maps": "https://maps.google.com/",
         "example": "https://example.com/", "example.com": "https://example.com/", "linkedin": "https://www.linkedin.com/",
         "calendar": "https://calendar.google.com/", "google calendar": "https://calendar.google.com/", "spotify": "https://open.spotify.com/",
         "google maps": "https://maps.google.com/", "youtube music": "https://music.youtube.com/", "reddit": "https://www.reddit.com/",
         "google account": "https://myaccount.google.com/", "my google account": "https://myaccount.google.com/",
         "account": "https://myaccount.google.com/"}
MAX_STEPS = 4
PROMPT_VERSION = 3          # 1: plain rules (baseline); 2: + examples, action enum, key/site normalisation; 3: + account read from the request, name/folder fields, vague words, follow-up context, step merging
ALL_ACTIONS = sorted({a for v in ACTIONS.values() for a in v})
KEY_WORDS = {"playpause": ["pause", "play", "play pause", "play/pause", "resume", "pause music", "stop music"],
             "nexttrack": ["next", "next song", "next track", "skip", "skip song"],
             "prevtrack": ["previous", "previous song", "prev", "back"],
             "volumeup": ["volume up", "louder", "turn up", "increase volume"],
             "volumedown": ["volume down", "quieter", "turn down", "decrease volume", "lower volume"],
             "mute": ["mute", "unmute", "silence"]}

SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "minItems": 1, "maxItems": MAX_STEPS, "items": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "enum": list(TOOLS)},
                "action": {"type": "string", "enum": [""] + ALL_ACTIONS},
                "account": {"type": "string"},
                "site": {"type": "string"},
                "app": {"type": "string"},
                "query": {"type": "string"},
                "target": {"type": "string"},
                "text": {"type": "string"},
                "key": {"type": "string"},
                "name": {"type": "string"},
                "folder": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": ["tool"]}},
    },
    "required": ["steps"],
}

SYSTEM = (
    "You turn the owner's request to their Windows PC assistant into steps. Answer ONLY with JSON matching the schema.\n"
    "Tools:\n" + "\n".join(f"- {k}: {v}" for k, v in TOOLS.items()) + "\n"
    "Rules:\n"
    "- Use the fewest steps (usually one). At most 4.\n"
    "- Copy the owner's own words into the fields: account = the account or profile name they said (e.g. \"VenkatAMV\", "
    "\"my work account\"), empty if they named none; site = the site name or address; app = the app name; query = what to "
    "search or play; text = text to type; key = a key such as ctrl+s, enter, playpause; name = a file or folder name; folder = where it is (Documents, Desktop, Downloads).\n"
    "- Playing a song or video is play_media, not open_website.\n"
    "- An error, crash or something not working in an app is troubleshoot.\n"
    "- \"stop\", \"cancel\", \"never mind\" is stop.\n"
    "- If the request is too vague to act on (no app, site or goal), use clarify with one short question.\n"
    "- Never add steps the owner did not ask for. Never decide that something is allowed: the system checks that.\n"
    "Actions: browser " + "/".join(ACTIONS["browser"]) + "; window " + "/".join(ACTIONS["window"]) + "; check_pc "
    + "/".join(ACTIONS["check_pc"]) + ".\n"
    "Examples (request -> steps):\n"
    "open netflix -> [{\"tool\":\"open_website\",\"site\":\"netflix\"}]\n"
    "open gmail in my Work profile -> [{\"tool\":\"open_website\",\"site\":\"gmail\",\"account\":\"Work\"}]\n"
    "play some Bach piano music -> [{\"tool\":\"play_media\",\"query\":\"Bach piano music\"}]\n"
    "open notepad and write hello -> [{\"tool\":\"open_app\",\"app\":\"notepad\"},{\"tool\":\"type_text\",\"app\":\"notepad\",\"text\":\"hello\"}]\n"
    "save the document in word -> [{\"tool\":\"press_key\",\"app\":\"word\",\"key\":\"ctrl+s\"}]\n"
    "pause the video -> [{\"tool\":\"press_key\",\"key\":\"playpause\"}]\n"
    "minimize excel -> [{\"tool\":\"window\",\"action\":\"minimize\",\"app\":\"excel\"}]\n"
    "what does the error in paint say -> [{\"tool\":\"window\",\"action\":\"inspect\",\"app\":\"paint\"}]\n"
    "is my wifi working -> [{\"tool\":\"check_pc\",\"action\":\"network\"}]\n"
    "which account am I logged in with -> [{\"tool\":\"browser\",\"action\":\"whoami\"}]\n"
    "show my open tabs -> [{\"tool\":\"browser\",\"action\":\"tabs\"}]\n"
    "excel crashes when I open it -> [{\"tool\":\"troubleshoot\",\"app\":\"excel\"}]\n"
    "open my tax notes on the desktop -> [{\"tool\":\"open_file\",\"name\":\"tax notes\",\"folder\":\"Desktop\"}]\n"
    "what is 2+2 -> [{\"tool\":\"answer\"}]\n"
    "open that -> [{\"tool\":\"clarify\",\"question\":\"What should I open?\"}]\n"
)


class DecisionError(Exception):
    pass


@dataclass
class Plan:
    steps: list[dict]
    status: str = "ok"                       # ok | clarify | fallback
    question: str = ""
    problems: list[str] = field(default_factory=list)
    model: str = ""
    seconds: float = 0.0
    raw: str = ""


_S = {"type": "string"}
_ARGS = {"open_website": {"site": _S, "account": _S}, "play_media": {"query": _S, "account": _S},
         "browser": {"action": {"type": "string", "enum": ACTIONS["browser"]}, "target": _S, "text": _S, "account": _S},
         "open_app": {"app": _S}, "window": {"action": {"type": "string", "enum": ACTIONS["window"]}, "app": _S},
         "type_text": {"app": _S, "target": _S, "text": _S}, "press_key": {"app": _S, "key": _S},
         "open_file": {"name": _S, "folder": _S}, "check_pc": {"action": {"type": "string", "enum": ACTIONS["check_pc"]}, "app": _S},
         "troubleshoot": {"app": _S}, "research": {"query": _S, "deliverable": {"type": "string", "enum": ["none", "report", "presentation"]}},
         "answer": {}, "clarify": {"question": _S}, "stop": {}}
# One schema per tool with only that tool's arguments: the grammar cannot emit fields a tool does not use, so plans are
# shorter (generation is ~10 tokens/s on this GPU) and carry fewer stray values. Behind a switch until measured.
SCHEMA_STRICT = {"type": "object", "required": ["steps"], "properties": {"steps": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
    "anyOf": [{"type": "object", "additionalProperties": False, "required": ["tool"],
               "properties": {"tool": {"const": t}, **a}} for t, a in _ARGS.items()]}}}}
STRICT_SCHEMA = os.environ.get("NEKO_DECIDE_STRICT_SCHEMA", "0") == "1"


def ask_model(request: str, model: str = DEFAULT_MODEL, url: str = LOCAL, timeout: float = 60.0, post=None) -> tuple[str, float]:
    extra = overlay().get("examples") or []
    system = SYSTEM + ("".join(f"\n{e['request']} -> {json.dumps(e['plan'])}" for e in extra) if extra else "")
    body = {"model": model, "temperature": 0, "max_tokens": 400, "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {"name": "plan", "schema": SCHEMA_STRICT if STRICT_SCHEMA else SCHEMA}},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": request}]}
    t0 = time.time()
    if post is not None:
        return post(body), time.time() - t0
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise DecisionError(f"the local decision model did not answer ({type(e).__name__})") from None
    return str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""), time.time() - t0


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def resolve_account(phrase: str, profiles: list[dict]) -> tuple[str | None, str]:
    """(profile folder or None, why). Empty phrase -> the default profile. Uses the same alias rules as the node."""
    q = _norm(re.sub(r"(?i)\b(my|the|account|profile|chrome|browser|google|gmail)\b", " ", phrase or ""))
    if not q:
        d = [p for p in profiles if p.get("default")]
        return (d[0]["profile"], "default account") if d else (None, "no default account is set")
    exact = [p for p in profiles if q in [_norm(a) for a in p.get("say") or p.get("aliases") or []] or q == _norm(p.get("profile"))]
    if len(exact) == 1:
        return exact[0]["profile"], f"'{phrase}' is {exact[0]['profile']}"
    near = exact or [p for p in profiles if any(len(_norm(a)) >= 4 and (_norm(a) in q or q in _norm(a)) for a in p.get("say") or p.get("aliases") or [])]
    if len(near) == 1:
        return near[0]["profile"], f"'{phrase}' is {near[0]['profile']}"
    if near:
        return None, "ambiguous: " + ", ".join(f"{p.get('name')} ({p.get('account')}, {p['profile']})" for p in near)
    return None, f"no Chrome profile matches '{phrase}'"


ACTIVE = "(active window)"
VAGUE = {"it", "this", "that", "something", "thing", "the thing", "there", "none", "null", "n/a", "na", "unknown", "page",
         "current page", "the page", "this page"}
NO_ACCOUNT = {"", "none", "null", "n/a", "na", "default account", "unknown"}


def account_from_text(request: str, profiles: list[dict]) -> str | None:
    """The account the owner NAMED in the request, read deterministically: the longest known profile alias among its
    words (one word, or two or three run together: "Venkat AMV" -> venkatamv), or the name after "profile/account
    called". None when no account is named. The model's own account field is only a fallback."""
    text = re.sub(r"(?i)'s\b", "", request or "")
    called = re.search(r"(?i)\b(?:profile|account)\s+(?:called|named)\s+([\w.-]+)", text)
    if called:
        return called.group(1)
    words = [_norm(w) for w in re.split(r"[^A-Za-z0-9.@-]+", text) if w]
    grams = {w for w in words} | {a + b for a, b in zip(words, words[1:])} | {a + b + c for a, b, c in zip(words, words[1:], words[2:])}
    aliases = {_norm(a) for p in profiles for a in (p.get("say") or p.get("aliases") or []) if len(_norm(a)) >= 4}
    aliases.discard("default")
    hits = sorted((g for g in grams if g in aliases), key=len, reverse=True)
    return hits[0] if hits else None


MEDIA_SAID = [("nexttrack", r"\b(next (song|track)|skip( the| this)?( song| track)?)\b"), ("prevtrack", r"\bprevious (song|track)\b"),
              ("mute", r"\b(un)?mute\b"), ("volumeup", r"\b(volume up|louder|turn (it |the volume )?up)\b"),
              ("volumedown", r"\b(volume down|quieter|turn (it |the volume )?down)\b")]


def media_key_from_text(request: str) -> str | None:
    """A media key the owner named outright ("skip to the next song", "mute") wins over the model's choice."""
    low = (request or "").lower()
    hits = [k for k, rx in MEDIA_SAID if re.search(rx, low)]
    return hits[0] if len(hits) == 1 else None


def normalize_key(key: str) -> str:
    """'pause' -> playpause, 'volume up' -> volumeup, 'Ctrl + S' -> ctrl+s: the node accepts only its own key names."""
    k = re.sub(r"\s*\+\s*", "+", (key or "").strip().lower())
    for name, words in KEY_WORDS.items():
        if k == name or k in words:
            return name
    return k


FORCED: dict | None = None                    # the regression gate evaluates a staged version without activating it
FAST_PATH = os.environ.get("NEKO_DECIDE_FAST_PATH", "1") != "0"   # rules for one-command requests (0 = model only, for baselines)
_OVERLAY = {"mtime": -1.0, "content": {}}


def overlay() -> dict:
    """What the owner promoted through skills.py (sites, examples); re-read when the registry changes."""
    if FORCED is not None:
        return FORCED
    from . import skills
    reg = skills.ROOT / "registry.json"
    mt = reg.stat().st_mtime if reg.exists() else 0.0
    if mt != _OVERLAY["mtime"]:
        _OVERLAY.update(mtime=mt, content=skills.active(skills.ROOT) if mt else {})
    return _OVERLAY["content"]


def resolve_site(site: str) -> str | None:
    s = (site or "").strip()
    if not s:
        return None
    if re.match(r"^https?://", s, re.I):
        return s
    low = re.sub(r"(?i)\b(website|site|web site|the)\b", "", s.lower()).strip()
    if low in SITES:
        return SITES[low]
    learned = overlay().get("sites") or {}
    if low in learned:                                       # added by a promoted version (built-in names always win)
        return learned[low]
    key = low.removesuffix(".com").strip()
    if key in SITES:
        return SITES[key]
    if re.search(r"\.(txt|pdf|docx?|pptx?|xlsx?|csv|md|png|jpe?g|gif|exe|msi|py|zip|log|json|wav|mp[34])$", low):
        return None                                           # a file name, not a website
    if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d{1,5})?(/\S*)?$", low, re.I):
        return "http://" + low if re.match(r"^(127\.|localhost|10\.|192\.168\.)", low) else "https://" + low
    return None


def _vague(v) -> bool:
    return str(v or "").strip().lower() in VAGUE


def validate(raw: str, profiles: list[dict], request: str = "") -> Plan:
    """Parse the model's JSON and turn every step into a concrete, checked tool call; anything unclear -> clarify.
    Facts the request states outright (the account named, "report"/"slides") are read from the request itself."""
    try:
        data = json.loads(raw)
    except ValueError:
        return Plan([], "fallback", problems=["the model's answer was not JSON"], raw=raw)
    named = account_from_text(request, profiles)
    steps, problems = [], []
    if MAKE_DOC.search(request) and not TOPIC.search(request):          # "create a pdf and open it": about what?
        return Plan([], "clarify", question="What should it be about?", problems=["no topic for the document"], raw=raw)
    for s in (data.get("steps") or [])[:MAX_STEPS]:
        tool = s.get("tool")
        if tool not in TOOLS:
            problems.append(f"unknown tool {tool!r}")
            continue
        if tool == "open_app" and resolve_site(s.get("app") or "") and (s.get("app") or "").lower() in SITES:
            tool, s = "open_website", dict(s, site=s.get("app"))          # "open youtube" is a site, not a Start-menu app
        out = {"tool": tool}
        if tool in ("open_website", "play_media", "browser"):
            phrase = named or ("" if str(s.get("account") or "").strip().lower() in NO_ACCOUNT else s.get("account", ""))
            folder, why = resolve_account(phrase, profiles)
            if folder is None:
                return Plan(steps, "clarify", question=f"Which Chrome account? {why}.", problems=[why], raw=raw)
            out["profile"] = folder
        if tool == "open_website":
            site = next((v for v in (s.get("site"), s.get("target"), s.get("query"), s.get("name")) if v and not _vague(v)), "")
            url = resolve_site(site)
            if not url:
                out["url"] = ""                                  # may be dropped below when a play/browser step follows
            else:
                out["url"] = url
        elif tool == "play_media":
            q = (s.get("query") or s.get("text") or s.get("target") or "").strip()
            if not q or _vague(q):
                return Plan(steps, "clarify", question="What should I play?", problems=["no query"], raw=raw)
            out["query"] = q[:200]
        elif tool == "browser":
            act = (s.get("action") or "").lower()
            target = "" if _vague(s.get("target")) else (s.get("target") or s.get("query") or "")
            if act not in ACTIONS["browser"]:
                act = "read" if not target else "find"
            out.update(action=act, target=target[:300], text=(s.get("text") or s.get("key") or "")[:500])
        elif tool in ("open_app", "window", "type_text", "press_key", "troubleshoot"):
            app = (s.get("app") or s.get("target") or "").strip()
            key = normalize_key((s.get("key") or s.get("text") or "").strip().lower())
            said_key = media_key_from_text(request) if tool == "press_key" else None
            if said_key and (key in KEY_WORDS or len(key) <= 1):     # a media request: what the owner said wins
                key = said_key
            if (not app or _vague(app)) and tool in ("troubleshoot", "window") and re.search(r"(?i)\b(screen|this error|the error|that error|popup|pop-up|dialog)\b", request):
                app = ACTIVE                                  # the window showing the error, found at run time
            if (not app or _vague(app)) and not (tool == "press_key" and key in KEY_WORDS):
                return Plan(steps, "clarify", question="Which app?", problems=["no app"], raw=raw)
            out["app"] = "" if _vague(app) else app[:80]
            if tool == "window":
                act = (s.get("action") or "focus").lower()
                out["action"] = act if act in ACTIONS["window"] else "focus"
            if tool == "type_text":
                out.update(target=(s.get("target") or "")[:120] if s.get("target") != app else "", text=(s.get("text") or "")[:500])
            if tool == "press_key":
                out["key"] = key[:30]
                said = (s.get("key") or s.get("text") or "").strip().lower() + " " + request.lower()
                if key == "playpause":                         # the media key toggles: keep what the owner wanted
                    out["intent"] = "pause" if re.search(r"\b(pause|stop)\b", said) else ("play" if re.search(r"\b(play|resume|continue)\b", said) else "")
        elif tool == "open_file":
            name = next((v for v in (s.get("name"), s.get("target"), s.get("query"), s.get("text")) if v and not _vague(v)), "").strip()
            if not name:
                return Plan(steps, "clarify", question="Which file?", problems=["no file name"], raw=raw)
            out.update(name=name[:120], folder=(s.get("folder") or s.get("site") or "")[:40])
        elif tool == "check_pc":
            act = (s.get("action") or "").lower()
            guess = "media" if re.search(r"(?i)\b(song|music|playing|video)\b", request) else ("app_info" if s.get("app") else "disk")
            out.update(check=act if act in ACTIONS["check_pc"] else guess, app=(s.get("app") or "")[:40])
        elif tool == "research":
            q = (s.get("query") or s.get("text") or "").strip()
            if not q:
                return Plan(steps, "clarify", question="What should I research?", problems=["no topic"], raw=raw)
            said = request + " " + json.dumps(s)
            deliverable = "presentation" if re.search(r"(?i)slides?|deck|presentation|ppt", said) else (
                "report" if re.search(r"(?i)report|pdf", said) else "none")
            out.update(query=q[:300], deliverable=deliverable)
        elif tool == "clarify":
            return Plan(steps, "clarify", question=(s.get("question") or "Could you say a bit more?")[:200], raw=raw)
        steps.append(out)
    # "open YouTube and play X": playing opens YouTube itself; an open step with no site before a browser step adds nothing
    play_after = any(st["tool"] == "play_media" for st in steps)
    browser_after = play_after or any(st["tool"] == "browser" for st in steps)
    steps = [st for st in steps if not (st["tool"] == "open_website" and ((browser_after and not st["url"])
                                                                           or (play_after and "youtube.com" in st["url"])))]
    # the account's name chose the Chrome profile; it is not text to type or search for on the page
    if named:
        steps = [st for st in steps if not (st["tool"] == "browser" and st.get("action") in ("type", "find", "fill", "search")
                                            and (st.get("text") or st.get("target") or "").strip().lower() == named.lower())]
    # "research X, make slides and open them": the research step opens its own file (and the coordinator checks a window
    # showing it appeared); a separate open step could not know the file's name yet
    made = [st for st in steps if st["tool"] == "research" and st.get("deliverable") != "none"]
    if made and re.search(r"(?i)\b(open|show)\b", request):
        made[0]["open_after"] = True
        steps = [st for st in steps if not (st["tool"] == "open_file" or (st["tool"] == "open_app" and re.search(
            r"(?i)powerpoint|acrobat|pdf|edge|word|reader", st.get("app", ""))))]
        # "... and explain": the explanation is the research summary; a page read or window step after it (seen in the
        # live mission 2026-10-16 15:56) would act on whatever happens to be open
        at = steps.index(made[0])
        steps = steps[:at + 1] + [st for st in steps[at + 1:] if not (st["tool"] == "window" or
                                                                       (st["tool"] == "browser" and st.get("action") in ("read", "find", "")))]
    # the troubleshooter reads the window itself: a separate "look at the window" step before it adds nothing
    if any(st["tool"] == "troubleshoot" for st in steps):
        steps = [st for st in steps if not (st["tool"] == "window" and st.get("action") in ("inspect", "focus", "read"))]
    # "play X and tell me when it is playing": the play step's own check (same video, position moving) is the answer; a
    # status check after it adds nothing (G07 live run 2026-10-16 planned a disk check there)
    if play_after:
        first_play = next(i for i, st in enumerate(steps) if st["tool"] == "play_media")
        steps = steps[:first_play + 1] + [st for st in steps[first_play + 1:] if st["tool"] != "check_pc"]
    # "... and tell me when it is playing": the coordinator's report is the answer; an answer step after real steps adds nothing
    if any(st["tool"] != "answer" for st in steps):
        steps = [st for st in steps if st["tool"] != "answer"]
    steps = complete_plan(steps, request, profiles)
    if any(st["tool"] == "open_website" and not st["url"] for st in steps):
        return Plan(steps, "clarify", question="Which website should I open?", problems=["no site"], raw=raw)
    if not steps:
        return Plan([], "fallback", problems=problems or ["no usable step"], raw=raw)
    for st in steps:
        st["post"] = POST.get(st["tool"], "the action reported success").format(**{k: st.get(k, "") for k in ("app", "profile", "action", "check")})
    return Plan(steps, "ok", problems=problems, raw=raw)


# What must be OBSERVED after each step for it to count as done (the coordinator's verify() checks these).
POST = {"open_website": "a Chrome window of profile {profile} shows the page",
        "play_media": "the chosen video is playing: same video id, position moving",
        "browser": "the {action} result was observed on the page (whoami: the signed-in account matches {profile})",
        "open_app": "a window for {app} appeared",
        "type_text": "the field in {app} contains the typed text (read back)",
        "window": "the {app} window is in the requested state",
        "press_key": "the key reached {app} (media keys: the playback state is checked first)",
        "open_file": "the file or folder opened",
        "check_pc": "the {check} check returned data",
        "troubleshoot": "the original error is gone after the fix, or the cause is reported as verified or a hypothesis",
        "research": "a report with checked citations exists (and the requested file passed its checks; if asked, a window showing it opened)",
        "open_output": "a window showing the file Neko made appeared",
        "stop": "the running work stopped"}
TYPE_SAID = re.compile(r"(?i)\b(?:(?:type|write|enter)\s+[\"']?(.+?)[\"']?\s+(?:in|into|on)|put\s+[\"']?(.+?)[\"']?\s+into)\s+"
                       r"(?:the\s+|my\s+)?([A-Za-z][\w.+-]*(?:\s[A-Za-z][\w.+-]*)?)\s*(?:app|window)?\s*[.!]?$")
GO_SITE = re.compile(r"(?i)\b(?:go to|open|visit)\s+(?:my\s+|the\s+)?([A-Za-z0-9.-]+(?:\.[a-z]{2,})?(?:\s(?:maps|account|drive|calendar|music))?)\b")
MAKE_DOC = re.compile(r"(?i)\b(create|make|write|generate|build|prepare)\b.{0,30}\b(pdf|report|document|slides?|deck|presentation|ppt)\b")
TOPIC = re.compile(r"(?i)\b(about|on|regarding|covering|of|for|explaining|comparing|research(?:ing)?|summari[sz]e|compare|explain|"
                   r"look up|find out)\s+(?!it\b|that\b|this\b|me\b|them\b)\w{3,}")


def complete_plan(steps: list[dict], request: str, profiles: list[dict]) -> list[dict]:
    """Deterministic completion of what the owner said outright but the model left out (measured omissions, 2026-10-16):
    'write X into WordPad' needs a typing step; 'go to youtube' on an account is opening the site, not reading a page;
    naming an account means checking that account before acting (opening a profile is not proof of who is signed in)."""
    m = TYPE_SAID.search(request or "")
    said, app = ((m.group(1) or m.group(2) or "").strip(), m.group(3).strip()) if m else ("", "")
    if m and (app.lower() in SITES or resolve_site(app)):   # "put on some music on youtube" is not typing into an app
        m = None
    if m and not any(st["tool"] == "type_text" for st in steps):
        # no "open the app" step is added: at run time the typing step opens the app only if no window of it exists
        # (a second window would make it unclear where the text goes)
        steps.append({"tool": "type_text", "app": app, "target": "", "text": said})
    elif m:
        for st in steps:
            if st["tool"] == "type_text" and not st.get("text"):
                st["text"] = said
    # the first "go to / open / visit X" whose X is a known site ("open my VenkatAMV account and go to youtube" -> youtube)
    hit = next(((g.group(1), u) for g in GO_SITE.finditer(request or "") for u in [resolve_site(g.group(1))] if u), None)
    url = hit[1] if hit else None
    if hit and " " in hit[0].strip() and hit[0].strip().lower() in SITES:      # "open my google account": the owner's own
        for st in steps:                                                        # words name the page exactly
            if st["tool"] == "open_website" and st.get("url") != url:
                st["url"] = url
                break
    if url:
        for i, st in enumerate(steps):
            if st["tool"] == "browser" and st.get("action") in ("read", "find", "") and not any(x["tool"] == "open_website" for x in steps):
                steps[i] = {"tool": "open_website", "profile": st.get("profile", "Default"), "url": url}
    named = account_from_text(request, profiles)
    acted = [st for st in steps if st["tool"] in ("open_website", "play_media")]
    if named and acted and not any(st["tool"] == "browser" and st.get("action") == "whoami" for st in steps):
        steps.insert(steps.index(acted[0]), {"tool": "browser", "action": "whoami", "profile": acted[0]["profile"], "target": "", "text": ""})
    return steps[:MAX_STEPS + 1]


FOLLOW_OPEN = re.compile(r"(?i)^\W*(?:please\s+|now\s+|can you\s+|ok\s+)?(?:open|show|launch|pull up)\s+(?:me\s+)?"
                         r"(?:it|that|this|them|the (?:file|report|deck|slides|presentation|pdf|document|page|site|app))"
                         r"(?:\s+(?:again|for me|now|please|up))*\W*$")


def follow_up(request: str, last: dict | None) -> Plan | None:
    """'open it' refers to what the last task verifiably produced: a made file, a page, an app. Nothing recent: ask."""
    if not FOLLOW_OPEN.match(request or ""):
        return None
    last = last or {}
    if last.get("file"):
        step = {"tool": "open_output", "path": last["file"], "post": POST["open_output"]}
    elif last.get("url"):
        step = {"tool": "open_website", "profile": last.get("profile") or "Default", "url": last["url"]}
        step["post"] = POST["open_website"].format(profile=step["profile"])
    elif last.get("app"):
        step = {"tool": "open_app", "app": last["app"]}
        step["post"] = POST["open_app"].format(app=last["app"])
    else:
        return Plan([], "clarify", question="Open what? Nothing was made or opened in the last few minutes.", model="rule")
    return Plan([step], "ok", model="rule")


APPS = {"notepad": "notepad", "calculator": "calc", "calc": "calc", "paint": "mspaint", "ms paint": "mspaint",
        "file explorer": "explorer", "explorer": "explorer", "spotify": "spotify", "chrome": "chrome", "google chrome": "chrome",
        "edge": "msedge", "microsoft edge": "msedge", "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
        "vs code": "code", "visual studio code": "code", "terminal": "wt", "settings": "ms-settings", "task manager": "taskmgr",
        "wordpad": "wordpad", "outlook": "outlook", "teams": "teams", "discord": "discord", "vlc": "vlc"}
FAST_BLOCK = re.compile(r"(?i)\b(and|then|also|after|before|it|that|this|them|those|account|profile|instead|actually|again|"
                        r"my \w+'s|for me and|unless|if)\b|[,;]|'s\b")
MEDIA_FAST = {r"(?:pause|stop playing|pause (?:the )?(?:music|video|song|playback))": ("playpause", "pause"),
              r"(?:resume|unpause|continue|resume (?:the )?(?:music|video|song|playback))": ("playpause", "play"),
              r"(?:next|skip)(?: (?:song|track|video))?(?: please)?|skip (?:this|the) (?:song|track)": ("nexttrack", ""),
              r"(?:previous|last|go back a)(?: (?:song|track|video))": ("prevtrack", ""),
              r"(?:turn (?:the )?)?volume up|louder|turn it up": ("volumeup", ""),
              r"(?:turn (?:the )?)?volume down|quieter|turn it down": ("volumedown", ""),
              r"mute|unmute": ("mute", "")}


def fast_path(request: str, profiles: list[dict]) -> Plan | None:
    """A whole request that is ONE simple, unambiguous command is planned by rules (no model, milliseconds); its plan goes
    through validate() exactly like the model's. Anything with a second part, a pronoun, an account or a profile, or a
    name it does not know, returns None and the model plans it."""
    r = re.sub(r"(?i)^(?:hey neko[, ]*|neko[, ]*|please |can you |could you |would you )+", "", (request or "").strip()).strip(" .!?")
    r = re.sub(r"(?i)\s+(?:please|for me|now)$", "", r)
    if not r or len(r) > 60 or FAST_BLOCK.search(r):
        return None
    low = r.lower()
    steps = None
    for pat, (key, intent) in MEDIA_FAST.items():
        if re.fullmatch(pat, low):
            steps = [{"tool": "press_key", "key": key, "text": intent or key}]
            break
    if steps is None:
        m = re.fullmatch(r"(?:open|launch|start|run|fire up)\s+(?:the\s+)?(.+?)(?:\s+app)?", low)
        g = re.fullmatch(r"(?:go to|open|visit|take me to)\s+(?:the\s+)?([a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?|[a-z ]+?)(?:\s+(?:website|site))?", low)
        p = re.fullmatch(r"(?:play|put on)\s+(?:some\s+)?(.{3,50})", low)
        w = re.fullmatch(r"(minimize|minimise|maximize|maximise|restore)\s+(?:the\s+)?(.+?)(?:\s+window)?", low)
        if m and m.group(1) in APPS:
            steps = [{"tool": "open_app", "app": m.group(1)}]
        elif g and (g.group(1) in SITES or re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}(/\S*)?", g.group(1))):
            steps = [{"tool": "open_website", "site": g.group(1), "account": ""}]
        elif p and not re.search(r"(?i)\b(on|in|from) (spotify|vlc|netflix|prime)\b", p.group(1)) and re.sub(
                r"(?i)\b(a|an|the|some|any|good|nice|new|song|songs|music|video|videos|track|tracks|something|anything)\b", "",
                p.group(1)).strip():                              # "play a song" names nothing: the model asks which
            steps = [{"tool": "play_media", "query": p.group(1), "account": ""}]
        elif w and w.group(2) in APPS:
            steps = [{"tool": "window", "action": w.group(1).replace("mise", "mize"), "app": w.group(2)}]
        elif re.fullmatch(r"how much (?:free )?(?:disk )?space(?: do i have| is there| is left)?(?: left)?(?: on (?:my )?(?:pc|computer|disk))?", low):
            steps = [{"tool": "check_pc", "action": "disk"}]
        elif re.fullmatch(r"(?:is my internet working|am i (?:connected|online)(?: to the internet)?|is the internet (?:working|down))", low):
            steps = [{"tool": "check_pc", "action": "network"}]
    if not steps:
        return None
    plan = validate(json.dumps({"steps": steps}), profiles, request)
    if plan.status != "ok":
        return None
    plan.model, plan.seconds = "rule", 0.0
    return plan


def decide(request: str, profiles: list[dict], model: str = DEFAULT_MODEL, post=None, timeout: float = 60.0,
           context: str = "", last: dict | None = None) -> Plan:
    """context: the owner's previous request, for follow-ups such as "actually use the Satish profile instead".
    last: what that request verifiably produced (coordinator.previous), for "open it"."""
    request = " ".join(str(request or "").split())[:1000]
    if re.fullmatch(r"(?i)\W*(stop|cancel|abort|never ?mind|forget it)\W*", request):     # no model needed
        return Plan([{"tool": "stop"}], "ok", model="rule")
    fu = follow_up(request, last)
    if fu is not None:
        return fu
    if not context and FAST_PATH:
        fp = fast_path(request, profiles)
        if fp is not None:
            return fp
    asked = f"Previous request: {context}\nNow: {request}" if context else request
    try:
        raw, secs = ask_model(asked, model, post=post, timeout=timeout)
    except DecisionError as e:
        return Plan([], "fallback", problems=[str(e)], model=model)
    plan = validate(raw, profiles, request + (" " + context if context and not account_from_text(request, profiles) else ""))
    plan.model, plan.seconds = model, round(secs, 2)
    return plan
