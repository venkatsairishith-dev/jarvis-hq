"""Checks that JARVIS's own Python packages stay pinned and usable.

Run in the jarvis-test image (repo mounted at /src):
    python -m pytest jarvis/tools/test_python_deps.py -q -p no:cacheprovider
"""
from __future__ import annotations

import ast
import importlib
import importlib.metadata
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GROUPS = ("jarvis", "jarvis-dev")
# Import name -> distribution name, for the packages in agent-venv.txt that JARVIS code imports and the tests need.
IMPORT_TO_DIST = {
    "dbos": "dbos",
    "pptx": "python-pptx",
    "pdfplumber": "pdfplumber",
    "pypdfium2": "pypdfium2",
}
REQUIREMENT_FILES = ("agent-venv.txt", "jarvis-venv.txt", "piper-venv.txt", "privacy-venv.txt", "liteparse-venv.txt")


def _agent_pins() -> dict[str, str]:
    """{normalised name: exact version} from requirements/agent-venv.txt."""
    out: dict[str, str] = {}
    for line in (REPO / "jarvis/tools/requirements/agent-venv.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.+\-]+)", line.strip())
        if m:
            out[_norm(m.group(1))] = m.group(2)
    return out


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _jarvis_imports() -> list[tuple[str, str, tuple[str, ...]]]:
    """(file, module, names) for every import of the IMPORT_TO_DIST packages in tracked Python files.

    Covers import statements and pytest.importorskip("...") / importlib.import_module("...") calls."""
    names = "|".join(IMPORT_TO_DIST)
    pattern = rf"^\s*(from|import)\s+({names})\b|(importorskip|import_module)\(\s*['\"]({names})\b"
    files = subprocess.run(
        ["git", "grep", "-lE", pattern, "--", "*.py"],
        cwd=REPO, capture_output=True, text=True, check=False,
    ).stdout.split()
    found = []
    for rel in files:
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module.split(".")[0] in IMPORT_TO_DIST:
                    found.append((rel, node.module, tuple(a.name for a in node.names)))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in IMPORT_TO_DIST:
                        found.append((rel, alias.name, ()))
            elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                func = node.func
                fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                arg = node.args[0].value
                if fname in ("importorskip", "import_module") and isinstance(arg, str) \
                        and arg.split(".")[0] in IMPORT_TO_DIST:
                    found.append((rel, arg, ()))
    return found


# ---------- every module JARVIS imports from these packages is importable ----------

def test_jarvis_imports_were_found():
    seen = {module.split(".")[0] for _, module, _ in _jarvis_imports()}
    assert seen == set(IMPORT_TO_DIST), f"import scan found {sorted(seen)}"


@pytest.mark.parametrize("rel,module,names", _jarvis_imports(), ids=lambda v: v if isinstance(v, str) else None)
def test_module_is_importable(rel, module, names):
    mod = importlib.import_module(module)
    for name in names:
        if name == "*":
            continue
        if not hasattr(mod, name):  # a submodule imported with "from pkg import sub"
            importlib.import_module(f"{module}.{name}")


def test_installed_versions_match_agent_venv_pins():
    pins = _agent_pins()
    for dist in IMPORT_TO_DIST.values():
        assert _norm(dist) in pins, f"{dist} is imported but not pinned in agent-venv.txt"
        assert importlib.metadata.version(dist) == pins[_norm(dist)], dist


# ---------- requirement files for the laptop venvs ----------

@pytest.mark.parametrize("filename", REQUIREMENT_FILES)
def test_requirement_files_pin_exactly_and_say_where_pins_come_from(filename):
    lines = (REPO / "jarvis/tools/requirements" / filename).read_text(encoding="utf-8").splitlines()
    pins = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    assert pins, f"{filename} has no pins"
    for line in pins:
        assert re.match(r"^[A-Za-z0-9_.\-]+==[A-Za-z0-9_.+\-]+[\s;]", line), f"not an exact pin: {line!r}"
        from_docs = "# from docs" in line and "verify on laptop" in line
        measured = re.search(r"# measured on laptop \d{4}-\d{2}-\d{2}", line) is not None   # read from the installed venv (uv pip list)
        assert from_docs or measured, f"pin without its source: {line!r}"
