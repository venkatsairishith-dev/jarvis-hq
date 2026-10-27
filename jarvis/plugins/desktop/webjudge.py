"""D77 (2026-10-27), the WEB JUDGE: the owner's practice problems are on a web page or a set of pages (a LeetCode-style practice list, a lab site) in his own Chrome.
For each problem of the set JARVIS reads the statement and the sample tests from the page, solves it (the same online-plan, local-execution path as the folder flow: the
model sees the redacted statement through the privacy gateway, the proposal is first run on the sample cases in the restricted runner), types the solution into the page's
code editor, clicks the page's own Run / Test button (never Submit), reads the verdict FROM THE PAGE, goes to the next problem with the page's Next control, and stops at the
end of the set.

Built on the D64 browser engine (browser_agent.py: observe -> locate by role and accessible name -> act -> verify by effect, with its recovery ladder, walls, host list and
stop flag). Nothing here is new browser automation; this module only decides WHAT to look for on a judge page and keeps the rules:

  * ABSOLUTE RULE, no exception, practice or not: the browser is wrapped by `SubmitGuard`. A click on anything that submits, finishes, finalises or grades (Submit, Submit
    Solution, Submit Code, Finish, Finalize, End Test, Check Answer, Turn in, ...), a coordinate click that lands on such a control, and every Enter key press (Ctrl+Enter
    and Ctrl+' submit on some sites) are REFUSED before they reach the kernel. The kernel also asks the owner's PIN for "submit" (policy.BROWSER_HARD), so there are two
    locks. If a result can only be seen by submitting, that problem is NOT verified and the report says so.
  * the exam / live-test guard (plugins/_shared/exam_guard.py over config/exam_signals.json) runs on every page BEFORE a problem is started: a proctoring / webcam /
    screen-share prompt or a contest page is declined (and the set stops); an assessment host or an honor-code text is ONE short question; only his answer on the form
    lets it go on.
  * page text is untrusted DATA (cleaned, fenced for the model, never an instruction); the engine's host list keeps the task on the page's site.
  * a verdict is read from the page's own text after Run (fixed vocabulary: passed n/m, all tests passed, wrong answer, runtime error ...). Never from the model.
  * the code is typed into the editor in ONE browser type job (the page's editor holds it afterwards; its first line and the number of characters typed are checked), never
    pasted and never "assumed".
"""
from __future__ import annotations

import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

from jarvis.plugins._shared import code_runner as cr, exam_guard, untrusted

from . import browser_agent as BA
from . import browser_snapshot as S
from . import problemset as P
from . import problemset_text as T

# Anything that submits, finishes or grades. Deliberately wide: a false refusal costs one skipped verification, a miss costs a graded attempt.
FINALIZE = re.compile(r"(?i)(?:\bsubmit\w*|\bsubmission\b|\bfinali[sz]e\w*|\bfinish\w*|\bend\s+(?:the\s+)?(?:test|exam|attempt|quiz|session|contest)\b|"
                      r"\bcomplete\s+(?:the\s+)?(?:test|exam|attempt|quiz|contest)\b|\bturn\s*in\b|\bhand\s*in\b|\bgrade\w*|\bcheck\s+(?:my\s+)?answers?\b|\bfinal\s+answer\b|"
                      r"\bconfirm\w*|\bsave\s+and\s+(?:exit|finish|close)\b|\bproceed\s+to\s+(?:grading|review|results?)\b|\bclaim\b|\bpublish\b|\bpost\s+(?:solution|answer)\b)")
ENTER_KEY = re.compile(r"(?i)(?:^|\+)(?:enter|return|numpadenter)$|^control\+'$|^ctrl\+'$|^(?:control|ctrl|meta|cmd|command)\+(?:enter|return)$")
RUN_NAME = re.compile(r"(?i)^(?:[▶►⏵]\s*)?(?:run(?:\s+(?:code|tests?|sample\w*|all|it|program|solution))?|test(?:\s+(?:code|run|it|solution|sample\w*))?|execute(?:\s+code)?|"
                      r"compile(?:\s*(?:&|and)\s*run)?|try\s+it|run\s+&\s+test)(?:\s*\(.*\))?$")
