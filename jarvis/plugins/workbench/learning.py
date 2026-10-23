"""Learning a task JARVIS does not know yet: analyse, research, plan, draft, test in the sandbox, refine, store, execute.

Stages of one attempt (all recorded in the learning record, nothing skipped silently):
 1. analyse     what the task needs (files, web, code, messages, money...), which running tools could do it, whether a
                validated workflow or lessons already cover it (familiar / partly familiar / unfamiliar), and which side
                effects would need the owner's approval
 2. research    the research plugin (its question is redacted) or no research, said plainly; sources are graded:
                official documentation, code host, reference, other
 3. plan        steps with dependencies, permissions, uncertainties, expected results and the explicit success criteria
 4. draft       a declarative workflow: written by the owner, by a local model (the plugin's drafter), or reused from the
                library. Code-bearing workflows are marked "needs the lab" (never run on this PC)
 5. test        the sandbox runs it on synthetic cases with explicit checks (``sandbox``); no cases = not validated
 6. refine      Propose-Probe-Compose (``ppc``) when a case fails; adoption only if every case passes
 7. store       a validated workflow becomes a skill (SKILL.md + workflow + cases + validation + provenance); the skill
                text is rated by the skill gate's own static rules (``jarvis/skills/skillgate.py``: reject / high /
                medium / low) and a "reject" or "high" rating stops it; lessons go to the library with their evidence
 8. execute     only on the owner's command, through JARVIS (``jarvis_bridge:delegate_task``): the kernel applies
                its policy, approvals, audit and kill switch to everything that happens
 9. verify      the run's output files are checked with the same success criteria; "done" without a checked file is
                reported as "postconditions not verified"

A found tutorial is not a learned task: status is "validated" only after stage 5/6 passed on cases, otherwise it says
exactly what remains untested.
"""
from __future__ import annotations

import importlib.util
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import library, ppc, safety, sandbox

NEEDS = [
    ("files", r"\b(file|folder|pdf|docx?|excel|xlsx|csv|spreadsheet|document|rename|organi[sz]e)\b"),
    ("web", r"\b(website|web|online|internet|search|scrape|download|url|page|api)\b"),
    ("code", r"\b(code|script|program|python|javascript|automate|app|build|deploy|function|repo)\b"),
    ("text", r"\b(summari[sz]e|extract|parse|clean|format|convert|reformat|translate|list|table|report)\b"),
    ("data", r"\b(total|sum|count|average|group|filter|sort|dedupe|reconcile|ledger|invoice|budget)\b"),
]
SIDE_EFFECTS = [
    ("external_message", r"\b(send|email|e-mail|message|whatsapp|sms|post|tweet|publish|notify|reply)\b"),
    ("purchase", r"\b(buy|purchase|pay|payment|order|subscribe|checkout|book)\b"),
    ("destructive", r"\b(delete|remove|wipe|erase|overwrite|drop|purge|uninstall)\b"),
    ("privileged", r"\b(install|admin|administrator|sudo|registry|firewall|permission|driver|service)\b"),
    ("external_upload", r"\b(upload|share|sync|backup to|google drive|dropbox|onedrive)\b"),
]
APPROVAL_FOR = {"external_message": "sending a message outside", "purchase": "spending money (the kernel refuses money actions)",
                "destructive": "deleting or overwriting", "privileged": "changing the PC's system or permissions",
                "external_upload": "uploading files outside"}
OFFICIAL = re.compile(r"(?i)^https?://(?:[\w-]+\.)*(?:docs\.[\w.-]+|developer\.[\w.-]+|learn\.microsoft\.com|support\.microsoft\.com|"
                      r"python\.org|readthedocs\.io|developers\.google\.com|support\.google\.com|cloud\.google\.com|"
                      r"developer\.mozilla\.org|w3\.org|ietf\.org|apple\.com/support|help\.[\w.-]+)(?:/|$)")
CODEHOST = re.compile(r"(?i)^https?://(?:www\.)?(?:github\.com|gitlab\.com|pypi\.org|npmjs\.com)/")
REFERENCE = re.compile(r"(?i)^https?://(?:[\w-]+\.)?(?:wikipedia\.org|stackoverflow\.com|stackexchange\.com)/")
_URL = re.compile(r"https?://[^\s)\]>\"']+")

