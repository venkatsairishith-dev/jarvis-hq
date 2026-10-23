"""Briefing: only what the records say, each fact with its source; suggestions marked; unread sources said."""
from __future__ import annotations

from jarvis.plugins.workbench import prompts, service, skillhub

from .test_skillhub import CASES, FIXED, TASK


def test_briefing_reads_records_and_marks_suggestions(alpha):
    pid = service.create_project("Sunrise Crumbs", "Priya", ["Sam Rao"])["project_id"]
    service.add_files(pid, alpha)
    service.analyze(pid)
    g = service.generate_prompt({"objective": "Write release notes", "kind": "single"})
    prompts.record_execution(g["id"], None, "jarvis", "cmd-1", "submitted")
    name = service.learn_task(TASK, CASES, FIXED, name="email-domains")["workflow"]
    skillhub.add_regression_case(name, {"name": "two-per-line", "inputs": {"input.txt": "a@x.org; b@y.org\n"},
                                        "checks": [{"check": "equals", "file": "domains.txt", "text": "x.org\ny.org"}]})
    skillhub.regress(name)                                            # quarantines it
    b = service.briefing({"ready": True, "approvals_waiting": 2})
    whats = " | ".join(f["what"] for f in b["facts"])
    assert "2 JARVIS approval(s) waiting" in whats
    assert "Sunrise Crumbs: 4 open question(s)" in whats and "unresolved conflict" in whats
    assert "presentation due by Friday" in whats or "presentation due" in whats
    assert f"Prompt {g['id']} was submitted and its result is not verified yet" in whats
    assert "Skill email-domains is quarantined" in whats
    assert all(f["source"] for f in b["facts"])
    assert any("Fix or roll back skill email-domains" in s["what"] for s in b["suggestions"])
    assert "Suggestions (JARVIS's, not facts):" in b["text"] and b["not_read"] == []


def test_briefing_says_what_it_could_not_read():
    b = service.briefing(None)
    assert b["not_read"] == ["JARVIS (approvals, status): not checked"]
    assert "nothing pending in the records" in b["text"]
    b = service.briefing({"error": "not reachable (ConnectError)"})
    assert "JARVIS: not reachable" in b["text"]
