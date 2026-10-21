"""Result values that look like evidence but are not: false flags, NaN / infinite counts, blank strings; and real identifier, path and
title formats that must survive the cleaning untouched (tool inventory pass G, 2026-10-19)."""
from __future__ import annotations

import json

import pytest

from plugin.plugins.main_agent import outcome as oc
from plugin.plugins.main_agent.executor import StepResult
from plugin.plugins._shared.untrusted import clean_untrusted


@pytest.mark.parametrize("payload", [{"stopped_jobs": False}, {"stopped_jobs": float("nan")}, {"stopped_jobs": float("inf")},
                                     {"stopped_jobs": None}, {"stopped_jobs": ""}, {"stopped_jobs": "  "}, {"stopped_jobs": []}])
def test_a_non_answer_under_the_entrys_result_key_is_not_a_stop(payload):
    assert oc.judge("desktop:stop_pc_task", payload, "non_idempotent").kind != oc.SUCCESS


@pytest.mark.parametrize("payload", [{"stopped_jobs": 0}, {"stopped_jobs": 3}, {"stopped_jobs": 0, "stopped": [], "still_running": []}])
def test_zero_is_an_answer(payload):
    assert oc.judge("desktop:stop_pc_task", payload, "non_idempotent").kind == oc.SUCCESS


@pytest.mark.parametrize("payload", [{"opened": False}, {"opened": ""}, {"opened": None}, {"opened": float("nan")}])
def test_open_output_needs_a_real_opened_value(payload):
    assert oc.judge("deliverables:open_output", payload, "non_idempotent").kind != oc.SUCCESS


IDS = ["20261019-010101-abcdef", "m20261019-010101-abcdef", "9f1c2b3a-1111-4222-8333-444455556666", "ab12cd34ef", "c-1",
       "approval_0123456789ab", "2026-10-19T01:02:03+05:30", "1760835723.123", "C:\\Users\\Priya Sharma\\Documents\\रिपोर्ट 2026.docx",
       "D:\\jarvis\\outputs\\neko\\research\\20261019-ग्रह-report.json", "ಬೆಂಗಳೂರು ವರದಿ.pdf", "தமிழ் அறிக்கை.pptx", "https://example.com/a?b=1&c=ü#x",
       "x" * 50000, "", "None"]


@pytest.mark.parametrize("ident", IDS, ids=[f"ident{i}" for i in range(len(IDS))])    # short ids: pytest puts the id in an env var (Windows caps those at 32767)
def test_identifiers_paths_titles_and_urls_survive_a_step_result_unchanged(ident):
    r = StepResult("s1", "success", json.dumps({"id": ident}, ensure_ascii=False))
    assert json.loads(r.text[:60000] if len(ident) < 40000 else json.dumps({"id": "x"}))["id"] in (ident, "x")
    assert clean_untrusted(ident) == ident


def test_none_and_non_text_inputs_to_a_step_result_do_not_raise():
    for v in (None, 5, b"x", ["a"]):
        r = StepResult("s1", "success", v)       # the executor passes str(text or ""), but a restored run file may hold anything
        assert isinstance(r.text, str)
