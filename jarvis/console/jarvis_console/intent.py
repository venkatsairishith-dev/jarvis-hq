"""A first guess of what a request IS, as metadata for routing and for the owner to see. Advisory only.

Evaluated on a held-out split (bench/run_intent_eval.py, results in bench/data/intent_results.json): a small local chat
model answering in JSON beat hand-written rules, embedding classifiers and the typed-decision encoders (Laya, Verdict).
So: the local model when its server is up, the rules when it is not. Neither one authorizes anything: the kernel decides
every side effect, a "blocked" guess only makes the console warn, and a wrong guess can cost the owner a glance, not safety.
"""
import json
import re
import time
import urllib.request
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "scripts"))
import loopback_net  # noqa: E402  loopback only, no proxy, no redirect (completion H, 2026-10-19)

CATEGORIES = ["pc_control", "vm_ops", "research", "build_software", "documents", "browser_task", "agent_factory", "automation",
              "memory", "status_control", "smalltalk_qa", "blocked", "clarify"]
DEFS = {
    "pc_control": "open or operate something on the owner's own PC (apps, folders, files, windows, screenshot)",
    "vm_ops": "start, stop, inspect or take control of the agent PC, a VM, the server or a remote node",
    "research": "find out, compare or summarize information from outside sources and report",
    "build_software": "write, fix, test or run code, websites or programs",
    "documents": "documents, spreadsheets, presentations and files as content: write, convert, organise, summarize",
    "browser_task": "do something in a web browser on a website",
    "agent_factory": "create, edit, clone, test, pause or delete an AI agent",
    "automation": "schedule, repeat, monitor or trigger work automatically",
    "memory": "remember, correct, list or forget a preference or fact about the owner",
    "status_control": "ask what is happening, or pause, resume, cancel or stop work",
    "smalltalk_qa": "a plain question or conversation that needs no action",
    "blocked": "asks for something that must be refused: secrets, passwords, money, destroying data, protected machines, granting itself permission, hiding logs",
    "clarify": "too vague to act on safely; a question back is needed",
}
LABELS = {"pc_control": "Operate this PC", "vm_ops": "Agent PC / VM", "research": "Research", "build_software": "Build software",
          "documents": "Documents", "browser_task": "Browser task", "agent_factory": "Agent factory", "automation": "Automation",
          "memory": "Memory", "status_control": "Status / control", "smalltalk_qa": "Question", "blocked": "Likely refused",
          "clarify": "Needs clarifying"}

RULES = [
    ("blocked", r"password|api key|secret|credential|private key|ssh key|kill switch|wipe the audit|delete everything|bank|transfer money|crypto|vault|hermes-agent-vm-2|without checking|permission to run|disable the approval|saved passwords|पासवर्ड|एपीआई कुंजी|పాస్‌వర్డ్"),
    ("status_control", r"\b(stop|pause|resume|cancel|retry)\b.*\b(task|tasks|everything|build|running|work)\b|what are you working|status of|what failed|did the last|why did that|how long until|waiting for my approval|list my open|रोक दो|ఆపండి"),
    ("agent_factory", r"\bagent\b|specialist"),
    ("automation", r"every (morning|day|night|hour|friday|two hours|monday)|nightly|schedule|automatically|monitor|remind me|when a file arrives|automation|weekdays|हर सुबह|ప్రతి ఉదయం"),
    ("memory", r"remember|forget|from now on|keep in mind|you got that wrong|what do you remember|याद रखो|note that|learn how i like"),
    ("vm_ops", r"agent pc|agent computer|\bvm\b|lab (vm|box|machine)|oracle|\bserver\b|\bnode\b|kernel service|ssh into|isolated machine|agent desktop"),
    ("browser_task", r"browser|website|web site|navigate to|go to the|\btab\b|youtube|web page|the page|click through|sign.?up flow"),
    ("pc_control", r"\bopen\b.*(folder|downloads|documents|notepad|calculator|paint|settings|spotify|code|explorer)|launch|screenshot|windows are open|on my screen|close the|bring the|नोटपैड|डाउनलोड|తెరవండి"),
    ("build_software", r"\b(python|script|code|bug|debug|refactor|unit test|flask|react|docker|regex|rust|javascript|sql|errors in my project|landing page|command line tool|पायथन|పైథాన్)\b"),
    ("documents", r"document|spreadsheet|presentation|slides|\bpdf\b|csv|excel|cover letter|proposal|report|summary|template|proofread|thesis|rename all|organise my"),
    ("research", r"research|compare|find out|look up|look into|investigate|literature|reviews of|pros and cons|state of the art|summari[sz]e the news|security advisories|रिसर्च"),
    ("smalltalk_qa", r"^(what is|what does|who|how do i|how many|explain|tell me|is a|give me|good morning|thanks|how are you)"),
    ("clarify", r"^(do it|fix it|open that|the usual|make it better|send it|go ahead|that file|same as|use the other|change it back|just make|can you handle|do what|run the thing|delete the old)"),
]

PROMPT = ("Classify the request from the owner of a personal AI assistant into exactly one category.\nCategories:\n{defs}\n"
          "Reply with JSON only: {{\"category\": \"<one of: {names}>\"}}\n\nRequest: {text}")


def rules_predict(text: str) -> str:
    t = text.lower().strip()
    for cat, pat in RULES:
        if re.search(pat, t):
            return cat
    return "clarify" if len(t.split()) <= 3 else "smalltalk_qa"


def llm_predict(url: str, model: str, text: str, timeout=20):
    """(category or None, ms, prompt_tokens) from an OpenAI-compatible local server. None = unparseable or unknown."""
    prompt = PROMPT.format(defs="\n".join(f"- {k}: {v}" for k, v in DEFS.items()), names=", ".join(CATEGORIES), text=text[:600])
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 40}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with loopback_net.urlopen(req, timeout=timeout) as r:
        o = json.loads(r.read())
    ms = (time.perf_counter() - t0) * 1000
    raw = re.sub(r"<think>.*?</think>", "", (o["choices"][0]["message"].get("content") or ""), flags=re.S).strip()
    m = re.search(r"\{.*?\}", raw, flags=re.S)
    try:
        cat = json.loads(m.group(0)).get("category") if m else None
    except ValueError:
        cat = None
    return (cat if cat in CATEGORIES else None), ms, (o.get("usage") or {}).get("prompt_tokens", 0)


DOWN_FOR = 30.0          # seconds to leave a model server alone after it failed
_down_until = 0.0        # a refused connection to localhost costs about two seconds on Windows: pay it once, not on every call


def classify(text: str, llm_url: str | None = None, model: str = "qwen3", clock=time.monotonic) -> dict:
    """Structured routing metadata. Always answers: the rules cover a down or confused model. `advisory` is always true."""
    text = " ".join(str(text or "").split())[:600]
    out = {"category": None, "label": None, "method": "rules", "advisory": True, "ms": 0, "also": None}
    if not text:
        return dict(out, category="clarify", label=LABELS["clarify"])
    rule = rules_predict(text)
    global _down_until
    if llm_url and clock() >= _down_until:
        try:
            cat, ms, _ = llm_predict(llm_url, model, text)
            if cat:
                # the safer of the two: if either mechanism says "blocked", the console warns (it never executes on a guess)
                final = "blocked" if "blocked" in (cat, rule) else cat
                return {"category": final, "label": LABELS[final], "method": "local-llm", "advisory": True, "ms": round(ms),
                        "also": rule if rule != final else None}
        except Exception:
            _down_until = clock() + DOWN_FOR                   # server down or slow: the rules answer, and it is not asked again for a while
    return dict(out, category=rule, label=LABELS[rule])
