"""The UI Automation worker's protocol and operations (D68), in this process over a fake desktop: what each operation does, which refusals it makes, and that an
answer never carries screen text. The same code runs in the child process in test_uia_lifecycle.py; the real library adapter is checked for completeness here."""
import inspect
import io
import json
import os
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "jarvis_node"))

import fakeuia  # noqa: E402
import uia_worker as W  # noqa: E402


def make(**kw):
    b = fakeuia.FakeBackend()
    ops = W.Ops(b, sleep=lambda s: kw.get("sleeps", []).append(s) if "sleeps" in kw else None, env=kw.get("env", {}))
    return b, ops


def ask(ops, op, **args):
    return json.loads(W.handle(ops, json.dumps({"id": 1, "op": op, "args": args})))


# ------------------------------------------------------------------------------------------------------------------------------ the wire
@pytest.mark.parametrize("line", [b"not json", b"[1,2]", b'{"op":"ping"}', b'{"id":"7","op":"ping"}', b'{"id":true,"op":"ping"}', b'{"id":1,"op":"rm -rf"}',
                                  b'{"id":1,"op":"ping","args":[1]}', b'{"id":1,"op":5}'])
def test_a_malformed_request_gets_a_bad_request_answer_and_the_loop_goes_on(line):
    _, ops = make()
    out = json.loads(W.handle(ops, line))
    assert out["ok"] is False and out["code"] == "bad request"
    assert ask(ops, "ping")["result"] == {"pong": True}


def test_an_answer_is_one_ascii_line_with_the_request_id():
    _, ops = make()
    raw = W.handle(ops, b'{"id": 41, "op": "windows", "args": {"proc": "notepad"}}')
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1 and raw.isascii()
    out = json.loads(raw)
    assert out["id"] == 41 and out["ok"] is True and out["result"]["windows"][0]["name"] == "notepad"


def test_an_unexpected_failure_gives_only_the_kind_never_the_text():
    b, ops = make()
    b.d.fail_set_value = True                                     # raises OSError("... secret-in-the-error")
    out = ask(ops, "type", proc="notepad", elem="15", text="hello")
    assert out == {"id": 1, "ok": False, "code": "other", "etype": "OSError"}
    assert "secret" not in json.dumps(out)


def test_a_primitive_the_adapter_lacks_is_unsupported_not_a_failure():
    class Thin(fakeuia.FakeBackend):
        def invoke(self, el):
            raise NotImplementedError
    ops = W.Ops(Thin(), sleep=lambda s: None, env={})
    assert ask(ops, "click", proc="calc", elem="num7Button")["code"] == "unsupported"


def test_an_answer_over_the_cap_is_replaced_not_sent():
    _, ops = make()
    ops.table["big"] = lambda a: {"t": "z" * 5000}
    out = json.loads(W.handle(ops, b'{"id":3,"op":"big","args":{}}', max_response=1000))
    assert out == {"id": 3, "ok": False, "code": "oversize"}


def test_a_request_line_over_the_cap_is_refused():
    _, ops = make()
    out = json.loads(W.handle(ops, b"x" * (W.MAX_REQUEST + 1)))
    assert out["code"] == "oversize"


# the codes the PowerShell path reports are winbackend.REASONS; the worker may add only protocol words
def test_the_worker_codes_are_the_nodes_reason_codes_plus_protocol_words():
    from jarvis_node import winbackend
    plain = set(W.CODES) - {"oversize", "bad request", "unsupported"}
    assert plain <= set(winbackend.REASONS)
    assert {"no window", "no element", "password field", "target is not the foreground window", "does not support", "pattern", "text not in field",
            "clipboard paste failed"} <= plain
    assert W.OpError("anything else").code == "other"


def test_serve_says_ready_answers_in_order_and_stops_on_quit():
    _, ops = make()
    rin = io.BytesIO(b'{"id":1,"op":"ping","args":{}}\n\n{"id":2,"op":"windows","args":{"proc":"calc"}}\n{"id":3,"op":"quit","args":{}}\n{"id":4,"op":"ping","args":{}}\n')
    rout = io.BytesIO()
    W.serve(ops, rin, rout, lib="fake 1", hard_exit=lambda c: None)
    lines = [json.loads(x) for x in rout.getvalue().splitlines()]
    assert lines[0]["ready"] is True and lines[0]["proto"] == W.PROTO and lines[0]["lib"] == "fake 1"
    assert [x.get("id") for x in lines[1:]] == [1, 2, 3] and lines[3]["result"] == {"bye": True}, "nothing after quit is answered"


