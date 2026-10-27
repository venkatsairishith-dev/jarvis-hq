"""The final-answer guard (D59, lever b): the sentence the owner reads at the end comes from what was VERIFIED, not from what a model says.

What was seen on 2026-10-26: the PC work was done and checked (Calculator open, weather read), and the model's last sentence was
lost in every run (a 429, a provider-side 400 "Unterminated string" while the model formed its reply). A model can also say "Done!"
over a step that failed. So the owner's last message is built like this:

    model text  --guard(...)-->  the model's text, if it is non-empty, has no error leak, and does not contradict the verified results
                                 otherwise a templated sentence made from the results (source="template", with the reasons)

The results are `Effect`s: one per step, `verified` (the effect was observed), `failed` (it was observed NOT to have happened or the
step errored), neither (reported by the node, not observed). `effects_from_record` reads the desktop coordinator's task record,
`effects_from_goal_result` reads desktop/goal.py's result. Nothing here calls a model or the network.

Contradiction rules (all deterministic, English plus a few Hindi and Telugu markers; the template itself is English only):
  empty        nothing, punctuation or an ellipsis only, a tool-call blob, a lone <think> block
  leak         an HTTP code, "Unterminated string", a traceback, "Remote sent ..." in the owner's message
  overclaim    the model claims success but the task did not complete, or a step failed, or the task needs the owner's PIN
  underclaim   the model says it failed while every step was verified (it would make the owner redo finished work)
  hedge-less   the model claims plain success while steps were only reported, not observed, and does not say so
  silent wait  the task waits for the owner's approval and the model does not say so

Standard library only.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

TEMPLATE, MODEL = "template", "model"
MAX_WHAT = 120
MAX_TEXT = 700

COMPLETED, FAILED, WAITING, CLARIFY, CANCELLED, OTHER = "completed", "failed", "waiting_for_permission", "needs_clarification", "cancelled", "other"


@dataclass(frozen=True)
class Effect:
    what: str                      # "Calculator is open", "the note was saved"
    verified: bool = False         # observed
    failed: bool = False           # observed absent, or the step errored
    detail: str = ""               # why it failed, short


@dataclass
class Guarded:
    text: str
    source: str                    # "model" | "template"
    reasons: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------------------ reading results
def _cut(text: str, n: int) -> str:
    """text[:n], never ending inside a half placeholder: the owner's display restores "[PERSON_1]" (D73), and "[PERSO" cannot be restored."""
    if len(text) <= n:
        return text
    head = text[:n]
    i = head.rfind("[")
    return head[:i] if i != -1 and "]" not in head[i:] and n - i <= 40 else head


def _short(text: Any, n: int = MAX_WHAT) -> str:
    return _cut(re.sub(r"\s+", " ", str(text or "")).strip(), n).rstrip(" .;,")


def _norm_status(status: Any) -> str:
    s = str(status or "").lower()
    if s in ("completed", "done", "ok", "success"):
        return COMPLETED
    if s in ("waiting_for_permission", "waiting", "needs_approval"):
        return WAITING
    if s in ("needs_clarification", "clarify", "asked"):
        return CLARIFY
    if s in ("cancelled", "canceled", "stopped"):
        return CANCELLED
    if s in ("failed", "denied", "error", "busy", "not_understood", "timeout"):
        return FAILED
    return OTHER


def effects_from_record(rec: dict) -> tuple[str, list[Effect]]:
    """(status, effects) from a coordinator task record (plugins/desktop/coordinator.py: steps[] with passed/checked/uncertainty)."""
    out: list[Effect] = []
    for s in (rec or {}).get("steps") or []:
        checked = _short(s.get("checked"))
        unc = _short(s.get("uncertainty"))
        if s.get("passed") is True:
            out.append(Effect(checked or f"step {s.get('n', '?')} ({s.get('tool', '')})", verified=not unc, detail=unc))
        else:
            out.append(Effect(checked or f"step {s.get('n', '?')} ({s.get('tool', '')})", failed=True, detail=unc or _short(rec.get("error"))))
    return _norm_status(rec.get("status")), out


