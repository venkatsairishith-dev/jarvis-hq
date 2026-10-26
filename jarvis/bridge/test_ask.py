"""Tests for the generic ask panel's backend (D55: generated UI = declarative forms only): the form language, the broker, the pattern guide, and
the three jobs the owner named (the decider's question, which file, where to save)."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

import pytest

from jarvis.bridge import ask as A
from jarvis.bridge import ask_spec as S
from jarvis.bridge import ask_tool as T
from jarvis.bridge import ui_patterns as U


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    for name in ("Downloads", "Documents", "Desktop", "Pictures"):
        (h / name).mkdir(parents=True)
    now = time.time()
    for i, (folder, name, size) in enumerate([("Downloads", "invoice-march.pdf", 2048), ("Documents", "notes.docx", 100), ("Desktop", "todo.txt", 10),
                                              ("Downloads", "my-password-list.txt", 5), ("Pictures", "cat.png", 70)]):
        p = h / folder / name
        if name.endswith(".png"):
            from PIL import Image
            Image.new("RGB", (300, 200), (200, 30, 30)).save(p)
        else:
            p.write_bytes(b"x" * size)
        os.utime(p, (now - i * 100, now - i * 100))
    return h


def walk(nodes):
    return list(S.iter_nodes(nodes))


def spec(*fields, **kw):
    return {"title": "T", "fields": list(fields), **kw}


# ---- the language ---------------------------------------------------------------------------------------------------------------------

def test_every_component_type_is_accepted_and_cleaned():
    full = spec(
        {"type": "section", "label": "S", "children": [{"id": "a", "type": "checkbox_group", "label": "A", "options": [{"id": "x", "label": "X", "size": 5, "checked": True}]}]},
        {"type": "row", "children": [{"type": "column", "children": [{"type": "callout", "text": "in a column"}]}, {"id": "b", "type": "radio", "variant": "segmented", "label": "B", "options": [{"id": "1", "label": "One"}, {"id": "2", "label": "Two"}]}]},
        {"type": "tabs", "tabs": [{"id": "t1", "label": "Tab", "children": [{"id": "c", "type": "select", "label": "C", "options": [{"id": "o", "label": "O"}]}]}]},
        {"type": "grid", "children": [{"id": "d", "type": "cards", "label": "D", "options": [{"id": "k", "label": "K", "pros": ["fast"], "cons": ["pricey"]}]}]},
        {"id": "e", "type": "split", "label": "E", "options": [{"id": "s1", "label": "S1", "body": "text\nmore"}]},
        {"id": "f", "type": "text", "label": "F", "multiline": True}, {"id": "g", "type": "number", "label": "G", "min": 0, "max": 10, "value": 50},
        {"id": "h", "type": "slider", "label": "H", "unit": "%"}, {"id": "i", "type": "toggle", "label": "I", "value": True},
        {"id": "j", "type": "date", "mode": "datetime", "label": "J", "value": "2026-10-26T09:30"},
        {"id": "k", "type": "table", "label": "K", "columns": [{"id": "n", "label": "Name"}], "rows": [{"id": "r1", "cells": {"n": "A"}}], "select": "many"},
        {"type": "info_table", "label": "L", "rows": [["a", "b"]]},
        {"type": "chart", "kind": "line", "labels": ["a", "b"], "series": [{"name": "s", "values": [1, 2]}]},
        {"type": "code_view", "text": "print(1)\n", "language": "python"}, {"type": "diff_view", "before": "a", "after": "b"},
        {"type": "steps", "items": [{"label": "one", "status": "done"}, {"label": "two", "status": "bogus"}]},
        {"type": "progress", "value": 30, "max": 60}, {"type": "progress"}, {"type": "callout", "level": "warn", "text": "careful"})
    out = S.clean_spec(full)
    types = {n["type"] for n in walk(out["fields"])}
    assert types == set(S.TYPES) - {"file_choice", "gallery"}, set(S.TYPES) - {"file_choice", "gallery"} - types
    assert "notes" not in out, "nothing needed simplifying"
    by = {n.get("id"): n for n in walk(out["fields"]) if n.get("id")}
    assert by["g"]["value"] == 10, "a number is clamped into its range"
    assert [i["status"] for i in next(n for n in walk(out["fields"]) if n["type"] == "steps")["items"]] == ["done", "pending"]
    progress = [n for n in walk(out["fields"]) if n["type"] == "progress"]
    assert progress[0]["value"] == 30 and progress[1]["indeterminate"] is True
    assert S.default_values(out)["a"] == ["x"] and S.default_values(out)["i"] is True


def test_every_text_is_plain_text():
    evil = "Click <a href='javascript:alert(1)'>here</a>\u202e\x00\u200b"
    out = S.clean_spec(spec({"id": "a", "type": "radio", "label": evil, "options": [{"id": "x", "label": evil, "detail": evil}]}, title=evil, intro=evil))
    flat = json.dumps(out, ensure_ascii=True)
    for bad in ("\\u202e", "\\u0000", "\\u200b"):
        assert bad not in flat
    assert "\n" not in out["title"] and "\n" not in out["intro"]
    # the markup stays as characters: the face draws every text with text interpolation, never as HTML
    assert "<a href" in out["intro"]


def test_an_unknown_component_or_bad_shape_falls_back_to_a_plain_list_with_a_note():
    out = S.clean_spec(spec({"id": "a", "type": "hologram", "label": "Spin", "options": [{"id": "x", "label": "Left"}, {"id": "y", "label": "Right"}]},
                            {"type": "tabs", "tabs": "nope"}, "not a node", {"id": "z", "type": "radio", "label": "Z", "options": []},
                            {"id": "b", "type": "chart", "labels": [], "series": []}, {"id": "t", "type": "table", "columns": [], "rows": []}))
    assert out["notes"] and all(n["type"] == "info_table" or n["type"] == "callout" for n in out["fields"]), out["fields"]
    first = out["fields"][0]
    assert first["type"] == "info_table" and ["Left", ""] in first["rows"] and ["Right", ""] in first["rows"]


def test_caps_on_nodes_depth_rows_and_characters():
    many = spec(*[{"type": "callout", "text": "x"} for _ in range(S.MAX_NODES + 40)])
    assert len(walk(S.clean_spec(many)["fields"])) <= S.MAX_NODES
    deep: dict = {"type": "callout", "text": "bottom"}
    for _ in range(8):
        deep = {"type": "section", "children": [deep]}
    out = S.clean_spec(spec(deep))
    assert out["notes"], "a part nested too deeply is simplified, with a note"
    rows = S.clean_spec(spec({"id": "t", "type": "table", "columns": [{"id": "c", "label": "C"}], "rows": [{"id": f"r{i}", "cells": {"c": "v"}} for i in range(900)]}))
    assert len(rows["fields"][0]["rows"]) == S.MAX_ROWS
    opts = S.clean_spec(spec({"id": "g", "type": "checkbox_group", "label": "G", "options": [{"id": f"o{i}", "label": "x"} for i in range(900)]}))
    assert len(opts["fields"][0]["options"]) == S.MAX_OPTIONS and opts["notes"]
    with pytest.raises(S.AskError):
        S.clean_spec(spec({"type": "code_view", "text": "y" * 20_000}, {"type": "diff_view", "before": "a" * 20_000, "after": "b" * 20_000}, {"type": "code_view", "text": "z" * 20_000}))


def test_a_form_never_asks_for_a_pin_or_a_password():
    for label in ("Your PIN", "Enter password", "One-time code", "API key"):
        with pytest.raises(S.AskError):
            S.clean_spec(spec({"id": "p", "type": "text", "label": label}))
    with pytest.raises(S.AskError):
        S.clean_spec(spec({"id": "p", "type": "text", "label": "Answer"}, title="Type your PIN here"))
    S.clean_spec(spec({"id": "p", "type": "info_table", "label": "PIN is asked in the console", "rows": [["a", "b"]]}))      # display text is fine
    with pytest.raises(S.AskError):
        S.clean_spec("not a spec")
    with pytest.raises(S.AskError):
        S.clean_spec({"title": "empty"})


def test_ids_must_be_unique_and_well_formed():
    out = S.clean_spec(spec({"id": "a", "type": "toggle", "label": "A"}, {"id": "a", "type": "toggle", "label": "dup"}, {"id": "9bad", "type": "toggle", "label": "bad"}))
    assert [n.get("id") for n in out["fields"] if n["type"] == "toggle"] == ["a"] and out["notes"]


def test_answers_are_checked_against_the_spec():
    sp = S.clean_spec(spec(
        {"id": "pick", "type": "radio", "label": "P", "required": True, "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]},
        {"id": "many", "type": "checkbox_group", "label": "M", "options": [{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}]},
        {"id": "n", "type": "number", "label": "N", "min": 1, "max": 5}, {"id": "t", "type": "text", "label": "T", "max_len": 5},
        {"id": "d", "type": "date", "label": "D"}, buttons=[{"id": "go", "label": "Go", "style": "primary"}, {"id": "no", "label": "No"}]))
    ok = S.validate_answer(sp, "go", {"pick": "b", "many": ["y", "x"], "n": 99, "t": "toolongtext", "d": "2026-01-02"})
    assert ok == {"pick": "b", "many": ["x", "y"], "n": 5, "t": "toolo", "d": "2026-01-02"}
    for bad in ({"pick": "zzz"}, {"pick": "a", "many": ["nope"]}, {"pick": "a", "n": "abc"}, {"pick": "a", "d": "tomorrow"}):
        with pytest.raises(S.AskError):
            S.validate_answer(sp, "go", bad)
    with pytest.raises(S.AskError):
        S.validate_answer(sp, "go", {})                                       # required, primary button
    assert S.validate_answer(sp, "no", {})["pick"] == "", "a secondary button (Cancel) does not need the required fields"
    with pytest.raises(S.AskError):
        S.validate_answer(sp, "hack", {"pick": "a"})


# ---- file lists come from the known folders only ------------------------------------------------------------------------------------------

def test_file_choice_lists_only_known_folders_and_hides_paths_from_the_panel(home):
    sp = S.clean_spec(spec({"id": "f", "type": "file_choice", "source": "recent", "label": "F", "required": True,
                            "options": [{"id": "evil", "label": "C:\\Windows\\win.ini", "path": "C:\\Windows\\win.ini"}]}), home)
    opts = sp["fields"][0]["options"]
    labels = [o["label"] for o in opts]
    assert "invoice-march.pdf" in labels and "notes.docx" in labels and "todo.txt" in labels
    assert "my-password-list.txt" not in labels, "a secret-looking name is never listed"
    assert "win.ini" not in json.dumps(opts), "the caller's own options are ignored"
    assert all("size" in o for o in opts)
    panel = S.for_panel(sp)
    assert "path" not in json.dumps(panel) and "invoice" in json.dumps(panel)
    bad = S.clean_spec(spec({"id": "f", "type": "file_choice", "source": "/etc", "label": "F"}), home)
    assert bad["notes"] and bad["fields"][0]["type"] == "info_table"


def test_gallery_lists_pictures_with_thumbnails(home):
    sp = S.clean_spec(spec({"id": "g", "type": "gallery", "source": "pictures", "label": "Pictures"}), home)
    opt = sp["fields"][0]["options"][0]
    assert opt["label"] == "cat.png" and opt["thumb"]["mime"] == "image/jpeg" and len(opt["thumb"]["data"]) < 20_000


def test_a_link_or_folder_is_never_listed(home):
    (home / "Downloads" / "sub").mkdir()
    if hasattr(os, "symlink"):
        try:
            os.symlink(home / "Documents" / "notes.docx", home / "Downloads" / "shortcut.pdf")
        except OSError:
            pass
    labels = [o["label"] for o in S.file_options("downloads", home)]
    assert "sub" not in labels and "shortcut.pdf" not in labels and "invoice-march.pdf" in labels


# ---- the broker ---------------------------------------------------------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


FORM = spec({"id": "pick", "type": "radio", "label": "P", "options": [{"id": "a", "label": "A", "checked": True}, {"id": "b", "label": "B"}]},
            buttons=[{"id": "ok", "label": "OK", "style": "primary"}, {"id": "cancel", "label": "Cancel"}])


def answer_later(broker, button="ok", values=None, delay=0.05, close=False):
    def go():
        time.sleep(delay)
        got = broker.wanted(0)
        form = got["form"]
        if close:
            broker.close(form["id"], form["token"])
        else:
            broker.submit(form["id"], form["token"], button, values if values is not None else form["values"])
    threading.Thread(target=go, daemon=True).start()


def test_ask_returns_the_owners_answer():
    b = A.AskBroker()
    answer_later(b, "ok", {"pick": "b"})
    got = run(b.ask(FORM, 5))
    assert got["answered"] is True and got["button"] == "ok" and got["values"] == {"pick": "b"}
    assert "not an approval" in got["note"] and b.wanted(0)["form"] is None


def test_ask_times_out_closes_and_is_replaced_without_inventing_an_answer():
    b = A.AskBroker()
    t0 = time.monotonic()
    assert run(b.ask(FORM, 0.2)) == {"answered": False, "reason": "timeout"} and time.monotonic() - t0 < 2
    assert b.wanted(0)["form"] is None, "a timed-out form leaves the panel"
    answer_later(b, close=True)
    assert run(b.ask(FORM, 5)) == {"answered": False, "reason": "closed"}

    async def replaced():
        first = asyncio.create_task(b.ask(FORM, 5))
        await asyncio.sleep(0.1)
        second = asyncio.create_task(b.ask(FORM, 0.3))
        return await first, await second
    one, two = run(replaced())
    assert one == {"answered": False, "reason": "replaced"} and two["reason"] == "timeout"


def test_submit_needs_the_forms_token_and_a_valid_answer():
    b = A.AskBroker()
    results = {}

    def waiter():
        results["r"] = run(b.ask(FORM, 5))
    th = threading.Thread(target=waiter)
    th.start()
    time.sleep(0.1)
    form = b.wanted(0)["form"]
    assert b.submit(form["id"], "wrong", "ok", {})["ok"] is False
    assert b.submit("q-other", form["token"], "ok", {})["ok"] is False
    assert b.submit(form["id"], form["token"], "ok", {"pick": "zzz"})["ok"] is False, "an option that is not on the form"
    assert b.submit(form["id"], form["token"], "ok", {"pick": "a"}) == {"ok": True, "done": True}
    th.join(3)
    assert results["r"]["values"] == {"pick": "a"}


def test_the_panels_long_poll_returns_at_once_when_a_form_opens():
    b = A.AskBroker()
    got = {}

    def poll():
        t = time.monotonic()
        got["r"] = b.wanted(0, wait_s=10)
        got["dt"] = time.monotonic() - t
    th = threading.Thread(target=poll)
    th.start()
    time.sleep(0.15)
    b.show(FORM)
    th.join(3)
    assert got["r"]["form"]["title"] == "T" and got["dt"] < 1.0
    seen = got["r"]["version"]
    assert b.wanted(seen) == {"version": seen, "unchanged": True}
    t = time.monotonic()
    assert b.wanted(seen, wait_s=0.2).get("unchanged") and time.monotonic() - t >= 0.15


def test_a_picked_file_is_answered_with_its_path_and_the_panel_never_saw_paths(home):
    b = A.AskBroker(home=home)
    sp = {"title": "Which file?", "fields": [{"id": "file", "type": "file_choice", "source": "recent", "label": "F", "required": True}]}
    out = {}

    def waiter():
        out["r"] = run(b.ask(sp, 5))
    th = threading.Thread(target=waiter)
    th.start()
    time.sleep(0.1)
    form = b.wanted(0)["form"]
    assert str(home) not in json.dumps(form), "no path reaches the panel"
    invoice = next(o for o in form["fields"][0]["options"] if o["label"] == "invoice-march.pdf")
    assert b.submit(form["id"], form["token"], "ok", {"file": invoice["id"]})["ok"]
    th.join(3)
    assert out["r"]["values"]["file"] == str(home / "Downloads" / "invoice-march.pdf")


def test_a_form_with_a_handler_stays_open_and_can_be_updated():
    b = A.AskBroker()
    seen = []

    def handler(button, values, form_id):
        seen.append((button, values))
        b.update(form_id, {"title": "Done", "intro": "ok", "fields": []}, busy=False)
        return {}
    fid = b.show(FORM, handler=handler, key="k")
    form = b.wanted(0)["form"]
    assert b.submit(fid, form["token"], "ok", {"pick": "b"}) == {"ok": True, "done": False}
    assert seen == [("ok", {"pick": "b"})] and b.wanted(0)["form"]["title"] == "Done"
    assert b.show(FORM, key="k") == fid, "the same key updates the open form in place"
    busy_id = b.show(FORM, handler=handler, key="k2")
    b.update(busy_id, busy=True)
    form = b.wanted(0)["form"]
    assert b.submit(busy_id, form["token"], "ok", {})["ok"] is False, "a click while it is working is refused"


def test_a_handler_that_raises_gives_the_panel_a_sentence_not_a_stack():
    b = A.AskBroker()
    fid = b.show(FORM, handler=lambda *a: 1 / 0)
    res = b.submit(fid, b.wanted(0)["form"]["token"], "ok", {})
    assert res["ok"] is False and "ZeroDivisionError" in res["message"] and "Traceback" not in json.dumps(res)


def test_the_ask_module_cannot_approve_anything():
    for mod in (A, S, T, U):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for forbidden in ("jarvis.plugins.jarvis_bridge", "client.approve", "kernel.policy", "pin_hash", "approve("):
            assert forbidden not in src, (mod.__name__, forbidden)


# ---- the pattern guide ---------------------------------------------------------------------------------------------------------------------

SAMPLES = {
    "pick_file": {"source": "recent"}, "choose": {"question": "Which?", "options": [{"label": "Notepad"}, {"label": "WordPad"}]},
    "settings": {"title": "Sound", "items": [{"id": "vol", "label": "Volume", "kind": "slider", "value": 40, "unit": "%"}, {"id": "mute", "label": "Mute", "kind": "toggle"},
                                              {"id": "dev", "label": "Output", "kind": "choice", "value": "b", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}]},
    "code": {"title": "Fix", "code": "x = 1\nprint(x)\n", "language": "python", "explanation": "Sets x.", "can_type": True},
    "compare": {"title": "Laptops", "columns": [{"id": "n", "label": "Name"}, {"id": "p", "label": "Price", "align": "right"}],
                "rows": [{"id": "a", "cells": {"n": "A", "p": "900"}}, {"id": "b", "cells": {"n": "B", "p": "1200"}}]},
    "job": {"title": "Building", "steps": [{"label": "Fetch", "status": "done"}, {"label": "Build", "status": "running"}], "value": 40, "label": "40%"},
    "data_view": {"title": "Sales", "kind": "bar", "labels": ["Jan", "Feb"], "series": [{"name": "2026", "values": [3, 5]}]},
    "confirm": {"title": "Send", "rows": [["To", "Asha"], ["Files", "2"]], "warn": "This cannot be undone."},
    "schedule": {"title": "Remind me", "note": "Dentist"}, "text": {"question": "What name?", "multiline": True},
    "save": {"what": "the report"}, "clarify": {"question": "Which one: notepad or wordpad?"},
}


def test_the_guide_file_and_the_builders_agree():
    guide = U.guide()
    assert set(guide) == set(U.PATTERN_IDS) and set(U.BUILDERS) | {"cleanup"} == set(guide)
    for pid, entry in guide.items():
        assert {"task", "recipe", "when", "data"} <= set(entry), pid
    assert set(SAMPLES) == set(U.BUILDERS), "every pattern has a sample in this test"
    for pid in U.PATTERN_IDS:
        assert pid in U.id_line()
    from jarvis.bridge.face_tools import CHAT_TOOLS
    for pid in U.PATTERN_IDS:
        assert pid in CHAT_TOOLS["jarvis_ask"], f"the model is told the id {pid}"


@pytest.mark.parametrize("pattern", sorted(SAMPLES))
def test_each_pattern_renders_from_sample_data(pattern, home):
    sp = S.clean_spec(U.build(pattern, SAMPLES[pattern]), home)
    assert sp["fields"] and sp["buttons"] and not sp.get("notes"), (pattern, sp.get("notes"))
    inputs = [n["id"] for n in S.iter_inputs(sp["fields"])]
    assert len(inputs) == len(set(inputs))
    S.validate_answer(sp, sp["buttons"][-1]["id"], S.default_values(sp))          # the last button (Cancel or Done) always works on the defaults
    json.dumps(S.for_panel(sp))


@pytest.mark.parametrize("pattern", sorted(SAMPLES))
def test_each_pattern_survives_nonsense_data(pattern, home):
    for data in (None, {}, [], "text", {"options": "x", "items": 5, "rows": {"a": 1}, "steps": [1, None], "series": "no", "columns": 3}):
        try:
            sp = S.clean_spec(U.build(pattern, data), home)
        except S.AskError:
            continue                                                               # refused with a reason is fine; a crash is not
        assert isinstance(sp["fields"], list)


def test_choose_picks_the_shape_that_fits():
    def kind(options):
        return S.clean_spec(U.choose({"options": options}))["fields"][0]
    assert kind([{"label": "Notepad"}, {"label": "WordPad"}])["variant"] == "segmented"
    assert kind([{"label": "A", "pros": ["fast"]}, {"label": "B", "cons": ["slow"]}])["type"] == "cards"
    long = kind([{"label": f"Option number {i} with a long name", "detail": "d"} for i in range(8)])
    assert long["type"] == "radio" and long["variant"] == "list"


def test_the_decider_question_becomes_a_choice(home):
    sp = S.clean_spec(U.clarify({"question": "Which one: notepad or wordpad?"}))
    field = sp["fields"][0]
    assert field["type"] == "radio" and [o["label"] for o in field["options"]] == ["notepad", "wordpad"] and field["required"]
    answered = S.validate_answer(sp, "ok", {"choice": "b"})
    assert answered == {"choice": "b"}
    other = S.clean_spec(U.clarify({"question": "What should I research?"}))
    assert other["fields"][0]["type"] == "text"


def test_which_file_lists_recent_files_with_names_and_sizes(home):
    sp = S.clean_spec(U.clarify({"question": "Which file?"}), home)
    field = sp["fields"][0]
    assert field["type"] == "file_choice" and field["source"] == "recent" and field["required"]
    first = field["options"][0]
    assert first["label"] == "invoice-march.pdf" and first["size"] == 2048
    assert U.pick_file({"source": "pictures"})["fields"][0]["type"] == "gallery"


def test_choose_where_to_save_offers_only_the_known_folders(home, monkeypatch):
    out = home / "outputs"
    out.mkdir()
    monkeypatch.setattr("jarvis.bridge.output_preview.default_root", lambda: out)
    sp = S.clean_spec(U.save({"what": "the report"}), home)
    opts = sp["fields"][0]["options"]
    assert [o["label"] for o in opts] == ["Downloads", "Documents", "Desktop", "Pictures", "JARVIS outputs"]
    paths = S.paths_of(sp)["folder"]
    assert set(paths.values()) == {str(home / n) for n in ("Downloads", "Documents", "Desktop", "Pictures")} | {str(out)}


def test_confirm_says_the_pin_is_still_asked_in_the_console():
    sp = S.clean_spec(U.confirm({"title": "Send it", "rows": [["To", "A"]]}))
    texts = json.dumps(sp)
    assert "PIN" in texts and [b["id"] for b in sp["buttons"]] == ["approve", "cancel"]


def test_job_form_is_updated_in_place_and_remembers_a_cancel_click():
    b = A.AskBroker()
    first = run(T.ask(b, None, None, "job", SAMPLES["job"]))
    assert first["shown"] is True and first["answered"] is False
    form = b.wanted(0)["form"]
    assert [n["type"] for n in form["fields"]] == ["steps", "progress"]
    b.submit(form["id"], form["token"], "cancel", {})
    again = run(T.ask(b, None, None, "job", {**SAMPLES["job"], "value": 80}))
    assert again["owner_clicked"] == "cancel"
    assert b.wanted(0)["form"]["id"] == form["id"] and b.wanted(0)["form"]["fields"][1]["value"] == 80


def test_the_ask_tool_never_raises():
    b = A.AskBroker()
    assert run(T.ask(b, None, None, "nope", {}))["reason"] == "invalid"
    assert run(T.ask(b, "not a spec", None, "", None))["reason"] == "invalid"
    assert run(T.ask(b, {"fields": [{"id": "p", "type": "text", "label": "PIN"}]}, None, "", None))["reason"] == "invalid"