Researcher = Callable[[str], dict[str, Any]]
Drafter = Callable[[str, dict[str, Any]], dict[str, Any] | None]


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", t.lower()) if w not in {"this", "that", "with", "from", "into", "have", "make"}}


def analyse(task: str, catalogue: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    t = str(task or "")
    needs = [n for n, rx in NEEDS if re.search(rx, t, re.I)]
    effects = [n for n, rx in SIDE_EFFECTS if re.search(rx, t, re.I)]
    tw = _words(t)
    tools = []
    for e in catalogue or []:
        overlap = len(tw & _words(str(e.get("description", "")) + " " + str(e.get("ref", ""))))
        if overlap:
            tools.append({"ref": e.get("ref"), "overlap": overlap})
    tools.sort(key=lambda x: -x["overlap"])
    from .skillhub import state as _state
    known = [w for w in library.list_workflows() if w.get("current") and len(tw & _words(w.get("task", ""))) >= 3
             and _state(w["name"]).get("status") == "active"]
    lessons = library.lessons(t, 5)
    familiarity = "familiar" if known else ("partly familiar" if lessons or (tools and tools[0]["overlap"] >= 3) else "unfamiliar")
    return {"needs": needs, "side_effects": effects, "approvals": [APPROVAL_FOR[e] for e in effects],
            "tools": tools[:6], "known_workflows": [w["name"] for w in known], "lessons": [x["id"] for x in lessons],
            "familiarity": familiarity}


def grade(url: str) -> str:
    if OFFICIAL.match(url):
        return "official documentation"
    if CODEHOST.match(url):
        return "code host"
    if REFERENCE.match(url):
        return "reference"
    return "other (check before trusting)"


def _repo_sources(task: str, repo_lookup: Callable | None) -> tuple[list[dict[str, Any]], list[str]]:
    """GitHub repositories the task names: their real facts (license, last push, archived, release) as graded sources."""
    if repo_lookup is None:
        return [], []
    from jarvis.plugins.research.sources import repos_in
    out, notes = [], []
    for owner, repo in repos_in(task):
        got = repo_lookup(owner, repo)
        if not got.get("ok"):
            notes.append(f"github.com/{owner}/{repo}: {got.get('why', 'not reachable')}")
            continue
        f = got["facts"]
        out.append({"url": got["url"], "title": got["title"], "grade": "code host", "platform": "github",
                    "retrieved_at": got["retrieved_at"], "facts": f,
                    "excerpt": (f"{got['excerpt']} | license {f['license']}; last push {f['pushed_at']}; archived {f['archived']}; "
                                f"latest release {(f.get('latest_release') or {}).get('tag', 'none')}")[:500]})
    return out, notes


def research(task: str, researcher: Researcher | None, repo_lookup: Callable | None = None) -> dict[str, Any]:
    repos, repo_notes = _repo_sources(task, repo_lookup)
    if researcher is None:
        return {"done": bool(repos), "why": ("" if repos else
                "no research tool is available, so the plan rests on the task text and the library only"),
                "sources": repos, "repo_notes": repo_notes, "official_sources": 0,
                "note": "only the named repositories were looked up; no web search ran" if repos else ""}
    try:
        got = researcher(f"How to: {task}. Official documentation, required steps, tools and limits.") or {}
    except Exception as e:  # noqa: BLE001
        return {"done": False, "why": f"research failed ({type(e).__name__})", "sources": []}
    sources = []
    for s in got.get("sources") or []:
        text = s if isinstance(s, str) else f"{s.get('title', '')} {s.get('url', '')}"
        for url in _URL.findall(str(text))[:1]:
            sources.append({"url": url[:300], "title": safety.safe_for_output(str(text).split(" — ")[0], 160), "grade": grade(url)})
    sources = repos + sources
    official = sum(1 for s in sources if s["grade"] == "official documentation")
    return {"done": True, "sources": sources[:12], "repo_notes": repo_notes, "summary": safety.safe_for_output(got.get("summary", ""), 3000),
            "official_sources": official,
            "note": "" if official else "no official documentation among the sources: treat the steps as unconfirmed"}


def plan(task: str, ana: dict[str, Any], res: dict[str, Any], workflow: dict[str, Any] | None,
         cases: list[dict[str, Any]]) -> dict[str, Any]:
    steps, uncertain = [], []
    if workflow:
        for i, st in enumerate(workflow.get("steps") or [], 1):
            steps.append({"n": i, "does": st.get("note") or st.get("op"), "permission": "sandbox only (no network, no files outside)",
                          "depends_on": [i - 1] if i > 1 else []})
    else:
        uncertain.append("no workflow has been drafted yet")
    if not res.get("done"):
        uncertain.append(res.get("why", "no research"))
    elif not res.get("official_sources"):
        uncertain.append("no official documentation was found")
    if not cases:
        uncertain.append("no example cases: the result cannot be validated")
    if workflow and sandbox.needs_lab(workflow):
        uncertain.append("the workflow needs code, which can only be tested in the lab")
    criteria = [c for case in cases for c in case.get("checks") or []]
    return {"steps": steps, "uncertainties": uncertain,
            "permissions": ["test: sandbox on synthetic data (no network, no access to your files)",
                            "real run: through JARVIS's kernel; it asks you before anything consequential"]
                           + [f"needs your approval: {a}" for a in ana.get("approvals", [])],
            "dependencies": [t["ref"] for t in ana.get("tools", [])[:3]],
            "expected": [f"{c.get('check')} {c.get('file', '')} {c.get('text', c.get('pattern', ''))}".strip() for c in criteria][:20],
            "success_criteria": criteria}


def skill_md(name: str, task: str, workflow: dict[str, Any], res: dict[str, Any], validation: dict[str, Any],
             limitations: list[str]) -> str:
    desc = safety.safe_for_output(task, 200).replace("\n", " ")
    lines = ["---", f"name: {json.dumps(name)}", f"description: {json.dumps(desc, ensure_ascii=False)}",
             f"version: {json.dumps(str(validation.get('version', '1')))}",
             "metadata:", "  source: jarvis-workbench (learned and validated in the sandbox)", "---", "",
             f"# {name}", "", f"Task: {desc}", "", "## Steps", ""]
    for i, st in enumerate(workflow.get("steps") or [], 1):
        params = {k: v for k, v in st.items() if k not in ("op", "_sid", "note")}
        lines.append(f"{i}. `{st.get('op')}` {json.dumps(params, ensure_ascii=False)[:200]}")
    lines += ["", "## Validation", "", f"- status: {validation.get('status')}",
              f"- cases passed: {validation.get('passed')}", f"- checks: {validation.get('checks')}", "",
              "## Sources", ""] + ([f"- {s['title']} {s['url']} ({s['grade']})" for s in res.get("sources") or []] or ["- none"])
    lines += ["", "## Limitations", ""] + [f"- {x}" for x in limitations]
    return "\n".join(lines) + "\n"


_GATE = None


def _skillgate():
    """skillgate.py puts jarvis/ at the front of sys.path when it loads (for its vendored parser). Inside the face that
    would let jarvis/config shadow the face's own config package, so sys.path is put back right after the load."""
    global _GATE
    if _GATE is not None:
        return _GATE
    import sys
    path = Path(__file__).resolve().parents[3] / "jarvis" / "skills" / "skillgate.py"
    spec = importlib.util.spec_from_file_location("jarvis_skillgate_for_workbench", path)
    mod = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
    _GATE = mod
    return mod


def gate(name: str, md: str) -> dict[str, Any]:
    """The skill gate's own static rating of the skill text. Fails closed: if the gate cannot run, the rating is 'high'."""
    try:
        sg = _skillgate()
        fm, body = sg.read_frontmatter(md)
        mapping, _ = sg.tools_of(body)
        r = sg.rate(name, [("SKILL.md", md.encode("utf-8"))], [], fm or {}, mapping)
        return {"tier": r["tier"], "reasons": r["reasons"], "data_sensitivity": r["data_sensitivity"]}
    except Exception as e:  # noqa: BLE001
        return {"tier": "high", "reasons": [f"the skill gate could not run ({type(e).__name__}); not adopted"]}


def _example_brief(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """What a drafter must know to fit the test cases: input names, output names, one example (synthetic, owner-given)."""
    inputs = sorted({str(k) for c in cases for k in (c.get("inputs") or {})})
    outputs = sorted({str(ch.get("file")) for c in cases for ch in c.get("checks") or [] if ch.get("file")})
    first = cases[0] if cases else {}
    return {"input_names": inputs, "output_names": outputs,
            "example_inputs": {k: str(v)[:600] for k, v in (first.get("inputs") or {}).items()},
            "example_checks": [c for c in first.get("checks") or []][:4]}


def learn(task: str, cases: list[dict[str, Any]] | None = None, *, catalogue: list[dict[str, Any]] | None = None,
          researcher: Researcher | None = None, drafter: Drafter | None = None, workflow: dict[str, Any] | None = None,
          proposer: Any = None, k: int = 2, mode: str = "parallel", name: str = "",
          repo_lookup: Callable | None = None) -> dict[str, Any]:
    task = safety.safe_for_output(task, 1500).strip()
    if len(task) < 8:
        raise ValueError("Describe the task in a sentence.")
    if safety.addressed_to_ai(task):
        raise ValueError("The task text reads like an instruction to override JARVIS's rules; it was not learned.")
    cases = [c for c in (cases or []) if isinstance(c, dict)][:20]
    rec: dict[str, Any] = {"task": task, "started": round(time.time(), 3), "stages": {}}
    ana = analyse(task, catalogue)
    rec["stages"]["analyse"] = ana
    res = research(task, researcher, repo_lookup)
    rec["stages"]["research"] = res
    wf = workflow
    origin = "given by the owner" if workflow else ""
    if wf is None and ana["known_workflows"]:
        try:
            got = library.load_workflow(ana["known_workflows"][0])
            wf, origin = got["workflow"], f"library {got['name']} {got['version']}"
            cases = cases or got.get("cases") or []
        except library.LibraryError:
            wf = None
    if wf is None and drafter is not None:
        try:
            wf = drafter(task, {"research": res, "analysis": ana, "lessons": library.lessons(task, 5), "examples": _example_brief(cases)})
            origin = "drafted by a local model"
        except Exception as e:  # noqa: BLE001
            rec["stages"]["draft_error"] = type(e).__name__
            wf = None
    rec["stages"]["plan"] = plan(task, ana, res, wf, cases)
    if wf is None:
        rec.update(status="needs a workflow", why=("no workflow could be drafted: give an example workflow, or run with a "
                                                   "local model so JARVIS can draft one"))
        return _finish(rec)
    rec["workflow_origin"] = origin
    problems = sandbox.validate_workflow(wf)
    redrafts = []
    while problems and drafter is not None and origin == "drafted by a local model" and len(redrafts) < 2 \
            and not sandbox.needs_lab(wf):
        # Propose again with the exact errors: a draft that does not fit the closed operation list is revised, not run
        redrafts.append({"problems": problems, "workflow": wf})
        try:
            again = drafter(task, {"research": res, "analysis": ana, "lessons": library.lessons(task, 5),
                                   "previous": wf, "problems": problems, "examples": _example_brief(cases)})
        except Exception as e:  # noqa: BLE001
            rec["stages"]["draft_error"] = type(e).__name__
            break
        if not isinstance(again, dict):
            break
        wf = again
        problems = sandbox.validate_workflow(wf)
    if redrafts:
        rec["stages"]["redrafts"] = redrafts
        rec["stages"]["plan"] = plan(task, ana, res, wf, cases)
    if sandbox.needs_lab(wf):
        rec.update(status="needs the lab", why="the workflow needs code; it was not run here. Test it in the lab (kernel-approved).",
                   workflow=wf)
        return _finish(rec)
    if problems:
        rec.update(status="invalid workflow", why="; ".join(problems), workflow=wf)
        return _finish(rec)
    if not cases:
        rec.update(status="needs examples", why="give at least one example input with the expected result (checks); "
                                                  "without one the workflow cannot be validated", workflow=wf)
        return _finish(rec)
    refined = ppc.refine(wf, cases, proposer=proposer, k=k, mode=mode, cue=task[:200])
    rec["stages"]["test_and_refine"] = {k2: refined[k2] for k2 in ("status", "why", "best", "base_probe", "best_probe",
                                                                   "table", "ledger")}
    if refined["adopted"] is None:
        rec.update(status="not validated", why=refined["why"] or "no candidate passed every case", workflow=wf)
        return _finish(rec)
    name = name or re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:40].strip("-") or "workflow"
    validation = {"status": "validated", "all_cases_passed": True, "passed": refined["best_probe"]["passed"],
                  "checks": refined["best_probe"]["checks"], "cases": len(cases), "at": round(time.time(), 3)}
    limitations = ["Validated on the synthetic cases listed in cases.json only.",
                   "The sandbox language is text and table operations; real files are read by JARVIS when it runs.",
                   ] + ([res["note"]] if res.get("note") else []) + rec["stages"]["plan"]["uncertainties"]
    md = skill_md(name, task, refined["adopted"], res, dict(validation, version="1"), limitations)
    g = gate(name, md)
    rec["stages"]["skill_gate"] = g
    if g["tier"] in ("reject", "high"):
        rec.update(status="not adopted", why=f"the skill gate rated it {g['tier']}: {'; '.join(g['reasons'])[:300]}",
                   workflow=refined["adopted"])
        return _finish(rec)
    saved = library.save_workflow(name, refined["adopted"], cases, md, validation,
                                  {"task": task, "origin": origin, "sources": res.get("sources", []), "gate": g,
                                   "ppc": refined["ledger"], "harness2_method": "Propose-Probe-Compose (adapted)"})
    stored = []
    for les in refined["lessons"]:
        try:
            stored.append(library.add_lesson(les, sources=[s["url"] for s in res.get("sources", [])], version=saved["version"],
                                             limitations="; ".join(limitations[:2]), validation=validation)["id"])
        except library.LibraryError as e:
            rec.setdefault("lessons_refused", []).append(str(e))
    if saved.get("duplicate_of"):
        rec["duplicate_of"] = saved["duplicate_of"]
    rec.update(status="validated", workflow_name=saved["name"], workflow_version=saved["version"], lessons=stored,
               workflow=refined["adopted"],
               why="every case passed in the sandbox; a real run still has to be checked after it runs")
    return _finish(rec)


def _finish(rec: dict[str, Any]) -> dict[str, Any]:
    rec["finished"] = round(time.time(), 3)
    rec["id"] = library.save_record(rec)
    return rec


# ---------------------------------------------------------------- real runs (through the kernel) and their checks
def execution_text(wf_record: dict[str, Any], input_note: str, limit: int = 2000) -> str:
    """What JARVIS is asked to do: the validated steps in words, where the input is, and the checks the output must pass."""
    wf = wf_record["workflow"]
    checks = [c for case in wf_record.get("cases") or [] for c in case.get("checks") or []]
    names = sorted({c.get("file") for c in checks if c.get("file")})
    steps = "; ".join(f"{st.get('op')} {json.dumps({k: v for k, v in st.items() if k not in ('op', 'note')}, ensure_ascii=False)}"
                      for st in wf.get("steps") or [])
    text = (f"Run the validated workflow '{wf_record['name']}' ({wf_record['version']}). Input: "
            f"{safety.safe_for_output(input_note, 300)}. Steps: {steps}. Save the result as {', '.join(names) or 'the output file'} "
            "in the outputs folder and reply with its full path. Do not send, upload or delete anything; ask before anything "
            "consequential.")
    return text if len(text) <= limit else text[:limit - 30].rsplit(" ", 1)[0] + " ... (steps cut)"


def verify_outputs(wf_record: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
    """Postconditions: the workflow's own checks (the ones that are not about a specific example's values) on real output."""
    checks = []
    for case in wf_record.get("cases") or []:
        for c in case.get("checks") or []:
            # shape checks hold for any input; equals / contains describe one example's values and are not applied to new data
            if c.get("check") in ("file_exists", "csv_columns", "json_valid", "line_count", "not_contains", "regex") and c not in checks:
                checks.append(c)
    results = [{"check": c, "ok": ok, "why": why} for c in checks for ok, why in [sandbox.check(c, files, 0.0)]]
    if not checks:
        return {"verified": False, "why": "the workflow has no checks that apply to new data", "results": []}
    return {"verified": all(r["ok"] for r in results), "results": results,
            "why": "" if all(r["ok"] for r in results) else "; ".join(r["why"] for r in results if not r["ok"])}