def effects_from_goal_result(res: dict) -> tuple[str, list[Effect]]:
    """(status, effects) from desktop/goal.py's GoalRunner result: steps[] with status/result/verified."""
    out: list[Effect] = []
    for s in (res or {}).get("steps") or []:
        st = str(s.get("status") or "")
        what = _short(s.get("goal") or s.get("executor") or f"step {s.get('n', '?')}")
        if st == "completed":
            out.append(Effect(what, verified=bool(s.get("verified")), detail="" if s.get("verified") else "reported, not observed"))
        elif st == "not_run":
            continue
        else:
            out.append(Effect(what, failed=True, detail=_short(s.get("result"))))
    return _norm_status((res or {}).get("status")), out


# -------------------------------------------------------------------------------------------------------------- the sentences
def template(status: str, effects: Iterable[Effect], *, approval_id: str = "", question: str = "", detailed: bool = True) -> str:
    """The deterministic sentence for the owner. `detailed=False` names counts only (for a reply that may be private)."""
    fx = list(effects)
    ok = [e for e in fx if e.verified]
    soft = [e for e in fx if not e.verified and not e.failed]
    bad = [e for e in fx if e.failed]
    done = "; ".join(e.what for e in ok)
    if status == WAITING:
        what = next((e.what for e in fx if e.failed or not e.verified), "") if detailed else ""
        return (f"This needs your approval first{': ' + what if what else ''}. Approve it in the JARVIS console with your PIN, then ask me to "
                f"continue{f' (approval {approval_id})' if approval_id else ''}." + (f" Already done: {done}." if done and detailed else ""))
    if status == CLARIFY:
        return question or "Could you say a bit more about what you want?"
    if status == CANCELLED:
        return "Stopped." + (f" Before the stop: {done}." if done and detailed else "")
    if status == COMPLETED and not bad:
        if not fx:
            return "Done."
        if not detailed:
            return f"Done. {len(ok)} of {len(fx)} steps were checked." if soft else "Done. Every step was checked."
        if soft:
            return (f"Done, but not everything was observed. Checked: {done or 'nothing'}. Only reported, not checked: "
                    + "; ".join(e.what for e in soft) + ".")
        return f"Done: {done}."
    first = bad[0] if bad else None
    head = "I could not finish" + (f": {first.what}" + (f" ({first.detail})" if first.detail and detailed else "") if first and detailed else "")
    if not detailed:
        return f"{head}. {len(ok)} of {len(fx)} steps were checked before it stopped." if fx else f"{head}."
    return f"{head}." + (f" Already done: {done}." if done else "")


# ------------------------------------------------------------------------------------------------------------ judging a model text
_BLOBS = re.compile(r"(?s)<think(?:ing)?>.*?</think(?:ing)?>|<tool_call>.*?</tool_call>|<\|[^|]{1,40}\|>")
_JSON_ONLY = re.compile(r'^\s*[\[{].*[\]}]\s*$', re.S)
_LEAK = re.compile(r"(?i)unterminated string|traceback \(most recent|\bHTTP\s*[45]\d\d\b|\b(?:error|status)\s*[:=]?\s*[45]\d\d\b|remote sent \d+|"
                   r"error from server|didn't finish|\b429\b.*\b(?:rate|capacity)\b|tool call has an unknown shape|reasoning_content")
_SUCCESS = re.compile(r"(?i)\b(done|completed?|finished|opened|saved|created|written|wrote|typed|deleted|removed|moved|sent|closed|playing|"
                      r"it'?s (?:open|done|saved)|i(?:'ve| have) (?:opened|saved|created|written|typed|done|deleted|finished)|all set|successfully)\b"
                      r"|हो गया|कर दिया|खोल दिया|सेव कर दिया|పూర్తయింది|చేశాను|తెరిచాను|సేవ్ చేశాను")
_FAILURE = re.compile(r"(?i)\b(couldn'?t|could not|can'?t|cannot|unable|failed|failure|didn'?t work|did not work|not able|wasn'?t able|was not able|"
                      r"no luck|something went wrong|sorry)\b|नहीं हो सका|नहीं कर सका|कर नहीं पाया|కాలేదు|చేయలేకపోయాను|సాధ్యం కాలేదు")