NEXT_NAME = re.compile(r"(?i)^(?:next(?:\s+(?:problem|question|challenge|exercise|task|one|page))?(?:\s*[>›»→])?|[>›»→]|›|»|→|continue\s+to\s+next(?:\s+problem|\s+question)?)$")
EDITOR_GOOD = re.compile(r"(?i)\b(code|editor|solution|source|your answer|write your|type your|program|script|function|implementation)\b")
EDITOR_BAD = re.compile(r"(?i)\b(search|custom|stdin|standard input|test ?case|testcase|email|e-mail|password|username|comment|note|filter|feedback|discussion|reply)\b")
RUNNING_WORDS = re.compile(r"(?i)\b(running|judging|pending|queued|compiling|evaluating|executing|please wait|loading)\b|\.\.\.$|…$")

ERR_RX = re.compile(r"(?i)\b(runtime error|compil(?:e|ation) error|syntax error|time limit exceeded|memory limit exceeded|segmentation fault|traceback|exception|internal error|error:)")
FRAC_RXS = (re.compile(r"(?i)\bpassed\s+(\d+)\s*(?:/|of|out of)\s*(\d+)"), re.compile(r"(?i)\b(\d+)\s*(?:/|of|out of)\s*(\d+)\s+(?:sample\s+)?(?:test ?cases?|tests?|cases?)\b"),
            re.compile(r"(?i)\b(\d+)\s*(?:/|of|out of)\s*(\d+)\s+(?:sample\s+)?(?:tests?\s+)?passed\b"))
ALL_PASS_RX = re.compile(r"(?i)\ball\s+(?:the\s+)?(?:sample\s+|visible\s+)?(?:test ?cases?|tests?|samples?)\s+passed\b|(?<!not )\baccepted\b|\bcorrect answer\b|\bsample tests? passed\b|\bwell done\b")
FAIL_RX = re.compile(r"(?i)\bwrong answer\b|\bnot accepted\b|\bfailed\b|\bincorrect\b|\bmismatch\b|\btest case \d+ failed\b|\bdoes not match\b")
ZERO_FAILED = re.compile(r"(?i)\b0\s+(?:tests?\s+|cases?\s+)?failed\b")


def finalizing(*names: Any) -> bool:
    """Would a click on a control with any of these names/texts submit, finish or grade something?"""
    return any(FINALIZE.search(str(n or "")) for n in names)


# ------------------------------------------------------------------------------------------------------------- the guard
class SubmitGuard(BA.Backend):
    """Wraps the engine's backend. Refuses, BEFORE the kernel is asked, every action that could submit or finalise (see the module docstring). Records what it refused."""

    def __init__(self, inner: BA.Backend):
        self.inner = inner
        self.page: S.Page | None = None
        self.refused: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def observe(self) -> dict:
        doc = self.inner.observe()
        try:
            self.page = S.parse(doc)
        except Exception:  # noqa: BLE001
            self.page = None
        return doc

    def take_local(self, ref):
        return self.inner.take_local(ref)

    def end(self) -> None:
        self.inner.end()

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def _el_names(self, ref: str) -> list[str]:
        pg = self.page
        if pg is None or not ref:
            return []
        out = []
        for e in pg.els:
            if e.ref == ref:
                out += [e.name, e.child_text, e.text]
                p = e.parent
                hops = 0
                while p >= 0 and hops < 3:                      # an icon or text inside a button / link / clickable <div>: that control's own name counts too
                    par = pg.els[p]                             # (a landmark such as <main> or <form> is not a control: its long name would refuse every click)
                    if par.role in ("button", "link", "menuitem", "tab", "option", "checkbox", "radio", "switch") or (par.role == "generic" and par.attrs.get("cursor")):
                        out += [par.name, par.child_text]
                    p = par.parent
                    hops += 1
        return out

    def _refuse(self, a: BA.Act, why: str) -> BA.Res:
        self.refused.append((a.kind, (a.label or a.value or "")[:60]))
        return BA.Res(False, "refused: " + why, "refused")

    def act(self, a: BA.Act) -> BA.Res:
        k = a.kind
        if k in ("click", "check", "drag", "hover", "xy_click"):
            names = [a.label] + self._el_names(a.ref)
            if finalizing(*names):
                return self._refuse(a, "JARVIS never clicks Submit, Finish or anything that grades a problem")
            if k == "xy_click" and self.page is not None and a.x is not None:
                for e in self.page.els:
                    b = e.box
                    if b and finalizing(e.name, e.child_text, e.text) and b[0] - 8 <= a.x <= b[0] + b[2] + 8 and b[1] - 8 <= (a.y or 0) <= b[1] + b[3] + 8:
                        return self._refuse(a, "that point is on a control that submits or grades")
        elif k == "press" and ENTER_KEY.search(str(a.value or "").strip()):
            return self._refuse(a, "Enter (and Ctrl+Enter) can submit on a judge page")
        self.sent.append((k, (a.label or a.value or "")[:60]))
        return self.inner.act(a)


