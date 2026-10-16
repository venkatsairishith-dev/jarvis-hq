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
         "calendar": "https://calendar.google.com/", "google calendar": "https://calendar.google.com/", "spotify": "https://open.spotify.com/"}
MAX_STEPS = 4

SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "minItems": 1, "maxItems": MAX_STEPS, "items": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "enum": list(TOOLS)},
                "action": {"type": "string"},
                "account": {"type": "string"},
                "site": {"type": "string"},
                "app": {"type": "string"},
                "query": {"type": "string"},
                "target": {"type": "string"},
                "text": {"type": "string"},
                "key": {"type": "string"},
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
    "search or play; text = text to type; key = a key such as ctrl+s, enter, playpause.\n"
    "- Playing a song or video is play_media, not open_website.\n"
    "- An error, crash or something not working in an app is troubleshoot.\n"
    "- \"stop\", \"cancel\", \"never mind\" is stop.\n"
    "- If the request is too vague to act on (no app, site or goal), use clarify with one short question.\n"
    "- Never add steps the owner did not ask for. Never decide that something is allowed: the system checks that.\n"
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


def ask_model(request: str, model: str = DEFAULT_MODEL, url: str = LOCAL, timeout: float = 60.0, post=None) -> tuple[str, float]:
    body = {"model": model, "temperature": 0, "max_tokens": 400, "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {"name": "plan", "schema": SCHEMA}},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": request}]}
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


def resolve_site(site: str) -> str | None:
    s = (site or "").strip()
    if not s:
        return None
    if re.match(r"^https?://", s, re.I):
        return s
    key = s.lower().removesuffix(".com").strip() if s.lower() not in SITES else s.lower()
    if s.lower() in SITES:
        return SITES[s.lower()]
    if key in SITES:
        return SITES[key]
    if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$", s, re.I):
        return "https://" + s
    return None


def validate(raw: str, profiles: list[dict]) -> Plan:
    """Parse the model's JSON and turn every step into a concrete, checked tool call; anything unclear -> clarify."""
    try:
        data = json.loads(raw)
    except ValueError:
        return Plan([], "fallback", problems=["the model's answer was not JSON"], raw=raw)
    steps, problems = [], []
    for s in (data.get("steps") or [])[:MAX_STEPS]:
        tool = s.get("tool")
        if tool not in TOOLS:
            problems.append(f"unknown tool {tool!r}")
            continue
        out = {"tool": tool}
        if tool in ("open_website", "play_media", "browser"):
            folder, why = resolve_account(s.get("account", ""), profiles)
            if folder is None:
                return Plan(steps, "clarify", question=f"Which Chrome account? {why}.", problems=[why], raw=raw)
            out["profile"] = folder
        if tool == "open_website":
            url = resolve_site(s.get("site") or s.get("target") or s.get("query") or "")
            if not url:
                return Plan(steps, "clarify", question="Which website should I open?", problems=["no site"], raw=raw)
            out["url"] = url
        elif tool == "play_media":
            q = (s.get("query") or s.get("text") or "").strip()
            if not q:
                return Plan(steps, "clarify", question="What should I play?", problems=["no query"], raw=raw)
            out["query"] = q[:200]
        elif tool == "browser":
            act = (s.get("action") or "").lower()
            if act not in ACTIONS["browser"]:
                act = "read" if not s.get("target") else "find"
            out.update(action=act, target=(s.get("target") or s.get("query") or "")[:300], text=(s.get("text") or s.get("key") or "")[:500])
        elif tool in ("open_app", "window", "type_text", "press_key", "troubleshoot"):
            app = (s.get("app") or s.get("target") or "").strip()
            key = (s.get("key") or s.get("text") or "").strip().lower()
            if not app and not (tool == "press_key" and key in ("playpause", "nexttrack", "prevtrack", "volumeup", "volumedown", "mute")):
                return Plan(steps, "clarify", question="Which app?", problems=["no app"], raw=raw)
            out["app"] = app[:80]
            if tool == "window":
                act = (s.get("action") or "focus").lower()
                out["action"] = act if act in ACTIONS["window"] else "focus"
            if tool == "type_text":
                out.update(target=(s.get("target") or "")[:120], text=(s.get("text") or "")[:500])
            if tool == "press_key":
                out["key"] = key[:30]
        elif tool == "open_file":
            name = (s.get("target") or s.get("query") or s.get("text") or "").strip()
            if not name:
                return Plan(steps, "clarify", question="Which file?", problems=["no file name"], raw=raw)
            out.update(name=name[:120], folder=(s.get("site") or s.get("app") or "")[:40])
        elif tool == "check_pc":
            act = (s.get("action") or "").lower()
            out.update(check=act if act in ACTIONS["check_pc"] else ("app_info" if s.get("app") else "disk"), app=(s.get("app") or "")[:40])
        elif tool == "research":
            q = (s.get("query") or s.get("text") or "").strip()
            if not q:
                return Plan(steps, "clarify", question="What should I research?", problems=["no topic"], raw=raw)
            deliverable = "presentation" if re.search(r"(?i)slide|deck|presentation|ppt", json.dumps(s)) else (
                "report" if re.search(r"(?i)report|pdf", json.dumps(s)) else "none")
            out.update(query=q[:300], deliverable=deliverable)
        elif tool == "clarify":
            return Plan(steps, "clarify", question=(s.get("question") or "Could you say a bit more?")[:200], raw=raw)
        steps.append(out)
    if not steps:
        return Plan([], "fallback", problems=problems or ["no usable step"], raw=raw)
    return Plan(steps, "ok", problems=problems, raw=raw)


def decide(request: str, profiles: list[dict], model: str = DEFAULT_MODEL, post=None, timeout: float = 60.0) -> Plan:
    request = " ".join(str(request or "").split())[:1000]
    if re.fullmatch(r"(?i)\W*(stop|cancel|abort|never ?mind|forget it)\W*", request):     # no model needed
        return Plan([{"tool": "stop"}], "ok", model="rule")
    try:
        raw, secs = ask_model(request, model, post=post, timeout=timeout)
    except DecisionError as e:
        return Plan([], "fallback", problems=[str(e)], model=model)
    plan = validate(raw, profiles)
    plan.model, plan.seconds = model, round(secs, 2)
    return plan
