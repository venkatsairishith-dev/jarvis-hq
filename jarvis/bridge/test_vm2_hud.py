"""D79: the HUD says how VM2 is, in four fixed words, and says nothing when VM2 is not set up."""
import asyncio
import json
import time

import pytest

from jarvis.bridge import face_tools as F
from jarvis.bridge.test_face_tools import CONSOLE, FakeClient
from jarvis.plugins._shared import vm2 as V
from jarvis.plugins._shared.tests.vm2_rig import Rig


def tools(tmp_path, cfg, **kw):
    return F.FaceTools(client_factory=lambda: FakeClient(), console=CONSOLE, pc_ops_path=tmp_path / "none.json", goal_progress_path=tmp_path / "no-goal.json",
                       vm2_state_path=tmp_path / "vm2_state.json", vm2_config=cfg, **kw)


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


def test_nothing_is_added_when_vm2_is_not_set_up(tmp_path):
    got = asyncio.run(tools(tmp_path, V.load_config(tmp_path / "none.json")).status())
    assert "vm2" not in got and "pc_line" not in got


@pytest.mark.parametrize("setup,word,line", [
    (lambda lab: None, "ready", "VM2 ready"),
    (lambda lab: lab.start_task(), "busy", "VM2 busy"),
    (lambda lab: (lab.start_task(), lab.take()), "needs_you", "VM2 needs you"),
])
def test_the_four_states_reach_the_hud_as_fixed_words(tmp_path, rig, setup, word, line):
    setup(rig.lab)
    got = asyncio.run(tools(tmp_path, rig.cfg).status())
    assert got["vm2"] == word and got["pc_line"] == line and F.PC_LINE_OK.match(got["pc_line"])


def test_offline_is_said_and_the_line_sits_next_to_a_pc_task_line(tmp_path, rig):
    rig.lab.close()
    p = tmp_path / "pc_task_state.json"
    p.write_text(json.dumps({"line": "Opening notepad", "state": "running", "updated": time.time()}), encoding="utf-8")
    t = tools(tmp_path, rig.cfg, pc_line_path=p, clean=lambda v: dict(v))
    got = asyncio.run(t.status())
    assert got["vm2"] == "offline" and got["pc_line"] == "Opening notepad - VM2 offline"


def test_a_pc_task_waiting_for_the_owner_at_a_screen_shows_in_the_hud_line(tmp_path):
    p = tmp_path / "pc_task_state.json"
    line = "I stopped because a step needs you at the screen. I will look again when you press Done."
    p.write_text(json.dumps({"line": line, "state": "needs_owner", "updated": time.time()}), encoding="utf-8")
    got = asyncio.run(F.FaceTools(client_factory=lambda: FakeClient(), console=CONSOLE, pc_ops_path=tmp_path / "none.json", goal_progress_path=tmp_path / "no-goal.json",
                                  pc_line_path=p, clean=lambda v: dict(v), vm2_config=V.Vm2Config()).status())
    assert got["pc_line"] == line


def test_a_broken_vm2_never_hides_the_rest_of_the_status(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "hud_fields", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    got = asyncio.run(tools(tmp_path, V.Vm2Config(enabled=True, control_url="http://127.0.0.1:9")).status())
    assert got["ok"] is True and "vm2" not in got
