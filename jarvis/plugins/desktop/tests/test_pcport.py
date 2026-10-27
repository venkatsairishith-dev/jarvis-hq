"""D65: the real Port is a thin translation: one kernel job per call, the right job body, local text kept local, refusals reported as a denied step."""
from __future__ import annotations

import pytest

from jarvis.plugins.desktop import core, pcport


class Pc:
    def __init__(self, answers=None):
        self.bodies, self.answers = [], answers or {}

    def __call__(self, body, timeout=90.0, what="", keep_local=False):
        self.bodies.append((dict(body), keep_local))
        key = body.get("check") or body.get("op")
        a = self.answers.get(key)
        return dict(a(body) if callable(a) else a if a is not None else {"ok": True})


def test_each_call_is_one_job_with_the_expected_body():
    pc = Pc({"windows": {"ok": True, "windows": [{"process": "notepad"}, "junk"], "idle_ms": 1200, "cursor": [5, 6]},
             "window_text": {"ok": True, "_local": [{"title": "t", "dialog": False, "texts": ["a"], "buttons": ["OK"]}, "junk"]},
             "window_ocr": {"ok": True, "_local": {"observed_lines": ["line one", "line two"]}},
             "window_vision": lambda b: {"ok": True, "_local": {"inferred": "seen text"}} if b["target"].endswith("|*") else {"ok": True, "ocr_point": {"x": 1, "y": 2}, "vision_point": {"x": 3, "y": 4}}})
    p = pcport.PcPort(pc)
    assert p.windows() == {"windows": [{"process": "notepad"}], "idle_ms": 1200, "cursor": [5, 6]}
    assert p.uia("notepad") == [{"title": "t", "dialog": False, "texts": ["a"], "buttons": ["OK"]}]
    assert p.ocr("notepad") == "line one\nline two" and p.vision("notepad") == "seen text"
    assert p.locate("notepad", "Save") == {"ocr": (1, 2), "vision": (3, 4)}
    assert [b for b, _ in pc.bodies] == [{"op": "windows"}, {"op": "diag", "check": "window_text", "target": "notepad"}, {"op": "diag", "check": "window_ocr", "target": "notepad"},
                                         {"op": "diag", "check": "window_vision", "target": "notepad|*"}, {"op": "diag", "check": "window_vision", "target": "notepad|Save"}]
    assert [k for _, k in pc.bodies] == [False, True, True, True, False], "window text is read as LOCAL text (taken from the node's local file); points are not text"


def test_a_failed_read_is_no_text_not_an_exception_and_a_bad_label_sends_nothing():
    pc = Pc({"window_text": {"ok": False}, "window_ocr": {"ok": False}, "window_vision": {"ok": False}})
    p = pcport.PcPort(pc)
    assert p.uia("x") == [] and p.ocr("x") == "" and p.vision("x") == "" and p.locate("x", "Save") == {}
    n = len(pc.bodies)
    assert p.locate("x", "a|b") == {} and p.locate("x", "") == {} and len(pc.bodies) == n
    with pytest.raises(ValueError):
        p.ocr("bad;name")


def test_a_refusal_of_the_kernel_is_a_denied_step_with_its_own_words():
    def pc(body, timeout=90.0, what="", keep_local=False):
        raise core.DesktopError("refused: that window belongs to a bank")
    r = pcport.PcPort(pc).act({"op": "ui", "action": "click", "target": "bank|Pay"})
    assert r["ok"] is False and r["state"] == "denied" and "bank" in r["error"]


def test_busy_means_its_cpu_time_grew_between_two_looks():
    cpu = iter([10.0, 11.5])
    pc = Pc({"app_info": {"ok": True, "rows": [{"responding": False}]}, "processes": lambda b: {"ok": True, "rows": [{"name": "excel", "cpu_s": next(cpu)}, {"name": "other", "cpu_s": 99}]}})
    slept = []
    info = pcport.PcPort(pc, sleep=slept.append).app_info("Excel.exe")
    assert info == {"responding": False, "busy": True} and slept == [1.0]
    pc = Pc({"app_info": {"ok": True, "rows": [{"responding": False}]}, "processes": {"ok": True, "rows": [{"name": "excel", "cpu_s": 10.0}]}})
    assert pcport.PcPort(pc, sleep=lambda s: None).app_info("excel")["busy"] is False