_HEDGE = re.compile(r"(?i)not (?:everything|all|certain)|uncertain|not (?:been )?(?:checked|observed|confirmed|verified)|could not (?:confirm|verify|check)|"
                    r"can'?t (?:confirm|verify)|reported|unverified|may |might |should be|i think|appears? to")
_WAIT = re.compile(r"(?i)approv|\bPIN\b|permission|confirm in the|waiting for you|अनुमति|मंज़ूरी|అనుమతి|ఆమోదం")


def _visible(text: Any) -> str:
    t = _BLOBS.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", t).strip()


def is_empty(text: Any) -> bool:
    """Nothing the owner could read: empty, punctuation/ellipsis only, a JSON or tool-call blob, a lone thinking block."""
    t = _visible(text)
    if not t or not re.search(r"\w", t):
        return True
    if _JSON_ONLY.match(t):
        try:
            json.loads(t)
            return True                                   # a JSON object is a blob, not a message
        except ValueError:
            return False
    return False


def problems(text: Any, status: str, effects: Iterable[Effect]) -> list[str]:
    """Why a model text cannot be trusted as the final message (empty list = it can)."""
    fx = list(effects)
    t = _visible(text)
    if is_empty(text):
        return ["empty"]
    out: list[str] = []
    if _LEAK.search(t):
        out.append("leak")
    says_ok, says_bad, hedges = bool(_SUCCESS.search(t)), bool(_FAILURE.search(t)), bool(_HEDGE.search(t))
    any_failed = any(e.failed for e in fx)
    soft = [e for e in fx if not e.verified and not e.failed]
    if status in (FAILED, CANCELLED, OTHER) or any_failed or status in (WAITING, CLARIFY):
        if says_ok and not says_bad and not hedges and not (status == WAITING and _WAIT.search(t)):
            out.append("overclaim")
    if status == WAITING and not _WAIT.search(t):
        out.append("silent_wait")
    if status == COMPLETED and fx and not any_failed and not soft and says_bad and not says_ok:
        out.append("underclaim")
    if status == COMPLETED and soft and says_ok and not hedges and not says_bad:
        out.append("hedgeless")
    return out


def guard(model_text: Any, status: str, effects: Iterable[Effect], *, approval_id: str = "", question: str = "", detailed: bool = True) -> Guarded:
    """The final message: the model's own text when it is sound, else the deterministic sentence from the verified results.
    With NO results at all (a chat reply, not a task), only an empty or leaking text is replaced."""
    fx = list(effects)
    if not fx and status in (COMPLETED, OTHER):
        why = [p for p in problems(model_text, status, fx) if p in ("empty", "leak")]
        if not why:
            return Guarded(_cut(_visible(model_text), MAX_TEXT), MODEL)
        return Guarded("Sorry, my answer did not come through. Please ask me again.", TEMPLATE, why)
    why = problems(model_text, status, fx)
    if not why:
        return Guarded(_cut(_visible(model_text), MAX_TEXT), MODEL)
    return Guarded(template(status, fx, approval_id=approval_id, question=question, detailed=detailed), TEMPLATE, why)


def answer_for_task(state: str, reply: str = "", error: str = "", *, detailed: bool = False, approval_id: str = "") -> str:
    """The sentence jarvis_task_result hands the chat model as `answer`, from the console's command row (state running/done/failed and
    the reply the console kept). With `detailed` False (the reply may be private) it carries no detail from the reply."""
    st = str(state or "")
    if st == "running":
        return "JARVIS is still working on it. It may be waiting for your approval."
    if st == "done":
        return _cut(re.sub(r"\s+", " ", str(reply)).strip(), MAX_TEXT) if detailed and _visible(reply) and not _LEAK.search(str(reply)) else "Done. JARVIS reports it finished."
    if st == "failed":
        why = _short(error, 160) if detailed else ""
        return "I could not finish that" + (f": {why}." if why else ".")
    return f"The task is {st or 'in an unknown state'}."
