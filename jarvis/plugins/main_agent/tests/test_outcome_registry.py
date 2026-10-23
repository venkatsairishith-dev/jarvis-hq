"""Every plugin entry OUR code owns has a registered outcome schema, with real success AND failure examples (tool/plugin inventory, G).

Coverage is enforced from the source: a new @plugin_entry in desktop, main_agent, jarvis_bridge, research, local_team, doc_tools,
deliverables, learn_vault  without a schema in outcome.SCHEMAS and an example here fails this test. Examples are
taken from the entries' return statements; learn_vault and doc_tools are RUN for real in a temp folder and their real results judged.
Third-party plugins stay conservative (see test_payload_outcome.py: an unknown shape from a writer is unknown / held)."""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from jarvis.plugins.main_agent import outcome as oc
from jarvis.plugins.main_agent.planner import effect_of

ROOT = pathlib.Path(__file__).resolve().parents[2]
OURS = ("desktop", "main_agent", "jarvis_bridge", "research", "local_team", "doc_tools", "deliverables", "learn_vault",
        "workbench")
S, F = oc.SUCCESS, "not_success"        # a failure example may be failed / unknown / cancelled / waiting: anything but success


def entries() -> list[str]:
    out = []
    for plugin in OURS:
        for f in (ROOT / plugin).rglob("*.py"):
            if "/tests/" in str(f):
                continue
            for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    for dec in n.decorator_list:
                        if isinstance(dec, ast.Call) and getattr(dec.func, "id", getattr(dec.func, "attr", "")) == "plugin_entry":
                            eid = next((k.value.value for k in dec.keywords if k.arg == "id" and isinstance(k.value, ast.Constant)), n.name)
                            out.append(f"{plugin}:{eid}")
    return sorted(set(out))


BRIEF_OK = {"state": "done", "ok": True, "rows": [], "observed": True}
BRIEF_BAD = {"state": "failed", "ok": False, "error": "no window"}

