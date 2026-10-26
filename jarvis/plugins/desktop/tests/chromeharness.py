"""The D64 test rig: the plugin's browser agent -> the REAL kernel (Owner.pc, job validator, policy, approvals, sealed site grants) -> the REAL node (browserext,
browserops, browserdl, browsercred) -> a FAKE Chrome (fakechrome.py) showing real HTML pages served over a real local HTTP server. Only Chrome and the console's
HTTP are faked."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
JARVIS = HERE.parents[3]
for p in (JARVIS / "kernel", JARVIS / "node"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from jarvis_kernel import policy, sitegrants  # noqa: E402
from jarvis_kernel.jobs import Jobs  # noqa: E402
from jarvis_kernel.owner import Owner, OwnerError  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402
from jarvis_kernel.tasks import Tasks  # noqa: E402
from jarvis_node import browserext, browsercred, browserdl, browserops  # noqa: E402

from jarvis.plugins.desktop import core  # noqa: E402
from jarvis.plugins.desktop.browser_agent import Agent, Budget  # noqa: E402
from jarvis.plugins.desktop.browser_backend import GrantsClient, KernelBackend  # noqa: E402
from jarvis.plugins.desktop.tests.fakechrome import FakeChrome, Web, World  # noqa: E402

PIN = "correct horse battery"
APPS = HERE.parent / "webapps"


class Harness:
    def __init__(self, tmp_path: Path, monkeypatch):
        import shutil
        self.tmp = Path(tmp_path)
        root = self.tmp / "web"
        shutil.copytree(APPS, root)
        self.web = Web(root)
        self.world = World(self.web, self.tmp)
        FakeChrome.WORLD = self.world
        self.outputs = self.tmp / "outputs"
        (self.outputs / "local-only").mkdir(parents=True)
        self.secrets = self.tmp / "secrets" / "sites"
        self.identity = self.tmp / "identity.json"
        self.identity.write_text(json.dumps({"email_pattern": "owner+{site}@example.com", "display_name": "Satish"}), encoding="utf-8")
        monkeypatch.chdir(self.tmp)
        monkeypatch.setenv("JARVIS_OUTPUTS", str(self.outputs))
        monkeypatch.setattr(core, "LOCAL_RESULTS", self.outputs / "local-only")
        monkeypatch.setattr(browserdl, "chrome_downloads", lambda: str(self.world.chrome_downloads))
        monkeypatch.setattr(browsercred, "SECRETS_DIR", str(self.secrets))
        monkeypatch.setattr(browsercred, "IDENTITY_FILE", str(self.identity))
        monkeypatch.setattr(sitegrants, "FILE", str(self.tmp / "trusted_sites.json"))
        monkeypatch.setattr(browserext, "load_registry", lambda path=None: {"profiles": [{"dir": "Default", "name": "Satish", "account": "sat***@gmail.com",
                                                                                          "account_id": "x", "default": True, "aliases": ["default", "satish"]}]})
        monkeypatch.setattr(browserext, "resolve", lambda alias, reg=None: {"dir": "Default", "name": "Satish", "account": "sat***@gmail.com", "account_id": "x"})
        browserext.POOL.clear()
        self.kernel = Kernel(str(self.tmp / "kernel"))
        self.owner = Owner(self.kernel, Tasks(self.kernel), Jobs(self.kernel))
        self.owner.node = "laptop"
        self.owner.set_pin(PIN)
        sitegrants.invalidate()
        self.calls: list = []           # every body the console was asked to queue (as the plugin sent it)
        self.jobs_run: list = []        # (op, params) the node ran
        self.pending: list = []
        self.owner_approves = False     # the owner confirms every card he is shown, with the PIN, as soon as the plugin waits
        self.approved: list = []
        self.denied_ops: list = []
        self.journal: list = []
        self.asks: list = []
        self.answers: list = []         # what the "owner" presses on each form: a button id or a callable(spec) -> button id
        self.stop_flag = False

    # ---- the console ---------------------------------------------------------------------------------------------------
    def call(self, method: str, name: str, body=None):
        if method == "GET" and name == "state":
            if self.owner_approves:
                for a in list(self.kernel.pending_approvals()):
                    try:
                        self.owner.approve(a["id"], a["id"], PIN)
                        self.approved.append(a["action"])
                    except OwnerError:
                        pass
            return 200, {"approvals": [{"id": a["id"]} for a in self.kernel.pending_approvals()]}
        if method == "GET" and name.startswith("job/"):
            try:
                return 200, self.owner.job(name.split("/", 1)[1])
            except OwnerError as e:
                return e.code, {"error": str(e)}
        if method == "POST" and name in ("deny", "job/cancel"):
            return 200, {}
        if method == "GET" and name == "site_grants":
            return self.owner.handle("GET", "/v1/jarvis/owner/site_grants", {})
        if method == "POST" and name.startswith("site_grant/"):
            return self.owner.handle("POST", "/v1/jarvis/owner/" + name, dict(body or {}))
        if method == "POST" and name == "pc":
            return self._pc(dict(body))
        return 404, {"error": f"fake console: {method} {name}"}

    def _pc(self, body: dict):
        body.pop("holder", None)
        self.calls.append(dict(body))
        try:
            r = self.owner.pc(body)
        except OwnerError as e:
            return e.code, {"error": str(e)}
        if r.get("status") == "needs_approval":
            self.pending = [r["approval_id"]]
            return 200, r
        job = self.owner.jobs.next_for("laptop")
        assert job and job["id"] == r["job"]
        self.jobs_run.append((job["params"]["op"], dict(job["params"])))
        try:
            res = browserext.run(job["params"], session_factory=FakeChrome)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": type(e).__name__}
        self.owner.jobs.finish("laptop", job["id"], res)
        return 200, r

    def pc_run(self):
        """The job runner the backend gets (what the plugin's pc() closure is)."""
        pc = core.Pc(call=self.call, sleep=lambda s: None, approval_wait_s=0.0 if not self.owner_approves else 30.0)
        pc.outage_poll_s = 0.0
        return pc.run

    # ---- grants as the owner gives them ------------------------------------------------------------------------------------
    def grant(self, hosts, abilities, days=30, note=""):
        hosts = [hosts] if isinstance(hosts, str) else list(hosts)
        body = {"hosts": hosts, "abilities": list(abilities), "days": days, "note": note}
        code, r = self.owner.handle("POST", "/v1/jarvis/owner/site_grant/request", body)
        assert code == 202, r
        aid = r["approval_id"]
        self.owner.approve(aid, aid, PIN)
        code, r2 = self.owner.handle("POST", "/v1/jarvis/owner/site_grant/apply", dict(body, approval_id=aid))
        assert code == 200, r2
        return r2

    # ---- the engine ---------------------------------------------------------------------------------------------------------
    def agent(self, hosts=("decks.test",), goal="make a deck", budget=None, planner=None, banners=None, **kw):
        backend = KernelBackend(self.pc_run(), "default", take_local=core.take_local)
        self.backend = backend
        grants = GrantsClient(owner=self.call, ttl=0.0)

        def ask(spec):
            self.asks.append(spec)
            a = self.answers.pop(0) if self.answers else "stop"
            btn = a(spec) if callable(a) else a
            return {"answered": btn is not None, "button": btn}
        ag = Agent(backend, goal=goal, hosts=hosts, planner=planner, ask=ask, journal=self.journal.append, stop=lambda: self.stop_flag,
                   budget=budget or Budget(max_seconds=600, patience_s=2.0), banners=banners or {"reject": ["Reject all"], "accept": ["Accept all"]},
                   grants=grants, sleep=lambda s: None, human=False, **kw)
        self.grants = grants
        return ag

    def close(self):
        FakeChrome.WORLD = None
        browserext.POOL.clear()
        self.web.close()
        self.kernel.db.close()
