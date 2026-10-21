"""Agent definitions: a persistent identity, instructions, permissions, limits and a verification procedure for a
specialist the owner asks for in a sentence ("a Python software engineer that writes code, tests it and fixes failures").

What is real, and what is not (stated in the definition itself, so the UI cannot overpromise):
- Enforced by the kernel: the lane (which models), lab dispatch only through the kernel's own path, attempts, time
  budget, runs per day, acceptance criteria that the independent verifier checks, who may update the task.
- Passed to the runtime but NOT enforced by the kernel: the tool names and the instructions. The lab runner runs
  Hermes/OpenCode/goose with their own fixed toolsets inside the sandboxed box; a definition cannot widen those.
- A workspace is the task's own folder in the lab box (isolated per task). A persistent per-agent folder would need a
  lab runner change and is not claimed.
A definition is a draft until the owner activates it; nothing runs for a draft except an explicit, bounded test run.
"""
import json
import re
import secrets
import time

from .jobs import LAB_AGENTS

STATES = ("draft", "active", "paused", "terminated")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 _.-]{2,39}$")
CAPABILITIES = {
    "lab.code": "write, run and test code in the lab box (its own sandboxed computer)",
    "lab.browser": "browse the web from the lab box's own browser",
    "lab.files": "create and edit files in the task's lab workspace",
    "output.save": "hand verified files to the owner's outputs folder (kernel-checked delivery)",
    "memory": "read and write its own memory entries only",
}
ROLES = {   # keyword -> (role, default tools, default instructions, default criteria)
    "python": ("software-engineer", ["lab.code", "lab.files", "output.save"],
               "You are a careful Python software engineer. Write small, readable code with tests. Run the tests yourself and read "
               "their real output before you claim anything works. When a test fails, find the cause, fix it and run again. Put the "
               "files to deliver in ./deliver/ with the tests, so they pass when run from a fresh folder.",
               ["the code runs without errors from a clean folder", "the included tests pass (exit code 0)", "no secrets, no network calls unless asked"]),
    "debug": ("debugger", ["lab.code", "lab.files"],
              "You are a debugging specialist. Reproduce the failure first, state the root cause with evidence, make the smallest fix, "
              "and show the failing case now passing. Do not change unrelated code.",
              ["the failure is reproduced before the fix", "the same check passes after the fix", "the change is minimal"]),
    "research": ("researcher", ["lab.browser", "lab.files", "output.save"],
                 "You are a research analyst. Collect sources, compare them, note dates and disagreements, and write a short report "
                 "that cites every claim with its source link. Say plainly what you could not verify.",
                 ["every claim has a source link", "disagreements and unknowns are listed", "the report is saved as a file"]),
    "web": ("web-developer", ["lab.code", "lab.browser", "lab.files", "output.save"],
            "You are a web developer. Build the site as plain, accessible HTML/CSS/JS, open it in the browser and check that it "
            "renders and the links work before you report it done. Put the finished files in ./deliver/.",
            ["the pages load without console errors", "all links resolve", "layout works at phone and desktop width"]),
    "writer": ("writer", ["lab.files", "output.save"],
               "You are a clear, concise writer. Match the requested audience and length, check facts you state, and deliver the text as a file.",
               ["the text matches the requested audience and length", "facts are checked or marked unverified"]),
    "data": ("data-analyst", ["lab.code", "lab.files", "output.save"],
             "You are a data analyst. Load the data, check quality first, compute what was asked, and report the numbers with the code "
             "that produced them. Never invent a number.",
             ["every reported number is reproduced by the delivered code", "data quality issues are listed"]),
}
GENERIC = ("assistant", ["lab.files", "output.save"],
           "You are a focused specialist. Do exactly the task, check your own result against the acceptance criteria with real "
           "evidence, and say plainly what is incomplete.", ["the task's stated goal is met", "the result is checked with evidence"])


