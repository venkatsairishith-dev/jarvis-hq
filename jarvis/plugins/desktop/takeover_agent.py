"""The browser agent with the takeover protocol (D79). A thin subclass of browser_agent.Agent: the engine's own file is not changed.

What it adds:
  * `hand_to_owner` (the engine calls it at every wall: CAPTCHA, bot check, 2FA or e-mail code, payment, a sign-in the owner must do) no longer shows the old one-off form. It
    raises a takeover (plugins/_shared/takeover.py): all JARVIS input stops, the owner gets ONE card and one quiet notice, he does the step, JARVIS looks again.
  * a cookie or consent box that the banner rules could not clear (the step failed with the box still on the page) is handed over the same way, once per step.
  * while a takeover waits, `act` refuses: the target is held, so no click or key can leave this process even if another thread tries.
  * `milestones`: the ids of the steps whose effect was VERIFIED. A task paused for the owner records them; the resumed task skips those steps and goes on from the first one
    that was not verified, instead of starting again from the top.
The engine never solves, clicks or types anything on a wall: this class has no code that touches the page at a wall, only code that waits for the owner.
"""
from __future__ import annotations

import re
import time

from jarvis.plugins._shared import takeover as TK

from . import browser_agent as BA
from . import browser_snapshot as S

CONSENT_WORDS = re.compile(r"(?i)\b(cookies?|consent|privacy (?:choices|settings|preferences)|accept all|reject all|manage preferences|we value your privacy)\b")


class TakeoverPaused(BA.NeedOwner):
    """The task is paused for the owner (he did not answer in time, chose "not now", or the wall stayed after the re-asks). `state["takeover"]` is what a durable
    pause needs: the request, where it was, and the milestones already verified."""

    def __init__(self, out: TK.Outcome, record: dict, state: dict):
        super().__init__("takeover", out.detail or "waiting for you", dict(state, takeover=record))
        self.outcome, self.record = out, record


def consent_blocking(pg: S.Page) -> bool:
    """A consent or cookie dialog is on the page and a control inside it is still there (the banner rules did not clear it)."""
    return any(e.in_dialog and CONSENT_WORDS.search(f"{e.name} {e.text}") for e in pg.els)


class TakeoverAgent(BA.Agent):
    def __init__(self, backend, *, takeover: "TK.Takeover | None" = None, tk_target: str = "pc", site: str = "", done_steps=(), **kw):
        super().__init__(backend, **kw)
        self.takeover, self.tk_target, self.site = takeover, tk_target, site
        self.milestones: list = list(done_steps)
        self._skip: set = set(done_steps)
        self._consent_asked: set = set()
        self.paused_for: "TK.Outcome | None" = None

    # ---- no input while the owner holds the screen
    def act(self, a: BA.Act) -> BA.Res:
        if self.takeover is not None and self.takeover.holding(self.tk_target):
            raise BA.AgentError("held for the owner: nothing is clicked or typed while a takeover waits")
        return super().act(a)

    # ---- the unit
    def do(self, st: BA.Step, params: dict = None) -> BA.StepResult:
        if st.id in self._skip:
            self._skip.discard(st.id)
            return BA.StepResult(st.id, True, "skipped", ["verified before the pause"], [], skipped=True)
        try:
            r = super().do(st, params)
        except BA.StepFailed:
            pg = self.page
            if self.takeover is not None and pg is not None and st.id not in self._consent_asked and consent_blocking(pg):
                self._consent_asked.add(st.id)
                self._hand("consent", pg.host, lambda: ("consent" if consent_blocking(self.observe()) else None))
                return self.do(st, params)
            raise
        if r.ok and not r.skipped:
            self.milestones.append(st.id)
        return r

    # ---- the wall
    def hand_to_owner(self, wall: S.Wall, pg: S.Page) -> None:
        if self.takeover is None:
            return super().hand_to_owner(wall, pg)
        self._log("wall", wall=wall.kind)

        def look():
            again = BA.owner_wall(self.observe(), self.expect_password)
            return again
        self._hand(TK.wall_kind(wall), pg.host, look)

    def _hand(self, kind: str, host: str, look) -> None:
        req = TK.make_request(self.tk_target, kind, host)
        out = self.takeover.hand_over(req, look)
        self._log("takeover", wall=kind)
        if out.status == "resolved":
            self.observe()
            return
        if out.status == "stop":
            raise BA.Stopped("the owner chose to stop at " + kind)
        record = {"id": req.id, "target": req.target, "kind": req.kind, "host": req.host, "status": out.status, "reasks": out.reasks, "site": self.site or req.host,
                  "milestones": list(self.milestones), "at": time.time()}
        self.paused_for = out
        raise TakeoverPaused(out, record, self.state())
