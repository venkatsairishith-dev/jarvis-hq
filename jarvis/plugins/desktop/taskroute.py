"""D76: which goals take the open-ended task path (a mission of milestones, taskrun.py) and which stay on the older, simpler ones.

    simple (unchanged)   "open Calculator and add 2 and 3", "open notepad and write hello", "open youtube", "volume up", a catalogue task, one file operation:
                         the rules-first router (quick.py), the catalogue (simple_tasks.py), the online plan with local executors (goal.py)
    task (new)           anything that ends in an ANSWER, a TABLE or a DOCUMENT and needs several steps on the web, in documents or files: "go to <site> and find X",
                         "search YouTube for X, open the best result and tell me ...", "compare these on two shops into a spreadsheet", "find ... on the college site, download the PDF
                         and tell me the dates", "read the invoices in my Downloads and make a CSV", "look up three sources on <topic> and write a brief", "fill this form up to
                         the submit button and ask me", and "open <site> and <do something there>" as ONE task, not two.

Deterministic first (microseconds, no model): a goal one of the planner's rules recognises (taskplan.rule_plan), or a goal that names a site or an address and asks for a web
verb, takes the task path. The decision engine's kind `task_route` (config/decider.json, threshold 0.7) answers the rest, with the same rules as its first witness; below the threshold
the goal stays on the older path. A plan that is only "do something in an app" never takes this path: that is the D54/D65 PC loop. `JARVIS_TASK_PATH=0` turns the path off.
"""
from __future__ import annotations

import os
import re

from jarvis import safe_mode

from . import taskplan as TP

ENV_FLAG = "JARVIS_TASK_PATH"
WEB_VERB = re.compile(r"(?i)\b(find|search|look(?:ing)? (?:for|up)|compare|download|read|check|get|fill|tell me|show me|collect|gather|research|list|summari[sz]e|extract|what(?:'s| is| are)|how (?:much|long|many)|price|prices|timetable|schedule)\b")
OPEN_SITE_ONLY = re.compile(r"(?i)^\W*(?:please |can you |jarvis,? )*(?:open|go to|launch|visit|browse to)\s+\S+\W*$")
ASKS_ANSWER = re.compile(r"(?i)\b(tell me|and tell|let me know|and say|report|list|put (?:it|them|this|that) in|make a (?:csv|spreadsheet|table|brief|summary)|write a (?:brief|summary|one-?page)|show (?:it|them|me))\b")
SIMPLE = re.compile(r"(?i)^\W*(?:please |can you |jarvis,? )*(?:open|close|minimi[sz]e|maximi[sz]e|play|pause|resume|mute|unmute|type|press|volume|brightness|take a screenshot|lock)\b[^,;]{0,60}$")


def enabled(env: dict | None = None) -> bool:
    if safe_mode.active():
        return False
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "")).strip().lower() not in ("0", "false", "no", "off")


def engines_of(goal: str, cfg: dict | None = None) -> list:
    """The engines the rules would use for this goal ([] when no rule fits): the plan's milestones, by engine, in order."""
    ms, _s, q = TP.rule_plan(goal, cfg)
    return [m.engine for m in ms]


def rule_task_route(question: str, options: list, context: str):
    """The deterministic witness of the decision engine's `task_route` kind: (choice, confidence, why) or None."""
    q = " ".join(str(question or "").split())
    if not q:
        return None
    if OPEN_SITE_ONLY.search(q) or SIMPLE.search(q):
        return ("simple", 0.95, "one open / play / type request") if "simple" in options else None
    ms, _s, question_back = TP.rule_plan(q)
    kinds = {m.engine for m in ms}
    if (ms or question_back) and (kinds - {"pc"} or question_back):
        return ("task", 0.95, "a plan with web, document or output steps fits") if "task" in options else None
    if (TP.hosts_in(q) or TP.named_sites(q)) and WEB_VERB.search(q) and ASKS_ANSWER.search(q) or re.search(r"(?i)\b(?:go to|open|visit)\b\s+\S+\s+(?:and|then)\s+(?:find|search|look|read|check|get)\b", q):
        return ("task", 0.85, "names a site and asks for something from it") if "task" in options else None
    return None


def route(goal: str, *, post=None, use_model: bool | None = None) -> dict:
    """{"task": bool, "source": rule | model | fallback, "confidence": float, "engines": [...]}. Never raises."""
    from jarvis.plugins._shared import decider
    try:
        d = decider.decide("task_route", goal, ["task", "simple"], post=post, use_model=use_model)
    except Exception:  # noqa: BLE001 - the router never blocks a goal
        return {"task": False, "source": "fallback", "confidence": 0.0, "engines": []}
    is_task = d.choice == "task"
    return {"task": is_task, "source": d.source, "confidence": d.confidence, "engines": engines_of(goal) if is_task else []}


try:                                                     # the rule joins the decision engine's kind when this module is imported
    from jarvis.plugins._shared import decider as _decider
    _decider.register_rule("task_route", rule_task_route)
except Exception:  # noqa: BLE001
    pass