def test_the_end_of_input_ends_serve_and_arms_the_hard_exit_for_a_worker_stuck_in_an_application():
    _, ops = make()
    exited = threading.Event()
    stuck = threading.Event()
    ops.table["stuck"] = lambda a: (stuck.wait(5), {})[1]          # the main loop is inside a hung application
    rin = io.BytesIO(b'{"id":1,"op":"stuck","args":{}}\n')        # then EOF: the node is gone
    t = threading.Thread(target=lambda: W.serve(ops, rin, io.BytesIO(), hard_exit=lambda c: exited.set(), grace_s=0.2), daemon=True)
    t.start()
    assert exited.wait(3), "the process must not wait for a hung application when the node has gone"
    stuck.set()
    t.join(3)


# ------------------------------------------------------------------------------------------------------------------------------ windows
def test_windows_lists_the_windows_of_one_process_and_the_foreground():
    b, ops = make()
    r = ask(ops, "windows", proc="notepad")["result"]
    assert r["fg"] == 1001 and [(w["hwnd"], w["pid"], w["name"]) for w in r["windows"]] == [(1001, 100, "notepad")] and r["windows"][0]["start"] > 0
    assert len(ask(ops, "windows", proc="")["result"]["windows"]) == 4
    assert ask(ops, "windows", proc="a;b")["code"] == "bad request"


# ------------------------------------------------------------------------------------------------------------------------------ find / click
def test_click_invokes_by_automation_id_or_by_name_and_says_no_element_otherwise():
    b, ops = make()
    assert ask(ops, "click", proc="calc", hwnd=2001, pid=200, elem="num7Button")["ok"]
    assert ask(ops, "click", proc="calc", hwnd=2001, pid=200, elem="Plus")["ok"]
    assert [x for x in b.d.log if x[0] == "invoke"] == [("invoke", "Seven"), ("invoke", "Plus")]
    assert ask(ops, "click", proc="calc", hwnd=2001, pid=200, elem="Minus")["code"] == "no element"


def test_a_classic_win32_button_without_invoke_gets_its_own_click_message():
    b, ops = make()
    assert ask(ops, "click", proc="oldtool", hwnd=4001, pid=400, elem="CommandButton_6")["ok"]
    assert b.d.log == [("bm_click", 4011)]


def test_an_element_that_cannot_be_invoked_and_is_no_button_is_refused():
    b, ops = make()
    assert ask(ops, "click", proc="calc", hwnd=2001, pid=200, elem="CalculatorResults")["code"] == "does not support"
    assert b.d.log == []


def test_the_window_is_found_by_process_name_when_the_node_gave_no_handle():
    b, ops = make()
    assert ask(ops, "click", proc="calc", elem="num7Button")["ok"]
    assert ask(ops, "click", proc="nothere", elem="x")["code"] == "no window"


def test_a_modern_app_is_found_by_its_frame_window_titled_with_its_name_and_two_matches_are_refused():
    b, ops = make()
    b.d.add_window(5001, 500, "applicationframehost", "Settings", "ApplicationFrameWindow", fakeuia.node("Settings", cls="ApplicationFrameWindow", ctype="Window", hwnd=5001, children=[
        fakeuia.node("Home", auto_id="HomeButton", ctype="Button", invoke=True)]))
    b.d.windows[5001]["proc"] = "applicationframehost"
    assert ask(ops, "click", proc="Settings", elem="HomeButton")["ok"]
    b.d.add_window(5002, 500, "applicationframehost", "Settings extra", "ApplicationFrameWindow", fakeuia.node("Settings extra", cls="ApplicationFrameWindow", ctype="Window", hwnd=5002))
    assert ask(ops, "click", proc="Settings", elem="HomeButton")["code"] == "no window", "more than one match is not a guess"


