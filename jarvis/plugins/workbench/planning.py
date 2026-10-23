"""A project plan from the requirements: tasks, dependencies, milestones, and the JARVIS tool or agent for each task.

Rules (deterministic, tested):
- An open question that blocks a deliverable becomes a "clarify" task for the owner, and the deliverable's draft depends on it.
- Each deliverable gets draft -> validate -> hand over. Research comes first where the type needs it.
- Tools are the existing JARVIS ones. A task whose tool is not running right now is marked ``tool_available: false``.
- What needs the owner's approval is marked, with the reason: anything sent outside (a message to the client, a web
  search query), code run by the lab agents, purchases. Nothing in the plan runs by itself.
- Types JARVIS cannot make itself (a logo image, a video, print artwork) get a design brief instead, and the plan says so.
"""
from __future__ import annotations

import time
from typing import Any

# deliverable type -> (what JARVIS produces, tool, format, needs research, note)
PRODUCERS: dict[str, dict[str, Any]] = {
    "report": {"make": "document", "tool": "workbench:build_deliverables (doc_tools writer)", "format": "docx", "research": False},
    "research": {"make": "research report", "tool": "research:research_topic", "format": "pdf or docx", "research": True,
                 "approval": "sends a web search query outside the PC (the research plugin redacts it first)"},
    "presentation": {"make": "presentation outline, then deck", "tool": "deliverables:make_presentation",
                     "format": "pptx", "research": False,
                     "note": "the deck builder (OfficeCLI + LibreOffice) runs on the Windows laptop; elsewhere an outline document is made"},
    "spreadsheet": {"make": "spreadsheet", "tool": "workbench:build_deliverables (CSV)", "format": "csv", "research": False},
    "content_calendar": {"make": "content calendar", "tool": "workbench:build_deliverables (CSV)", "format": "csv", "research": False},
    "logo": {"make": "design brief", "tool": "workbench:build_deliverables (doc_tools writer)", "format": "docx", "research": False,
             "note": "JARVIS has no image-generation tool: the brief is for a designer or a design tool"},
    "print_design": {"make": "design brief and copy", "tool": "workbench:build_deliverables (doc_tools writer)", "format": "docx",
                     "research": False, "note": "artwork itself needs a designer or a design tool"},
    "social_posts": {"make": "post copy and briefs", "tool": "workbench:build_deliverables (doc_tools writer)", "format": "docx",
                     "research": False, "note": "images for the posts need a designer or a design tool"},
    "video": {"make": "video script and shot list", "tool": "workbench:build_deliverables (doc_tools writer)", "format": "docx",
              "research": False, "note": "JARVIS does not edit video"},
    "website": {"make": "website (code)", "tool": "jarvis_bridge:delegate_task (lab agents)", "format": "code repository",
                "research": False, "approval": "the lab agents write and run code; the kernel asks for approval"},
    "app_or_code": {"make": "software (code)", "tool": "jarvis_bridge:delegate_task (lab agents)", "format": "code repository",
                    "research": False, "approval": "the lab agents write and run code; the kernel asks for approval"},
    "email_or_message": {"make": "message draft", "tool": "workbench:build_deliverables (doc_tools writer)", "format": "md",
                         "research": False, "approval": "sending a message to anyone needs your approval; JARVIS only drafts it"},
}
PROMPT_KIND = {"website": "coding", "app_or_code": "coding", "research": "research"}


def _available(tool: str, running: set[str] | None) -> bool:
    if running is None:
        return True
    plugin = tool.split(":", 1)[0]
    return plugin in running or plugin == "workbench"


