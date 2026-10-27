"""D74 (2026-10-27): ASK, DO NOT GUESS, in the desktop plugin's three doors (do_goal, quick_command, run_pc_task).

The rules are plugins/_shared/ambiguity.py over config/ambiguity.json. This module is the glue and nothing more:

    gate_request(text, fetch, ask)   before ANY plan or action: a request that must be asked about becomes ONE question (a jarvis_ask form on the owner's panel when
                                     the plugin runs inside the face's tool server, else a form in the result for the chat model to show); when the owner answers,
                                     the ORIGINAL request goes on with his answer put in. Clearly resolved requests pass through untouched, the same text.
    finish(res, ...)                 the decider's / planner's own 'ask' outcome (needs_clarification with a question and nothing done) ends in the SAME kind of form,
                                     not in a sentence the chat model may leave empty; with a panel it is asked and the goal runs once more with the answer.

Nothing here approves anything: a form collects a choice (D55), the kernel still decides every job and asks for the PIN as before.
It never raises into a request: any failure means 'no question' (the planner, the coordinator and the kernel have their own rules).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from jarvis.plugins._shared import ambiguity

LOG = logging.getLogger("jarvis.ambiguity")          # content-free: the rule's fixed name and a count, never the owner's words
PLANNED_BY = "ambiguity_rules"
ENV_OFF = "JARVIS_AMBIGUITY_GATE"


def enabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_OFF, "")).strip().lower() not in ("0", "false", "no", "off") and ambiguity.load().enabled


@dataclass
class Outcome:
    texts: list[str]                         # the request(s) to carry on with: the given text when nothing was asked
    result: dict | None = None               # set: stop here, nothing runs; this is the answer to give (the question, or 'no answer, nothing done')
    asked: int = 0                           # how many questions the owner was shown
    resolved: bool = False                   # the text changed because he answered
    verdicts: list[str] = field(default_factory=list)      # the rules that fired (fixed words)


Fetch = Callable[[set], Awaitable[ambiguity.Context]]
Ask = Callable[[dict, float], Awaitable[dict | None]]


def input_request(question: str) -> dict:
    """The pending-input contract (main_agent/input_request.py): the same shape run_pc_task already returns for a clarification."""
    return {"title": "Clarification", "prompt": str(question)[:500], "answer_arg": "answers", "expires_s": 3600,
            "fields": [{"name": "clarification", "kind": "text", "label": "Your answer", "max_len": 300}]}


def question_result(v: ambiguity.Verdict, *, answered: bool = False, why: str = "") -> dict:
    """The do_goal-shaped result when nothing runs. The form holds the question and generic labels only (ambiguity.form): file names and window titles stay on the panel."""
    form = ambiguity.form(v)
    labels = " / ".join(o["label"] for o in form["options"])
    if answered is False and why == "no_panel":
        summary = (f"I need one answer before I do anything: {v.question}" + (f" Options: {labels}." if labels else "")
                   + " Nothing was done. Show this question with jarvis_ask (a choice with a Something else box), then call desktop__do_goal again with the original goal and the answer put in.")
    else:
        summary = f"I asked: {v.question} I did not get an answer, so I did nothing."
    out = {"status": "needs_clarification", "planned_by": PLANNED_BY, "summary": summary[:600], "question": v.question[:300], "ask": form, "steps": [],
           "reason": (v.reason or "")[:200], "input_request": input_request(v.question)}
    return out


def merge(results: list[dict]) -> dict:
    """One answer for several requests run in turn ('close these windows'): the first that did not complete decides the status; the summaries are joined."""
    results = [r for r in results if isinstance(r, dict)]
    if not results:
        return {"status": "failed", "planned_by": PLANNED_BY, "summary": "Nothing was run.", "steps": []}
    if len(results) == 1:
        return results[0]
    bad = next((r for r in results if r.get("status") != "completed"), None)
    head = bad or results[0]
    out = dict(head)
    out["status"] = (bad or results[0]).get("status", "completed")
    out["summary"] = " ".join(str(r.get("summary") or "").strip() for r in results)[:1500]
    out["steps"] = [s for r in results for s in (r.get("steps") or [])][:20]
    return out


async def gate_request(text: str, fetch: Fetch, ask: Ask | None, answers: dict | None = None, rules: ambiguity.Rules | None = None) -> Outcome:
    """Run the rules on `text`; ask (once per ambiguity, at most `max_rounds` times); return what to do next. Never raises."""
    given = " ".join(str(text or "").split())
    try:
        r = rules or ambiguity.load()
        if not enabled() or not r.enabled or not given:
            return Outcome([text])
        cur, asked, fired = given, 0, []
        clar = ""
        if isinstance(answers, dict) and isinstance(answers.get("clarification"), str) and answers["clarification"].strip():
            clar = " ".join(answers["clarification"].split())[:300]          # the pending-input contract: a re-run with the owner's answer
        for _ in range(r.max_rounds):
            need = ambiguity.needs(cur, r)
            ctx = await fetch(need) if need else ambiguity.Context()
            if need and ctx is None:
                ctx = ambiguity.Context()
            v = ambiguity.check(cur, ctx, r)
            if v is None:
                return Outcome([cur], asked=asked, resolved=cur != given, verdicts=fired)
            fired.append(v.rule)
            LOG.info("gate: rule=%s round=%d panel=%s", v.rule, len(fired), ask is not None)
            if v.auto_text:                                                  # the request means this (the one window JARVIS opened): nothing to ask
                cur = v.auto_text
                continue
            if clar:                                                         # re-run after he answered elsewhere (the chat model asked him): put it in, once
                new = ambiguity.resolve(v, {"answered": True, "button": "ok", "values": {"other": clar}})
                clar = ""
                if new:
                    cur = new[0] if len(new) == 1 else cur
                    if len(new) > 1:
                        return Outcome(new, asked=asked, resolved=True, verdicts=fired)
                    continue
            if ask is None:
                return Outcome([], question_result(v, why="no_panel"), asked=asked, verdicts=fired)
            asked += 1
            answer = await ask(ambiguity.spec(v), r.ask_timeout_s)
            new = ambiguity.resolve(v, answer)
            if not new:
                return Outcome([], question_result(v, answered=False, why="unanswered"), asked=asked, verdicts=fired)
            if len(new) > 1:
                return Outcome(new, asked=asked, resolved=True, verdicts=fired)
            cur = new[0]
        # more questions than the budget allows: stop rather than loop
        return Outcome([], {"status": "needs_clarification", "planned_by": PLANNED_BY, "steps": [], "question": "Could you say the whole request again, with the names in it?",
                            "summary": "I asked a few questions and still cannot tell what you mean. I did nothing. Say the whole request again with the names in it.",
                            "ask": ambiguity.form(ambiguity.clarify_verdict("Could you say the whole request again, with the names in it?", given, rules=r)),
                            "reason": "too many questions"}, asked=asked, verdicts=fired)
    except Exception:  # noqa: BLE001 - the gate must never stop an ordinary request
        return Outcome([text])


def _nothing_done(res: dict) -> bool:
    for s in res.get("steps") or []:
        if isinstance(s, dict) and s.get("status") in ("completed", "waiting_for_permission", "failed"):
            return False
    return True


def _question_of(res: dict) -> str:
    q = str(res.get("question") or "").strip()
    if q:
        return q
    for s in res.get("steps") or []:
        if isinstance(s, dict) and s.get("status") == "needs_clarification" and str(s.get("result") or "").strip():
            return str(s["result"]).strip()
    return ""


async def finish(res: dict, text: str, ask: Ask | None, rerun: Callable[[str], Awaitable[dict]], rules: ambiguity.Rules | None = None) -> dict:
    """The decider's 'ask' outcome (a planner clarification, a folder / file / app it was not sure of) ends in the same form: it is attached to the result and, when the
    owner's panel is here and NOTHING had been done yet, asked at once; the goal then runs once more with his answer added (the "(The owner's clarification: ...)" line the
    planner already understands). A question from the middle of a task (steps already done) is attached but never re-run: that would repeat them."""
    try:
        if not isinstance(res, dict) or res.get("status") != "needs_clarification" or res.get("planned_by") == PLANNED_BY or not enabled():
            return res
        if isinstance(res.get("ask"), dict) and ambiguity.ask_form.is_form(res["ask"]) and not _nothing_done(res):
            return res                                                            # a form from the control loop (pcagent): it already has its own words
        q = _question_of(res)
        if not q and not isinstance(res.get("ask"), dict):
            return res
        r = rules or ambiguity.load()
        if res.get("planned_by") == "translation":
            ask = None                                                            # a doubtful translation: the form is shown, but the goal is not re-run on a mixed text
        v = ambiguity.clarify_verdict(q or "Could you say a bit more?", text, rules=r)
        res = {**res, "ask": ambiguity.form(v), "question": q or v.question}
        res.setdefault("input_request", input_request(v.question))
        if ask is None or not _nothing_done(res):
            return res
        answer = await ask(ambiguity.spec(v), r.ask_timeout_s)
        new = ambiguity.resolve(v, answer)
        if not new:
            res["summary"] = (str(res.get("summary") or v.question).rstrip() + " I did not get an answer, so I did nothing.")[:600]
            return res
        res2 = await rerun(new[0])
        return await finish_once(res2, text)
    except Exception:  # noqa: BLE001
        return res


async def finish_once(res: dict, text: str) -> dict:
    """The second result: if it still wants an answer, the form is attached and nobody waits again (no loop)."""
    try:
        if isinstance(res, dict) and res.get("status") == "needs_clarification" and not isinstance(res.get("ask"), dict):
            q = _question_of(res)
            if q:
                v = ambiguity.clarify_verdict(q, text)
                return {**res, "ask": ambiguity.form(v), "question": q}
    except Exception:  # noqa: BLE001
        pass
    return res
