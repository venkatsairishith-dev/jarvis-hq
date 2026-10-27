"""D77: a browser `type` job may carry a few lines of code (a practice problem's solution for the page's editor): line breaks and up to 4000
characters. Nothing else widened: every other browser op, and every other text field, keeps the one-line, 500-character rule."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.jobs import CODE_TEXT_MAX, JobError, _check_params  # noqa: E402

BASE = {"profile": "default", "op": "type", "url": "", "target": "Code editor", "text": ""}
CODE = "n = int(input())\nprint(n * n)\n"


def ok(**kw):
    _check_params("browser_ext", dict(BASE, **kw))


def bad(**kw):
    with pytest.raises(JobError):
        _check_params("browser_ext", dict(BASE, **kw))


def test_a_few_lines_of_code_can_be_typed():
    ok(text=CODE)
    ok(text="def f(x):\n    return {x: [x, (x, 1)]}\n")           # indentation, braces and brackets


def test_the_size_limit_is_four_thousand_characters():
    ok(text=("x = 1\n" * 1000)[:CODE_TEXT_MAX])
    bad(text="x" * (CODE_TEXT_MAX + 1))


def test_a_nul_byte_is_never_accepted():
    bad(text="print(1)\0\n")


def test_a_non_string_text_is_refused():
    bad(text=["print(1)"])


@pytest.mark.parametrize("op", ["click", "select", "press", "check", "hover", "goto", "wait", "scroll"])
def test_every_other_op_keeps_the_one_line_rule(op):
    extra = {"url": "https://judge.test/"} if op == "goto" else {}
    text = {"scroll": "down", "wait": "text:Done", "press": "Enter"}.get(op, "Option")
    ok(op=op, text=text, **extra)
    bad(op=op, text=text + "\nsecond line", **extra)


def test_other_ops_are_still_limited_to_five_hundred_characters():
    bad(op="select", text="y" * 501)


def test_the_target_stays_one_line_even_for_type():
    bad(target="Code\neditor")
    bad(target="c" * 301)


def test_unknown_fields_are_still_refused_for_type():
    bad(text=CODE, cookie="x")
