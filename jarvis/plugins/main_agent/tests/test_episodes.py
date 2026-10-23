"""Episodes (2026-10-22): what the agent keeps about its own runs, what it can learn from them, and what it must never keep."""
from __future__ import annotations

import asyncio
import json
import os
import stat

from jarvis.plugins.main_agent import episodes as E
from jarvis.plugins.main_agent.planner import Capability, planner_messages
from test_run_control import make, settle  # noqa: E402 - the harness of the sibling test file

SECRET_WORDS = "Priyanka quarterly payroll spreadsheet 4417 priyanka@example.com"


def ep(tmp_path, clock=None, **kw):
    return E.Episodes(tmp_path / "learning", clock=clock or (lambda: 1_000_000.0), **kw)


def rec(e, task="open the report", state="done", shape=("plugin:desktop:run_pc_task", "think"), err="", run="r1", sec=3.0, **kw):
    steps = [{"ref": s, "out": "success" if not err else "failure", "err": err} for s in shape]
    return e.record(run_id=run, task=task, source="voice", mode="work", state=state, shape=list(shape), steps=steps, seconds=sec, **kw)


def test_the_log_keeps_no_words_numbers_or_addresses_from_the_request(tmp_path):
    e = ep(tmp_path)
    rec(e, task=SECRET_WORDS)
    raw = (tmp_path / "learning" / E.FILE).read_text()
    for word in ("Priyanka", "priyanka", "payroll", "spreadsheet", "4417", "example.com", "quarterly"):
        assert word.lower() not in raw.lower(), word
    row = json.loads(raw)
    assert row["words"] == len(row["tok"]) == 4                     # priyanka, quarterly, payroll, spreadsheet (digits and the address are dropped)
    assert all(len(t) == 10 and int(t, 16) >= 0 for t in row["tok"])


def test_the_salt_is_owner_only_and_makes_fingerprints_private_to_this_install(tmp_path):
    a, b = ep(tmp_path), E.Episodes(tmp_path / "other")
    assert a.tokens("payroll report") == a.tokens("report payroll") != b.tokens("payroll report")
    mode = stat.S_IMODE(os.stat(tmp_path / "learning" / E.SALT_FILE).st_mode)
    assert os.name == "nt" or mode == 0o600
    rec(a, task="payroll report")
    assert os.name == "nt" or stat.S_IMODE(os.stat(tmp_path / "learning" / E.FILE).st_mode) == 0o600


def test_error_classes_replace_error_text(tmp_path):
    cases = {"The agent model did not answer (RateLimitError).": "rate_limit", "The planner's plan was not valid JSON.": "planner_format",
             "None of the available helpers can do that request.": "no_helper", "needs your approval or answer: x": "needs_owner",
             "no single file matches": "no_match", "timed out; whether it had any effect is not known": "timeout",
             "I cannot create folders or files yet": "cannot_do", "something odd": "other", "": ""}
    for text, cls in cases.items():
        assert E.error_class(text) == cls, text


def test_similar_requests_are_found_by_shared_words_only(tmp_path):
    e = ep(tmp_path)
    rec(e, task="send the monthly invoice to the accountant", run="a")
    rec(e, task="play some relaxing music", run="b")
    near = e.similar("send the invoice to accountant now")
    assert [r["run"] for _, r in near] == ["a"]
    assert e.similar("music") == []                                   # one shared word is not enough
    assert e.similar("totally unrelated sentence here") == []


def test_hints_name_the_shape_that_worked_and_the_one_that_failed_and_never_any_words(tmp_path):
    e = ep(tmp_path)
    for i in range(3):
        rec(e, task="summarise the latest news about solar panels", shape=("plugin:web_search:search", "think"), run=f"g{i}")
    rec(e, task="summarise latest solar panels news", shape=("jarvis",), state="failed", err="timeout", run="b1")
    allowed = {"think", "jarvis", "plugin:web_search:search"}
    line = e.hints("summarise the news about solar panels", allowed)
    assert "succeeded 3 of 4" in line and "plugin:web_search:search > think" in line
    assert "failed 1 times" in line and "(timeout)" in line and "jarvis" in line
    assert "solar" not in line.lower() and "news" not in line.lower()
    # a shape made of helpers that do not exist now is never suggested
    assert "web_search" not in e.hints("summarise the news about solar panels", {"think", "jarvis"})
    assert e.hints("completely different request words", allowed) == ""


