"""Every test gets its own Workbench folder and may read only the synthetic fixtures (no real client data anywhere)."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def workbench_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_DIR", str(tmp_path / "wb"))
    monkeypatch.setenv("DOC_TOOLS_READ_ROOTS", str(FIXTURES) + __import__("os").pathsep + str(tmp_path / "readable"))
    monkeypatch.setenv("JARVIS_LOCAL_LLM", "http://127.0.0.1:1/v1")          # nothing listens: no model is ever reached
    (tmp_path / "readable").mkdir()
    return tmp_path / "wb"


@pytest.fixture
def alpha():
    return [str(FIXTURES / "client_alpha" / n) for n in ("whatsapp_chat.txt", "call_transcript.srt", "brief.md")]


@pytest.fixture
def beta():
    return [str(FIXTURES / "client_beta" / n) for n in ("telegram_export.json", "kickoff_call.vtt")]
