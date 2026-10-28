"""D76 (2026-10-27): the executor of the open-ended task path. A MISSION (missions.py: durable, resumable, Stop, the PC lock, the replan loop) whose steps are the planner's
milestones (taskplan.py); each milestone runs here, on the engine it names, and is VERIFIED BY ITS EFFECT before the next one starts.

    plan       online model, through the privacy gateway, text only (taskplan.mission_plan); the rules plan when it is down or unusable
    web        webgoal.WebGoal over the owner's real Chrome: snapshot, act, verify by effect, the D64 recovery ladder, walls handed back to him
    pc         the D65 PC loop (`drive`) for one thing in a desktop app
    doc        the files of a folder / the file just downloaded, read locally (doc_reader: LiteParse text layer first, OCR for scans)
    output     CSV / XLSX / a one-page brief, written safely under the deliverables folder, read back, and shown in the Preview panel
    ask        a jarvis_ask form (never an approval): before a form is submitted, the owner decides; the click itself still needs his PIN

Every milestone returns: ok, verified, facts (only what was proven), unverified (what could not be), a reason WORD when it did not work, and what the owner must do (a wall, a
grant card, an approval). The final answer (`final_text`) is built from those facts alone and says plainly what was not done.

The board (taskboard.py) keeps what was found and what was spent across milestones and restarts. The budget (time, actions, pages, tokens) is HARD: when one is used up the task
stops and reports what it has. Nothing in a page, a document or a plan changes the goal: only the owner's words and the planner's checked milestones decide what happens.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import browser_agent as BA
from . import taskoutput as TO
from . import taskplan as TP
from .taskboard import Board, BudgetExceeded
from .taskextract import Extractor, in_text, norm, registrable
from .webgoal import Outcome, WebGoal

REASON_SAYS = {
    "not_found": "I could not find it", "no_effect": "the page did not do what was needed", "wall": "a check only you can pass (CAPTCHA, a code, a payment or a sign-in) was in the way",
    "budget": "the task's budget (time, steps or pages) ran out", "no_grant": "it needed your one-time approval for the site", "no_file": "no usable file arrived",
    "no_values": "the values could not be read from where they should be", "host": "the page went to a site the task may not visit", "stopped": "you stopped the task",
    "failed": "a step failed", "asked": "it needed your answer", "approval": "it is waiting for your approval in the console", "none": "it did not finish"}


@dataclass
class Hands:
    """What the plugin lends the runner. Each is optional; a missing one makes the milestones that need it fail with a plain reason, never crash."""
    make_agent: "Callable | None" = None             # (hosts) -> browser_agent.Agent (the real one has the kernel behind it)
    take_local: "Callable | None" = None
    grants: object = None
    search: "Callable | None" = None                  # (query, n) -> [{"title", "url", "snippet"}]; the real one redacts through the privacy gateway first
    ask: "Callable | None" = None                     # (spec) -> {"answered", "button"}: a jarvis_ask form on the owner's panel
    doc_list: "Callable | None" = None                # (folder, ext, days) -> [{"name", "path", "days_ago"}]
    doc_read: "Callable | None" = None                # (path) -> text (local only)
    pc: "Callable | None" = None                      # (goal, app) -> result of the PC loop
    llm: "Callable | None" = None                     # (question, text, names) -> {name: value}: the local model's optional extraction
    planner: object = None                            # browser_planner.LocalPlanner
    release_local: "Callable | None" = None           # (text) -> Released: an excerpt for the VERIFIED LOCAL model (secret pass only; the text stays on this PC)
    write_local: "Callable | None" = None             # (messages) -> (text, provider): the local model writes the brief
    release_text: "Callable | None" = None            # (text) -> Released: one short excerpt cleared for the online model by the gateway (screen_text.release: it also needs the owner's switch)
    ask_reason: "Callable | None" = None              # (messages) -> (text, provider): the online model, through the gateway, no tools
    show: "Callable | None" = None                    # (path) -> None: queue the file for the Preview panel
    stop: Callable = lambda: False
    line: Callable = lambda act, app="": None         # the "what I am doing" line (taskstate)
    pause_for_owner: Callable = lambda: True          # False = the owner stopped while the task waited for him to finish using the PC
    sleep: Callable = lambda s: None
    out_dir: "Callable | None" = None                 # () -> Path of the deliverables folder
    now: Callable = datetime.datetime.now
    grant_wait_s: float = 300.0
    verify_file: "Callable | None" = None


class TaskRunner:
    def __init__(self, hands: Hands, board_dir: "Path | str", cfg: "dict | None" = None):
        self.h = hands
        self.board_dir = Path(board_dir)
        self.cfg = cfg or TP.config()

    # ------------------------------------------------------------------------------------------------------------------------------- the board
    def board(self, mid: str, goal: str = "") -> Board:
        safe = re.sub(r"[^A-Za-z0-9_-]", "", mid)[:60] or "task"
        return Board.load(self.board_dir / f"{safe}.json", self.cfg["limits"], goal)

    # ------------------------------------------------------------------------------------------------------------------------------- one milestone
    def run_step(self, step: dict, mid: str, goal: str) -> dict:
        ms = TP.Milestone.from_step(step)
        b = self.board(mid, goal)
        if b.over():
            return self._result(ms, Outcome(False, "budget", f"the task's {b.over()} budget is used up"), b, stop_task=True)
        b.begin()
        self.h.line({"web": "browse", "doc": "read_doc", "output": "write_output", "ask": "ask_owner", "pc": "click"}.get(ms.engine, "look"), ms.site)
        try:
            if ms.engine == "web":
                out = self._web(ms, b, goal)
            elif ms.engine == "doc":
                out = self._doc(ms, b)
            elif ms.engine == "output":
                out = self._output(ms, b, goal)
            elif ms.engine == "ask":
                out = self._ask(ms, b, goal)
            elif ms.engine == "pc":
                out = self._pc(ms, b, goal)
            else:
                out = Outcome(False, "failed", "unknown engine")
        except BudgetExceeded as e:
            out = Outcome(False, "budget", f"the task's {e.what} budget is used up")
        except Exception as e:  # noqa: BLE001 - a milestone that crashed is a failed milestone with its class named, never the task's crash
            out = Outcome(False, "failed", f"the step stopped ({type(e).__name__})")
        finally:
            b.end()
        for f in out.facts:
            if out.ok:
                b.fact(f)
        b.mark(ms.id, "done" if out.ok else out.reason, mode=ms.mode)
        res = self._result(ms, out, b)
        if ms.engine == "output" and out.ok and out.data.get("shown") and self.h.show:
            try:
                self.h.show(out.data["shown"])
            except Exception:  # noqa: BLE001
                pass
        b.save()
        return res

    def _result(self, ms, out: Outcome, b: Board, stop_task: bool = False) -> dict:
        r = {"ok": bool(out.ok), "verified": bool(out.ok), "facts": [x for x in out.facts][:6], "unverified": [x for x in out.unverified][:4], "reason": out.reason,
             "detail": out.detail[:200], "engine": ms.engine, "mode": ms.mode}
        if out.data.get("private"):
            r["private"] = True
        for k in ("file", "shown", "rows", "sources", "domains", "values", "labels", "injections"):
            if k in out.data:
                r[k] = out.data[k]
        if out.reason == "stopped":
            r["stopped"] = True
        if out.reason == "budget" or stop_task:
            r["stop_task"] = True
        if out.reason == "approval" and out.approval_id:
            r.update(state="waiting_for_permission", approval_id=out.approval_id, how=out.how or "Approve it in the JARVIS console with your PIN, then tell me to continue.")
            if out.data.get("approval_body"):
                r["approval_body"] = out.data["approval_body"]
        if out.needs_owner:
            r["needs_owner"] = {k: str(v)[:200] for k, v in out.needs_owner.items() if k in ("kind", "site", "detail", "approval_id")}
        if not out.ok and out.reason in ("wall", "asked", "no_grant", "host") and out.needs_owner and not r.get("state"):
            r["interrupted"] = True                                    # the owner has to do something: 'done' / 'redo' continues the mission from here
            r["error"] = f"{REASON_SAYS.get(out.reason, out.reason)}: {out.detail}"[:240]
        return r

    # ------------------------------------------------------------------------------------------------------------------------------- web
    def _web(self, ms, b: Board, goal: str) -> Outcome:
        if self.h.make_agent is None:
            return Outcome(False, "failed", "the browser is not available here")
        wg = WebGoal(self.h.make_agent, Extractor(self.h.llm), b, self.cfg, goal, stop=self.h.stop, ask=self.h.ask, grants=self.h.grants, take_local=self.h.take_local,
                     search=self.h.search, grant_wait_s=self.h.grant_wait_s, line=self.h.line, pause_for_owner=self.h.pause_for_owner, sleep=self.h.sleep,
                     planner=self.h.planner, verify_file=self.h.verify_file)
        out = wg.run(ms)
        if wg.injections:
            out.data["injections"] = wg.injections
        if out.ok and ms.check.get("kind") in ("page_has", "url_has") and wg.agent is not None:
            ok, why = self._web_check(wg, ms)
            if not ok:
                out = Outcome(False, "no_effect", why, facts=[], unverified=[why])
        return out

    def _web_check(self, wg: WebGoal, ms) -> "tuple[bool, str]":
        """The planner's own success check, looked at on the live page: the words it said must be there are there, or the address has what it said."""
        try:
            pg = wg.agent.observe()
        except Exception:  # noqa: BLE001
            return True, ""
        c = ms.check
        text = norm(pg.title + " " + " ".join(pg.lines) + " " + " ".join(e.name for e in pg.els if e.name))
        if c["kind"] == "url_has":
            want = norm(c.get("text", ""))
            if want and want not in norm(pg.url) and want not in norm(pg.host):
                return False, "the address does not have what the plan said it should"
            return True, ""
        anys, alls = [norm(x) for x in c.get("any", [])], [norm(x) for x in c.get("all", [])]
        if anys and not any(a in text for a in anys):
            return False, "none of the words the plan expected is on the page"
        if alls and not all(a in text for a in alls):
            return False, "some words the plan expected are not on the page"
        return True, ""

    # ------------------------------------------------------------------------------------------------------------------------------- documents
    def _doc(self, ms, b: Board) -> Outcome:
        if self.h.doc_read is None:
            return Outcome(False, "failed", "reading documents is not available here")
        ex = Extractor(self.h.llm)
        names = ms.extract or ["vendor", "date", "total"]
        if ms.mode == "download_read":
            f = b.files[-1] if b.files else None
            if not f or not f.get("path"):
                return Outcome(False, "no_file", "there is no downloaded file to read")
            try:
                text = self.h.doc_read(f["path"])
            except Exception as e:  # noqa: BLE001
                return Outcome(False, "no_file", f"the downloaded file could not be read ({type(e).__name__})")
            found, missing = ex.read(names, text or "", question=ms.goal)
            if not found:
                return Outcome(False, "no_values", f"none of {', '.join(names)} could be read from the downloaded file")
            rec = {"kind": "doc", "file": f.get("name", ""), "host": f.get("host", "")}
            facts = []
            for n, fd in found.items():
                rec[n] = fd.value
                if fd.many:
                    rec[n + "_all"] = list(fd.many)
                    facts.append(f"{n}: " + "; ".join(fd.many[:10]))
                else:
                    facts.append(f"{n}: {fd.value}")
            b.add_record(rec)
            out = Outcome(True, "none", facts=["read the downloaded file, " + " | ".join(facts)])
            if missing:
                out.ok, out.reason, out.detail = False, "no_values", f"could not read {', '.join(missing)} from the file"
                out.unverified = [out.detail]
            return out
        # mode files: the files of a folder
        if self.h.doc_list is None:
            return Outcome(False, "failed", "listing the owner's folders is not available here")
        try:
            files = self.h.doc_list(ms.folder or "Downloads", ms.ext or "pdf", int(ms.days or 0))
        except (ValueError, OSError) as e:
            return Outcome(False, "no_file", str(e)[:160] if isinstance(e, ValueError) else "the folder could not be read")
        if ms.seek:                                                      # one document the owner named
            files = [f for f in files if ms.seek.lower() in f["name"].lower()]
        if not files:
            return Outcome(False, "no_file", f"no {ms.ext or 'pdf'} files were found in {ms.folder or 'Downloads'}" + (f" from the last {ms.days} days" if ms.days else ""))
        rows, bad, told = 0, [], []
        private = bool(set(names) & {"vendor", "seller", "supplier", "total", "amount", "invoice_number", "number"})        # money and parties stay in the file; dates and the like he asked to be told
        for fd in files[:15]:
            if self.h.stop():
                return Outcome(False, "stopped", "the owner stopped the task")
            try:
                text = self.h.doc_read(fd["path"])
            except Exception as e:  # noqa: BLE001
                bad.append(f"{fd['name']}: could not be read ({type(e).__name__})")
                continue
            found, missing = ex.read(names, text or "")
            if not found:
                bad.append(f"{fd['name']}: nothing could be read")
                continue
            rec = {"kind": "doc", "file": fd["name"]}
            for n, f in found.items():
                rec[n] = f.value
                if f.many:
                    rec[n + "_all"] = list(f.many)
                    told.append(f"{fd['name']}: {n}: " + "; ".join(f.many[:12]))
                elif not private:
                    told.append(f"{fd['name']}: {n}: {f.value}")
            b.add_record(rec)
            rows += 1
            if missing:
                bad.append(f"{fd['name']}: {', '.join(missing)} not found")
        out = Outcome(rows > 0, "none" if rows else "no_values", data={"private": True, "rows": rows} if private else {"rows": rows}, unverified=bad[:4])
        where = "the folder" if ms.folder.startswith(("/", chr(92))) or ":" in ms.folder else ms.folder    # chr(92): a backslash in an f-string expression is a SyntaxError before Python 3.12
        out.facts = [f"read {rows} of {len(files)} file(s) in {where}; each value was found in the file's own text"] if rows else []
        if rows and told:
            out.facts += told[:6]
        if not rows:
            out.detail = "none of the files could be read"
        return out

    # ------------------------------------------------------------------------------------------------------------------------------- outputs
    def _out_root(self) -> Path:
        return Path(self.h.out_dir()) if self.h.out_dir else TO.deliverables_dir()

    ALIAS = {"product": "subject", "shop": "host", "store": "host", "site": "host", "link": "url", "address": "url"}

    def _rows(self, columns: list, b: Board) -> list:
        rows = []
        for r in b.records:
            row = {c: r.get(self.ALIAS.get(c, c), "") for c in columns}
            if any(str(row[c]).strip() for c in columns if c not in ("url", "shop", "host")):
                rows.append(row)
        return rows

    def _output(self, ms, b: Board, goal: str) -> Outcome:
        root = self._out_root()
        if ms.mode in ("csv", "xlsx"):
            cols = ms.columns or ["subject", "name", "price", "url"]
            rows = self._rows(cols, b)
            need = int(ms.check.get("min_rows") or ms.check.get("min") or 1)
            if len(rows) < need:
                return Outcome(False, "no_values", f"only {len(rows)} row(s) were found, {need} are needed for the table",
                               unverified=[f"the table would have {len(rows)} row(s) where {need} were expected"] if rows else [])
            path = TO.write_csv(root, ms.name or "table", cols, rows, self.cfg)
            kinds = [("csv", path)]
            if ms.mode == "xlsx":
                x = TO.write_xlsx(root, ms.name or "table", cols, rows, self.cfg)
                if x is not None:
                    kinds.append(("xlsx", x))
            notes = []
            if ms.mode == "xlsx" and len(kinds) == 1:
                notes.append("the XLSX library is not installed here, so only the CSV was made (Excel opens it)")
            facts, shown = [], str(path)
            for kind, p in kinds:
                try:
                    header, got = TO.read_back_csv(p) if kind == "csv" else TO.read_back_xlsx(p)
                except Exception as e:  # noqa: BLE001
                    return Outcome(False, "failed", f"the {kind.upper()} file could not be read back ({type(e).__name__})")
                want = TO.table_rows(rows, cols, self.cfg)
                if [h for h in header] != [TO.safe_cell(c, 60) for c in cols] or len(got) != len(want) or any([str(x) for x in g] != [str(y) for y in w] and not _num_equal(g, w) for g, w in zip(got, want)):
                    return Outcome(False, "failed", f"the {kind.upper()} file does not say what was meant (read back and compared)")
                facts.append(f"{kind.upper()} file written and read back: {len(got)} row(s), columns {', '.join(cols)}")
                b.add_output({"type": kind, "file": p.name, "path": str(p), "rows": len(got), "columns": cols})
            out = Outcome(True, "none", facts=facts + notes, data={"file": path.name, "shown": shown, "rows": len(rows), "private": any(r.get("kind") == "doc" for r in b.records)})
            out.unverified = notes
            return out
        if ms.mode == "brief":
            return self._brief(ms, b, goal, root)
        return Outcome(False, "failed", "unknown output")

    def _brief(self, ms, b: Board, goal: str, root: Path) -> Outcome:
        rules = self.cfg["research"]
        sources = b.sources
        if not sources:
            return Outcome(False, "no_values", "no sources were read, so there is nothing to write a brief from")
        topic = ms.name or ms.query or "the topic"
        dis = TO.disagreements(sources)
        text, provider, problems = "", "", []
        routes = [("the local model on this PC", self.h.release_local, self.h.write_local),
                  ("the online model (excerpts cleared by the privacy gateway)", self.h.release_text, self.h.ask_reason)]
        for label, release, write in routes:                              # the local model first: the pages' text then never leaves this PC
            for attempt in (1, 2):
                text = self._model_brief(release, write, topic, sources, rules, dis, problems if attempt == 2 else [], b)
                if text:
                    provider = label
                    problems = TO.brief_problems(text, sources, rules, dis)
                    if not problems:
                        break
            if text and not problems:
                break
        if not text or problems:
            text = TO.extractive_brief(topic, sources, rules)
            provider = "short phrases assembled by JARVIS (no model)"
            problems = TO.brief_problems(text, sources, rules, dis)
        md, docx = TO.write_brief(root, "brief-" + (ms.name or topic), text)
        back = md.read_text(encoding="utf-8")
        if norm(back) != norm(text):
            return Outcome(False, "failed", "the brief file does not say what was written")
        doms = len({registrable(s["host"]) for s in sources})
        b.add_output({"type": "brief", "file": md.name, "path": str(md), "sources": len(sources)})
        facts = [f"brief written and read back ({len(TO.words(back))} words), citing {len(sources)} source(s) from {doms} different site(s) with their titles and addresses; written by {provider}"]
        if dis:
            facts.append("the sources disagree on: " + "; ".join(lab for lab, _ in dis))
        out = Outcome(not problems, "none" if not problems else "no_values", facts=facts, data={"file": md.name, "shown": str(md), "sources": len(sources), "domains": doms})
        if docx is not None:
            out.facts.append("a Word copy was made too")
        if problems:
            out.detail = "; ".join(problems)[:200]
            out.unverified = [f"the brief does not meet the research rules: {p}" for p in problems][:3]
        return out

    def _model_brief(self, release, write, topic: str, sources: list, rules: dict, dis: list, problems: list, b: Board) -> str:
        """The brief from a model: the topic and SHORT excerpts, each passed through `release` first (the local route: the secret pass; the online route: the privacy gateway
        and the owner's switch; an excerpt that is not cleared stops that route). '' when there is no such model, nothing is cleared, or the token budget is used up."""
        if write is None or release is None:
            return ""
        try:
            b.check("tokens")
        except BudgetExceeded:
            return ""
        cleared = []
        for s in sources:
            try:
                rel = release(s["text"][: int(rules["excerpts_online_chars"])])
            except Exception:  # noqa: BLE001
                return ""
            if not getattr(rel, "allowed", False) or not str(getattr(rel, "text", "")).strip():
                return ""
            cleared.append({**s, "text": rel.text})
        msgs = TO.brief_messages(topic, cleared, rules, dis)
        if problems:
            msgs[-1]["content"] += "\n\nThe last draft broke these rules, fix them: " + "; ".join(problems)[:300]
        try:
            text, _prov = write(msgs)
        except Exception:  # noqa: BLE001
            return ""
        b.spend("tokens", (sum(len(m["content"]) for m in msgs) + len(text or "")) / 4)
        return str(text or "").strip()

    # ------------------------------------------------------------------------------------------------------------------------------- ask and pc
    def _ask(self, ms, b: Board, goal: str) -> Outcome:
        sub = b.d.get("submit") or {}
        filled = b.d.get("filled") or {}
        if ms.mode == "confirm_submit":
            if not filled:
                return Outcome(False, "no_values", "the form was not filled, so there is nothing to ask about")
            if self.h.ask is None:
                return Outcome(True, "none", facts=[f"the form on {filled.get('host')} is filled up to the submit button and NOT submitted; I cannot ask you here, so it waits for you"],
                               unverified=["no way to ask you from here"])
            self.h.line("ask_owner", filled.get("host", ""))
            spec = {"title": "The form is ready: submit it?", "intro": "JARVIS filled the form and stopped at the submit button. Submitting is yours to decide, and it needs your PIN in the console.",
                    "fields": [{"type": "info_table", "label": "The form", "rows": [["Site", str(filled.get("host", ""))], ["Fields typed", ", ".join(filled.get("labels") or [])],
                                                                                  ["Button", str(sub.get("label") or "(none found)")]]}],
                    "buttons": [{"id": "later", "label": "Leave it unsubmitted", "style": "primary"}, {"id": "go", "label": "Ask for approval to submit", "style": "secondary"}]}
            try:
                ans = self.h.ask(spec)
            except Exception:  # noqa: BLE001
                ans = None
            if not (isinstance(ans, dict) and ans.get("answered")):
                return Outcome(True, "none", facts=[f"the form on {filled.get('host')} is filled up to the submit button and NOT submitted; you did not answer, so it stays as it is"], data={"asked": True})
            if ans.get("button") != "go":
                return Outcome(True, "none", facts=[f"the form on {filled.get('host')} is filled and NOT submitted, as you chose"], data={"asked": True})
            if self.h.make_agent is None or not sub.get("label"):
                return Outcome(False, "failed", "there is no submit button I can press; press it yourself in Chrome")
            return self._press_submit(ms, b, goal, sub)
        return Outcome(False, "failed", "unknown question")

    def _press_submit(self, ms, b: Board, goal: str, sub: dict) -> Outcome:
        """The owner asked for the approval card: the click goes to the kernel as what it is (a form submission: the evidence text says so), and the kernel holds it for his PIN."""
        wg = WebGoal(self.h.make_agent, Extractor(None), b, self.cfg, goal, stop=self.h.stop, ask=self.h.ask, grants=self.h.grants, take_local=self.h.take_local,
                     sleep=self.h.sleep, planner=None)
        try:
            wg._new_agent(ms)
            pg = wg.look()
            el = next((e for e in pg.els if e.role == "button" and (e.name or "") == sub.get("label")), None)
            if el is None:
                return Outcome(False, "not_found", "the submit button is not on the page any more")
            a = BA.Act("click", label=el.name, role="button", ref=el.ref, host=pg.host, ctx="submit this form")
            try:
                wg.agent.act(a)
            except BA.NeedApproval as e:
                body = {}
                try:
                    body = wg.agent.backend._body(a)
                except Exception:  # noqa: BLE001
                    body = {}
                return Outcome(False, "approval", "the submit is held for your PIN in the console", approval_id=getattr(e.res, "approval_id", ""), how=getattr(e.res, "how", ""),
                               needs_owner={"kind": "approval", "detail": "submitting the form needs your PIN"}, data={"approval_body": body})
            return Outcome(True, "none", facts=[f"the submit button was pressed on {pg.host} after you approved it"])
        except BA.Stopped:
            return Outcome(False, "stopped", "the owner stopped the task")
        except BA.AgentError as e:
            return Outcome(False, "failed", str(e)[:160])
        finally:
            wg._done_agent()

    def _pc(self, ms, b: Board, goal: str) -> Outcome:
        if self.h.pc is None:
            return Outcome(False, "failed", "controlling the PC is not available here")
        r = self.h.pc(ms.goal or goal, ms.check.get("title", ""))
        facts = [str(x) for x in (r.get("facts") or [])]
        loose = [str(x) for x in (r.get("unverified") or []) if x]
        if r.get("state") == "waiting_for_permission":
            return Outcome(False, "approval", "a step on the PC is held for your PIN", approval_id=str(r.get("approval_id") or ""), how=str(r.get("how") or ""),
                           needs_owner={"kind": "approval", "detail": "a PC step needs your PIN"})
        if r.get("status") == "needs_clarification" or r.get("ask"):
            return Outcome(False, "asked", str(r.get("question") or "the PC loop needs your answer")[:200], needs_owner={"kind": "question", "detail": "the PC loop asked you something"})
        if r.get("expected_missing") or not facts:
            return Outcome(False, "no_effect", "the effect on the PC was not seen" if not facts else "the expected effect was not found", unverified=loose)
        return Outcome(True, "none", facts=facts[:5], unverified=loose)


