"""Site playbooks with self-healing (D64, 2026-10-26): a flow that worked on a website is saved as a named, parameterised playbook of steps found by SEMANTIC locators
(role + name, text, label, nearby text), not by brittle selectors, and replayed next time.

    config/site_playbooks/<host>.<name>.json     shipped with JARVIS (read-only for agents: push_gate PROTECTED); written from the research and not yet seen on the
                                                 real site (`verified_live: false`) until the laptop runs it
    <data dir>/site_playbooks/<host>.<name>.json what JARVIS learned or repaired on this laptop; it wins over the shipped file for the same host and name

* A playbook is phases of steps (open, signin, create, wait, edit, export ...). A phase can say when it applies (`skip_if`: already signed in).
* REPLAY runs the steps through the engine (browser_agent.Agent). When a step only worked through a recovery rung (another locator, the decider, the vision model), the
  engine hands back the locator that worked; the playbook REPAIRS the step (the old name stays as a synonym, the new one is the name), records the repair (what, when,
  how) and saves the repaired copy in the learned store. A vision repair is noted but not turned into a locator (a point on a screenshot is not a locator).
* LEARN ("learn this site"): the engine performs the goal once with the planner (the local decision engine) and the vision model, and records what worked.
* A playbook never stores credentials or typed secrets: a value is a {parameter}, a reference the NODE resolves (@cred:password, @identity:email) or a short harmless
  constant. Anything else is turned into a parameter or refused, and the finished file is scanned for secrets before it is written.
* It records the site's automation terms in one sentence (`terms_note`) and the date they were read: the grant card shows it to the owner.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import browser_agent as BA
from . import browser_snapshot as S
from .browser_agent import Step, StepResult
from .browser_snapshot import Locator

JARVIS = Path(__file__).resolve().parents[2]
BUILTIN_DIR = JARVIS / "config" / "site_playbooks"
NAME_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
HOST_RX = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")
REF_RX = re.compile(r"^@(?:cred|identity):[a-z][a-z0-9_]{0,20}$")
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
SECRETISH = re.compile(r"(?i)pass(?:word|code|phrase)|\bpin\b|\botp\b|verification|security code|secret|token|api[ _-]?key|card number|cvv|iban")
EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TOKENISH = re.compile(r"[A-Za-z0-9_\-]{20,}")


class PlaybookError(Exception):
    pass


def learned_dir() -> Path:
    env = os.environ.get("JARVIS_PLAYBOOKS_DIR")
    if env:
        return Path(env)
    try:
        from . import coordinator as coord
        return Path(coord.DATA) / "plugins" / "site_playbooks"
    except Exception:  # noqa: BLE001
        return Path("site_playbooks-learned")


@dataclass
class Phase:
    name: str
    steps: list
    skip_if: dict = field(default_factory=dict)
    note: str = ""
    fallback: bool = False                              # runs only when the phase before it failed (sign-up after a sign-in that could not be done)


@dataclass
class Playbook:
    name: str
    host: str
    hosts: list
    phases: list
    params: list = field(default_factory=list)
    needs: list = field(default_factory=list)           # abilities the owner grants for the whole flow
    start: str = ""
    goal: str = ""
    terms_note: str = ""
    terms_read: str = ""                                # the date the vendor's own terms were read
    terms_source: str = ""
    formats: list = field(default_factory=list)
    verified_live: bool = False
    version: int = 1
    source: str = "builtin"
    repairs: list = field(default_factory=list)
    stats: dict = field(default_factory=lambda: {"ok": 0, "failed": 0, "last_ok": "", "last_failed": ""})
    notes: dict = field(default_factory=dict)

    # ------------------------------------------------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict, source: str = "builtin") -> "Playbook":
        if not isinstance(d, dict):
            raise PlaybookError("a playbook is an object")
        name, host = str(d.get("name", "")), str(d.get("host", "")).lower()
        if not NAME_RX.match(name) or not HOST_RX.match(host):
            raise PlaybookError("a playbook needs a plain name and an exact host")
        hosts = [str(h).lower() for h in (d.get("hosts") or [host])]
        if any(not HOST_RX.match(h) for h in hosts) or host not in hosts:
            raise PlaybookError("hosts must be exact host names that include the playbook's own")
        phases = []
        for p in d.get("phases") or []:
            if not isinstance(p, dict) or not NAME_RX.match(str(p.get("name", ""))):
                raise PlaybookError("a phase needs a name")
            phases.append(Phase(str(p["name"]), [Step.from_dict(s) for s in p.get("steps") or []], dict(p.get("skip_if") or {}), str(p.get("note", ""))[:200], bool(p.get("fallback"))))
        if not phases:
            raise PlaybookError("a playbook needs at least one phase")
        return cls(name=name, host=host, hosts=hosts, phases=phases, params=[dict(x) for x in d.get("params") or [] if isinstance(x, dict)],
                   needs=[str(n) for n in d.get("needs") or []], start=str(d.get("start", ""))[:300], goal=str(d.get("goal", ""))[:200],
                   terms_note=str(d.get("terms_note", ""))[:400], terms_read=str(d.get("terms_read", ""))[:20], terms_source=str(d.get("terms_source", ""))[:200],
                   formats=[str(f) for f in d.get("formats") or []], verified_live=bool(d.get("verified_live")), version=int(d.get("version", 1) or 1),
                   source=source if source in ("builtin", "learned") else str(d.get("source", "builtin")), repairs=[dict(r) for r in d.get("repairs") or [] if isinstance(r, dict)][-50:],
                   stats=dict({"ok": 0, "failed": 0, "last_ok": "", "last_failed": ""}, **(d.get("stats") or {})), notes=dict(d.get("notes") or {}))

    def to_dict(self) -> dict:
        return {"name": self.name, "host": self.host, "hosts": self.hosts, "version": self.version, "source": self.source, "goal": self.goal, "start": self.start,
                "params": self.params, "needs": self.needs, "formats": self.formats, "terms_note": self.terms_note, "terms_read": self.terms_read, "terms_source": self.terms_source,
                "verified_live": self.verified_live, "stats": self.stats, "repairs": self.repairs[-50:], "notes": self.notes,
                "phases": [{"name": p.name, **({"skip_if": p.skip_if} if p.skip_if else {}), **({"note": p.note} if p.note else {}), **({"fallback": True} if p.fallback else {}), "steps": [s.to_dict() for s in p.steps]}
                           for p in self.phases]}

    def step(self, step_id: str):
        for p in self.phases:
            for s in p.steps:
                if s.id == step_id:
                    return s
        return None

    def required_params(self) -> list:
        return [p["name"] for p in self.params if p.get("required")]

    def defaults(self) -> dict:
        return {p["name"]: str(p["default"]) for p in self.params if "default" in p}


# ---------------------------------------------------------------------------------------------------------------------- the store
class Store:
    def __init__(self, builtin: Path = None, learned: Path = None):
        self.builtin = Path(builtin or BUILTIN_DIR)
        self.learned = Path(learned or learned_dir())
        self.problems: list = []

    def _load(self, folder: Path, source: str) -> list:
        out = []
        for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                pb = Playbook.from_dict(json.loads(f.read_text(encoding="utf-8-sig")), source)
                out.append(pb)
            except (OSError, ValueError, PlaybookError, KeyError, TypeError) as e:
                self.problems.append(f"{f.name}: {str(e)[:100]}")
        return out

    def all(self) -> list:
        """Every playbook, a learned one replacing the shipped one of the same host and name."""
        got = {(p.host, p.name): p for p in self._load(self.builtin, "builtin")}
        for p in self._load(self.learned, "learned"):
            got[(p.host, p.name)] = p
        return list(got.values())

    def get(self, host: str, name: str = "make_deck"):
        host = str(host).lower()
        return next((p for p in self.all() if p.host == host and p.name == name), None)

    def for_goal(self, name: str) -> list:
        return [p for p in self.all() if p.name == name]

    def path_for(self, pb: Playbook) -> Path:
        return self.learned / f"{pb.host}.{pb.name}.json"

    def save_learned(self, pb: Playbook) -> Path:
        pb = scrub_for_save(pb)
        pb.source = "learned"
        self.learned.mkdir(parents=True, exist_ok=True)
        path = self.path_for(pb)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pb.to_dict(), indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return path


# ---------------------------------------------------------------------------------------------------------------------- never a secret in a playbook
def _value_ok(v: str) -> bool:
    if not v or PLACEHOLDER.fullmatch(v) or REF_RX.match(v):
        return True
    if PLACEHOLDER.search(v) and not EMAIL_RX.search(v):
        return True
    return len(v) <= 40 and not EMAIL_RX.search(v) and not TOKENISH.search(v) and not re.search(r"\d{7,}", v) and not SECRETISH.search(v)


def scrub_for_save(pb: Playbook) -> Playbook:
    """A copy that holds no credential and no typed secret. A value that is not a parameter, a reference or a short harmless constant becomes a parameter (a
    password field becomes the reference the node resolves). The finished text is scanned once more; a secret anywhere refuses the save."""
    from jarvis.plugins._shared import screen_text
    d = json.loads(json.dumps(pb.to_dict()))
    for ph in d["phases"]:
        for st in ph["steps"]:
            v = st.get("value", "")
            label = (st.get("loc") or {}).get("name") or (st.get("loc") or {}).get("label") or st.get("id", "value")
            plain = bool(v) and not (PLACEHOLDER.fullmatch(v) or REF_RX.match(v))
            if v and (not _value_ok(v) or (plain and st.get("do") in ("fill", "select") and SECRETISH.search(label))):
                if re.search(r"(?i)pass(?:word|code)", label):
                    st["value"] = "@cred:password"
                elif EMAIL_RX.search(v) and re.search(r"(?i)e-?mail|user", label):
                    st["value"] = "@identity:email"
                else:
                    param = re.sub(r"[^a-z0-9_]", "", re.sub(r"\s+", "_", label.lower()))[:20] or "value"
                    st["value"] = "{" + param + "}"
            if st.get("to"):
                u = urllib.parse.urlsplit(st["to"])
                st["to"] = urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, "", ""))      # no query, no fragment: they can hold tokens
            for k in ("note", "intent"):
                if k in st and (EMAIL_RX.search(st[k]) or TOKENISH.search(st[k])):
                    st[k] = EMAIL_RX.sub("an e-mail address", TOKENISH.sub("a token", st[k]))
    texts = [str(x.get("default", "")) for x in d.get("params") or []]
    texts += [str(d.get(k, "")) for k in ("terms_note", "terms_source", "goal", "start")] + [str(v) for v in (d.get("notes") or {}).values()]
    for ph in d["phases"]:
        for st in ph["steps"]:
            texts += [st.get(k, "") for k in ("value", "to", "note", "intent")]
    for t in texts:
        if t and not REF_RX.match(t):
            _clean, hits = screen_text.scrub_text(t)
            if hits:
                raise PlaybookError("the playbook would hold something that looks like a secret; it was not saved")
    return Playbook.from_dict(d, pb.source)


# ---------------------------------------------------------------------------------------------------------------------- replay and repair
@dataclass
class RunResult:
    ok: bool
    done: list = field(default_factory=list)            # step ids that passed
    results: list = field(default_factory=list)
    repaired: list = field(default_factory=list)
    failed: "str | None" = None
    why: str = ""
    data: dict = field(default_factory=dict)
    phase: str = ""


def params_for(pb: Playbook, given: dict) -> dict:
    params = {**pb.defaults(), **{k: str(v) for k, v in (given or {}).items() if v is not None}}
    missing = [n for n in pb.required_params() if not str(params.get(n, "")).strip()]
    if missing:
        raise PlaybookError("the playbook needs: " + ", ".join(missing))
    return params


def apply_repair(pb: Playbook, rep: dict) -> bool:
    """Turn the engine's repair into the playbook step: the new locator becomes the name and the old name stays as a synonym. Vision repairs are noted only."""
    st = pb.step(rep.get("step", ""))
    if st is None:
        return False
    when = time.strftime("%Y-%m-%d")
    to = rep.get("to") or {}
    rec = {"step": st.id, "how": rep.get("how"), "date": when, "from": rep.get("from"), "to": to if rep.get("how") != "vision" else {"vision": True}}
    pb.repairs.append(rec)
    pb.repairs = pb.repairs[-50:]
    if rep.get("how") == "vision" or not to:
        st.note = (st.note + " | " if st.note else "") + f"found by the vision model on {when}: no usable name"
        st.note = st.note[:200]
        return True
    old = st.loc
    new = Locator.from_dict(to)
    synonyms = [x for x in (list(old.any_of) + [old.name, old.text]) if x and x != new.name][:5]
    st.loc = Locator(role=new.role or old.role, name=new.name or old.name, text=new.text, label=new.label or old.label, near=new.near or old.near, xpath=old.xpath if rep.get("how") == "xpath" else "",
                     nth=new.nth, frame=new.frame, any_of=tuple(synonyms))
    return True