EXAMPLES: dict[str, tuple[list, list]] = {
    # ---- desktop (_brief entries: state + ok; tasks / missions: status; troubleshooter: outcome; stop / recover)
    **{f"desktop:{e}": ([BRIEF_OK], [BRIEF_BAD, {"state": "denied", "ok": False}, {"state": "cancelled", "ok": False, "error": "stopped"},
                                     {"state": "waiting_for_permission", "approval_id": "ab12", "ok": False}, {}, None])
       for e in ("open_website", "play_youtube", "run_browser_action", "run_pc_action")},
    "desktop:check_pc": ([{"state": "done", "ok": True, "rows": [{"free_gb": 120}]}], [BRIEF_BAD, {"error": "refused"}]),
    "desktop:read_window_text": ([{"ok": True, "app": "notepad", "lines": 3, "source": "from OCR (less reliable)"}],
                                 [{"ok": False, "error": "no window"}]),
    "desktop:find_on_screen": ([{"ok": True, "found": False, "source": "from OCR"}], [{"ok": False, "error": "capture failed"}]),
    "desktop:list_browser_profiles": ([{"profiles": [{"name": "default"}]}], [{"error": "no registry"}]),
    "desktop:recent_pc_alerts": ([{"alerts": [], "count": 0}], [{"error": "monitor off"}]),
    "desktop:mission_status": ([{"mission": "m20261019-010101-abcdef", "status": "running", "steps": []}, {"status": "none"}],
                               [{"mission": "m", "status": "failed", "error": "budget"}, {"status": "waiting_for_permission", "approval_id": "a"}]),
    "desktop:start_mission": ([{"mission": "m20261019-010101-abcdef", "status": "started"}], [{"mission": "m", "status": "failed"}, {}]),
    "desktop:resume_mission": ([{"ok": True, "mission": "m", "status": "continuing", "from": "waiting"}],
                               [{"ok": False, "error": "no such mission"}, {"ok": False, "status": "none", "error": "there is no mission"}]),
    "desktop:stop_mission": ([{"mission": "m", "stopped": True}], [{"mission": "m", "stopped": False}]),
    "desktop:run_pc_task": ([{"status": "completed", "task": "t", "steps": [{"n": 1, "passed": True}]}],
                            [{"status": "failed", "steps": [{"passed": False}]}, {"status": "busy", "error": "another PC task is running"},
                             {"status": "needs_clarification", "question": "which one?"}, {"status": "cancelled"}, {"status": "denied"},
                             {"status": "not_understood", "error": "no plan"}, {"status": "waiting_for_permission"}]),
    "desktop:run_troubleshooter": ([{"outcome": "fixed and verified"}, {"outcome": "diagnosed"}, {"outcome": "hypothesis only"}],
                                   [{"outcome": "the fix was not applied"}, {"outcome": "not approved: nothing was changed"},
                                    {"outcome": "fix applied, but the check failed (undo with the backup)"},
                                    {"outcome": "stopped by the owner; nothing further was done"}, {"outcome": "waiting for your approval"}]),
    "desktop:stop_pc_task": ([{"stopped_jobs": 0, "stopped": [], "still_running": [], "requested": 0, "message": "Nothing was running on the PC."},
                              {"stopped_jobs": 1, "stopped": ["t"], "still_running": [], "requested": 1, "message": "Stopped 1"}],
                             [{"stopped_jobs": 0, "still_running": [{"id": "t"}], "requested": 1, "message": "Stop was sent, but 1"}]),
    "desktop:recover_pc_lock": ([{"recovered": True, "how": "gone"}], [{"recovered": False, "reason": "not held"}, {"how": "?"}]),
    "desktop:stop_pc_job": ([{"id": "j1", "state": "cancelled", "note": "ok"}], [{"id": "j1", "state": "failed", "error": "unknown job"}]),
    # ---- jarvis_bridge
    "jarvis_bridge:delegate_task": ([{"command_id": "c-1", "state": "running", "message": "JARVIS has the task"}],
                                    [{"state": "failed", "error": "console down"}, {}]),
    "jarvis_bridge:task_result": ([{"command_id": "c-1", "state": "done", "message": "JARVIS finished."},
                                   {"command_id": "c-1", "state": "running", "message": "still working"}],
                                  [{"command_id": "c-1", "state": "failed", "message": "JARVIS could not complete it: x"},
                                   {"command_id": "c-1", "state": "cancelled"}]),
    "jarvis_bridge:cancel_task": ([{"command_id": "c-1", "cancelled": True, "tasks_stopped": 1, "message": "ok"}],
                                  [{"command_id": "c-1", "cancelled": False, "message": "already finished"}]),
    "jarvis_bridge:jarvis_status": ([{"ready": True, "busy": False, "message": "JARVIS is ready."}], [{"ready": False, "error": "down"}]),
    "jarvis_bridge:pending_approvals": ([{"count": 0, "approvals": []}], [{"error": "console down"}]),
    "jarvis_bridge:connector_overview": ([{"connectors": []}], [{"error": "console down"}]),
    # ---- main_agent
    "main_agent:plan_task": ([{"mode": "work", "summary": "s", "steps": []}], [{"error": "no model"}]),
    "main_agent:run_task": ([{"run_id": "ab12cd34ef", "mode": "work", "state": "planning", "steps": []}],
                            [{"state": "failed", "error": "no plan"}, {"state": "busy", "message": "still working"}, {}]),
    "main_agent:resume_run": ([{"run_id": "ab12cd34ef", "state": "running"}], [{"state": "failed", "error": "nothing to resume"}]),
    "main_agent:run_status": ([{"run_id": "ab12cd34ef", "state": "held", "steps": {}}], [{"error": "no such run"}]),
    "main_agent:stop_task": ([{"requested": True, "stopped": True, "still_running": [], "message": "Stopped"},
                              {"requested": False, "stopped": False, "still_running": [], "message": "Nothing is running."}],
                             [{"requested": True, "stopped": False, "still_running": [{"id": "r"}], "message": "Stop requested, 1 still running"}]),
    # ---- research / local_team
    "research:research_topic": ([{"summary": "s", "spec": {"summary": "s"}, "stats": {}, "notes": []}],
                                [{"summary": "s", "qa_passed": False}, {"error": "nothing was searched"}, {}]),
    "local_team:ask_local": ([{"specialist": "general", "routed_by": "rule", "answer": "42", "seconds": 1.0}], [{"error": "no answer"}, {}]),
    "local_team:solve_with_team": ([{"specialist": "code", "answer": "x", "adviser_used": False, "note": "adviser skipped: gateway down"}],
                                   [{"error": "no answer"}, {"answer": "x", "timed_out": True}]),
    "local_team:team_status": ([{"local_models": "up", "privacy_gateway": "down"}], [{"error": "x"}]),
}


def test_every_entry_our_code_owns_has_a_schema_and_examples():
    found = entries()
    assert len(found) >= 55, found
    missing_schema = [e for e in found if e not in oc.SCHEMAS]
    missing_examples = [e for e in found if e not in EXAMPLES and not (ROOT / e.split(":")[0]).exists() is False and e not in LIVE]
    stale = [e for e in oc.SCHEMAS if e.split(":")[0] in OURS and e not in found]
    assert missing_schema == [], f"entries without an outcome schema: {missing_schema}"
    assert missing_examples == [], f"entries without success/failure examples: {missing_examples}"
    assert stale == [], f"schemas for entries that no longer exist: {stale}"