def _num_equal(got: list, want: list) -> bool:
    try:
        return all((str(g) == str(w)) or (float(g) == float(str(w).lstrip("'"))) for g, w in zip(got, want))
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------------------------------------------------------------- the plan, as a mission's planner
def mission_plan(goal: str, context: str = "", *, ask_online: "Callable | None" = None, cfg: "dict | None" = None) -> dict:
    """The planner of a task mission: {status, steps, question, problems}. First plan: the online model (text only, through the gateway), checked by taskplan.parse_plan, the rules
    when it is down or unusable. A RE-PLAN (after a milestone failed) is made once, differently: the failed milestone is tried another way or the owner is asked where to look."""
    cfg = cfg or TP.config()
    text = None
    note = []
    if ask_online is not None:
        try:
            text, _prov = ask_online(TP.plan_messages(goal, context))
        except Exception as e:  # noqa: BLE001 - no online plan: the rules plan, and it is said
            text = None
            note.append(f"no online plan ({type(e).__name__}); planned by rules")
    plan = TP.plan_for(goal, text, cfg)
    plan["problems"] = (note + list(plan.get("problems") or []))[:6]
    if context and plan.get("status") == "ok":
        plan = _replan_variant(goal, plan, context, cfg)
    return plan


def _replan_variant(goal: str, plan: dict, context: str, cfg: dict) -> dict:
    """Plan only what remains, differently. The failed milestone's id is in the context ('(m2 follow)'): everything before it was done; the rest is planned again, and a web
    milestone that could not find its page is tried through the site's search first, or the owner is asked where to look."""
    m = re.search(r"\((m\d+|[a-z]\d*) (\w+)\)", context or "")
    if not m:
        return plan
    fid = m.group(1)
    steps = plan["steps"]
    k = next((i for i, s in enumerate(steps) if s.get("id") == fid), None)
    if k is None:
        return plan
    rest = steps[k:]
    first = TP.Milestone.from_step(rest[0])
    if first.engine == "web" and first.mode in ("follow", "download") and first.site and TP.search_template(first.site, cfg) and not any(s.get("mode") == "search" for s in rest[:1]):
        q = first.query or first.seek
        if q and q != "*best*":
            srch = TP.Milestone(id=fid + "s", engine="web", mode="search", goal=f"search {first.site} for the words, a different way in", site=first.site, query=q[:120], check={"kind": "url_has", "text": first.site})
            rest = [srch.to_step(), {**rest[0], "seek": "*best*", "query": q[:120]}] + rest[1:]
            return {**plan, "steps": rest, "problems": (plan.get("problems") or []) + ["re-planned: the search page first"]}
    if first.engine == "web" and first.mode in ("follow", "lookup", "read", "download"):
        where = first.site or "the site"
        return {"status": "clarify", "steps": [], "question": f"I could not find it on {where}. Where should I look, or what is it called there?", "problems": plan.get("problems") or [], "planned_by": plan.get("planned_by", "rules")}
    return {**plan, "steps": rest}