class Runner:
    def __init__(self, agent: BA.Agent, store: Store = None, save_repairs: bool = True):
        self.agent, self.store, self.save_repairs = agent, store, save_repairs

    def run(self, pb: Playbook, given: dict, phases=None, stop_after: str = "") -> RunResult:
        """Replay the playbook (or the named phases of it). Raises what the engine raises (StepFailed, NeedOwner, Stopped ...); the caller decides about the next site."""
        params = params_for(pb, given)
        res = RunResult(True)
        for h in pb.hosts:
            self.agent.allow_host(h)
        if not self.agent.signin_site:
            self.agent.signin_site = pb.host          # a hop to the sign-in provider is made on behalf of this site (the kernel needs its grant)
        changed = False
        plist = [ph for ph in pb.phases if phases is None or ph.name in phases]
        pending = None                                 # a phase that failed and has a fallback phase after it (sign-up after a sign-in that could not be done)
        for i, ph in enumerate(plist):
            if ph.fallback:
                if pending is None:
                    continue
                err, pending = pending, None
                if not self.agent.has_grant(pb.host, "signup"):
                    self._record(pb, ok=False)
                    raise err
                self.agent._log("fallback", wall=ph.name)
                res.ok, res.failed, res.why = True, None, ""
            elif pending is not None:
                self._record(pb, ok=False)
                raise pending
            res.phase = ph.name
            if ph.skip_if:
                pg = self.agent.observe()
                if self.agent._holds(ph.skip_if, pg):
                    self.agent._log("phase_skipped", wall=ph.name)
                    continue
            try:
                for st in ph.steps:
                    try:
                        r = self.agent.do(st, params)
                    except BA.StepFailed as e:
                        res.ok, res.failed, res.why = False, st.id, e.why
                        raise
                    res.results.append(r)
                    if r.ok:
                        res.done.append(st.id)
                    if r.data:
                        res.data.update({k: v for k, v in r.data.items() if k in ("name", "ext", "size", "sha256", "local_result", "source_verified")})
                    if r.repair and apply_repair(pb, r.repair):
                        res.repaired.append(r.repair)
                        changed = True
            except BA.StepFailed as e:
                nxt = plist[i + 1] if i + 1 < len(plist) else None
                if nxt is not None and nxt.fallback and not ph.fallback:
                    pending = e
                    continue
                self._record(pb, ok=False)
                raise
            if stop_after and ph.name == stop_after:
                break
        if pending is not None:
            self._record(pb, ok=False)
            raise pending
        self._record(pb, ok=True)
        if changed and self.save_repairs and self.store is not None:
            pb.phases = [p for p in pb.phases if p.name != "signup_generic"]          # the generic sign-up steps are the engine's, not part of the saved playbook
            pb.version += 1
            try:
                self.store.save_learned(pb)
            except PlaybookError:
                pass
        return res

    def _record(self, pb: Playbook, ok: bool) -> None:
        pb.stats["ok" if ok else "failed"] = int(pb.stats.get("ok" if ok else "failed", 0)) + 1
        pb.stats["last_ok" if ok else "last_failed"] = time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------------------------------------------------- learn this site