# ------------------------------------------------------------------------------------------------------------- reading a judge page
def _fold(s: Any) -> str:
    return " ".join(str(s or "").lower().split())


def statement_of(pg: S.Page) -> tuple[str, str]:
    """(title, statement text): the headings, paragraphs and texts of the page without the names of its controls and its result area; untrusted DATA, cleaned and capped.
    A dropped control or result line leaves a blank line, so a sample's output block ends where the page's own controls begin."""
    controls = {_fold(e.shown) for e in pg.els if e.role in ("button", "link", "tab", "menuitem", "option", "combobox", "textbox", "searchbox", "checkbox", "radio", "status", "alert", "log",
                                                              "timer", "progressbar", "main", "banner", "navigation", "form", "region", "group", "contentinfo", "complementary", "search")
                and e.shown}
    seen, lines = set(), []
    for ln in pg.lines:
        f = _fold(ln)
        if not f:
            continue
        if f in controls:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if f not in seen:
            seen.add(f)
            lines.append(ln.strip())
    head = next((e for e in pg.els if e.role == "heading" and e.name), None)
    title = (head.name if head else pg.title) or ""
    return untrusted.clean_untrusted(title, 120).strip(), untrusted.clean_untrusted("\n".join(lines).strip("\n"), 6000)


def find_editor(pg: S.Page) -> "S.El | None":
    """The code input: the text box that is called a code editor / editor / solution ..., not a search box or a custom-input box; the biggest one when several fit."""
    best, best_score = None, 0.0
    for e in pg.els:
        if e.role not in ("textbox", "searchbox") or not e.ref or e.disabled or e.attrs.get("hidden"):
            continue
        name = e.name or ""
        score = 0.0
        if EDITOR_GOOD.search(name):
            score += 3.0
        if EDITOR_BAD.search(name) and not re.search(r"(?i)code\s+editor|your code|solution", name):
            score -= 5.0
        b = e.box
        if b:
            score += min(2.0, (b[2] * b[3]) / 40000.0)
        if not name and b and b[2] * b[3] > 20000:
            score += 1.0
        if score > best_score:
            best, best_score = e, score
    return best if best_score >= 1.0 else None


def find_run(pg: S.Page) -> "S.El | None":
    for e in pg.els:
        if e.role in ("button", "link") and e.ref and not e.disabled and RUN_NAME.match((e.shown or "").strip()) and not finalizing(e.shown):
            return e
    return None


def find_next(pg: S.Page) -> "S.El | None":
    for e in pg.els:
        if e.role in ("button", "link") and e.ref and not e.disabled and NEXT_NAME.match((e.shown or "").strip()) and not finalizing(e.shown):
            return e
    return None


def page_language(pg: S.Page) -> str:
    for e in pg.els:
        if e.role == "combobox" and re.search(r"(?i)\blang", e.name or ""):
            return cr.norm_language(re.sub(r"[^A-Za-z+#0-9]", "", e.text or "")) or ""
    return ""


@dataclass
class RunVerdict:
    state: str = "unknown"          # passed | failed | error | unknown | running
    passed: int = 0
    total: int = 0
    detail: str = ""


