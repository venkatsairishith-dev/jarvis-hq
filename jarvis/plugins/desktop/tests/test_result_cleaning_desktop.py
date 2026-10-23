"""Re-review M12: what the desktop plugin's entries return to the chat model is cleaned at the plugin boundary, field by field and
by default. Each test runs the REAL entry twice: undecorated (`__wrapped__`, to reproduce the leak the review found) and as the host
calls it (cleaned). Synthetic canaries only; no model, no node."""
import asyncio
import json

import pytest

from jarvis.plugins._shared import screen_text as st
from jarvis.plugins._shared.tests.fakes import ALL, C_EMAIL, C_FILE, C_KEY, C_OCR, C_PATH, C_STREET
from jarvis.plugins.desktop import DesktopPlugin
from jarvis.plugins.desktop import coordinator as C
from jarvis.plugins.desktop import missions


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    monkeypatch.delenv(st.FLAG, raising=False)


def leaks(value):
    raw = json.dumps(value, default=str, ensure_ascii=False)
    return [c for c in ALL if c in raw]


STEP = {"n": 1, "tool": "open_app", "agent": "desktop", "attempts": 1, "passed": True,
        "checked": f"the window {C_PATH} appeared; {C_OCR} was visible", "uncertainty": f"unsure about {C_STREET}"}


class _Host:
    def __init__(self):
        self._cmd_loop = None

        class Plugins:
            async def call_entry_json(self, *a, **k):
                raise AssertionError("not used")
        self.plugins = Plugins()
        for name in ("_agents", "_stop_pc_tasks"):
            setattr(self, name, getattr(DesktopPlugin, name).__get__(self))

    async def _missions_ready(self):
        return True


def test_run_pc_task_steps_checked_uncertain_question_are_cleaned(monkeypatch):
    class Stub:
        def __init__(self, agents, profiles, cancelled=lambda: False, **kw):
            pass

        def run(self, request):
            return {"id": "t-1", "status": "needs_clarification", "steps": [dict(STEP, uncertain=[f"{C_EMAIL}"])],
                    "question": f"Which one: {C_FILE} or {C_EMAIL}?", "uncertain": [f"{C_OCR} {C_PATH}"],
                    "plan": {"model": "m", "seconds": 1.5}, "route": "pc"}
    monkeypatch.setattr(C, "Coordinator", Stub)
    monkeypatch.setattr(C, "summary", lambda rec: f"Asked: {rec['question']}")
    raw = asyncio.run(DesktopPlugin.run_pc_task.__wrapped__(_Host(), request="open the budget"))
    assert leaks(raw.value), "the review's leak must reproduce on the undecorated entry"
    got = asyncio.run(DesktopPlugin.run_pc_task(_Host(), request="open the budget"))
    assert leaks(got.value) == [] and got.value["status"] == "needs_clarification" and got.value["task"] == "t-1"
    assert got.value["steps"][0]["n"] == 1 and got.value["steps"][0]["passed"] is True and got.value["plan_seconds"] == 1.5
    assert "<a .xlsx file>" in got.value["question"] and "[email]" in got.value["question"]


def test_mission_status_steps_explain_and_question_are_cleaned(monkeypatch):
    rec = {"id": "m1", "goal": f"make slides for {C_FILE}", "status": "waiting_for_permission", "replans": 0, "steps": [dict(STEP)],
           "error": f"failed on {C_PATH} {C_KEY}", "question": f"Open {C_PATH}?", "approval_id": "a1", "how": "approve in the console",
           "explain": f"File: {C_PATH}\nthe report for {C_OCR}"}
    monkeypatch.setattr(missions, "latest", lambda: rec)
    monkeypatch.setattr(missions, "load", lambda m: rec)
    host = _Host()
    raw = asyncio.run(DesktopPlugin.mission_status.__wrapped__(host))
    assert leaks(raw.value), "the review's leak must reproduce on the undecorated entry"
    got = asyncio.run(DesktopPlugin.mission_status(host))
    assert leaks(got.value) == [] and got.value["mission"] == "m1" and got.value["status"] == "waiting_for_permission"
    assert got.value["approval_id"] == "a1" and got.value["replans"] == 0


def test_every_desktop_and_bridge_entry_is_wrapped_so_a_future_entry_cannot_forget_it():
    """A new @plugin_entry without @screen_text.cleaned fails here (the boundary is the default, not a list of entries)."""
    from jarvis.plugins.jarvis_bridge import JarvisBridgePlugin
    from jarvis.pluginapi.decorators import ENTRY_META_ATTR as EVENT_META_ATTR
    for cls in (DesktopPlugin, JarvisBridgePlugin):
        entries = [(n, f) for n, f in vars(cls).items() if callable(f) and getattr(f, EVENT_META_ATTR, None) is not None
                   and getattr(getattr(f, EVENT_META_ATTR), "event_type", "") == "plugin_entry"]
        assert entries, cls
        unwrapped = [n for n, f in entries if not getattr(f, "__screen_cleaned__", False)]
        assert unwrapped == [], (cls.__name__, unwrapped)