def learn(agent: BA.Agent, goal: str, start_url: str, name: str, params: dict, planner, max_steps: int = 25, wanted: dict = None) -> Playbook:
    """'Learn this site': JARVIS performs the goal once. At each page the planner (the local decision engine: words first, then the decider, with the vision model
    behind the engine's own recovery) picks ONE control by its number; the engine acts and verifies; what worked is recorded as a step with a semantic locator.
    `wanted` = {"done_text": [...]} ends the run when one of the texts shows; each typed value comes from `params` (the goal's own words) and is recorded as a {parameter}.
    The page's text never becomes a step's value."""
    host = (urllib.parse.urlsplit(start_url).hostname or "").lower()
    steps: list = []
    agent.allow_host(host)
    first = Step("open", "goto", to=start_url, new_tab=True, expect={"host_is": host})
    agent.do(first)
    steps.append(Step("open", "goto", to=urllib.parse.urlunsplit(urllib.parse.urlsplit(start_url)._replace(query="", fragment="")), new_tab=True, expect={"host_is": host}))
    wanted = wanted or {}
    todo_fields = [(k, str(v)) for k, v in (params or {}).items()]
    for n in range(max_steps):
        pg = agent.observe()
        if any(pg.has_text(t) for t in wanted.get("done_text", [])):
            break
        w = BA.owner_wall(pg)
        if w is not None:
            agent.hand_to_owner(w, pg)
            pg = agent.page
        opts = [(i, role, nm) for i, role, nm in S.safe_names(pg, 40) if not BA.NEVER_PROPOSE.search(nm)]
        if not opts:
            break
        # a field the goal has a value for comes first
        fld = None
        for k, v in todo_fields:
            cand = [(i, role, nm) for i, role, nm in opts if role in ("textbox", "searchbox") and BA.fill_name_fits(k, nm)]
            if cand:
                fld = (k, v, cand[0])
                break
        if fld is not None:
            k, v, (i, role, nm) = fld
            el = pg.els[i]
            loc = Locator(role=el.role, name=el.name)
            st = Step(f"fill_{k}", "fill", loc, value="{" + k + "}", intent=f"type the {k}")
            agent.do(st, {k: v})
            steps.append(st)
            todo_fields = [(kk, vv) for kk, vv in todo_fields if kk != k]
            continue
        sub = f"{goal} (step {n + 1})"
        prop = planner.propose(goal, sub, pg, opts, Step("learn", "click", Locator(), intent=goal)) if planner else None
        if not prop or prop.get("idx") not in {o[0] for o in opts}:
            break
        el = pg.els[prop["idx"]]
        loc = Locator(role=el.role, name=el.name or el.child_text)
        st = Step(f"click_{n + 1}", "click", loc, intent=str(prop.get("why", ""))[:100], expect={"changed": True})
        try:
            agent.do(st, params)
        except BA.StepFailed:
            continue
        steps.append(st)
    pb = Playbook(name=name, host=host, hosts=[host], phases=[Phase("learned", steps)], params=[{"name": k, "required": True} for k in (params or {})],
                  start=steps[0].to, goal=goal[:200], source="learned", verified_live=True)
    return scrub_for_save(pb)