def parse_run_result(lines: list[str]) -> RunVerdict:
    """The verdict the PAGE shows after Run, in a fixed vocabulary. `lines` are page text lines (untrusted). Errors win over counts; "n/m passed" is believed over words;
    a page that shows nothing recognisable is `unknown` (never guessed)."""
    text = "\n".join(str(x) for x in lines if x)
    if not text.strip():
        return RunVerdict()
    clean = untrusted.clean_untrusted(text, 600)
    if ERR_RX.search(clean):
        return RunVerdict("error", detail=_one_line(clean, ERR_RX))
    for rx in FRAC_RXS:
        m = rx.search(clean)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > 0:
                return RunVerdict("passed" if a == b else "failed", a, b, _one_line(clean, rx) if a != b else f"the page reports {a} of {b} sample tests passed")
    if ZERO_FAILED.search(clean) and ALL_PASS_RX.search(clean):
        return RunVerdict("passed", detail="the page reports every sample test passed")
    if FAIL_RX.search(clean):
        return RunVerdict("failed", detail=_one_line(clean, FAIL_RX))
    if ALL_PASS_RX.search(clean):
        return RunVerdict("passed", detail="the page reports every sample test passed")
    if RUNNING_WORDS.search(clean):
        return RunVerdict("running")
    return RunVerdict()


def _one_line(text: str, rx: "re.Pattern[str]") -> str:
    for ln in text.splitlines():
        if rx.search(ln):
            return " ".join(ln.split())[:120]
    return " ".join(text.split())[:120]


# ------------------------------------------------------------------------------------------------------------- the job
@dataclass
class WebHands:
    """What the host lends for the browser side: a function that builds the engine for the hosts this task may visit and returns it with its guard."""
    make_agent: Callable[[list[str]], tuple[BA.Agent, SubmitGuard]]
    run_wait_s: float = 25.0
    sleep: Callable[[float], None] = time.sleep


