"""make_docs: descriptions come from the files, every mapped file appears, models come from the lock. No network.
Run: python -m pytest jarvis/tools/test_make_docs.py -q -p no:cacheprovider"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_docs as md  # noqa: E402


@pytest.mark.parametrize("path,text,want", [
    ("jarvis/x/a.py", '"""Starts the thing. More detail here."""\nx = 1\n', "Starts the thing."),
    ("jarvis/x/b.py", "#!/usr/bin/env python\n# Does B.\nimport os\n", "Does B."),
    ("jarvis/x/__init__.py", "", "Marks `x` as a Python package (no code)."),
    ("jarvis/x/__main__.py", "from . import main\nmain()\n", "Command-line entry point: `python -m` on the `x` package runs this."),
    ("jarvis/s/run.ps1", "<#\n.SYNOPSIS\n  Starts the stack.\n#>\nparam()\n", "Starts the stack."),
    ("jarvis/s/run.ps1", "# Starts the console.\nparam()\n", "Starts the console."),
    ("jarvis/s/run.sh", "#!/bin/sh\n# Builds the image.\nset -e\n", "Builds the image."),
    ("jarvis/u/a.ts", "/** The API client. */\nexport const x = 1;\n", "The API client."),
    ("jarvis/u/b.tsx", "// Settings screen.\nimport React from 'react';\n", "Settings screen."),
    ("jarvis/c/x.json", json.dumps({"_note": "Egress checkpoint for the browser."}), "Egress checkpoint for the browser."),
    ("jarvis/c/y.json", json.dumps({"about": "Sites JARVIS never drives."}), "Sites JARVIS never drives."),
    ("jarvis/d/k.service", "[Unit]\nDescription=JARVIS kernel\n", "JARVIS kernel"),
    ("jarvis/h/i.html", "<html><title>JARVIS Hub</title></html>", "JARVIS Hub"),
    ("jarvis/x/c.py", "x = 1\n", ""),
    ("jarvis/c/z.json", "[1, 2]", ""),
    ("jarvis/x/bad.py", "def (:\n", ""),
])
def test_descriptions_come_from_the_file(path, text, want):
    assert md.describe(path, text) == want


def test_first_sentence_is_short_and_table_safe():
    s = md.first_sentence("A | B does many things. Second sentence.")
    assert s == "A \\| B does many things."
    assert len(md.first_sentence("word " * 200)) <= 220


@pytest.mark.parametrize("path", [
    "jarvis/kernel/tests/test_x.py", "jarvis/egress/test_proxy.py", "jarvis/ui/node_modules/a/b.js", "jarvis/vendor/x/y.py",
    "jarvis/third_party/README.md", "jarvis/desktop/shell/src/main.rs", "jarvis/evals/neko95/bench/cases/a.json",
    "jarvis/bench/neko/result.json", "plugin/plugins/research/tests/test_a.py",
])
def test_tests_data_and_upstream_code_are_not_mapped(path):
    assert md.EXCLUDE.search(path)


def test_real_repo_map_lists_every_tracked_file_once():
    files = md.tracked()
    assert files and len(files) == len(set(files))
    text = md.build_file_map(files=files)
    for f in files:
        folder, _, name = f.rpartition("/")
        assert f"## `{folder}/`" in text and f"| `{name}` |" in text, f


def test_models_page_lists_every_model_file_in_the_lock(tmp_path):
    lock = {"machines": {"laptop": "the laptop"}, "entries": [
        {"name": "qwen-a-q4km", "registry_name": "Qwen A (decider)", "kind": "model", "machine": ["laptop"], "bytes": 2_500_000_000,
         "licence": "Apache-2.0", "optional": False},
        {"name": "nli-x-model", "registry_name": "Checkers", "kind": "model", "machine": ["laptop"], "bytes": 10, "licence": "MIT"},
        {"name": "minicheck-y-model", "registry_name": "Checkers", "kind": "model", "machine": ["laptop"], "bytes": 10, "licence": "MIT"},
        {"name": "parakeet-int8", "registry_name": "Parakeet (English STT)", "kind": "archive", "machine": ["laptop"], "bytes": 5},
        {"name": "llama-cpp-b1", "registry_name": "llama.cpp (llama-server)", "kind": "archive", "machine": ["laptop"]},
    ], "not_locked": [{"registry_name": "uv", "unpinned_reason": "No version chosen."}]}
    p = tmp_path / "lock.json"
    p.write_text(json.dumps(lock), encoding="utf-8")
    text = md.build_models(p)
    assert "| Qwen A (decider) | `qwen-a-q4km` | laptop | 2.5 GB | Apache-2.0 | no |" in text
    assert "Checkers: nli" in text and "Checkers: minicheck" in text           # unrelated models in one registry row are split
    assert "Parakeet (English STT)" in text.split("## Runtimes")[0]            # speech models shipped as archives are models
    assert "llama.cpp (llama-server)" in text.split("## Runtimes")[1]          # runtimes are listed separately
    assert "**uv**: No version chosen." in text


def test_real_lock_builds():
    text = md.build_models()
    assert "## Models" in text and "decider" in text