# --------------------------------------------------------------------------------------------------------------------------------- verify (for coordinator.verify) and the answer
def verify_step(step: dict, r: dict) -> "tuple[bool, str, str]":
    """(passed, what was checked, uncertainty) of a finished milestone: it passes only on what its executor VERIFIED by effect; the uncertainty names what could not be. The reason
    given is a fixed word (the re-plan reads it), never a page's words."""
    if r.get("stopped"):
        return False, "stopped by the owner", "reason: stopped"
    if not r.get("verified") or not r.get("ok"):
        code = str(r.get("reason") or "none")
        code = code if code in TP.REASONS else "failed"
        return False, "the milestone's effect was not verified", f"reason: {code} ({step.get('id', 'm')} {step.get('mode', '')})"
    facts = "; ".join(str(x) for x in r.get("facts") or [])
    if not facts:
        return False, "nothing was verified", f"reason: none ({step.get('id', 'm')} {step.get('mode', '')})"
    loose = "; ".join(str(x) for x in r.get("unverified") or [] if x)
    return True, facts, loose[:240]


def final_text(rec: dict) -> str:
    """The answer to the owner, from the verified facts of the finished steps alone. What was not done, and why, is said in plain words; what could not be verified is said as that."""
    steps = [s for s in rec.get("steps", []) if not s.get("held")]
    done = [s for s in steps if s.get("passed")]
    facts = []
    for s in done:
        r = s.get("result") or {}
        if r.get("private"):
            facts.append(str(r.get("facts", [""])[0])[:240] if r.get("facts") else f"step {s.get('n')} done (the values stay in the file)")
        else:
            facts += [str(x) for x in (r.get("facts") or [])]
    failed = [s for s in steps if not s.get("passed")]
    status = rec.get("status")
    parts = []
    if status == "completed" and not failed:
        parts.append("Done. " + " ".join(f.rstrip(".") + "." for f in facts[:8]))
    else:
        if facts:
            parts.append("What I did and checked: " + " ".join(f.rstrip(".") + "." for f in facts[:8]))
        else:
            parts.append("I have nothing verified yet.")
        for s in failed[-2:]:
            r = s.get("result") or {}
            why = REASON_SAYS.get(str(r.get("reason") or ""), "it did not finish")
            parts.append(f"Not done: {s.get('args', {}).get('goal') or s.get('args', {}).get('mode') or 'a step'} ({why}" + (f": {r.get('detail')}" if r.get("detail") else "") + ").")
        if status == "stopped" and rec.get("error"):
            parts.append(f"I stopped: {rec['error']}.")
        elif status == "cancelled":
            parts.append("Stopped, as you asked.")
        elif status == "waiting_for_permission":
            parts.append(f"A step is waiting for your approval in the JARVIS console (approval {rec.get('approval_id')}); approve it with your PIN, then tell me to continue.")
        elif status == "needs_owner":
            parts.append(str(rec.get("error") or "I need you for one step") + " Do it, then tell me to continue ('redo') or say 'done'.")
        elif status == "needs_clarification":
            parts.append(str(rec.get("question") or "I need to know one more thing."))
    inj = sum(int((s.get("result") or {}).get("injections") or 0) for s in steps)
    if inj:
        parts.append(f"{inj} place(s) on web pages spoke to me as if they were giving orders; I ignored them.")
    unv = [u for s in done for u in ((s.get("result") or {}).get("unverified") or [])]
    if unv:
        parts.append("Could not be verified: " + "; ".join(unv)[:300] + ".")
    out = next((s.get("result") for s in reversed(done) if (s.get("result") or {}).get("shown")), None)
    if out:
        parts.append(f"The file ({out.get('file')}) is ready to show in the Preview panel.")
    return " ".join(parts)[:1500]
