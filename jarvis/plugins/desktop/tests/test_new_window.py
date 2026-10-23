"""'A new window showing the file appeared' (audit PC-D1, 2026-10-18): the node's window listing and the plugin's check
must agree on window identity. The real node function (jarvis_node.jobs.ui_windows) feeds the real plugin code
(core.window_ids/new_window and the research/open_output agents), then the real coordinator verify(). Fake lister, fake
console job runner, no Windows, no servers."""
import asyncio
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "jarvis" / "node"))
sys.path.insert(0, str(ROOT / "jarvis" / "kernel"))

from jarvis_node import jobs as nodejobs  # noqa: E402

from jarvis.plugins.desktop import DesktopPlugin, core  # noqa: E402
from jarvis.plugins.desktop import coordinator as C  # noqa: E402
from jarvis.plugins.desktop import decide as D  # noqa: E402
from jarvis.pluginapi import Ok  # noqa: E402

DESKTOP = {"hwnd": 11, "pid": 1, "process": "explorer", "title": "Downloads", "class": "x", "foreground": False,
           "minimized": False, "x": 0, "y": 0, "w": 800, "h": 600}
OLD_DECK = {"hwnd": 33, "pid": 3, "process": "POWERPNT", "title": "solar-deck - PowerPoint", "class": "x", "foreground": False,
            "minimized": False, "x": 0, "y": 0, "w": 800, "h": 600}
NEW_DECK = {"hwnd": 22, "pid": 2, "process": "POWERPNT", "title": "solar-deck - PowerPoint", "class": "x", "foreground": True,
            "minimized": False, "x": 0, "y": 0, "w": 800, "h": 600}


def listing(*ws):
    return nodejobs.ui_windows({}, lister=lambda: [dict(w) for w in ws])


# ---------------------------------------------------------------- the contract: node listing -> plugin check
def test_a_newly_opened_window_is_identified():
    before = core.window_ids(listing(DESKTOP))
    w, why = core.new_window(listing(DESKTOP, NEW_DECK), before, "solar-deck")
    assert w is not None and w["process"] == "POWERPNT" and why == ""


def test_an_existing_window_is_not_counted_as_new():
    before = core.window_ids(listing(DESKTOP, OLD_DECK))
    w, why = core.new_window(listing(DESKTOP, dict(OLD_DECK, foreground=True)), before, "solar-deck")   # only raised
    assert w is None and "no new window" in why


def test_an_existing_window_beyond_the_40_described_is_still_known():
    many = [dict(DESKTOP, hwnd=100 + i, title=f"Window {i}") for i in range(45)] + [OLD_DECK]
    before = core.window_ids(listing(*many))
    assert before is not None and len(before["ids"]) == 46
    w, _ = core.new_window(listing(OLD_DECK, *many[:-1]), before, "solar-deck")     # the old deck comes to the top
    assert w is None


@pytest.mark.parametrize("broken", [None, "", 0, "not-a-handle"])
def test_missing_or_bad_ids_never_verify(broken):
    before = core.window_ids(listing(DESKTOP))
    after = listing(DESKTOP, dict(NEW_DECK, hwnd=broken))
    assert after["ids_complete"] is False
    w, why = core.new_window(after, before, "solar-deck")
    assert w is None and "no usable window ids" in why
    # a "before" list without usable ids: no claim either, whatever comes after
    assert core.window_ids(listing(dict(DESKTOP, hwnd=broken))) is None
    assert core.new_window(listing(DESKTOP, NEW_DECK), None, "solar-deck")[0] is None


def test_an_old_node_without_ids_never_verifies():
    old = {"ok": True, "count": 2, "windows": [{"title": "Downloads", "process": "explorer"},
                                               {"title": "solar-deck - PowerPoint", "process": "POWERPNT"}]}
    assert core.window_ids(old) is None
    assert core.new_window(old, core.window_ids(listing(DESKTOP)), "solar-deck")[0] is None


def test_ids_from_another_node_process_are_stale(monkeypatch):
    before = core.window_ids(listing(DESKTOP))
    monkeypatch.setattr(nodejobs, "WINDOW_ID_EPOCH", "restarted0000")
    monkeypatch.setattr(nodejobs, "_WINDOW_ID_KEY", b"another key 1234")
    w, why = core.new_window(listing(DESKTOP, NEW_DECK), before, "solar-deck")
    assert w is None and "restarted" in why


def test_two_matching_new_windows_are_ambiguous_and_not_verified():
    """Documented rule: when two new windows both show the file's name, which one is the file is unknown -> unverified."""
    before = core.window_ids(listing(DESKTOP))
    twin = dict(NEW_DECK, hwnd=44, pid=4, foreground=False)
    w, why = core.new_window(listing(DESKTOP, NEW_DECK, twin), before, "solar-deck")
    assert w is None and "2 new windows" in why
    seen, why = core.wait_new_window(lambda: listing(DESKTOP, NEW_DECK, twin), before, "solar-deck", tries=2, sleep=lambda s: None)
    assert seen == "" and "2 new windows" in why


