"""Workbench operations, without the plugin runtime (so every path is tested with no model and no the face host).

The plugin (``__init__.py``) wraps these in entries and supplies the parts that need the host: the local model (only if
verified local), the research plugin, and JARVIS through jarvis_bridge. Every function returns plain data.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from . import deliver, ingest, learning, library, planning, prompts, requirements, safety
from .projects import Project, ProjectError, create, list_projects, read_json, write_json


# ---------------------------------------------------------------- client projects
def create_project(name: str, client: str = "", owner_names: list[str] | None = None) -> dict[str, Any]:
    p = create(name, client, owner_names)
    return {"project_id": p.id, "folder": str(p.dir), "policy": p.policy()}


def add_files(project_id: str, paths: list[str], languages: dict[str, str] | None = None) -> dict[str, Any]:
    p = _open(project_id)
    if not isinstance(paths, list) or not paths:
        raise ProjectError("Give one or more full file paths.")
    return ingest.add_files(p, [str(x) for x in paths][:50], languages=languages)


def _open(project_id: str) -> Project:
    p = Project(project_id)
    if not p.exists():
        raise ProjectError("No such project.")
    return p


def analyze(project_id: str, transcriber=None, suggest: Callable | None = None,
            running_plugins: set[str] | None = None) -> dict[str, Any]:
    """Extract every source, build requirements (with evidence), conflicts, questions, the plan and the task prompts."""
    p = _open(project_id)
    ext = ingest.extract_all(p, transcriber=transcriber)
    return _rebuild(p, ext, suggest, running_plugins)


def _rebuild(p: Project, ext: dict[str, Any] | None, suggest: Callable | None, running_plugins: set[str] | None) -> dict[str, Any]:
    segs = ingest.segments(p)
    model_items, model_note = [], ""
    if suggest is not None:
        ok, why = safety.egress_allowed(p.policy(), _local_url())
        if ok:
            try:
                model_items = suggest(segs) or []
            except Exception as e:  # noqa: BLE001
                model_note = f"the local model's suggestions failed ({type(e).__name__}); rules only"
        else:
            model_note = f"model suggestions not used: {why}"
    answers = read_json(p.path("answers.json"), {}) or {}
    req = requirements.extract(segs, p.meta().get("owner_names") or [], answers, model_items)
    if model_note:
        req["limits"].append(model_note)
    write_json(p.path("requirements.json"), req)
    p.path("requirements.md").write_text(requirements.to_markdown(req, p.meta().get("name", "")), encoding="utf-8")
    plan = planning.make_plan(req, running_plugins)
    prompt_rows = deliver.make_task_prompts(p, req, plan)
    write_json(p.path("plan.json"), plan)
    p.path("plan.md").write_text(planning.to_markdown(plan), encoding="utf-8")
    p.log("analyzed", items=len(req["items"]), conflicts=len(req["conflicts"]))
    return {"project_id": p.id, "extraction": ext, "stats": req["stats"],
            "deliverables": [{"id": d["id"], "type": d["type"], "requirements": d["requirements"]} for d in req["deliverables"]],
            "conflicts": [{"id": c["id"], "topic": c["topic"], "values": c["values"], "status": c["status"]} for c in req["conflicts"]],
            "open_questions": [{"id": q["id"], "text": q["text"]} for q in req["questions"] if q["status"] == "open"],
            "ignored_instructions": len(req["ignored_instructions"]), "tasks": len(plan["tasks"]), "prompts": prompt_rows,
            "files": ["requirements.md", "plan.md"]}


def _local_url() -> str:
    from .localmodel import base_url
    return base_url()


def answer_questions(project_id: str, answers: dict[str, str], suggest: Callable | None = None,
                     running_plugins: set[str] | None = None) -> dict[str, Any]:
    p = _open(project_id)
    if not isinstance(answers, dict) or not answers:
        raise ProjectError("Give answers as {question id: answer}.")
    req = read_json(p.path("requirements.json"), None)
    if not isinstance(req, dict):
        raise ProjectError("Analyse the project first.")
    keys = {q["id"]: q["key"] for q in req["questions"]}
    saved = read_json(p.path("answers.json"), {}) or {}
    unknown = []
    for qid, ans in answers.items():
        if qid not in keys:
            unknown.append(qid)
            continue
        saved[keys[qid]] = safety.safe_for_output(ans, 500)
    write_json(p.path("answers.json"), saved)
    out = _rebuild(p, None, suggest, running_plugins)
    out["unknown_question_ids"] = unknown
    return out


def build(project_id: str, writer: Callable | None, writer_note: str = "") -> dict[str, Any]:
    p = _open(project_id)
    req = read_json(p.path("requirements.json"), None)
    plan = read_json(p.path("plan.json"), None)
    if not isinstance(req, dict) or not isinstance(plan, dict):
        raise ProjectError("Analyse the project first.")
    if writer is not None:
        ok, why = safety.egress_allowed(p.policy(), _local_url())
        if not ok:
            writer, writer_note = None, why
    built = deliver.build(p, req, plan, writer, writer_note)
    val = deliver.validate(p, req, plan)
    made = [x for x in val["deliverables"] if x["verdict"] in ("verified", "needs review")]
    overall = ("blocked" if not made else "verified" if val["verified"] == len(val["deliverables"]) else
               "needs_review" if val["verified"] == 0 else "partly_verified")
    return {"project_id": p.id, "status": overall, "files": [f["file"] for f in built["files"]] + ["SUMMARY.md", "validation.json"],
            "deliverables": [{k: x.get(k) for k in ("deliverable", "type", "verdict", "file", "problems", "why")}
                             for x in val["deliverables"]],
            "verified": val["verified"], "needs_review": val["needs_review"], "folder": str(p.path("deliverables"))}


def status(project_id: str) -> dict[str, Any]:
    p = _open(project_id)
    req = read_json(p.path("requirements.json"), {}) or {}
    val = read_json(p.path("deliverables", "validation.json"), {}) or {}
    return {"project_id": p.id, "name": p.meta().get("name"), "sources": [{k: r.get(k) for k in ("id", "original_name", "kind")}
                                                                       for r in p.manifest()],
            "stats": req.get("stats"), "open_questions": [{"id": q["id"], "text": q["text"]}
                                                          for q in req.get("questions", []) if q["status"] == "open"],
            "deliverables": [{k: x.get(k) for k in ("deliverable", "type", "verdict", "file")} for x in val.get("deliverables", [])],
            "prompts": prompts.list_prompts(p), "policy": p.policy()}


def set_provider_policy(project_id: str, mode: str, providers: list[str] | None, confirmed_by_owner: bool) -> dict[str, Any]:
    """The owner's choice for one project. External providers need confirmed_by_owner=True (the panel's own button)."""
    p = _open(project_id)
    if mode not in safety.POLICIES:
        raise ProjectError(f"mode must be one of {', '.join(safety.POLICIES)}.")
    if mode == safety.EXTERNAL_ALLOWED and confirmed_by_owner is not True:
        raise ProjectError("Allowing an external AI provider for client material needs the owner's explicit confirmation.")
    meta = p.meta()
    meta["policy"] = {"ai_providers": mode, "authorised_providers": [safety.safe_for_output(x, 40) for x in providers or []][:5]
                      if mode == safety.EXTERNAL_ALLOWED else [],
                      "authorised_by_owner": mode == safety.EXTERNAL_ALLOWED, "changed": round(time.time(), 3)}
    write_json(p.path("project.json"), meta)
    p.log("policy_changed", mode=mode)
    return {"project_id": p.id, "policy": meta["policy"]}


# ---------------------------------------------------------------- prompt generator
def _proj_or_none(project_id: str | None) -> Project | None:
    return _open(project_id) if project_id else None


def project_context(p: Project, max_items: int = 60) -> dict[str, Any]:
    """Requirement items (with where they came from) and open questions, for a prompt about this project."""
    req = read_json(p.path("requirements.json"), {}) or {}
    reqs = [{"id": it["id"], "kind": it["kind"], "text": it["text"], "where": it["evidence"][0]["where"]}
            for it in req.get("items", []) if it["kind"] != "question" and it["role"] != "owner"][:max_items]
    return {"requirements": reqs, "open_questions": [q["text"] for q in req.get("questions", []) if q["status"] == "open"]}


def generate_prompt(spec: dict[str, Any], source_paths: list[str] | None = None, save: bool = True) -> dict[str, Any]:
    spec = dict(spec or {})
    project = _proj_or_none(spec.get("project_id"))
    if project is not None:
        ctx = project_context(project)
        spec.setdefault("requirements", ctx["requirements"])
        spec.setdefault("open_questions", ctx["open_questions"])
    sources = list(spec.get("sources") or [])
    for path in (source_paths or [])[:10]:
        from jarvis.plugins.doc_tools.core import DocError, read_document
        try:
            doc = read_document(str(path), 20000)
            sources.append({"label": doc["name"], "text": doc["text"]})
        except DocError as e:
            sources.append({"label": Path(str(path)).name[:60], "text": f"(not read: {e})"})
    spec["sources"] = sources
    built = prompts.build(spec, project.policy() if project else None)
    out = {"status": built["status"], "text": built["text"], "compact": built["compact"], "withheld": built["withheld"],
           "sections": list(prompts.SECTIONS), "chars": len(built["text"])}
    if save:
        out.update(prompts.save_new(built, project))
    return out


def get_prompt(prompt_id: str, project_id: str = "", version: int | None = None) -> dict[str, Any]:
    got = prompts.load(prompt_id, _proj_or_none(project_id), version)
    pr = got["prompt"]
    return {"id": prompt_id, "title": got["index"].get("title"), "version": pr["version"], "status": got["index"].get("status"),
            "target": pr.get("target_info"), "provenance": pr.get("provenance"), "text": pr["text"],
            "compact": pr["compact"], "how": pr.get("how"), "versions": got["index"].get("versions"),
            "executions": got["index"].get("executions") or [], "kind": pr["spec"]["kind"]}


def edit_prompt(prompt_id: str, project_id: str = "", text: str | None = None, sections: dict[str, Any] | None = None) -> dict[str, Any]:
    return prompts.edit(prompt_id, _proj_or_none(project_id), text=text, sections=sections)


def regenerate_prompt(prompt_id: str, project_id: str = "", changes: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _proj_or_none(project_id)
    return prompts.regenerate(prompt_id, project, changes, project.policy() if project else None)


def execution_request(prompt_id: str, project_id: str = "", via: str = "auto") -> dict[str, Any]:
    """What to send and where. The plugin performs the call; this decides route and text (never more than 2,000 chars)."""
    got = prompts.load(prompt_id, _proj_or_none(project_id))
    pr = got["prompt"]
    kind = pr["spec"]["kind"]
    if via not in ("auto", "main_agent", "jarvis"):
        raise prompts.PromptError("via must be auto, main_agent or jarvis.")
    tinfo = pr.get("target_info") or prompts.target_info(pr["spec"])
    if tinfo["route"] == "copy only" and via == "auto":
        raise prompts.PromptError(f"This prompt targets '{tinfo['runtime']}', which Workbench cannot run: copy or export it, "
                                  "or choose JARVIS or main_agent explicitly.")
    if via == "auto":
        route = tinfo["route"] if tinfo["route"] in ("jarvis", "main_agent") else "main_agent"
        if kind in ("coding", "multi_agent") or project_id:
            route = "jarvis"
    else:
        route = via
    return {"route": route, "text": pr["compact"][:prompts.EXEC_LIMIT], "version": pr["version"], "target": tinfo,
            "budget": {"submit_timeout_s": 30, "run_limits": ("main_agent: MAIN_AGENT_STEP_TIMEOUT per step and its overall limit"
                                                              if route == "main_agent" else "the kernel's job limits and approvals")}}


def rename_prompt(prompt_id: str, project_id: str, title: str) -> dict[str, Any]:
    return prompts.rename(prompt_id, _proj_or_none(project_id), title)


def duplicate_prompt(prompt_id: str, project_id: str = "") -> dict[str, Any]:
    return prompts.duplicate(prompt_id, _proj_or_none(project_id))


def compare_prompt(prompt_id: str, project_id: str = "", a: int = 1, b: int | None = None) -> dict[str, Any]:
    project = _proj_or_none(project_id)
    cur = prompts.load(prompt_id, project)["index"].get("current", 1)
    return prompts.compare(prompt_id, project, int(a), int(b or cur))


def export_prompt(prompt_id: str, project_id: str = "", version: int | None = None) -> dict[str, Any]:
    return prompts.export(prompt_id, _proj_or_none(project_id), version)


# ---------------------------------------------------------------- learning
def learn_task(task: str, examples: list[dict[str, Any]] | None = None, workflow: dict[str, Any] | None = None, *,
               catalogue=None, researcher=None, drafter=None, proposer=None, k: int = 2, mode: str = "parallel",
               name: str = "", repo_lookup=None) -> dict[str, Any]:
    rec = learning.learn(task, examples, catalogue=catalogue, researcher=researcher, drafter=drafter, workflow=workflow,
                         proposer=proposer, k=k, mode=mode, name=name, repo_lookup=repo_lookup)
    ttr = rec["stages"].get("test_and_refine") or {}
    return {"record": rec["id"], "status": rec["status"], "why": rec.get("why", ""),
            "familiarity": rec["stages"]["analyse"]["familiarity"], "approvals_needed": rec["stages"]["analyse"]["approvals"],
            "research": {k: rec["stages"]["research"].get(k) for k in ("done", "why", "official_sources", "note", "repo_notes")},
            "sources": rec["stages"]["research"].get("sources", [])[:6], "plan": rec["stages"]["plan"],
            "probe": {"base": ttr.get("base_probe"), "best": ttr.get("best_probe"), "ledger": ttr.get("ledger")},
            "workflow": rec.get("workflow_name"), "version": rec.get("workflow_version"), "lessons": rec.get("lessons", []),
            "skill_gate": rec["stages"].get("skill_gate")}


def workflow_run_text(name: str, input_note: str) -> dict[str, Any]:
    from . import skillhub
    wf = skillhub.runnable(name)                      # active + validated, else refused with the reason
    skillhub.record_use(name, "real", None, "submitted to JARVIS; unverified until its output is checked")
    return {"text": learning.execution_text(wf, input_note), "name": wf["name"], "version": wf["version"]}


def reuse_skill(name: str, inputs: dict[str, str]) -> dict[str, Any]:
    """Run a validated skill in the sandbox on NEW input (no real files, no side effects) and apply its data-independent
    checks. This is how a learned skill is shown to generalise before anyone runs it for real."""
    from . import sandbox, skillhub
    wf = skillhub.runnable(name)
    if not isinstance(inputs, dict) or not inputs:
        raise library.LibraryError("Give the new input as {file name: text}.")
    r = sandbox.run_case(wf["workflow"], {"inputs": {str(k)[:60]: str(v)[:200000] for k, v in inputs.items()}})
    checked = learning.verify_outputs(wf, r.get("outputs") or {}) if r.get("ok") else {"verified": False, "why": r.get("error")}
    skillhub.record_use(name, "sandbox", bool(r.get("ok") and checked.get("verified")), checked.get("why", ""))
    return {"name": name, "version": wf["version"], "ok": bool(r.get("ok")), "error": r.get("error"),
            "outputs": {k: v[:4000] for k, v in (r.get("outputs") or {}).items()}, "checks": checked}


def verify_run_outputs(name: str, output_paths: list[str]) -> dict[str, Any]:
    out = _verify_run_outputs(name, output_paths)
    from . import skillhub
    skillhub.record_use(name, "real", bool(out.get("verified")), out.get("why", ""))
    return out


def _verify_run_outputs(name: str, output_paths: list[str]) -> dict[str, Any]:
    """Read the files a run produced (Document Tools' read rules) and apply the workflow's checks to them."""
    wf = library.load_workflow(name)
    files: dict[str, str] = {}
    refused = []
    for pth in (output_paths or [])[:10]:
        try:
            real = ingest.check_readable(pth)
            files[real.name] = real.read_text(encoding="utf-8", errors="replace")[:2_000_000]
        except (ProjectError, OSError) as e:
            refused.append({"file": Path(str(pth)).name[:80], "why": str(e)[:200]})
    # the checks name the file the sandbox wrote; a real run may use a dated name: match by extension when unique
    want = {c.get("file") for case in wf.get("cases") or [] for c in case.get("checks") or [] if c.get("file")}
    for w in want:
        if w and w not in files:
            same = [n for n in files if Path(n).suffix == Path(w).suffix]
            if len(same) == 1:
                files[w] = files[same[0]]
    out = learning.verify_outputs(wf, files)
    out["refused"] = refused
    if not files:
        out.update(verified=False, why="no output file could be read, so the run's postconditions are not verified")
    return out


def briefing(jarvis: dict[str, Any] | None = None, max_items: int = 30) -> dict[str, Any]:
    """A briefing from records only. Each item: what, source (the record it was read from), and whether it is a FACT read
    from a record or a SUGGESTION JARVIS makes from it. Nothing is invented; a source that could not be read says so.
    ``jarvis`` is what jarvis_bridge reported (approvals waiting, status), passed in by the plugin; None = not checked."""
    import time as _t

    from . import prompts as _prompts
    from . import skillhub
    from .projects import list_projects
    facts: list[dict[str, Any]] = []
    sugg: list[dict[str, Any]] = []
    unread: list[str] = []
    if jarvis is None:
        unread.append("JARVIS (approvals, status): not checked")
    elif jarvis.get("error"):
        unread.append(f"JARVIS: {jarvis['error']}")
    else:
        n = int(jarvis.get("approvals_waiting") or 0)
        facts.append({"what": f"{n} JARVIS approval(s) waiting", "source": "jarvis_bridge:pending_approvals"})
        if jarvis.get("ready") is False:
            facts.append({"what": f"JARVIS is not ready: {jarvis.get('message', '')[:120]}", "source": "jarvis_bridge:jarvis_status"})
        if n:
            sugg.append({"what": "Review the waiting approvals in the JARVIS console first", "because": "approvals block tasks"})
    for pr in list_projects():
        p = Project(pr["id"])
        src = f"workbench project {pr['id']}"
        req = read_json(p.path("requirements.json"), None)
        if not isinstance(req, dict):
            facts.append({"what": f"{pr['name']}: not analysed yet ({pr['sources']} file(s))", "source": src})
            continue
        open_q = [q for q in req.get("questions", []) if q["status"] == "open"]
        conf = [c for c in req.get("conflicts", []) if c["status"] == "open"]
        if open_q:
            facts.append({"what": f"{pr['name']}: {len(open_q)} open question(s), e.g. {open_q[0]['text'][:100]}",
                          "source": f"{src} requirements.json"})
        if conf:
            facts.append({"what": f"{pr['name']}: {len(conf)} unresolved conflict(s) ({', '.join(c['topic'] for c in conf[:3])})",
                          "source": f"{src} requirements.json"})
        for d in req.get("deliverables", []):
            dl = next((x["text"] for x in d.get("deadlines") or []), "")
            if dl:
                facts.append({"what": f"{pr['name']}: {d['type'].replace('_', ' ')} due {dl} (as the client said it)",
                              "source": f"{src} {d['deadlines'][0]['item']}"})
        val = read_json(p.path("deliverables", "validation.json"), None)
        if isinstance(val, dict):
            nr = [x for x in val.get("deliverables", []) if x.get("verdict") == "needs review"]
            if nr:
                facts.append({"what": f"{pr['name']}: {len(nr)} deliverable(s) need review", "source": f"{src} validation.json"})
        if open_q:
            sugg.append({"what": f"Answer {pr['name']}'s open questions, then rebuild its deliverables",
                         "because": "deliverables made while questions are open stay 'needs review'"})
        for pmt in _prompts.list_prompts(p):
            if pmt.get("status") == "submitted for execution":
                facts.append({"what": f"{pr['name']}: prompt {pmt['id']} was submitted and its result is not verified yet",
                              "source": f"{src} prompts/{pmt['id']}/index.json"})
    for pmt in _prompts.list_prompts(None):
        if pmt.get("status") == "submitted for execution":
            facts.append({"what": f"Prompt {pmt['id']} was submitted and its result is not verified yet",
                          "source": f"prompts/{pmt['id']}/index.json"})
    for sk in skillhub.search("", 200):
        if sk.get("kind") == "learned" and sk.get("status") in ("quarantined", "disabled"):
            facts.append({"what": f"Skill {sk['name']} is {sk['status']}", "source": f"library/workflows/{sk['name']}/state.json"})
            if sk["status"] == "quarantined":
                sugg.append({"what": f"Fix or roll back skill {sk['name']}", "because": "it failed its regression cases"})
    text = ["Briefing (" + _t.strftime("%Y-%m-%d %H:%M") + ")", "Facts (read from records):"]
    text += [f"- {f['what']} [{f['source']}]" for f in facts[:max_items]] or ["- nothing pending in the records"]
    if sugg:
        text += ["Suggestions (JARVIS's, not facts):"] + [f"- {x['what']} ({x['because']})" for x in sugg[:10]]
    if unread:
        text += ["Not read:"] + [f"- {u}" for u in unread]
    return {"facts": facts[:max_items], "suggestions": sugg[:10], "not_read": unread, "text": "\n".join(text),
            "generated": round(_t.time(), 3)}


__all__ = ["create_project", "add_files", "analyze", "answer_questions", "build", "status", "set_provider_policy",
           "generate_prompt", "get_prompt", "edit_prompt", "regenerate_prompt", "execution_request", "learn_task",
           "workflow_run_text", "verify_run_outputs", "list_projects"]
