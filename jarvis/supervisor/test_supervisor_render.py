"""Tests for the per-boot token and the config renderer. Run: python -m pytest jarvis/supervisor/test_supervisor_render.py -q -p no:cacheprovider"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boot_token  # noqa: E402
import render  # noqa: E402


def test_token_is_64_hex_and_unique():
    a, b = boot_token.new_token(), boot_token.new_token()
    assert re.fullmatch(r"[0-9a-f]{64}", a) and a != b


def test_rotate_overwrites_old_token_and_missing_reads_none(tmp_path):
    assert boot_token.read_token(tmp_path / "run") is None
    first = boot_token.rotate(tmp_path / "run")
    second = boot_token.rotate(tmp_path / "run")
    assert first != second and boot_token.read_token(tmp_path / "run") == second


def test_render_substitutes_and_raises_on_unknown():
    assert render.render("host=${HOST} port=${PORT}", {"HOST": "127.0.0.1", "PORT": "48911"}) == "host=127.0.0.1 port=48911"
    with pytest.raises(KeyError) as e:
        render.render("${MISSING}", {})
    assert "MISSING" in str(e.value)


# built at run time so no literal secret-shaped string sits in the repository
FAKES = ["s" + "k-abcdef0123456789", "0123456789abcdef" * 2 + "01234567", "e" + "yJhbGciOiJIUzI1NiJ9", "g" + "hp_abcdefgh1234"]


@pytest.mark.parametrize("secret", FAKES)
def test_secret_looking_values_are_refused_without_echo(secret):
    with pytest.raises(ValueError) as e:
        render.render("k=${K}", {"K": secret})
    assert secret not in str(e.value) and "'K'" in str(e.value)


def test_file_references_and_plain_values_are_allowed():
    out = render.render("${A} ${B} ${C}", {"A": "file:providers/groq.key", "B": "127.0.0.1", "C": "48911"})
    assert out == "file:providers/groq.key 127.0.0.1 48911"


def test_scan_dir_finds_planted_secret_and_passes_rendered_configs(tmp_path):
    t = tmp_path / "t.tmpl"
    t.write_text("key=${K}\nhost=${H}\n", encoding="utf-8")
    out = tmp_path / "run" / "conf.ini"
    render.render_to(t, out, {"K": "file:providers/groq.key", "H": "127.0.0.1"})
    assert render.scan_dir(tmp_path / "run") == []
    (tmp_path / "run" / "bad.ini").write_text("key" + "=" + FAKES[0] + "abcd\n", encoding="utf-8")
    assert render.scan_dir(tmp_path / "run") == ["bad.ini"]
