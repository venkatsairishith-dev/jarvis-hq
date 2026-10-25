"""Where the tests keep their files. The reader refuses anything under AppData (and Windows, Program Files, ...), and on Windows pytest's own temp
folder is %LOCALAPPDATA%\\Temp: every test that wrote a file there was refused for the right reason (found on the real laptop, 2026-10-25, 59 failures).
On Windows, when the temp folder is in such a place, the files go into a folder of the user's profile that the rule allows, and are removed afterwards."""
import os
import shutil
from pathlib import Path

import pytest

from jarvis.plugins.doc_reader import core


def _refused(path: Path) -> bool:
    return any(part.lower() in core.BLOCKED_DIRS for part in path.parts)


class _Factory:
    def __init__(self, base: Path):
        self.base, self.n = base, 0

    def getbasetemp(self) -> Path:
        return self.base

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        self.n += 1
        path = self.base / f"{basename}{self.n}"
        path.mkdir(parents=True)
        return path


@pytest.fixture(scope="session")
def tmp_path_factory(tmp_path_factory):
    base = Path(tmp_path_factory.getbasetemp())
    if os.name != "nt" or not _refused(base):
        yield tmp_path_factory
        return
    safe = Path(os.environ.get("JARVIS_TEST_TMP") or (Path.home() / "jarvis-doc-reader-test-tmp"))
    shutil.rmtree(safe, ignore_errors=True)
    safe.mkdir(parents=True)
    try:
        yield _Factory(safe.resolve())
    finally:
        shutil.rmtree(safe, ignore_errors=True)
