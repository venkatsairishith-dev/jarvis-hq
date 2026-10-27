"""The desktop plugin's `web_task` step (D64, 2026-10-26): "make a deck / slides / a presentation" is done on a FREE web generator in the owner's own Chrome, by the
browser agent (browser_agent.py) driven by the site playbooks (browser_playbooks.py) and the ranked shortlist (ppt_flow.py). This module only wires the real parts
together for one step: the PC job runner of the current task (so Stop reaches the node job), the kernel's per-site grants, the owner's ask panel, the journal.

What comes back is for the chat model, so it holds verified facts only (what file arrived, its type, size, slide or page count, which site) and never a path
other than the copy shown in the Preview panel, page text, a credential or a typed value.
"""
from __future__ import annotations


import time

from . import browser_agent as BA
from . import browser_playbooks as PB
from . import core, ppt_flow
from .browser_backend import GrantsClient, KernelBackend
from .browser_planner import LocalPlanner

SITE_SECONDS = 900.0            # one site gets this long (and 200 actions) before the next one is tried


def deck_params(step: dict) -> dict:
    """The deck request from the step: a topic (the owner's words), a number of slides (1-20, default 5), an optional theme, the formats to keep."""
    topic = " ".join(str(step.get("topic") or step.get("query") or "").split())[:400]
    try:
        slides = max(1, min(int(step.get("slides") or 5), 20))
    except (TypeError, ValueError):
        slides = 5
    formats = tuple(f for f in str(step.get("formats") or "pptx,pdf").replace(" ", "").split(",") if f in ("pptx", "pdf", "png", "jpg")) or ("pptx", "pdf")
    return {"topic": topic, "slides": slides, "theme": " ".join(str(step.get("theme") or "").split())[:40], "formats": formats}


def run_deck(step: dict, *, pc_run, stop=lambda: False, ask=None, profile: str = "default", journal=None, show=None, store=None, grants=None,
             planner=None, owner=None, shortlist=None, deliverables_dir=None, grant_wait_s: float = 300.0, touch=None, sleep=time.sleep,
             takeover=None, resume: dict = None) -> dict:
    """Run the deck flow for one step. `pc_run(body, timeout, what)` is the job runner of the current task (it returns the node job's state and result).
    Never raises for a site that failed; the owner's stop and a wall he chooses to stop at come back as `stopped`.
    takeover (D79): the takeover broker; a wall is then handed to the owner on his PC with one card instead of the old form, and a wall he does not clear in time pauses the task
    durably (`needs_owner.takeover` in the result). resume: the record of such a pause; the steps already verified are not repeated."""
    p = deck_params(step)
    if len(p["topic"]) < 3:
        return {"ok": False, "error": "what should the deck be about?", "needs": "topic"}
    def nap(sec):
        if touch:
            touch("waiting for the owner's PIN")          # the wait is alive: the stuck check must not stop the task while he reads the card
        sleep(sec)
    grants = grants or GrantsClient(owner=owner or core.owner, sleep=nap)
    store = store or PB.Store()
    banners = ppt_flow.load_banners()

    def factory(hosts):
        backend = KernelBackend(pc_run, profile, take_local=core.take_local)
        kw = dict(goal=f"make a {p['slides']}-slide deck", hosts=hosts, planner=planner or LocalPlanner(), ask=ask, journal=journal, stop=stop,
                  budget=BA.Budget(max_seconds=SITE_SECONDS), banners=banners, grants=grants)
        if takeover is not None:
            from .takeover_agent import TakeoverAgent
            site = hosts[0] if hosts else ""
            done = ((resume or {}).get("milestones") or ()) if (resume or {}).get("site") in (site, None, "") else ()
            return TakeoverAgent(backend, takeover=takeover, tk_target="pc", site=site, done_steps=done, **kw)
        return BA.Agent(backend, **kw)
    flow = ppt_flow.DeckFlow(factory, store, grants, shortlist=shortlist, show=show, take_local=core.take_local, stop=stop, ask=ask, want_signup=bool(step.get("signup")),
                             deliverables_dir=deliverables_dir, grant_wait_s=grant_wait_s)
    try:
        res = flow.run(p["topic"], p["slides"], p["theme"], p["formats"])
    except BA.Stopped as e:
        return {"ok": False, "stopped": True, "error": str(e)[:160], "summary": "stopped before the deck was made"}
    except (BA.BudgetExceeded, BA.BackendLost, ppt_flow.FlowError) as e:
        return {"ok": False, "error": str(e)[:200]}
    return result_dict(res)


def result_dict(res: "ppt_flow.DeckResult") -> dict:
    """The model-facing result: verified facts and short reasons. No path except the Preview copy; no page text."""
    out = {"ok": bool(res.ok), "summary": res.summary[:500], "site": res.site, "facts": res.facts[:8], "files": res.files[:4], "verified_files": len(res.files),
           "tried": [{"site": t.host, "outcome": t.outcome, "why": t.why[:120]} for t in res.tried][:6]}
    if res.shown:
        out["file"] = res.shown
    if res.needs_owner:
        no = {k: v for k, v in res.needs_owner.items() if k in ("kind", "site", "detail", "tried", "takeover")}
        out["needs_owner"] = no
        out["error"] = str(no.get("detail") or res.summary)[:200]
    return out
