"""Unknown data types fail closed in the shared cleaners: bytes, objects (with a __str__ that raises or holds a canary), huge or
cyclic structures never raise, never loop, and never carry a canary through (tool inventory pass F, 2026-10-19)."""
from __future__ import annotations

import json
import time

import pytest

from jarvis.plugins._shared import screen_text as st
from jarvis.plugins._shared.tests.fakes import C_EMAIL, C_KEY, C_PATH


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(st.FLAG, raising=False)


class Raises:
    def __str__(self):
        raise RuntimeError("no")

    __repr__ = __str__


class Holds:
    def __str__(self):
        return f"holds {C_EMAIL} and {C_PATH} and {C_KEY}"


def dump(v):
    return json.dumps(v, ensure_ascii=False, default=str)


def cyc_list():
    a = []
    a.append(a)
    a.append(C_EMAIL)
    return a


def cyc_dict():
    d = {"k": C_PATH}
    d["self"] = d
    return d


def deep(n):
    v = C_EMAIL
    for _ in range(n):
        v = [v]
    return v


@pytest.mark.parametrize("value", [b"x " + C_EMAIL.encode(), bytearray(C_PATH.encode()), Raises(), Holds(), cyc_list(), cyc_dict(), deep(5000),
                                   {"a": deep(3000)}, [C_KEY] * 20000, {i: C_EMAIL for i in range(20000)}, {(1, 2): C_PATH}, {C_EMAIL: 1},
                                   {1, 2, 3}, float("inf"), 2 ** 5000, object(), type, lambda: C_PATH, memoryview(b"abc")])
def test_clean_result_never_raises_loops_or_passes_a_canary(value):
    t0 = time.monotonic()
    out = st.clean_result(value)
    assert time.monotonic() - t0 < 20
    raw = dump(out)
    for canary in (C_EMAIL, C_PATH, C_KEY):
        assert canary not in raw, canary


@pytest.mark.parametrize("value", [None, b"bytes " + C_EMAIL.encode(), Raises(), Holds(), cyc_list(), 5, ["a"], {"a": C_KEY}])
def test_release_of_a_non_text_value_is_withheld_or_cleared_never_raw(value):
    got = st.release_safely(value)
    assert C_EMAIL not in got.text and C_PATH not in got.text and C_KEY not in got.text


def test_clean_message_takes_any_value():
    for v in (None, b"x", Raises(), Holds(), cyc_list(), 5):
        out = st.clean_message(v, 200)
        assert isinstance(out, str) and C_EMAIL not in out and C_KEY not in out


def test_kernel_scrub_is_applied_to_text_only_and_a_non_text_value_is_an_error_not_a_pass():
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4] / "jarvis" / "kernel"))
    from jarvis_kernel.scrub import scrub
    for bad in (b"token=abcdefgh12345678", None, 5, {"a": 1}):
        with pytest.raises((TypeError, AttributeError)):
            scrub(bad)                                           # never returns the value unchanged as if it were clean
    assert "ghp_" not in scrub("x ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")[0]