def test_the_star_means_the_first_text_field_and_never_a_password_box():
    b, ops = make()
    assert ask(ops, "find", proc="loginapp", hwnd=3001, pid=300, elem="*")["result"]["type"] == "Edit"
    for n in b.d.windows[3001]["root"]["children"]:
        if not n["password"]:
            n["ctype"] = "Button"                                   # only the password box is left
    assert ask(ops, "find", proc="loginapp", hwnd=3001, pid=300, elem="*")["code"] == "no element"


def test_a_bad_element_name_is_refused_before_looking():
    _, ops = make()
    for bad in ("", "a\nb", "x" * 121):
        assert ask(ops, "find", proc="calc", hwnd=2001, pid=200, elem=bad)["code"] == "bad request"


# ------------------------------------------------------------------------------------------------------------------------------ focus
def test_focus_does_nothing_when_the_window_is_already_in_front_and_otherwise_tries_up_to_three_times():
    b, ops = make()
    assert ask(ops, "focus", proc="notepad", hwnd=1001, pid=100)["result"] == {"foreground": True}
    assert b.alt_taps == 0, "an Alt tap would close an open menu"
    assert ask(ops, "focus", proc="calc", hwnd=2001, pid=200)["ok"] and b.d.foreground == 2001 and b.alt_taps == 1 and b.attach_tries == [False]


def test_a_minimised_window_is_restored_first():
    b, ops = make()
    b.d.windows[2001]["minimized"] = True
    b.d.foreground = 2001
    assert ask(ops, "focus", proc="calc", hwnd=2001, pid=200)["ok"]
    assert ("show", 2001, 9) in b.d.log


def test_a_foreground_lock_is_tried_three_times_with_the_thread_attach_from_the_second_and_then_refused():
    b, ops = make()
    b.d.refuse_foreground = True
    assert ask(ops, "focus", proc="calc", hwnd=2001, pid=200)["code"] == "target is not the foreground window"
    assert b.attach_tries == [False, True, True]


# ------------------------------------------------------------------------------------------------------------------------------ type
def test_text_goes_in_by_the_value_pattern_and_is_read_back():
    b, ops = make()
    r = ask(ops, "type", proc="notepad", hwnd=1001, pid=100, elem="15", text="hello world")
    assert r["ok"] and b.d.windows[1001]["root"]["children"][0]["value"] == "hello world"
    assert [x for x in b.d.log if x[0] == "keys"] == [], "no keystroke was needed"


def test_a_field_without_a_value_pattern_is_typed_into_with_keys_in_paced_pieces_and_read_back_from_the_window_text():
    sleeps = []
    b, ops = make(sleeps=sleeps, env={})
    r = ask(ops, "type", proc="oldtool", hwnd=4001, pid=400, elem="1001", text="0123456789abcdefXYZ")
    assert r["ok"] and b.d.windows[4001]["root"]["children"][0]["wm_text"] == "0123456789abcdefXYZ"
    assert [x[1] for x in b.d.log if x[0] == "keys"] == ["01234567", "89abcdef", "XYZ"], "pieces of 8 characters (D65 human feel)"
    assert any(s in (0.04, 0.055, 0.07) for s in sleeps), "a beat of 40 to 70 ms between pieces"


def test_pacing_can_be_turned_off_and_a_long_text_goes_in_one_piece():
    b, ops = make(env={"JARVIS_UI_PACE_MS": "0"})
    assert ask(ops, "type", proc="oldtool", hwnd=4001, pid=400, elem="1001", text="a" * 30)["ok"]
    assert [len(x[1]) for x in b.d.log if x[0] == "keys"] == [30]
    b, ops = make(env={"JARVIS_UI_PACE_CAP": "10"})
    assert ask(ops, "type", proc="oldtool", hwnd=4001, pid=400, elem="1001", text="b" * 30)["ok"]
    assert [len(x[1]) for x in b.d.log if x[0] == "keys"] == [30]


def test_a_password_field_is_never_typed_into():
    b, ops = make()
    r = ask(ops, "type", proc="loginapp", hwnd=3001, pid=300, elem="pw", text="hunter2")
    assert r["code"] == "password field"
    assert [x for x in b.d.log if x[0] in ("keys", "set_value")] == []
    assert b.d.windows[3001]["root"]["children"][1]["value"] == ""


