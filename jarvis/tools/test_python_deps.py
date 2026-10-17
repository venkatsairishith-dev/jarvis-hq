"""Checks that JARVIS's own Python packages and the N.E.K.O.-Tauri submodule stay pinned and usable.

Run in the neko-test image (repo mounted at /src):
    python -m pytest jarvis/tools/test_python_deps.py -q -p no:cacheprovider
"""
from __future__ import annotations

import ast
import importlib
import importlib.metadata
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SUBMODULE_PATH = "jarvis/third_party/neko-tauri"
OFFICIAL_URL = "https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri"
GROUPS = ("jarvis", "jarvis-dev")
# Import name -> distribution name, for every package in the jarvis groups that JARVIS code imports.
IMPORT_TO_DIST = {
    "dbos": "dbos",
    "pptx": "python-pptx",
    "pdfplumber": "pdfplumber",
    "pypdfium2": "pypdfium2",
    "kaldi_native_fbank": "kaldi-native-fbank",
}
REQUIREMENT_FILES = ("jarvis-venv.txt", "piper-venv.txt", "privacy-venv.txt")


def _pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _group_pins() -> dict[str, dict[str, str]]:
    """{group: {normalised name: exact version}} from pyproject's [dependency-groups]."""
    out: dict[str, dict[str, str]] = {}
    groups = _pyproject()["dependency-groups"]
    for group in GROUPS:
        out[group] = {}
        for spec in groups[group]:
            m = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.+\-]+)", spec.strip())
            assert m, f"{group}: {spec!r} is not an exact == pin"
            out[group][_norm(m.group(1))] = m.group(2)
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


def test_installed_versions_match_the_jarvis_groups():
    pins = {name: ver for group in _group_pins().values() for name, ver in group.items()}
    for dist in IMPORT_TO_DIST.values():
        assert _norm(dist) in pins, f"{dist} is imported but not pinned in a jarvis group"
        assert importlib.metadata.version(dist) == pins[_norm(dist)], dist


# ---------- uv.lock contains the jarvis groups ----------

def test_uv_lock_has_the_jarvis_groups():
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    root = [p for p in lock["package"] if p.get("source", {}).get("editable") == "."]
    assert len(root) == 1, "no editable root package in uv.lock"
    root = root[0]
    versions = {p["name"]: p["version"] for p in lock["package"] if "version" in p}
    for group, pins in _group_pins().items():
        locked_names = {d["name"] for d in root["dev-dependencies"][group]}
        assert locked_names == set(pins), f"uv.lock group {group}: {sorted(locked_names)}"
        specs = {d["name"]: d["specifier"] for d in root["metadata"]["requires-dev"][group]}
        for name, version in pins.items():
            assert specs[name] == f"=={version}", f"uv.lock {group}: {name} {specs[name]}"
            assert versions[name] == version, f"uv.lock resolves {name} {versions[name]}, pinned {version}"


# ---------- requirement files for the laptop venvs ----------

@pytest.mark.parametrize("filename", REQUIREMENT_FILES)
def test_requirement_files_pin_exactly_and_say_where_pins_come_from(filename):
    lines = (REPO / "jarvis/tools/requirements" / filename).read_text(encoding="utf-8").splitlines()
    pins = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    assert pins, f"{filename} has no pins"
    for line in pins:
        assert re.match(r"^[A-Za-z0-9_.\-]+==[A-Za-z0-9_.+\-]+\s", line), f"not an exact pin: {line!r}"
        assert "# from docs" in line and "verify on laptop" in line, f"pin without its source: {line!r}"


# ---------- N.E.K.O.-Tauri submodule ----------

def test_gitmodules_points_at_the_official_url():
    text = (REPO / ".gitmodules").read_text(encoding="utf-8")
    sections = re.split(r"(?m)^\[submodule ", text)
    entry = [s for s in sections if re.search(rf"(?m)^\s*path\s*=\s*{re.escape(SUBMODULE_PATH)}\s*$", s)]
    assert len(entry) == 1, f"{SUBMODULE_PATH} not in .gitmodules"
    url = re.search(r"(?m)^\s*url\s*=\s*(\S+)\s*$", entry[0]).group(1)
    assert url.removesuffix(".git") == OFFICIAL_URL


def _readme_pin() -> str:
    readme = (REPO / "jarvis/third_party/README.md").read_text(encoding="utf-8")
    row = [line for line in readme.splitlines() if line.startswith("|") and f"`{SUBMODULE_PATH}`" in line]
    assert len(row) == 1, "README has no table row for the submodule"
    pins = re.findall(r"`([0-9a-f]{40})`", row[0])
    assert len(pins) == 1, "README row has no 40-character commit pin"
    return pins[0]


def test_submodule_commit_equals_the_readme_pin():
    out = subprocess.run(
        ["git", "ls-files", "--stage", "--", SUBMODULE_PATH],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert out and out[0] == "160000", f"{SUBMODULE_PATH} is not a submodule (gitlink) in the index"
    assert out[1] == _readme_pin()


def test_checked_out_submodule_is_at_the_pin_and_apache_2():
    checkout = REPO / SUBMODULE_PATH
    if not (checkout / ".git").exists():
        pytest.skip("submodule not initialised (git submodule update --init)")
    head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == _readme_pin()
    licence = (checkout / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in licence and "Version 2.0, January 2004" in licence