def test_a_window_with_another_name_is_not_the_file():
    before = core.window_ids(listing(DESKTOP))
    assert core.new_window(listing(DESKTOP, dict(NEW_DECK, title="Untitled - Notepad")), before, "solar-deck")[0] is None


# ---------------------------------------------------------------- the plugin's agents and the coordinator's verdict
class _Host:
    """Just enough of the plugin object for DesktopPlugin._agents: a live event loop and a fake plugin bus."""

    def __init__(self, on_open):
        self._cmd_loop = asyncio.new_event_loop()
        threading.Thread(target=self._cmd_loop.run_forever, daemon=True).start()
        host = self

        class Plugins:
            async def call_entry_json(self, entry, args, timeout=0):
                host.calls.append(entry)
                on_open()
                if entry == "research:research_topic":
                    return Ok({"summary": "three checked findings", "file": "D:\\out\\solar-deck.pptx", "qa_passed": True})
                return Ok({"opened": args.get("path")})
        self.plugins, self.calls = Plugins(), []

    def close(self):
        self._cmd_loop.call_soon_threadsafe(self._cmd_loop.stop)


@pytest.fixture
def desk(monkeypatch):
    """The plugin's agents over a fake console whose 'windows' jobs run the REAL node listing over `screen`."""
    screen = [dict(DESKTOP)]

    class FakePc:
        def __init__(self, *a, **k):
            self.current_job, self.stopped = None, False

        def run(self, body, timeout=90.0, what=""):
            if body.get("op") == "windows":
                return {"state": "done", "result": nodejobs.ui_windows({}, lister=lambda: [dict(w) for w in screen])}
            return {"state": "done", "result": {"ok": True}}
    monkeypatch.setattr(core, "Pc", FakePc)
    monkeypatch.setattr(core, "NEW_WINDOW_POLL_S", 0.0, raising=False)
    hosts = []

    def make(on_open):
        h = _Host(on_open)
        hosts.append(h)
        return DesktopPlugin._agents(h, h._cmd_loop)
    yield screen, make
    for h in hosts:
        h.close()


def _run(tmp_path, agents, step):
    return C.Coordinator(agents, [], tasks_dir=tmp_path / "tasks", lock_path=tmp_path / "lock").run("x", D.Plan([step], "ok", model="fake"))


OUTPUT = {"tool": "open_output", "path": "D:\\jarvis\\outputs\\solar-deck.pptx"}
RESEARCH = {"tool": "research", "query": "solar panels", "deliverable": "presentation", "open_after": True}


def test_open_output_passes_when_a_new_window_shows_the_file(tmp_path, desk):
    screen, make = desk
    agents = make(lambda: screen.append(dict(NEW_DECK)))
    rec = _run(tmp_path, agents, OUTPUT)
    assert rec["status"] == "completed" and rec["steps"][0]["passed"]
    assert "window showing it appeared" in rec["steps"][0]["checked"]


def test_open_output_already_open_elsewhere_is_not_passed(tmp_path, desk):
    screen, make = desk
    screen.append(dict(OLD_DECK))
    agents = make(lambda: screen.__setitem__(-1, dict(OLD_DECK, foreground=True)))    # PowerPoint only raises it
    rec = _run(tmp_path, agents, OUTPUT)
    assert rec["status"] == "failed" and not rec["steps"][0]["passed"]
    assert "no new window" in rec["steps"][0]["uncertainty"] and "Done" not in C.summary(rec)


def test_open_output_without_window_ids_is_not_passed(tmp_path, desk):
    screen, make = desk
    agents = make(lambda: screen.append(dict(NEW_DECK, hwnd=None)))
    rec = _run(tmp_path, agents, OUTPUT)
    assert rec["status"] == "failed" and not rec["steps"][0]["passed"]


def test_research_deck_opened_is_verified_and_not_when_two_windows_match(tmp_path, desk):
    screen, make = desk
    agents = make(lambda: screen.append(dict(NEW_DECK)))
    rec = _run(tmp_path, agents, RESEARCH)
    assert rec["status"] == "completed" and rec["steps"][0]["passed"], rec["steps"][0]
    del screen[1:]
    agents = make(lambda: screen.extend([dict(NEW_DECK), dict(NEW_DECK, hwnd=44, pid=4)]))
    rec = _run(tmp_path, agents, RESEARCH)
    assert rec["status"] == "failed" and not rec["steps"][0]["passed"]
    assert "no window showing it appeared" in rec["steps"][0]["checked"] and "2 new windows" in rec["error"]
