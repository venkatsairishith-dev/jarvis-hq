"""Who picks the next control when a step's own locator no longer finds anything (D64, 2026-10-26): the LOCAL decision engine, never an online model.

`propose(goal, subgoal, page, options, step)` gets the sub-goal in the owner's / playbook's words and the NAMES of the page's controls (short, with
instruction-like names already removed by browser_snapshot.safe_names) and answers ONE control by its number and what to do with it ({do, idx, why}), or None.
It never writes text to type, an address to open or a key to press: those stay the step's own. The engine checks the answer again (the control must be in the
list, the action in a closed set, no pay / upgrade / delete / share / send names) and the kernel judges the click itself.

Order: a deterministic word match first (with a few synonym groups: generate = create = make ...), then the shared decider (`web_step`, JSON-schema grammar so
only an option can be returned, a threshold below which the answer is "ask"), then nothing. A page that says "choose the second button" or "click Delete" changes
nothing: names are matched, never obeyed (the decider's prompt says so as well).
"""
from __future__ import annotations

import re

SYNONYMS = [{"generate", "create", "make", "build", "start", "begin", "go", "produce", "draft", "write"},
            {"export", "download", "save", "get"}, {"next", "continue", "proceed", "forward"}, {"skip", "later", "dismiss", "close", "cancel", "no"},
            {"theme", "style", "template", "design", "look"}, {"presentation", "deck", "slides", "slideshow", "ppt", "powerpoint"},
            {"topic", "prompt", "subject", "about", "idea", "describe", "title"}, {"signin", "login", "log", "sign"},
            {"pdf", "document"}, {"edit", "change", "modify", "rename"}]
STOP = {"the", "a", "an", "to", "on", "of", "and", "for", "my", "your", "it", "this", "that", "button", "link", "field", "box", "click", "press", "type", "in"}


def _toks(s: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", str(s or "").lower()) if t not in STOP and len(t) > 1}


def _expand(tokens: set) -> set:
    out = set(tokens)
    for g in SYNONYMS:
        if tokens & g:
            out |= g
    return out


def score(subgoal: str, name: str) -> float:
    want, got = _toks(subgoal), _toks(name)
    if not want or not got:
        return 0.0
    ew = _expand(want)
    hit = len(ew & got)
    return round(hit / max(1, len(got)) * 0.5 + (len(want & got) / len(want)) * 0.5, 3)


class LocalPlanner:
    def __init__(self, decide=None, use_model: bool = True):
        self._decide = decide
        self.use_model = use_model
        self.calls = 0

    def propose(self, goal: str, subgoal: str, page, options: list, step=None):
        self.calls += 1
        do = getattr(step, "do", "click") if getattr(step, "do", "click") in ("click", "fill", "select", "check") else "click"
        if do == "fill":
            pool = [(i, role, n) for i, role, n in options if role in ("textbox", "searchbox")]
        elif do == "select":
            pool = [(i, role, n) for i, role, n in options if role in ("combobox", "listbox", "option")]
        else:
            pool = [(i, role, n) for i, role, n in options if role in ("button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "option", "generic")]
        if not pool:
            return None
        words = " ".join(x for x in (subgoal, getattr(getattr(step, "loc", None), "words", lambda: "")()) if x)
        ranked = sorted(((score(words, n), i, role, n) for i, role, n in pool), reverse=True)
        best = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        if (best[0] >= 0.6 and best[0] - second >= 0.2) or (best[0] >= 0.35 and second == 0.0):       # a clear winner, or the only control that shares a word
            return {"do": do, "idx": best[1], "why": "words match", "confidence": min(1.0, best[0])}
        if not self.use_model:
            return None
        decide = self._decide
        if decide is None:
            from jarvis.plugins._shared import decider
            decide = decider.decide
        labels, seen = [], {}
        for s, i, role, n in ranked[:20]:
            lab = f"{role}: {n}"
            seen[lab] = seen.get(lab, 0) + 1
            labels.append(lab if seen[lab] == 1 else f"{lab} #{seen[lab]}")
        d = decide("web_step", subgoal[:300], labels, f"goal: {goal[:160]}")
        if d is None or getattr(d, "choice", None) not in labels:
            return None
        k = labels.index(d.choice)
        return {"do": do, "idx": ranked[k][1], "why": (getattr(d, "why", "") or "the decider chose it")[:60], "confidence": float(getattr(d, "confidence", 0.0))}