class AgentError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def draft_from_sentence(sentence: str) -> dict:
    """A proposed definition from one sentence. Template-based and deterministic: the owner reviews and edits it before
    anything is activated."""
    s = " ".join(str(sentence or "").split())
    if not 8 <= len(s) <= 400:
        raise AgentError("describe the agent in one sentence (8 to 400 characters)")
    low = s.lower()
    key = next((k for k in ROLES if k in low), None)
    if key is None:
        key = "python" if re.search(r"\b(code|coder|programmer|software|developer|engineer)\b", low) else None
    role, tools, instr, crit = ROLES.get(key, GENERIC)
    m = re.search(r"(?:create|make|build|add)?\s*(?:an?|the)?\s*(.{3,40}?)\s+agent\b", low)
    title = (m.group(1) if m else role).strip()
    name = " ".join(w.capitalize() for w in re.sub(r"[^a-z0-9 ]", "", title).split())[:34] or role.title()
    if not NAME.match(name):
        name = role.replace("-", " ").title()
    return {"name": name, "purpose": s[:400], "role": role, "instructions": instr + f"\n\nOwner's description of this agent: {s[:300]}",
            "tools": tools, "skills": [], "runtime": "hermes", "lane": "public", "model_pref": None,
            "model_fallback": "any model the lane allows, in the kernel's order (never crosses to the private lane)",
            "workspace": {"root": "the task's own folder in the lab box", "network": "through the lab's egress rules"},
            "permissions": ["no access to the owner's computer, files or accounts", "no credentials or secrets",
                            "files reach the owner only through the kernel's verified delivery"],
            "criteria": crit, "verification": "an independent verifier session checks every criterion against real output and re-runs "
                                              "delivered tests from a clean folder; the task completes only when it passes",
            "max_attempts": 3, "budget_s": 1800, "max_runs_per_day": 12}