def test_keys_are_not_sent_unless_the_target_really_owns_the_foreground():
    b, ops = make()
    b.d.windows[4001]["root"]["children"][0]["wm_text"] = ""
    orig = b.bring_to_front
    # focus succeeds (the window is in front), then another window takes the foreground before the keys: the second check must stop them
    b.set_focus = lambda el: (setattr(b.d, "foreground", 2001), True)[1]
    b.d.foreground = 4001
    r = ask(ops, "type", proc="oldtool", hwnd=4001, pid=400, elem="1001", text="secret words")
    assert r["code"] == "target is not the foreground window" and [x for x in b.d.log if x[0] == "keys"] == []
    b.bring_to_front = orig


def test_text_that_did_not_arrive_is_a_failure_not_a_success():
    b, ops = make()
    b.set_value = lambda el, text: None                              # the control ignores the value
    assert ask(ops, "type", proc="notepad", hwnd=1001, pid=100, elem="15", text="hello")["code"] == "text not in field"


def test_a_text_over_two_thousand_characters_is_refused():
    _, ops = make()
    assert ask(ops, "type", proc="notepad", hwnd=1001, pid=100, elem="15", text="a" * 2001)["code"] == "bad request"


# ------------------------------------------------------------------------------------------------------------------------------ window state
@pytest.mark.parametrize("show,want", [(6, "minimized"), (3, "maximized"), (9, "normal")])
def test_window_state_reads_the_state_back(show, want):
    b, ops = make()
    assert ask(ops, "window_state", proc="calc", hwnd=2001, pid=200, show=show, want=want)["result"] == {"state": want}


def test_a_window_that_did_not_change_state_is_a_pattern_failure():
    b, ops = make()
    b.show_window = lambda hwnd, cmd: None
    assert ask(ops, "window_state", proc="calc", hwnd=2001, pid=200, show=6, want="minimized")["code"] == "pattern"
    assert ask(ops, "window_state", proc="calc", hwnd=2001, pid=200, show=7, want="minimized")["code"] == "bad request"


# ------------------------------------------------------------------------------------------------------------------------------ paste (type_focused)
def test_paste_puts_the_text_in_the_focused_control_restores_the_clipboard_and_reads_it_back():
    b, ops = make()
    doc = b.d.windows[1001]["root"]["children"][0]
    b.d.focused, b.d.clipboard = doc, "the owner's clipboard"
    r = ask(ops, "paste", proc="notepad", hwnd=1001, pid=100, text="line one\nline two")
    assert r["result"] == {"typed": "read back"} and doc["text"] == "line one\nline two"
    assert b.d.clipboard == "the owner's clipboard"


def test_a_paste_that_does_not_land_is_text_not_in_field_except_in_an_editor_that_cannot_be_read():
    b, ops = make()
    doc = b.d.windows[1001]["root"]["children"][0]
    b.d.focused, b.d.paste_lands = doc, False
    assert ask(ops, "paste", proc="notepad", hwnd=1001, pid=100, text="never arrives")["code"] == "text not in field"
    assert ask(ops, "paste", proc="Code", hwnd=1001, pid=100, text="never arrives")["result"] == {"typed": "none"}, "Monaco holds a fragment: not observed, the save's disk read-back is the proof"


def test_nothing_is_pasted_when_another_window_has_the_foreground():
    b, ops = make()
    b.d.refuse_foreground = True
    assert ask(ops, "paste", proc="calc", hwnd=2001, pid=200, text="hello")["code"] == "target is not the foreground window"
    assert ("ctrl+v",) not in b.d.log


def test_a_clipboard_with_no_text_is_not_overwritten_afterwards():
    b, ops = make()
    b.d.focused, b.d.clipboard = b.d.windows[1001]["root"]["children"][0], None
    ask(ops, "paste", proc="notepad", hwnd=1001, pid=100, text="x")
    assert b.d.clipboard == "x", "there was nothing to put back (None), as in the script"


