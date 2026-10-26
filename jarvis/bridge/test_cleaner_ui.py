"""Tests for the cleaner panel's service and chat tool (D55): what the owner's words may tick and run, the form it builds, progress, and that the
chat model never sees a file name."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time

import pytest

from jarvis.bridge import ask as A
from jarvis.bridge import ask_tool as T
from jarvis.bridge import cleaner_ui as U
from jarvis.bridge.ask_spec import iter_inputs, iter_nodes
from jarvis.bridge.test_cleaner import NOW, World, make_review


@pytest.fixture
def pc(tmp_path):
    w = World(tmp_path / "pc")
    me = "Users/me"
    w.mk(f"{me}/AppData/Local/Temp/secret-report-2026.tmp", 1000, 2)
    w.mk("Windows/Temp/w.tmp", 4000, 3)
    w.mk(f"{me}/AppData/Local/Google/Chrome/User Data/Default/Cache/c1", 600, 5)
    make_review(w)
    broker = A.AskBroker(home=w.env.home)
    service = U.CleanerService(w.cleaner, broker, spawn=lambda fn: fn())            # background work runs inline: deterministic
    return w, broker, service


def form(broker):
    return broker.wanted(0)["form"]


def ticked_ids(f):
    return {o["id"] for n in iter_inputs(f["fields"]) for o in n.get("options", []) if o.get("checked")}


def test_open_scans_shows_the_panel_with_safe_rows_ticked_and_review_rows_not(pc):
    w, broker, svc = pc
    out = svc.tool("open", "open the cleaner")
    f = form(broker)
    assert f["title"] == "Clean up this PC" and not f["busy"]
    safe = next(n for n in iter_inputs(f["fields"]) if n["id"] == "safe")
    review = next(n for n in iter_inputs(f["fields"]) if n["id"] == "review")
    assert [o["id"] for o in safe["options"]][:2] == ["user_temp", "windows_temp"] and all(o.get("checked") for o in safe["options"])
    assert review["options"] and not any(o.get("checked") for o in review["options"]), "review rows are never ticked for the owner"
    assert any(n.get("collapsed") for n in iter_nodes(f["fields"])), "the review section starts collapsed"
    assert {b["id"] for b in f["buttons"]} == {"clean", "scan", "close"}
    assert out["ok"] and out["phase"] == "ready" and out["ticked_bytes"] > 0
    assert (w.root / "Users/me/Downloads/setup.exe").exists(), "nothing was removed by opening"
    assert (w.root / "Users/me/AppData/Local/Temp/secret-report-2026.tmp").exists()


def test_the_chat_model_gets_ids_sizes_and_counts_but_no_file_names(pc):
    w, broker, svc = pc
    results = [svc.tool("open", "open the cleaner"), svc.tool("check", "old installers and duplicates"), svc.tool("list"), svc.tool("clean", "clean now")]
    text = json.dumps(results)
    for name in ("secret-report", "setup.exe", "report.pdf", "copy-of-report", "Downloads", "AppData", "disk.img", "Users", "c1"):
        assert name not in text, name
    assert results[0]["rows"][0].keys() >= {"id", "label", "bytes", "files", "kind"}
    # ...while the owner's panel does show them
    svc.on_click("scan", {}, svc.form_id)
    assert any("disk.img" in o["label"] for n in iter_inputs(form(broker)["fields"]) for o in n.get("options", []))


def test_check_and_uncheck_by_plain_words(pc):
    w, broker, svc = pc
    svc.tool("open")
    svc.tool("uncheck", "everything")
    assert svc.list()["ticked"] == []
    out = svc.tool("check", "temp")
    assert {t["id"] for t in out["ticked"]} == {"user_temp", "windows_temp"}
    svc.tool("uncheck", "windows temp")
    assert {t["id"] for t in svc.list()["ticked"]} == {"user_temp"}, "the longer phrase picks the one row"
    out = svc.tool("check", "browser cache, recycle bin and the flux capacitor")
    assert {t["id"] for t in out["ticked"]} == {"user_temp", "browser_cache", "recycle_bin"} and out["unknown"] == ["the flux capacitor"]
    assert ticked_ids(form(broker)) >= {"user_temp", "browser_cache", "recycle_bin"}, "the open panel follows the owner's words"


def test_all_and_everything_never_tick_a_review_row(pc):
    w, broker, svc = pc
    svc.tool("open")
    svc.tool("uncheck", "all")
    svc.tool("check", "everything")
    out = svc.list()
    assert out["ticked"] and all(t["kind"] == "safe" for t in out["ticked"])
    svc.tool("check", "all safe")
    assert all(t["kind"] == "safe" for t in svc.list()["ticked"])


def test_a_review_row_is_ticked_only_when_the_owner_names_it(pc):
    w, broker, svc = pc
    svc.tool("open")
    out = svc.tool("check", "also the old installers")
    review = [t for t in out["ticked"] if t["kind"] == "review"]
    assert [t["id"] for t in review] == ["old_installers"] and review[0]["files"] == 1 and review[0]["bytes"] == 5000
    assert ticked_ids(form(broker)) >= {f"old_installers:{i['id']}" for i in svc.rows[10]["items"]}
    assert not any(t["id"] in ("duplicates", "big_unused", "empty_folders") for t in out["ticked"])


def test_clean_runs_only_when_the_owners_words_say_clean(pc):
    w, broker, svc = pc
    svc.tool("open")
    for words in ("", "go ahead", "sure", "the temp files look big", "do not clean yet", "don't clean anything", "not yet, clean later maybe"):
        out = svc.tool("clean", words)
        assert out["ok"] is False and "clean" in out["message"].lower(), words
    assert (w.root / "Windows/Temp/w.tmp").exists() and svc.phase == "ready"
    for words in ("clean", "clean up", "Clean now.", "yes, clean it"):
        svc.phase, svc.idle = "ready", threading.Event()
        svc.idle.set()
        svc.safe = {"windows_temp"}
        assert U.says_clean(words), words
    out = svc.tool("clean", "clean now")
    assert out["ok"] and out["phase"] == "done" and out["result"]["freed_bytes"] == 4000
    assert not (w.root / "Windows/Temp/w.tmp").exists()


def test_clean_with_nothing_ticked_does_nothing(pc):
    w, broker, svc = pc
    svc.tool("open")
    svc.tool("uncheck", "all")
    out = svc.tool("clean", "clean up")
    assert out["ok"] is False and "Nothing is ticked" in out["message"]
    assert (w.root / "Users/me/AppData/Local/Temp/secret-report-2026.tmp").exists()


def test_clean_before_the_panel_is_open_is_refused(pc):
    w, broker, svc = pc
    assert svc.tool("clean", "clean up my PC")["ok"] is False


def test_clean_up_my_pc_runs_the_safe_list_and_nothing_from_the_review_list(pc):
    w, broker, svc = pc
    out = svc.tool("open", "clean up my PC")
    assert out["phase"] == "done" and "safe list" in out["message"]
    assert not (w.root / "Users/me/AppData/Local/Temp/secret-report-2026.tmp").exists() and not (w.root / "Windows/Temp/w.tmp").exists()
    assert w.bin.emptied, "the safe list includes the Recycle Bin row"
    for rel in ("Users/me/Downloads/setup.exe", "Users/me/Downloads/report (1).pdf", "Users/me/Documents/disk.img", "Users/me/Downloads/empty"):
        assert (w.root / rel).exists(), rel
    assert w.bin.trashed == []
    chrome = w.root / "Users/me/AppData/Local/Google/Chrome/User Data/Default"
    assert not (chrome / "Cache" / "c1").exists()


def test_open_without_clean_words_does_not_clean(pc):
    w, broker, svc = pc
    for words in ("open the cleaner", "show me what can be cleaned", "I want to clean up later", "how do I clean up my PC later", "don't clean up my PC"):
        svc2 = U.CleanerService(w.cleaner, A.AskBroker(home=w.env.home), spawn=lambda fn: fn())
        out = svc2.tool("open", words)
        assert out["phase"] == "ready", words
        assert (w.root / "Windows/Temp/w.tmp").exists(), words


def test_the_panels_clean_button_is_the_owners_confirmation(pc):
    w, broker, svc = pc
    svc.tool("open")
    f = form(broker)
    inst = next(o["id"] for n in iter_inputs(f["fields"]) if n["id"] == "review" for o in n["options"] if o["id"].startswith("old_installers:"))
    res = broker.submit(f["id"], f["token"], "clean", {"safe": ["windows_temp"], "review": [inst]})
    assert res == {"ok": True, "done": False}
    assert not (w.root / "Windows/Temp/w.tmp").exists() and not (w.root / "Users/me/Downloads/setup.exe").exists()
    assert (w.root / "Users/me/AppData/Local/Temp/secret-report-2026.tmp").exists(), "only what was ticked"
    assert w.bin.trashed and w.bin.trashed[0].endswith("setup.exe")
    done = form(broker)
    assert done["title"] == "Done" and "Freed 4 KB" in done["intro"]
    tables = [n for n in iter_nodes(done["fields"]) if n["type"] == "info_table"]
    assert any(r[0] == "Moved to the Recycle Bin" for t in tables for r in t["rows"]), "the result names the bin and keeps an undo list"
    assert any("setup.exe" in r[0] for t in tables for r in t["rows"])


def test_the_panel_cannot_tick_what_is_not_on_the_form(pc):
    w, broker, svc = pc
    w.mk("Windows/System32/kernel.dll", 10, 100)
    svc.tool("open")
    f = form(broker)
    res = broker.submit(f["id"], f["token"], "clean", {"safe": ["system32"], "review": ["duplicates:../../Windows"]})
    assert res["ok"] is False
    assert (w.root / "Windows/System32/kernel.dll").exists()


def test_clicking_clean_with_nothing_ticked_says_so(pc):
    w, broker, svc = pc
    svc.tool("open")
    f = form(broker)
    broker.submit(f["id"], f["token"], "clean", {"safe": [], "review": []})
    after = broker.wanted(0)
    assert after["form"]["status"] == "Tick something first." and after["form"]["title"] == "Clean up this PC"


def test_scan_again_and_close(pc):
    w, broker, svc = pc
    svc.tool("open")
    f = form(broker)
    (w.root / "Windows/Temp/more.tmp").write_bytes(b"z" * 50)
    os.utime(w.root / "Windows/Temp/more.tmp", (NOW - 5 * 86400,) * 2)
    broker.submit(f["id"], f["token"], "scan", {})
    row = next(o for n in iter_inputs(form(broker)["fields"]) for o in n.get("options", []) if o["id"] == "windows_temp")
    assert row["size"] == 4050
    f = form(broker)
    assert broker.submit(f["id"], f["token"], "close", {}) == {"ok": True, "done": True}
    assert broker.wanted(0)["form"] is None and svc.form_id == ""


def test_progress_reaches_the_panel_while_it_works(pc):
    w, broker, svc = pc
    seen = []
    real = broker.update

    def spy(form_id, spec=None, busy=None, status=None, progress=None, clear_progress=False):
        if progress:
            seen.append((busy, status, dict(progress)))
        return real(form_id, spec, busy=busy, status=status, progress=progress, clear_progress=clear_progress)
    broker.update = spy
    svc.tool("open")
    scans = [p for b, s, p in seen if "total" in p and p.get("label")]
    assert scans and scans[-1]["done"] == scans[-1]["total"] == 13
    svc.tool("clean", "clean up")
    cleaning = [p for b, s, p in seen if "bytes_total" in p]
    assert cleaning and cleaning[-1]["done"] == cleaning[-1]["total"] and cleaning[-1]["bytes_total"] > 0


def test_the_cleaner_row_texts_are_plain_text(pc):
    w, broker, svc = pc
    svc.tool("open")
    text = json.dumps(form(broker))
    assert "<script" not in text and "javascript:" not in text


# ---- through the real tool server ----------------------------------------------------------------------------------------------------------

def test_the_tools_are_registered_and_the_panel_calls_work(pc):
    import jarvis.bridge.face_tools as F
    from jarvis.bridge.test_face_tools import FakeClient, tools
    w, broker, svc = pc
    svc.spawn = lambda fn: fn()
    server = F.build_server(tools(FakeClient()), profile="chat", asker=broker, cleaner=svc)

    async def go():
        listed = {t.name for t in await server.list_tools()}
        opened = await server.call_tool("jarvis_cleaner", {"action": "open", "items": "open the cleaner"})
        wanted = await server.call_tool("jarvis_ask_wanted", {"seen": 0, "wait_s": 0})
        return listed, opened, wanted
    listed, opened, wanted = asyncio.run(go())
    assert {"jarvis_ask", "jarvis_cleaner"} <= listed and not listed & {"jarvis_ask_wanted", "jarvis_ask_submit", "jarvis_ask_close"}
    assert "secret-report" not in str(opened) and "Clean up this PC" in str(wanted)


def test_jarvis_ask_cleanup_pattern_opens_the_cleaner_for_the_model_without_names(pc):
    w, broker, svc = pc
    out = asyncio.run(T.ask(broker, svc, None, "cleanup", None))
    assert out["phase"] == "ready" and "secret-report" not in json.dumps(out)