def test_stats_show_rates_trend_errors_and_weak_helpers(tmp_path):
    e = ep(tmp_path)
    for i in range(20):
        rec(e, task=f"old task {i}", state="failed", err="rate_limit", shape=("plugin:desktop:run_pc_task",), run=f"o{i}")
    for i in range(20):
        rec(e, task=f"new task {i}", state="done", shape=("plugin:desktop:run_pc_task",), run=f"n{i}")
    st = e.stats()
    assert st["episodes"] == 40 and st["success_rate"] == 0.5
    assert st["trend"] == {"last20": 1.0, "previous20": 0.0}
    assert st["errors"][0] == {"class": "rate_limit", "count": 20}
    assert E.Episodes(tmp_path / "empty").stats()["success_rate"] is None
    for i in range(4):
        rec(e, task="weak", state="failed", err="no_match", shape=("plugin:learn_vault:read_note",), run=f"w{i}")
    assert any(w["name"] == "plugin:learn_vault:read_note" for w in e.stats()["weak"])


def test_old_and_surplus_episodes_are_dropped_and_forget_erases_everything(tmp_path):
    now = [1_000_000.0]
    e = ep(tmp_path, clock=lambda: now[0], keep=5, max_age_days=10)
    for i in range(8):
        rec(e, task=f"task {i}", run=f"k{i}")
    assert [r["run"] for r in e.all()] == [f"k{i}" for i in range(3, 8)]
    now[0] += 11 * 86400
    rec(e, task="fresh", run="fresh")
    assert [r["run"] for r in e.all()] == ["fresh"]
    assert e.forget() == 1 and e.all() == [] and not (tmp_path / "learning" / E.SALT_FILE).exists()


def test_a_damaged_line_is_skipped_not_fatal(tmp_path):
    e = ep(tmp_path)
    rec(e, task="good one here")
    with open(tmp_path / "learning" / E.FILE, "a") as fh:
        fh.write("{not json\n[1,2]\n")
    assert len(e.all()) == 1


# ---- in the agent -------------------------------------------------------------------------------------------------------------------
def test_a_finished_task_is_recorded_and_a_similar_request_gets_the_experience_in_its_planning_prompt(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path)
        for _ in range(2):
            await p.run_task(task="write a short summary of the quarterly budget", mode="work")
            await settle(p)
        rows = p._ep().all()
        assert [r["state"] for r in rows] == ["done", "done"] and rows[0]["shape"] == ["think"]
        raw = (tmp_path / "main_agent_learning" / E.FILE).read_text()
        assert "budget" not in raw and "summary" not in raw                              # not a word of the request is on disk
        await p.run_task(task="write a summary of the quarterly budget please", mode="work")
        await settle(p)
        planner_prompts = [m[0]["content"] for m in p.llm_calls]
        assert "Experience from this owner's earlier runs" not in planner_prompts[0]
        assert "succeeded 2 of 2 times: think" in planner_prompts[2]
        assert "budget" not in planner_prompts[2].split("Experience")[1]                 # the hint itself carries no request words
    asyncio.run(go())


def test_a_failed_plan_is_recorded_as_a_planner_failure(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path)

        async def broken(messages, **kw):
            raise ma_failed("The agent model did not answer (RateLimitError).")
        from jarvis.plugins.main_agent.executor import StepFailed as ma_failed
        p._llm = broken
        await p.run_task(task="open my report", mode="work")
        await settle(p)
        row = p._ep().all()[-1]
        assert row["state"] == "failed" and row["shape"] == [] and row["plan_err"] == "rate_limit"
    asyncio.run(go())


def test_the_entries_report_and_forget(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path)
        await p.run_task(task="write a short summary of the quarterly budget", mode="work")
        await settle(p)
        st = (await p.learning_stats()).value
        assert st["episodes"] == 1 and st["success_rate"] == 1.0
        res = (await p.forget_learning()).value
        assert res["state"] == "forgotten" and res["forgotten"] == 1 and (await p.learning_stats()).value["episodes"] == 0
    asyncio.run(go())


def test_planner_messages_carry_hints_only_when_given():
    caps = [Capability("think", "think", "t")]
    assert "Experience" not in planner_messages("x", caps, 3)[0]["content"]
    assert planner_messages("x", caps, 3, hints="Experience: a")[0]["content"].endswith("Experience: a")


def test_stats_count_how_often_self_correction_was_tried_and_rescued(tmp_path):
    e = ep(tmp_path)
    rec(e, task="open report one", state="failed", err="no_match", run="a", attempt=1)
    rec(e, task="open report one", state="done", shape=("think",), run="a", attempt=2)
    rec(e, task="open report two", state="failed", err="no_match", run="b", attempt=1)
    rec(e, task="open report two", state="failed", err="no_match", shape=("think",), run="b", attempt=2)
    assert e.stats()["self_correction"] == {"tried": 2, "rescued": 1}
    assert E.Episodes(tmp_path / "none").stats()["self_correction"] == {"tried": 0, "rescued": 0}
