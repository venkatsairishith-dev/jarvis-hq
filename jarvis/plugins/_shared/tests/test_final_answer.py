"""final_answer: the owner's last message is built from the VERIFIED results when the model's text is empty, leaks an error or contradicts them."""
from jarvis.plugins._shared import final_answer as fa
from jarvis.plugins._shared.final_answer import Effect

CALC = Effect("a Calculator window appeared", verified=True)
WEATHER = Effect("the weather for Paris was read", verified=True)
SAVE_FAIL = Effect("the note was saved", failed=True, detail="no file was written")
SOFT = Effect("the key Enter was sent to notepad")


def g(text, status, effects, **kw):
    return fa.guard(text, status, effects, **kw)


def test_empty_texts():
    for t in ("", "   ", None, "...", "…", "<think>I should open it</think>", '{"name": "get_weather", "arguments": {}}',
              "<tool_call>{}</tool_call>", "[]"):
        assert fa.is_empty(t), repr(t)
    for t in ("Done.", "Opened Calculator", "{curly} but words"):
        assert not fa.is_empty(t), repr(t)


def test_empty_model_text_after_verified_work_gets_the_templated_sentence():
    r = g("", fa.COMPLETED, [CALC, WEATHER])
    assert r.source == fa.TEMPLATE and r.reasons == ["empty"]
    assert r.text == "Done: a Calculator window appeared; the weather for Paris was read."


def test_sound_model_text_is_kept():
    r = g("Calculator is open and it is 12 degrees in Paris.", fa.COMPLETED, [CALC, WEATHER])
    assert r.source == fa.MODEL and r.reasons == [] and "12 degrees" in r.text


def test_provider_error_text_is_never_the_final_message():
    r = g("Error from server: Remote sent 400 response: Unterminated string starting at: line 1 column 104", fa.COMPLETED, [CALC])
    assert r.source == fa.TEMPLATE and "leak" in r.reasons and "Unterminated" not in r.text


def test_model_says_done_but_a_step_failed():
    r = g("All done! I saved your note.", fa.FAILED, [CALC, SAVE_FAIL])
    assert r.source == fa.TEMPLATE and "overclaim" in r.reasons
    assert r.text.startswith("I could not finish: the note was saved (no file was written).") and "Already done: a Calculator window appeared." in r.text


def test_model_says_failed_but_everything_was_verified():
    r = g("Sorry, I couldn't open Calculator.", fa.COMPLETED, [CALC])
    assert r.source == fa.TEMPLATE and r.reasons == ["underclaim"] and r.text == "Done: a Calculator window appeared."


def test_honest_failure_text_is_kept():
    r = g("I couldn't save the note, the save did not go through.", fa.FAILED, [SAVE_FAIL])
    assert r.source == fa.MODEL


def test_plain_success_over_unobserved_steps_needs_a_hedge():
    r = g("Done, the note is saved.", fa.COMPLETED, [CALC, SOFT])
    assert r.source == fa.TEMPLATE and r.reasons == ["hedgeless"] and "Only reported, not checked: the key Enter was sent to notepad." in r.text
    ok = g("Done, though I could not confirm the Enter key took effect.", fa.COMPLETED, [CALC, SOFT])
    assert ok.source == fa.MODEL


def test_waiting_for_the_pin_must_be_said():
    r = g("Sure, I'll take care of it.", fa.WAITING, [CALC, Effect("delete the file", failed=True)], approval_id="ap-1")
    assert r.source == fa.TEMPLATE and "silent_wait" in r.reasons
    assert "needs your approval first: delete the file" in r.text and "(approval ap-1)" in r.text and "Already done: a Calculator window appeared" in r.text
    kept = g("This needs your approval with the PIN first.", fa.WAITING, [Effect("delete the file", failed=True)])
    assert kept.source == fa.MODEL


def test_cancelled_and_clarify():
    assert g("", fa.CANCELLED, [CALC]).text == "Stopped. Before the stop: a Calculator window appeared."
    assert fa.template(fa.CLARIFY, [], question="Which one: notepad or wordpad?") == "Which one: notepad or wordpad?"


def test_no_effects_is_a_chat_reply_only_empty_or_leak_are_replaced():
    assert g("The capital of France is Paris.", fa.COMPLETED, []).source == fa.MODEL
    assert g("", fa.COMPLETED, []).source == fa.TEMPLATE
    assert g("HTTP 429 rate capacity exceeded", fa.COMPLETED, []).source == fa.TEMPLATE


def test_hindi_and_telugu_markers():
    r = g("हो गया, मैंने सेव कर दिया", fa.FAILED, [SAVE_FAIL])
    assert r.source == fa.TEMPLATE and "overclaim" in r.reasons
    r = g("పూర్తయింది", fa.FAILED, [SAVE_FAIL])
    assert r.source == fa.TEMPLATE
    assert g("నోట్ సేవ్ కాలేదు", fa.FAILED, [SAVE_FAIL]).source == fa.MODEL


def test_private_variant_names_counts_only():
    t = fa.template(fa.FAILED, [CALC, SAVE_FAIL], detailed=False)
    assert t == "I could not finish. 1 of 2 steps were checked before it stopped."
    assert fa.template(fa.COMPLETED, [CALC, WEATHER], detailed=False) == "Done. Every step was checked."
    assert "salary" not in fa.template(fa.COMPLETED, [Effect("salary.xlsx opened", verified=True)], detailed=False)


def test_effects_from_coordinator_record():
    rec = {"status": "completed", "steps": [
        {"n": 1, "tool": "open_app", "passed": True, "checked": "a window for calc appeared", "uncertainty": ""},
        {"n": 2, "tool": "press_key", "passed": True, "checked": "Enter was sent to notepad", "uncertainty": "reported ok, not observed: the key press"}]}
    status, fx = fa.effects_from_record(rec)
    assert status == fa.COMPLETED and fx[0].verified and not fx[1].verified and not fx[1].failed
    status, fx = fa.effects_from_record({"status": "failed", "error": "step 1 (type_text): nothing", "steps": [
        {"n": 1, "tool": "type_text", "passed": False, "checked": "the action did not succeed", "uncertainty": "x"}]})
    assert status == fa.FAILED and fx[0].failed


def test_effects_from_goal_result():
    res = {"status": "failed", "steps": [{"n": 1, "goal": "open Calculator", "status": "completed", "verified": True},
                                          {"n": 2, "goal": "read the weather", "status": "failed", "result": "gateway down"},
                                          {"n": 3, "goal": "x", "status": "not_run"}]}
    status, fx = fa.effects_from_goal_result(res)
    assert status == fa.FAILED and [e.failed for e in fx] == [False, True] and fx[0].verified


def test_answer_for_task():
    assert fa.answer_for_task("running").startswith("JARVIS is still working")
    assert fa.answer_for_task("done", "Done: a window appeared.", detailed=True) == "Done: a window appeared."
    assert fa.answer_for_task("done", "Done: salary.xlsx opened.") == "Done. JARVIS reports it finished."
    assert fa.answer_for_task("done", "HTTP 400 Unterminated string", detailed=True) == "Done. JARVIS reports it finished."
    assert fa.answer_for_task("failed", error="approval expired", detailed=True) == "I could not finish that: approval expired."
    assert fa.answer_for_task("failed", error="secret thing") == "I could not finish that."
    assert fa.answer_for_task("weird") == "The task is weird."