# ------------------------------------------------------------------------------------------------------------------------------ snapshot and window text
def test_snapshot_has_the_fields_of_ui_py_and_stops_at_the_limits():
    b, ops = make()
    r = ask(ops, "snapshot", proc="notepad", hwnd=1001, pid=100, max=60, depth=4, values=False)["result"]
    assert set(r) == {"window", "process", "class", "elements", "truncated"} and r["window"] == "Untitled - Notepad" and r["process"] == "notepad"
    assert set(r["elements"][0]) == {"depth", "type", "name", "id", "cls", "enabled", "offscreen", "value"} and r["truncated"] is False
    assert [e["name"] for e in r["elements"]] == ["Text editor", "File", "Status bar"] and all(e["value"] is None for e in r["elements"])
    assert ask(ops, "snapshot", proc="notepad", hwnd=1001, pid=100, max=2)["result"]["truncated"] is True


def test_snapshot_reads_values_but_never_from_a_password_box():
    b, ops = make()
    b.d.windows[3001]["root"]["children"][0]["value"] = "alice"
    b.d.windows[3001]["root"]["children"][1]["value"] = "hunter2"
    els = ask(ops, "snapshot", proc="loginapp", hwnd=3001, pid=300, values=True)["result"]["elements"]
    assert [e["value"] for e in els] == ["alice", None]


def test_snapshot_of_nothing_is_the_old_no_window_dict_and_proc_empty_means_the_foreground_window():
    b, ops = make()
    assert ask(ops, "snapshot", proc="nothere")["result"] == {"error": "no window"}
    assert ask(ops, "snapshot", proc="")["result"]["window"] == "Untitled - Notepad"


def test_snapshot_depth_limit():
    b, ops = make()
    b.d.windows[2001]["root"]["children"][0]["children"] = [fakeuia.node("deep", ctype="Text")]
    assert [e["depth"] for e in ask(ops, "snapshot", proc="calc", hwnd=2001, pid=200, depth=1)["result"]["elements"]] == [1, 1, 1]
    assert max(e["depth"] for e in ask(ops, "snapshot", proc="calc", hwnd=2001, pid=200, depth=2)["result"]["elements"]) == 2


def test_window_text_lists_texts_and_buttons_of_each_window_and_each_dialog_and_skips_passwords():
    b, ops = make()
    rows = ask(ops, "window_text", name="oldtool")["result"]["rows"]
    assert [r["title"] for r in rows] == ["Old tool", "Saved?"] and rows[1]["dialog"] is True and rows[1]["owner"] == "Old tool" and rows[1]["class"] == "#32770"
    assert rows[1]["texts"] == ["Save changes to Untitled?"] and rows[1]["buttons"] == ["Save", "Don't save"]
    assert rows[0]["dialog"] is False and "Save" in rows[0]["buttons"]
    login = ask(ops, "window_text", name="loginapp")["result"]["rows"][0]
    assert login["texts"] == ["User name"], "the password box is left out"
    assert ask(ops, "window_text", name="nothere")["result"] == {"rows": []}


# ------------------------------------------------------------------------------------------------------------------------------ the adapter
def test_the_real_adapter_and_the_fake_implement_every_primitive_so_a_library_can_be_swapped():
    import uia_lib_uiautomation as real
    need = {n for n, f in inspect.getmembers(W.Backend, inspect.isfunction) if not n.startswith("_")}
    for cls in (real.UiautomationBackend, fakeuia.FakeBackend):
        missing = {n for n in need if getattr(cls, n) is getattr(W.Backend, n)}
        assert not missing, f"{cls.__name__} does not implement {sorted(missing)}"


def test_typed_text_becomes_unicode_key_events_never_key_syntax():
    import uia_lib_uiautomation as real
    ev = real.unicode_events("a+{b}")
    assert [e[1] for e in ev[0::2]] == [ord(c) for c in "a+{b}"], "braces and plus are plain characters"
    assert all(e[0] == 0 and e[2] & real.KEYEVENTF_UNICODE for e in ev)
    assert [e[2] & real.KEYEVENTF_KEYUP for e in ev[:4]] == [0, real.KEYEVENTF_KEYUP, 0, real.KEYEVENTF_KEYUP]
    assert [(e[0], e[2]) for e in real.unicode_events("\n\t")] == [(0x0D, 0), (0x0D, 2), (0x09, 0), (0x09, 2)]
    assert [e[1] for e in real.unicode_events("\U0001F600")[0::2]] == [0xD83D, 0xDE00], "above U+FFFF: the two UTF-16 halves"
    assert len(real.unicode_events("a\r\nb")) == 6 and real.unicode_events("") == []