class AgentDefs:
    def __init__(self, kernel, tasks, dispatch=None, clock=time.time):
        self.k, self.tasks, self.dispatch, self.clock = kernel, tasks, dispatch, clock
        kernel.db.execute(
            "CREATE TABLE IF NOT EXISTS agent_defs(id TEXT PRIMARY KEY, name TEXT UNIQUE, purpose TEXT, role TEXT, instructions TEXT, "
            "runtime TEXT, lane TEXT, tools TEXT, skills TEXT, model_pref TEXT, model_fallback TEXT, workspace TEXT, permissions TEXT, "
            "criteria TEXT, verification TEXT, max_attempts INT, budget_s INT, max_runs_per_day INT, state TEXT, version INT, "
            "history TEXT, created REAL, updated REAL)")

    # ---- validation
    def normalize(self, d: dict) -> dict:
        if not isinstance(d, dict):
            raise AgentError("the definition must be an object")
        name = " ".join(str(d.get("name", "")).split())
        if not NAME.match(name):
            raise AgentError("the name is 3-40 characters, starting with a letter (letters, digits, space, - _ .)")
        instr = str(d.get("instructions", "")).strip()
        if not 20 <= len(instr) <= 6000:
            raise AgentError("instructions are 20 to 6000 characters")
        runtime = str(d.get("runtime") or "hermes")
        if runtime not in LAB_AGENTS:
            raise AgentError(f"runtime must be one of {', '.join(sorted(LAB_AGENTS))}")
        lane = str(d.get("lane") or "public")
        if lane != "public":
            raise AgentError("specialist agents run in the lab on public-lane models; a private-lane agent is not created here", 403)
        tools = d.get("tools") if isinstance(d.get("tools"), list) else []
        bad = [t for t in tools if t not in CAPABILITIES]
        if bad:
            raise AgentError("unknown tool(s): " + ", ".join(map(str, bad[:5])) + ". Known: " + ", ".join(sorted(CAPABILITIES)))
        crit = [str(c).strip()[:300] for c in (d.get("criteria") or []) if str(c).strip()][:20]
        if not crit:
            raise AgentError("at least one acceptance criterion is required")
        for key, lo, hi, dflt in (("max_attempts", 1, 5, 3), ("budget_s", 120, 5400, 1800), ("max_runs_per_day", 1, 100, 12)):
            v = d.get(key, dflt)
            if not isinstance(v, int) or not lo <= v <= hi:
                raise AgentError(f"{key} must be a whole number from {lo} to {hi}")
        scrubbed = re.search(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*\S{8,}", instr)
        if scrubbed:
            raise AgentError("the instructions look like they contain a credential")
        return {"name": name, "purpose": str(d.get("purpose", ""))[:400], "role": str(d.get("role", "assistant"))[:40],
                "instructions": instr, "runtime": runtime, "lane": lane, "tools": sorted(set(tools)),
                "skills": [str(x)[:60] for x in (d.get("skills") or [])][:20], "model_pref": (str(d["model_pref"])[:60] if d.get("model_pref") else None),
                "model_fallback": str(d.get("model_fallback") or "kernel order within the lane")[:200],
                "workspace": d.get("workspace") if isinstance(d.get("workspace"), dict) else {"root": "the task's own folder in the lab box"},
                "permissions": [str(p)[:200] for p in (d.get("permissions") or [])][:20], "criteria": crit,
                "verification": str(d.get("verification") or "independent verifier")[:500],
                "max_attempts": d.get("max_attempts", 3), "budget_s": d.get("budget_s", 1800), "max_runs_per_day": d.get("max_runs_per_day", 12)}

    def describe(self, d: dict) -> dict:
        """The definition with what is enforced and what is only passed along, for the review screen."""
        a = self.normalize(d)
        a["capability_notes"] = {t: CAPABILITIES[t] for t in a["tools"]}
        a["enforced_by_kernel"] = ["lane (models)", "lab dispatch path", "attempts", "time budget", "runs per day",
                                   "acceptance criteria and independent verification", "task ownership", "delivery checks"]
        a["not_enforced_by_kernel"] = ["tool names and instructions are given to the lab agent; its toolset is fixed by the lab runner",
                                       "the workspace is the task's own folder; there is no persistent per-agent workspace yet"]
        return a

    # ---- CRUD and lifecycle
    def create(self, d: dict) -> dict:
        a = self.normalize(d)
        aid, now = "ag-" + secrets.token_hex(4), self.clock()
        try:
            self.k.db.execute(
                "INSERT INTO agent_defs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, a["name"], a["purpose"], a["role"], a["instructions"], a["runtime"], a["lane"], json.dumps(a["tools"]),
                 json.dumps(a["skills"]), a["model_pref"], a["model_fallback"], json.dumps(a["workspace"]), json.dumps(a["permissions"]),
                 json.dumps(a["criteria"]), a["verification"], a["max_attempts"], a["budget_s"], a["max_runs_per_day"], "draft", 1,
                 json.dumps([{"v": 1, "at": now, "op": "create"}]), now, now))
        except Exception as e:
            if "UNIQUE" in str(e):
                raise AgentError(f"an agent named '{a['name']}' already exists", 409)
            raise
        self.k.audit.append("agent_def_created", {"id": aid, "name": a["name"], "runtime": a["runtime"]})
        return self.get(aid)

    def get(self, aid: str):
        r = self.k.db.execute("SELECT * FROM agent_defs WHERE id=?", (str(aid)[:20],)).fetchone()
        if not r:
            return None
        cols = [c[1] for c in self.k.db.execute("PRAGMA table_info(agent_defs)").fetchall()]
        a = dict(zip(cols, r))
        for c in ("tools", "skills", "workspace", "permissions", "criteria", "history"):
            a[c] = json.loads(a[c] or "[]")
        a["history"] = a["history"][-10:]
        a["tasks"] = self.recent_tasks(a["name"])
        today = self.k.db.execute("SELECT COUNT(*) FROM tasks WHERE owner=? AND created>?", (f"agent:{a['name']}", self.clock() - 86400)).fetchone()[0]
        a["runs_today"] = today
        return a

    def list(self):
        return [self.get(r[0]) for r in self.k.db.execute("SELECT id FROM agent_defs ORDER BY created DESC LIMIT 100").fetchall()]

    def recent_tasks(self, name: str, limit=8):
        rows = self.k.db.execute("SELECT id, goal, state, created, updated, attempts, max_attempts FROM tasks WHERE owner=? "
                                 "ORDER BY created DESC LIMIT ?", (f"agent:{name}", limit)).fetchall()
        return [dict(zip(("id", "goal", "state", "created", "updated", "attempts", "max_attempts"), r)) for r in rows]

    def update(self, aid: str, d: dict, expected_version=None) -> dict:
        cur = self.get(aid)
        if not cur:
            raise AgentError("no such agent", 404)
        if cur["state"] == "terminated":
            raise AgentError("a terminated agent cannot be edited; clone it", 409)
        if expected_version is not None and int(expected_version) != cur["version"]:
            raise AgentError("the agent changed since you read it; reload and try again", 409)
        keys = ("name", "purpose", "role", "instructions", "runtime", "lane", "tools", "skills", "model_pref", "model_fallback",
                "workspace", "permissions", "criteria", "verification", "max_attempts", "budget_s", "max_runs_per_day")
        merged = {k: cur[k] for k in keys}
        merged.update({k: v for k, v in d.items() if k in keys})
        a = self.normalize(merged)
        hist = cur["history"] + [{"v": cur["version"] + 1, "at": self.clock(), "op": "update"}]
        r = self.k.db.execute(
            "UPDATE agent_defs SET name=?, purpose=?, role=?, instructions=?, runtime=?, lane=?, tools=?, skills=?, model_pref=?, "
            "model_fallback=?, workspace=?, permissions=?, criteria=?, verification=?, max_attempts=?, budget_s=?, max_runs_per_day=?, "
            "version=version+1, history=?, updated=?, state=CASE WHEN state='active' THEN 'draft' ELSE state END WHERE id=? AND version=?",
            (a["name"], a["purpose"], a["role"], a["instructions"], a["runtime"], a["lane"], json.dumps(a["tools"]), json.dumps(a["skills"]),
             a["model_pref"], a["model_fallback"], json.dumps(a["workspace"]), json.dumps(a["permissions"]), json.dumps(a["criteria"]),
             a["verification"], a["max_attempts"], a["budget_s"], a["max_runs_per_day"], json.dumps(hist[-20:]), self.clock(), aid, cur["version"]))
        if r.rowcount != 1:
            raise AgentError("the agent changed while updating; try again", 409)
        self.k.audit.append("agent_def_updated", {"id": aid, "back_to_draft": cur["state"] == "active"})   # an edited active agent is reviewed again
        return self.get(aid)

    def set_state(self, aid: str, state: str) -> dict:
        cur = self.get(aid)
        if not cur:
            raise AgentError("no such agent", 404)
        ok = {"draft": {"active", "terminated"}, "active": {"paused", "terminated"}, "paused": {"active", "terminated"}, "terminated": set()}
        if state not in ok[cur["state"]]:
            raise AgentError(f"cannot go from {cur['state']} to {state}", 409)
        self.k.db.execute("UPDATE agent_defs SET state=?, updated=?, version=version+1 WHERE id=?", (state, self.clock(), aid))
        self.k.audit.append("agent_def_" + state, {"id": aid})
        return self.get(aid)

    def clone(self, aid: str, new_name: str = None) -> dict:
        cur = self.get(aid)
        if not cur:
            raise AgentError("no such agent", 404)
        if str(cur.get("instructions", "")).startswith("[text removed"):          # retention.py removed this definition's text
            raise AgentError("this agent's text was removed by the retention policy, so it cannot be cloned", 409)
        keys = ("purpose", "role", "instructions", "runtime", "lane", "tools", "skills", "model_pref", "model_fallback", "workspace",
                "permissions", "criteria", "verification", "max_attempts", "budget_s", "max_runs_per_day")
        d = {k: cur[k] for k in keys}
        d["name"] = (new_name or f"{cur['name']} copy")[:40]
        return self.create(d)

    def delete(self, aid: str) -> dict:
        cur = self.get(aid)
        if not cur:
            raise AgentError("no such agent", 404)
        if cur["state"] == "active":
            raise AgentError("pause or terminate the agent first", 409)
        open_tasks = self.k.db.execute("SELECT COUNT(*) FROM tasks WHERE owner=? AND state NOT IN ('completed','failed','cancelled')",
                                       (f"agent:{cur['name']}",)).fetchone()[0]
        if open_tasks:
            raise AgentError(f"{open_tasks} task(s) of this agent are still open", 409)
        self.k.db.execute("DELETE FROM agent_defs WHERE id=?", (aid,))
        self.k.audit.append("agent_def_deleted", {"id": aid, "name": cur["name"]})
        return {"deleted": aid, "verified": self.get(aid) is None}

    # ---- bounded test run through the kernel's own lab path
    def test(self, aid: str, task_text: str) -> dict:
        cur = self.get(aid)
        if not cur:
            raise AgentError("no such agent", 404)
        if cur["state"] in ("terminated",):
            raise AgentError("a terminated agent cannot run", 409)
        if self.dispatch is None:
            raise AgentError("the lab dispatch path is not available", 503)
        text = " ".join(str(task_text or "").split())
        if not 8 <= len(text) <= 1500:
            raise AgentError("describe the test task in 8 to 1500 characters")
        if cur["runs_today"] >= cur["max_runs_per_day"]:
            raise AgentError("this agent has used its runs for today", 429)
        crit = cur["criteria"][:19] + ["the work stays within the test task given; nothing else is changed"]
        budget = min(cur["budget_s"], 900)
        tid = self.tasks.create(f"agent:{cur['name']}", "public", f"test run of agent '{cur['name']}'", text[:1000], None, crit, "agent-test",
                                min(cur["max_attempts"], 2), max(budget, 120))
        instr = cur["instructions"] + "\n\nTask for this test run:\n" + text
        code, reply = self.dispatch(f"agent:{cur['name']}", "public", tid, {"instructions": instr[:8000], "criteria": crit,
                                                                           "agent": cur["runtime"], "public_ok": True})
        if code != 200:
            self.tasks.update(None, tid, state="failed", result="not dispatched: " + str(reply.get("reason", ""))[:200])
            raise AgentError(str(reply.get("reason", "the lab refused the test"))[:200], 409)
        self.k.audit.append("agent_def_test", {"id": aid, "task": tid})
        return {"task": tid, "job": reply.get("job"), "agent": cur["name"], "budget_s": budget}