LIVE = {"learn_vault:vault_status", "learn_vault:list_notes", "learn_vault:read_note", "learn_vault:search_notes", "learn_vault:save_note",
        "learn_vault:record_progress", "learn_vault:get_progress", "doc_tools:read_document", "doc_tools:write_document",
        "doc_tools:list_outputs", "deliverables:make_presentation", "deliverables:make_report", "deliverables:open_output",
        "deliverables:show_in_folder", "deliverables:start_slideshow", "deliverables:regenerate", "deliverables:list_deliverables"}
EXAMPLES.update({
    "deliverables:make_report": ([{"file": "r.pdf", "folder": "f", "qa": {"passed": True}, "kind": "pdf"}],
                                 [{"file": "r.pdf", "folder": "f", "qa": {"passed": False}}, {}]),
    "deliverables:make_presentation": ([{"file": "d.pptx", "folder": "f", "qa": {"passed": True}, "slides": 5}],
                                       [{"file": "d.pptx", "qa": {"passed": False}}, {}]),
    "deliverables:regenerate": ([{"file": "d.pptx", "folder": "f", "qa": {"passed": True}}], [{"file": "d.pptx", "qa": {"passed": False}}]),
    "deliverables:open_output": ([{"opened": "C:\\out\\r.pdf"}], [{"opened": False}, {}]),
    "deliverables:show_in_folder": ([{"shown": "C:\\out\\r.pdf"}], [{"shown": False}, {}]),
    "deliverables:start_slideshow": ([{"presenting": "C:\\out\\d.pptx"}], [{"presenting": False}, {}]),
    "deliverables:list_deliverables": ([{"items": [], "tools": {}}], [{"error": "no folder"}]),
})


EXAMPLES.update({
    "main_agent:pending_inputs": ([{"count": 0, "requests": []}], [{"error": "x"}]),
    "main_agent:learning_stats": ([{"episodes": 0, "success_rate": None}], [{"error": "x"}]),
    "main_agent:forget_learning": ([{"state": "forgotten", "forgotten": 3}], [{"error": "x"}, {}]),
    "main_agent:answer_input": ([{"state": "answered", "run_id": "r1", "request_id": "q-1", "message": "m"}],
                                [{"state": "needs_input", "run_id": "r1", "message": "busy"}, {"error": "expired"}, {}]),
    "main_agent:cancel_input": ([{"state": "cancelled", "run_id": "r1", "request_id": "q-1"}], [{"error": "not waiting"}, {}]),
    "workbench:create_project": ([{"project_id": "acme", "folder": "f", "policy": {}}], [{"error": "name"}, {}]),
    "workbench:set_provider_policy": ([{"project_id": "acme", "policy": {"ai_providers": "local_only"}}], [{}, {"error": "x"}]),
    "workbench:add_project_files": ([{"added": [{"id": "F1"}], "refused": []}], [{"added": [], "refused": [{"why": "protected"}]}]),
    "workbench:analyze_project": ([{"project_id": "acme", "stats": {"items": 3}, "open_questions": []}], [{"error": "no project"}, {}]),
    "workbench:answer_questions": ([{"project_id": "acme", "stats": {"items": 3}}], [{"error": "analyse first"}, {}]),
    "workbench:project_status": ([{"project_id": "acme", "stats": {}}], [{"error": "No such project."}]),
    "workbench:generate_prompt": ([{"id": "P-x-abcdef", "version": 1, "status": "suggestion", "text": "t"}], [{"error": "objective"}, {}]),
    "workbench:get_prompt": ([{"id": "P-x-abcdef", "status": "submitted for execution", "text": "t"}], [{"error": "Unknown prompt id."}]),
    "workbench:edit_prompt": ([{"id": "P-x-abcdef", "version": 2}], [{"error": "empty"}, {}]),
    "workbench:regenerate_prompt": ([{"id": "P-x-abcdef", "version": 3}], [{"error": "x"}, {}]),
    "workbench:build_deliverables": ([{"status": "verified", "verified": 2, "needs_review": 0, "files": ["a"]}],
                                     [{"status": "needs_review", "verified": 0, "needs_review": 2, "files": ["a"]},
                                      {"status": "partly_verified", "verified": 1, "needs_review": 1},
                                      {"status": "blocked", "verified": 0, "files": ["requirements.md"]}, {}]),
    "workbench:learn_task": ([{"status": "validated", "workflow": "w", "version": "v1"}],
                             [{"status": "not validated", "why": "1/2"}, {"status": "needs examples"}, {"status": "needs the lab"},
                              {"status": "not adopted"}, {"status": "invalid workflow"}, {"status": "needs a workflow"}, {}]),
    "workbench:list_learned": ([{"workflows": [], "lessons": []}], [{"error": "x"}]),
    "workbench:execute_prompt": ([{"state": "submitted", "route": "jarvis", "ref": "cmd-1"}],
                                 [{"state": "refused", "route": "jarvis", "message": "kill switch"}, {"state": "failed"}, {}]),
    "workbench:run_learned_workflow": ([{"state": "submitted", "command_id": "cmd-1"}],
                                       [{"state": "refused", "message": "kill switch"}, {"state": "failed"}, {}]),
    "workbench:check_workflow_output": ([{"verified": True, "results": []}], [{"verified": False, "why": "no file"}, {}]),
    "workbench:rename_prompt": ([{"id": "P-x-abcdef", "title": "t"}], [{"error": "Give a title."}, {}]),
    "workbench:duplicate_prompt": ([{"id": "P-y-abcdef", "version": 1}], [{"error": "Unknown prompt id."}, {}]),
    "workbench:compare_prompt": ([{"summary": ["v2 was edited"], "diff": "-a\n+b"}], [{"error": "No such version."}]),
    "workbench:export_prompt": ([{"file": "/w/export-v1.md", "version": 1}], [{"error": "x"}, {}]),
    "workbench:export_skill": ([{"file": "/w/s.json", "sha256": "ab"}], [{"error": "Unknown skill."}, {}]),
    "workbench:search_skills": ([{"skills": []}], [{"error": "x"}]),
    "workbench:briefing": ([{"facts": [], "suggestions": [], "not_read": [], "text": "Briefing"}], [{"error": "x"}]),
    "workbench:inspect_skill": ([{"name": "s", "version": "v1", "state": {"status": "active"}}], [{"error": "Unknown skill."}]),
    "workbench:reuse_skill": ([{"ok": True, "checks": {"verified": True}, "outputs": {}}],
                              [{"ok": True, "checks": {"verified": False, "why": "missing column"}}, {"ok": False, "error": "x"}, {}]),
    "workbench:regress_skill": ([{"passed": True, "failed_cases": []}], [{"passed": False, "failed_cases": ["c1"]}, {}]),
    "workbench:disable_skill": ([{"status": "disabled", "reason": "r"}], [{"status": "active"}, {"error": "Unknown skill."}, {}]),
    "workbench:enable_skill": ([{"status": "active"}], [{"status": "quarantined"}, {"error": "still failing"}, {}]),
    "workbench:rollback_skill": ([{"current": "v1", "previous": "v2"}], [{"error": "v3 was not validated; rollback refused."}, {}]),
    "workbench:import_skill": ([{"name": "x", "status": "imported-unverified", "sha256": "ab", "gate": {"tier": "low"}}],
                               [{"error": "That file is protected and will not be read."}, {}]),
})


