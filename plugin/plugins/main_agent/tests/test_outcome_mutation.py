"""Regression tests added after mutation testing of outcome.py (2026-10-19): reason text and its limits, the never-raises wrapper,
the strings-only path, unrecognised status words and which kinds of verdict take their reason from the payload."""
from __future__ import annotations

from plugin.plugins.main_agent import outcome as oc


def test_judge_never_raises_and_says_the_result_could_not_be_read(monkeypatch):
    def boom(ref, value, effect):
        raise RuntimeError("x")
    monkeypatch.setattr(oc, "_judge", boom)
    v = oc.judge("desktop:run_pc_task", {"status": "ok"}, "non_idempotent")
    assert v == oc.Verdict(oc.UNKNOWN, "its result could not be read")


def test_a_registered_entry_with_a_nested_layer_and_a_plain_string_is_unrecognised_not_unreadable():
    v = oc.judge("wechat_integration:get_dashboard_state", "logged in", "none")
    assert v == oc.Verdict(oc.UNKNOWN, "its result had no recognisable shape")
    assert oc.judge("wechat_integration:get_dashboard_state", None, "none").kind == oc.UNKNOWN
    assert oc.judge("wechat_integration:get_dashboard_state", [1, 2], "none").kind == oc.UNKNOWN


def test_the_reason_taken_from_the_payload_is_cut_at_300_characters():
    v = oc.judge("desktop:run_pc_task", {"status": "failed", "error": "E" * 500}, "non_idempotent")
    assert v.kind == oc.FAILED
    assert v.reason == "E" * 300


def test_a_failed_string_result_keeps_its_first_300_characters_and_a_success_string_none():
    v = oc.judge("x:unregistered", "Failed to open " + "z" * 400, "none")
    assert v.kind == oc.FAILED and len(v.reason) == 300 and v.reason.startswith("Failed to open")
    assert oc.judge("x:unregistered", "Done, all good", "none") == oc.Verdict(oc.SUCCESS)
    assert oc.judge("x:unregistered", "  could not connect  ", "non_idempotent") == oc.Verdict(oc.FAILED, "could not connect")
    assert oc.judge("x:unregistered", "waiting for your approval", "none").kind == oc.WAITING


def test_failed_waiting_and_cancelled_verdicts_use_the_payloads_own_words_but_unknown_ones_do_not():
    failed = oc.judge("desktop:run_pc_task", {"status": "failed", "error": "Disk full while saving"}, "non_idempotent")
    assert failed == oc.Verdict(oc.FAILED, "Disk full while saving")
    waiting = oc.judge("desktop:run_pc_task", {"status": "waiting", "question": "Which window?"}, "non_idempotent")
    assert waiting == oc.Verdict(oc.WAITING, "Which window?")
    cancelled = oc.judge("desktop:run_pc_task", {"status": "cancelled", "message": "Stopped on request"}, "non_idempotent")
    assert cancelled.kind == oc.CANCELLED and cancelled.reason == "Stopped on request"
    unknown = oc.judge("desktop:run_pc_task", {"timed_out": True, "message": "Took too long to answer"}, "non_idempotent")
    assert unknown == oc.Verdict(oc.UNKNOWN, "timed_out")


def test_an_unrecognised_status_word_is_unknown_for_a_change_but_ignored_for_a_read():
    v = oc.judge("deliverables:open_output", {"status": "frobnicating", "opened": "x.docx"}, "non_idempotent")
    assert v.kind == oc.UNKNOWN
    assert v.reason == "a status that is not recognised (frobnicating)"
    long = oc.judge("deliverables:open_output", {"status": "q" * 50, "opened": "x.docx"}, "non_idempotent")
    assert long.reason == "a status that is not recognised (" + "q" * 30 + ")"
    assert oc.judge("deliverables:list_deliverables", {"status": "frobnicating", "items": [1]}, "none").kind == oc.SUCCESS
    # a recognised success word with the entry's result key is a success
    assert oc.judge("deliverables:open_output", {"status": "done", "opened": "x.docx"}, "non_idempotent") == oc.Verdict(oc.SUCCESS)


def test_layers_find_failures_hidden_under_data_and_result_and_stop_at_depth_five():
    assert oc.judge("x:unregistered", {"ok": True, "data": {"error": "inner failure"}}, "non_idempotent").kind == oc.FAILED
    assert oc.judge("x:unregistered", {"result": {"result": {"ok": False}}}, "non_idempotent").kind == oc.FAILED
    deep = {"ok": True}
    for _ in range(8):
        deep = {"data": deep}
    assert len(oc._layers(deep)) == 6                     # depth 0..5
    assert oc._layers("text") == [] and oc._layers(None) == [] and oc._layers([{"a": 1}]) == []
