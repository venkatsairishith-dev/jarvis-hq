"""The D76 test rig for open-ended tasks: the D64 rig (fake Chrome showing real HTML from a temp folder, behind the REAL kernel, policy, approvals, sealed grants and the REAL node
code; chromeharness.py) plus a task runner whose hands are real except the model calls, the owner's panel and the web search, which are fakes the test controls. The sites are
in tests/webapps: shop-a.test and shop-b.test (prices), college.test (a PDF), search.test (a video site), forms.test (a form), inject.test (a page that talks to the agent),
captcha.test (a human check) and news-a/b/c.test (one topic, three sites)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jarvis.plugins.desktop import core, missions
from jarvis.plugins.desktop import taskplan as TP
from jarvis.plugins.desktop import taskrun as TR
from jarvis.plugins.desktop.browser_planner import LocalPlanner
from jarvis.plugins.desktop.tests.chromeharness import Harness
from jarvis.plugins.desktop.browser_backend import GrantsClient

SITES = {
    "searchtube": {"host": "search.test", "search": "https://search.test/results?search_query={q}", "result_hint": "/watch", "title_strip": " - SearchTube"},
    "shop a": {"host": "shop-a.test", "search": "https://shop-a.test/search.html?q={q}", "result_hint": "", "title_strip": ""},
}


def pdf_with(lines) -> bytes:
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for ln in lines:
        c.drawString(60, y, ln)
        y -= 22
    c.save()
    return buf.getvalue()


def write_cfg(tmp: Path, **over) -> dict:
    path = Path(tmp) / "web_task.json"
    base = json.loads((TP.CONFIG_FILE).read_text(encoding="utf-8-sig"))
    base["sites"] = SITES
    for k, v in over.items():
        base[k] = {**base.get(k, {}), **v} if isinstance(v, dict) else v
    path.write_text(json.dumps(base), encoding="utf-8")
    TP._CFG["sig"] = None
    return TP.config(path)


class TaskHarness(Harness):
    def __init__(self, tmp_path, monkeypatch, **cfg_over):
        super().__init__(tmp_path, monkeypatch)
        self.cfg = write_cfg(self.tmp, **cfg_over)
        self.deliv = self.tmp / "deliverables"
        self.boards = self.tmp / "boards"
        self.grants_client = GrantsClient(owner=self.call, ttl=0.0)
        self.search_results: list = []
        self.search_calls: list = []
        self.docs: dict = {}                    # path -> text (what the document reader returns)
        self.doc_files: list = []
        self.online_brief = None                # fn(messages) -> text
        self.shown: list = []
        self.lines: list = []
        self.owner_present = True               # pause_for_owner
        self.llm = None
        self.pc_calls: list = []
        self.pc_result: dict = {"facts": ["Calculator opened and showed 15"], "status": "completed"}
        self.web.hooks[("shop-b.test", "/find")] = lambda world, form: "goto:/find.html"      # the search box of shop-b leads to its results page
        self.home = self.tmp / "home"
        (self.home / "Downloads").mkdir(parents=True)

    # ---- the hands
    def ask_fn(self, spec):
        self.asks.append(spec)
        a = self.answers.pop(0) if self.answers else "stop"
        btn = a(spec) if callable(a) else a
        return {"answered": btn is not None, "button": btn}

    def make_agent(self, hosts):
        return self.agent(hosts=tuple(hosts), goal="task", planner=LocalPlanner(use_model=False))

    def hands(self, **over) -> TR.Hands:
        class _Rel:
            def __init__(self, text):
                self.allowed, self.text = True, text

        def search(q, n):
            self.search_calls.append((q, n))
            return list(self.search_results)

        h = TR.Hands(make_agent=self.make_agent, take_local=core.take_local, grants=self.grants_client, search=search, ask=self.ask_fn,
                     doc_list=lambda folder, ext, days: list(self.doc_files), doc_read=lambda p: self.docs[p], pc=lambda goal, app: (self.pc_calls.append((goal, app)) or self.pc_result),
                     llm=self.llm, planner=LocalPlanner(use_model=False), release_text=(lambda t: _Rel(t)),
                     ask_reason=(lambda msgs: (self.online_brief(msgs), "fake")) if self.online_brief else None,
                     show=self.shown.append, stop=lambda: self.stop_flag, line=lambda act, app="": self.lines.append((act, app)),
                     pause_for_owner=lambda: self.owner_present, out_dir=lambda: self.deliv, grant_wait_s=0.0)
        for k, v in over.items():
            setattr(h, k, v)
        return h

    def runner(self, **over) -> TR.TaskRunner:
        return TR.TaskRunner(self.hands(**over), self.boards, self.cfg)

    # ---- documents
    def make_invoice(self, name: str, vendor: str, date: str, total: str, days_ago: float = 1, number: str = "INV-1") -> Path:
        import os
        import time
        p = self.home / "Downloads" / name
        p.write_bytes(pdf_with([vendor, f"Invoice No: {number}", f"Date: {date}", "Description        Qty     Amount", "Consulting services   1     100.00", f"Total: {total}"]))
        t = time.time() - days_ago * 86400
        os.utime(p, (t, t))
        return p

    def doc_hands(self) -> dict:
        from jarvis.plugins.doc_reader import core as dr
        from jarvis.plugins.desktop import taskglue

        def lister(folder, ext, days):
            got = dr.recent_files(folder or "Downloads", 15, home=self.home)
            root = Path(got["folder"])
            return [{"name": f["name"], "path": str(root / f["name"]), "days_ago": f["days_ago"]} for f in got["files"]
                    if (not ext or f["name"].lower().endswith("." + ext)) and (not days or f["days_ago"] <= days)]
        return {"doc_list": lister, "doc_read": taskglue.pdf_text}

    # ---- plans and runs
    def plan(self, goal: str, text=None) -> dict:
        return TP.plan_for(goal, text, self.cfg)

    def run_plan(self, goal: str, plan: "dict | None" = None, mid: str = "t1", runner: "TR.TaskRunner | None" = None, stop_on_fail: bool = True) -> list:
        """Run the milestones in order the way a mission does: a step passes only when taskrun.verify_step says so; the first one that does not ends the run."""
        plan = plan or self.plan(goal)
        assert plan["status"] == "ok", plan
        r = runner or self.runner()
        out = []
        for st in plan["steps"]:
            res = r.run_step(st, mid, goal)
            passed, checked, unc = TR.verify_step(st, res)
            out.append({"step": st, "res": res, "passed": passed, "checked": checked, "unc": unc})
            if not passed and stop_on_fail:
                break
        return out

    def board(self, mid: str = "t1", goal: str = ""):
        return self.runner().board(mid, goal)

    def clicked(self) -> list:
        return list(self.world.click_log)