@pytest.mark.parametrize("ref", sorted(EXAMPLES))
def test_registered_schemas_judge_real_success_and_failure_examples(ref):
    assert ref in oc.SCHEMAS, ref
    ok, bad = EXAMPLES[ref]
    effect = effect_of("plugin", ref)
    assert ok and bad, "each entry needs at least one success and one failure example"
    for payload in ok:
        got = oc.judge(ref, payload, effect)
        assert got.kind == S, (ref, payload, got)
    for payload in bad:
        got = oc.judge(ref, payload, effect)
        assert got.kind != S, (ref, payload, got)


def test_learn_vault_and_doc_tools_are_run_for_real_and_their_results_judged(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("DOC_TOOLS_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("DOC_TOOLS_READ_ROOTS", str(tmp_path / "vault") + __import__("os").pathsep + str(tmp_path / "out"))
    from jarvis.plugins.doc_tools import core as docs
    from jarvis.plugins.learn_vault import core as vault
    cases = [
        ("learn_vault:vault_status", {**vault.status()}),                                                       # vault does not exist yet
        ("learn_vault:save_note", vault.save_note("notes/ग्रह-Ünïcode name.md", "# hello\n", "create")),
        ("learn_vault:list_notes", {"notes": vault.list_notes("")}),
        ("learn_vault:read_note", vault.read_note("notes/ग्रह-Ünïcode name.md")),
        ("learn_vault:search_notes", {"hits": vault.search_notes("hello")}),
        ("learn_vault:record_progress", vault.record_progress("cells", "learning", "ok")),
        ("learn_vault:get_progress", {"topics": vault.progress()}),
        ("doc_tools:write_document", docs.write_document("report", "# title\nbody", "md")),
        ("doc_tools:list_outputs", {"files": docs.list_outputs()}),
    ]
    for ref, payload in cases:
        got = oc.judge(ref, payload, effect_of("plugin", ref))
        assert got.kind == S, (ref, payload, got)
    written = docs.write_document("report", "second", "md")
    assert oc.judge("doc_tools:read_document", docs.read_document(written["path"]), "none").kind == S