class WebJob(P.Job):
    """A problem-set job whose problems are the pages of a practice set. The record (manifest) is the same as the folder flow's, so Stop, resume and the budgets work alike."""

    def __init__(self, job: str, deps: P.Deps, hands: WebHands):
        super().__init__(job, deps)
        self.h = hands
        self.m.setdefault("web", {"submit_refused": 0, "pages": 0, "ended": ""})

    # ---- the whole set
    def run_set(self) -> str:
        """Visits the pages of the set in order; returns the status for P.deliver: completed | partial | stopped | held."""
        url = str(self.params.get("url") or "")
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        ag, guard = self.h.make_agent([host] if host else [])
        self._ag, self._guard = ag, guard
        status = "completed"
        try:
            try:
                if url:
                    ag.do(BA.Step("open", "goto", to=url, new_tab=True, expect={"host_is": host}))
                    pg = ag.observe()
                else:
                    pg = ag.observe()                           # "this page": the page that is open in his Chrome now
                    ag.allow_host(pg.host)
                status = self._pages(pg)
            except BA.Stopped:
                raise P.Stopped()
            except BA.NeedOwner as e:
                self.m["web"]["ended"] = f"the page needs you: {e.detail[:120]}"
                status = "partial"
            except BA.NeedApproval:
                self.m["web"]["ended"] = "a step was held for your PIN in the JARVIS console; it was not done"
                status = "partial"
            except (BA.BudgetExceeded, BA.BackendLost, BA.StepFailed, BA.AgentError) as e:
                self.m["web"]["ended"] = f"the browser stopped: {str(e)[:140]}"
                status = "partial"
        finally:
            self.m["web"]["submit_refused"] = len(guard.refused)
            self.m["web"]["injection_notes"] = ag.injections
            try:
                ag.backend.end()
            except Exception:  # noqa: BLE001
                pass
            self.save()
        return status

    def _pages(self, pg: S.Page) -> str:
        seen: set[str] = set()
        ag = self._ag
        for _ in range(max(1, self.budget.max_problems)):
            self.check_stop()
            title, text = statement_of(pg)
            ident = _fold(pg.url) + "|" + _fold(title)
            if ident in seen:
                self.m["web"]["ended"] = "the next page was a page already done"
                break
            seen.add(ident)
            self.m["web"]["pages"] = len(seen)
            p = self._entry(ident, title, text, pg)
            if p["status"] in ("",) and self.over_time():
                p["status"], p["why"] = P.NOT_TRIED, "the time budget was used up"
                break
            if p["status"] in (P.HELD, P.TAUGHT):                  # a page that was declined or only taught earlier: the set does not go on past it, on a resume either
                self.m["web"]["ended"] = "this page looks like a live test: the set was not continued"
                break
            if p["status"] in ("", P.NOT_TRIED, P.BLOCKED):
                p["status"], p["why"] = "", ""
                verdict = self._gate(pg, title, text, p)
                if verdict == "stop":
                    break
                if verdict == "go":
                    self.d.progress("solving", p["n"], p["n"])
                    self._problem(pg, p)
                    self.save()
            nxt = find_next(self._look())
            if nxt is None:
                self.m["web"]["ended"] = "no Next control: the end of the set"
                break
            before = self._look()
            try:
                ag.do(BA.Step("next", "click", S.Locator(role=nxt.role, name=nxt.shown), expect={"changed": True}))
            except BA.StepFailed as e:
                self.m["web"]["ended"] = f"Next did not lead anywhere ({e.why[:80]})"
                break
            pg = ag.observe()
            if pg.url == before.url and _fold(statement_of(pg)[0]) == _fold(title):
                self.m["web"]["ended"] = "Next did not change the page: the end of the set"
                break
        else:
            self.m["web"]["ended"] = f"stopped after {self.budget.max_problems} problems"
            return "partial"
        if any(q.get("status") in (P.NOT_TRIED,) for q in self.m["problems"]):
            return "partial"
        return "held" if self.m["problems"] and all(q.get("status") == P.HELD for q in self.m["problems"]) else "completed"

    def _look(self) -> S.Page:
        return self._ag.observe()

    def _entry(self, ident: str, title: str, text: str, pg: S.Page) -> dict:
        for q in self.m["problems"]:
            if q.get("ident") == ident:
                return q
        samples = T.extract_samples(text, self.rules)
        q = {"n": len(self.m["problems"]) + 1, "ident": ident, "file": "(web page)", "title": title[:80], "text": text, "common": "", "key": P._key(ident),
             "samples": [{"input": s.input, "output": s.output} for s in samples], "lang_said": page_language(pg) or T.detect_language(text, self.rules),
             "status": "", "attempts": 0, "pages_unreadable": [], "notes": [], "typed": False}
        self.m["problems"].append(q)
        return q

    # ---- the live-test guard, before a problem is started
    def _gate(self, pg: S.Page, title: str, text: str, p: dict) -> str:
        v = exam_guard.assess(pg.title, pg.url, [text[:5000]], [])
        self.m.setdefault("owner_declared_practice", {"by": "his own command", "words": self.params.get("request_words", "")[:200], "at": time.time()})
        if not v.hit:
            return "go"
        attested = (self.m.get("exam") or {}).get("owner_attested_practice")
        if v.level == exam_guard.ASK and attested:
            return "go"
        flagged = {"(web page)": v}
        self._ask_exam(flagged, mark=False)                      # the same ONE short question as the folder flow
        ex = self.m.get("exam") or {}
        choice = ex.get("answer")
        if choice == "practice" and v.level == exam_guard.ASK:
            return "go"
        if choice == "study":
            p["mode"] = "teach"
            try:
                lang = cr.norm_language(page_language(pg)) or "python"
                notes, removed = self.teach_text(p, lang, [T.Sample(s["input"], s["output"]) for s in p.get("samples", [])])
                p["notes"] = [notes[:3000]]
                p["status"], p["why"] = P.TAUGHT, "study notes with different examples; the question on the page was not solved" + (" (a solving code block was removed)" if removed else "")
            except P.ModelError as e:
                p["status"], p["why"] = P.BLOCKED, f"the study notes could not be made: {str(e)[:100]}"
            self.m["web"]["ended"] = "this looks like a live test: study notes only, the set was not continued"
            return "stop"
        p["status"], p["why"] = P.HELD, ("looks like a live test (" + "; ".join(v.reasons[:2]) + "): not solved" if v.level == exam_guard.DECLINE else "looks like a live test; no answer to go ahead")
        self.m["web"]["ended"] = "this looks like a live test: nothing was typed and the set was not continued"
        return "stop"

    # ---- one problem
    def _problem(self, pg: S.Page, p: dict) -> None:
        ag = self._ag
        editor = find_editor(pg)
        if editor is None:
            p["status"], p["why"] = P.BLOCKED, "I could not find the code editor on this page by its role and name, so nothing was typed"
            return
        run_el = find_run(pg)
        self._language(pg, p)
        feedback, prior, rounds = "", "", 1 + max(0, self.budget.retries)
        for attempt in range(1, rounds + 1):
            self.check_stop()
            try:
                best, lang, lang_note, samples, explain, text = self.solve_core(p, feedback, prior, attempts=(1 + max(0, self.budget.retries)) if attempt == 1 else 1)
            except P.ModelError as e:
                p["status"], p["why"] = P.BLOCKED, f"the online model could not be used: {str(e)[:140]}"
                return
            if best is None or best[2] == "wrong_language":
                p["status"], p["why"] = P.FAILED, "the model did not give a program in the expected layout"
                return
            prop, runs, how = best
            if how == "unsafe":
                p["status"], p["why"] = P.UNSAFE, "the model's code used the shell, the network or other files, so it was not typed into the page"
                return
            p["lang_used"] = p.get("lang_used") or lang
            p["samples_local"] = f"{sum(1 for r in runs if r.ok)}/{len(runs)}" if runs else "-"
            code = prop.code
            if len(code) > 3900:
                p["status"], p["why"] = P.BLOCKED, "the solution is longer than I type into a page in one go"
                return
            typed_ok, why = self._type(editor, code)
            if not typed_ok:
                p["status"], p["why"] = P.BLOCKED, why
                return
            p["typed"], p["saved_as"] = True, "(typed in the page)"
            if run_el is None:
                p["status"], p["why"] = P.UNTESTED, ("this page has no separate Run or Test button; seeing a result would need Submit, which JARVIS never clicks, so the page did not verify it"
                                                      + (f" (my own check on the sample cases: {p['samples_local']})" if p.get("samples_local") not in (None, "-") else ""))
                return
            verdict = self._run_on_page(run_el)
            p["page_run"] = {"state": verdict.state, "passed": verdict.passed, "total": verdict.total, "detail": verdict.detail[:140]}
            if verdict.total:
                p["samples_passed"], p["samples_total"] = verdict.passed, verdict.total
            if verdict.state == "passed":
                p["status"], p["why"] = P.PASSED, ""
                return
            if verdict.state in ("unknown", "running"):
                p["status"], p["why"] = P.UNTESTED, "I clicked Run but the page showed no result I could read, so it is not verified"
                return
            p["status"], p["why"] = P.FAILED, f"the page says: {verdict.detail[:100]}" if verdict.detail else "the page reported a failure"
            feedback = ("Your program was typed into the practice page and its Run button gave this result on the page's sample tests:\n"
                        + untrusted.fence("page_result", verdict.detail or verdict.state, 400) + "\nFix the program. Reply in exactly the same layout.")
            prior = text
            if attempt < rounds and not (self.over_time() or self.over_tokens()):
                editor = find_editor(self._look()) or editor
                run_el = find_run(self._look()) or run_el
                continue
            return

    def _language(self, pg: S.Page, p: dict) -> None:
        """If the page has a language drop-down that is not on the language we run, pick the matching option (best effort; the answer is checked by reading it back)."""
        want = cr.norm_language(self.params.get("language") if self.params.get("language") not in (None, "", "statement") else "") or "python"
        combo = next((e for e in pg.els if e.role == "combobox" and re.search(r"(?i)\blang", e.name or "")), None)
        if combo is None:
            return
        cur = cr.norm_language(re.sub(r"[^A-Za-z+#0-9]", "", combo.text or ""))
        if cur == want or (cur and self.params.get("language") in (None, "", "statement")):
            p["lang_said"] = p.get("lang_said") or cur
            return
        opts = [e for e in pg.els if e.role == "option" and e.parent == combo.i and cr.norm_language(re.sub(r"[^A-Za-z+#0-9]", "", e.name or "")) == want]
        if not opts:
            return
        try:
            self._ag.do(BA.Step("language", "select", S.Locator(role="combobox", name=combo.name), value=opts[0].name, optional=True))
            p["lang_said"] = want
        except BA.StepFailed:
            pass

    def _type(self, editor: "S.El", code: str) -> tuple[bool, str]:
        """ONE type job into the editor found by role and name, then the editor is read back: the characters typed must match and its first line must show. A hostile
        placeholder in the code (`{name}`) is passed through unchanged (the engine would otherwise try to fill it)."""
        ag = self._ag
        params = {m: "{" + m + "}" for m in set(re.findall(r"\{([a-z_][a-z0-9_]*)\}", code))}
        try:
            r = ag.do(BA.Step("type_code", "fill", S.Locator(role=editor.role, name=editor.name), value=code, expect={"typed": len(code)}, intent="type the solution into the code editor"), params)
        except BA.StepFailed as e:
            return False, f"the editor did not take the code ({e.why[:100]})"
        after = ag.observe()
        el, _s, _h = S.find(after, S.Locator(role=editor.role, name=editor.name), 0.6) if editor.name else (find_editor(after), 0, "")
        first = next((ln.strip() for ln in code.splitlines() if ln.strip()), "")[:40]
        shown = (el.text if el is not None else "") or ""
        if shown and first and first.lower() not in shown.lower():
            return False, "the editor holds something other than the code that was typed"
        self.m["web"]["typed_checks"] = self.m["web"].get("typed_checks", 0) + 1
        return True, "readback" if shown else "typed_only"

    def _run_on_page(self, run_el: "S.El") -> RunVerdict:
        """Click the page's own Run (a separate control from Submit; the guard would refuse anything else), then read the verdict from the page itself."""
        ag = self._ag
        before = ag.observe()
        before_lines = {_fold(x) for x in before.lines}
        try:
            ag.do(BA.Step("run", "click", S.Locator(role=run_el.role, name=run_el.shown), expect={"noop_ok": True}, intent="run the code on the page's sample tests"))     # (the same verdict twice changes nothing on the page: the click is NOT repeated)
        except BA.StepFailed as e:
            return RunVerdict("unknown", detail=f"the Run button could not be used ({e.why[:60]})")
        end = ag.clock() + self.h.run_wait_s
        polls, last = 0, RunVerdict()
        while True:
            self.check_stop()
            pg = ag.observe()
            polls += 1
            region = [e.name or e.text for e in pg.els if e.role in ("status", "alert") and (e.name or e.text)]
            new = [x for x in pg.lines if _fold(x) not in before_lines]
            last = parse_run_result(region + new)
            if last.state not in ("unknown", "running") and (polls >= 2 or pg.sig() != before.sig()):
                return last
            if ag.clock() >= end:
                return last if last.state != "running" else RunVerdict("unknown", detail="the page was still running when I stopped waiting")
            self.h.sleep(ag.budget.poll_s)


def run_web(job: str, deps: P.Deps, hands: WebHands) -> dict:
    """The whole web job; the same record, budgets, Stop and report as the folder flow. Never raises."""
    j = WebJob(job, deps, hands)
    status = "completed"
    try:
        if not j.params:
            return {"ok": False, "status": "failed", "error": "the job's parameters were not found", "summary": "I could not find what this problem-set job was for."}
        j.m["phase"] = "solving"
        status = j.run_set()
    except P.Stopped:
        status = "stopped"
    except Exception as e:  # noqa: BLE001
        j.event(f"stopped by an error ({type(e).__name__})")
        status = "partial"
    res = P.deliver(j, status)
    if j.m.get("web", {}).get("ended"):
        res["summary"] = (res["summary"] + " " + j.m["web"]["ended"][:160].rstrip(".") + ".")[:600]
    g = getattr(j, "_guard", None)
    res["submit_clicked"] = sum(1 for k, label in (g.sent if g else []) if k in ("click", "check", "xy_click", "drag") and finalizing(label))     # 0 by construction; counted, not assumed
    res["submit_refused"] = j.m.get("web", {}).get("submit_refused", 0)
    return res