def make_plan(req: dict[str, Any], running_plugins: set[str] | None = None) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    items = {it["id"]: it for it in req.get("items", [])}

    def add(**t: Any) -> str:
        t["id"] = f"T{len(tasks) + 1}"
        t.setdefault("depends_on", [])
        t.setdefault("approval", "")
        tasks.append(t)
        return t["id"]

    clarify_for: dict[str, list[str]] = {}
    open_q = [q for q in req.get("questions", []) if q["status"] == "open"]
    for q in open_q:
        tid = add(kind="clarify", title=f"Answer: {q['text'][:140]}", who="you", tool="workbench:answer_questions",
                  question=q["id"], deliverable=",".join(q["blocks"]), tool_available=True)
        for d in q["blocks"]:
            clarify_for.setdefault(d, []).append(tid)
    client_q = [q for q in open_q if q["key"] != "owner_identity" and not q["key"].startswith("audio_language")]
    if client_q:
        add(kind="message_draft", title="Draft the questions for the client (you send it)", who="JARVIS drafts, you send",
            tool="workbench:build_deliverables", deliverable="", tool_available=True,
            approval="a message to the client is sent only by you")
    milestones = []
    for d in req.get("deliverables", []):
        prod = PRODUCERS.get(d["type"], PRODUCERS["report"])
        deps = list(clarify_for.get(d["id"], []))
        if prod.get("research"):
            r = add(kind="research", title=f"Research for the {d['type'].replace('_', ' ')}", who="research plugin",
                    tool="research:research_topic", deliverable=d["id"], depends_on=deps,
                    tool_available=_available("research:research_topic", running_plugins),
                    approval=PRODUCERS["research"]["approval"])
            deps = [r]
        draft = add(kind="draft", title=f"Make the {prod['make']} ({d['type'].replace('_', ' ')})", who="JARVIS",
                    tool=prod["tool"], format=prod["format"], deliverable=d["id"], depends_on=deps,
                    requirements=d["requirements"] + d.get("applies_general", []),
                    tool_available=_available(prod["tool"], running_plugins), approval=prod.get("approval", ""),
                    note=prod.get("note", ""), prompt_kind=PROMPT_KIND.get(d["type"], "client_deliverable"))
        check = add(kind="validate", title=f"Check the {d['type'].replace('_', ' ')} against requirements "
                    f"{', '.join(d['requirements'][:8])}", who="JARVIS", tool="workbench:validate_deliverables",
                    deliverable=d["id"], depends_on=[draft], tool_available=True)
        add(kind="handover", title=f"Review and send the {d['type'].replace('_', ' ')} to the client", who="you",
            tool="", deliverable=d["id"], depends_on=[check], tool_available=True,
            approval="sending to the client is yours")
        deadline = next((x["text"] for x in d["deadlines"]), "")
        milestones.append({"deliverable": d["id"], "type": d["type"], "due": deadline or "no date in the sources",
                           "promised": [x["text"] for x in d.get("commitments") or []],
                           "evidence": [x["item"] for x in d["deadlines"]] if deadline else []})
    for it in items.values():
        if it["kind"] == "commitment":
            milestones.append({"commitment": it["id"], "text": it["text"][:160], "due": it.get("deadline") or "no date given"})
    deliverables = req.get("deliverables", [])
    scope = {"objectives": [f"{d['id']}: {PRODUCERS.get(d['type'], PRODUCERS['report'])['make']} ({d['type'].replace('_', ' ')})"
                            for d in deliverables],
             "out_of_scope": "anything the sources do not ask for; new requirements need the client or you to state them"}
    acceptance = {d["id"]: ([f"{r} is met and listed under 'Requirements covered'" for r in d["requirements"]
                             if items.get(r, {}).get("role") != "owner"]
                            + ["the file opens", "no figures that are not in the sources or your answers",
                               "no secrets; nothing from text addressed to an AI"]) for d in deliverables}
    risks = [f"open conflict {c['id']}: {c['topic']}" for c in req.get("conflicts", []) if c["status"] == "open"]
    risks += [f"{len(open_q)} open question(s) may change the deliverables"] if open_q else []
    low = [it["id"] for it in items.values() if it.get("confidence") == "low" and it["kind"] != "question"]
    risks += [f"low-confidence items need checking: {', '.join(low[:12])}"] if low else []
    risks += [f"{d['type'].replace('_', ' ')}: {PRODUCERS[d['type']]['note']}" for d in deliverables if PRODUCERS.get(d["type"], {}).get("note")]
    if req.get("sensitive"):
        risks.append(f"{len(req['sensitive'])} source passage(s) hold personal or secret data: keep them out of outputs and logs")
    permissions = sorted({t["approval"] for t in tasks if t.get("approval")})
    tools = sorted({t["tool"].split(" ")[0] for t in tasks if t.get("tool")})
    test_plan = ["validate_deliverables: reopen each file; check requirement coverage (listed + key words); flag invented "
                 "figures, secrets and leaked AI-addressed text; Hindi/Telugu items are checked by hand",
                 "you review every 'needs review' item before anything goes to the client"]
    return {"generated": round(time.time(), 3), "tasks": tasks, "milestones": milestones, "scope": scope,
            "acceptance": acceptance, "risks": risks, "permissions": permissions, "tools": tools, "test_plan": test_plan,
            "blocked_by_questions": {d: q for d, q in clarify_for.items()},
            "not_makeable_here": [{"deliverable": d["id"], "type": d["type"], "why": PRODUCERS[d["type"]]["note"]}
                                  for d in req.get("deliverables", []) if PRODUCERS.get(d["type"], {}).get("note")]}


def to_markdown(plan: dict[str, Any]) -> str:
    lines = ["# Project plan", "", "Nothing here runs by itself; tasks marked with approval need your OK.", "",
             "| Task | What | Who / tool | Depends on | Approval |", "|---|---|---|---|---|"]
    for t in plan["tasks"]:
        tool = t["tool"] + ("" if t.get("tool_available", True) else " (not running now)")
        lines.append(f"| {t['id']} | {t['title']} | {t['who']}: {tool} | {', '.join(t['depends_on']) or '-'} | "
                     f"{t['approval'] or '-'} |")
    lines += ["", "## Milestones", ""]
    for m in plan["milestones"]:
        if "deliverable" in m:
            extra = f" (you promised: {', '.join(m['promised'])})" if m["promised"] else ""
            lines.append(f"- {m['deliverable']} {m['type'].replace('_', ' ')}: due {m['due']}{extra}")
        else:
            lines.append(f"- Your commitment {m['commitment']}: {m['text']} (due {m['due']})")
    if plan.get("scope"):
        lines = lines[:3] + ["## Scope", ""] + [f"- {o}" for o in plan["scope"]["objectives"]] + \
            [f"- Out of scope: {plan['scope']['out_of_scope']}", ""] + lines[3:]
    if plan.get("risks"):
        lines += ["", "## Risks", ""] + [f"- {r}" for r in plan["risks"]]
    if plan.get("acceptance"):
        lines += ["", "## Acceptance criteria", ""]
        for d, crit in plan["acceptance"].items():
            lines += [f"- {d}: " + "; ".join(crit)]
    if plan.get("permissions"):
        lines += ["", "## Needs your approval", ""] + [f"- {p}" for p in plan["permissions"]]
    if plan.get("test_plan"):
        lines += ["", "## Test plan", ""] + [f"- {t}" for t in plan["test_plan"]]
    if plan["not_makeable_here"]:
        lines += ["", "## What JARVIS cannot make itself", ""]
        lines += [f"- {x['deliverable']} {x['type'].replace('_', ' ')}: {x['why']}" for x in plan["not_makeable_here"]]
    return "\n".join(lines) + "\n"
