"""Generate the "how it works" reference docs (plan P10; owner's note: "explain each file, each model"):

- jarvis/docs/neko/FILE_MAP.md: every JARVIS-owned code and config file, grouped by folder, with one line saying what it does.
  The line comes from the file itself (Python module docstring, the leading comment of a script, "_note"/"about" in JSON, a
  systemd Description=, an HTML <title>), so the map stays true when code changes. Files without one are listed as such.
- jarvis/docs/neko/MODELS.md: every model JARVIS can download (jarvis/tools/jarvis.lock.json), with its role, machines, size and
  licence.

Upstream code is not mapped file by file (N.E.K.O's own tree, jarvis/vendor, jarvis/third_party, the Tauri shell subtree); tests
and data files are left out. Uses git ls-files, so only tracked files appear.

    python jarvis/tools/make_docs.py            write both files
    python jarvis/tools/make_docs.py --check    exit 1 if either file is out of date (for the test server / caretaker)
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILE_MAP = ROOT / "jarvis" / "docs" / "neko" / "FILE_MAP.md"
MODELS = ROOT / "jarvis" / "docs" / "neko" / "MODELS.md"
LOCK = ROOT / "jarvis" / "tools" / "jarvis.lock.json"

# JARVIS's own code: the jarvis/ tree and the plugins JARVIS added to N.E.K.O (README.MD, "Where JARVIS lives")
ROOTS = ("jarvis/",) + tuple(f"plugin/plugins/{p}/" for p in (
    "desktop", "research", "deliverables", "local_team", "main_agent", "jarvis_bridge", "learn_vault", "doc_tools", "workbench"))
EXCLUDE = re.compile(
    r"^jarvis/(vendor|third_party|desktop/shell|docs)/"           # upstream code and the docs themselves
    r"|(^|/)(tests?|__tests__|fixtures|results|evidence|node_modules|dist|build|cases|data)/"
    r"|(^|/)(test_[^/]*|conftest\.py|[^/]*\.test\.[a-z]+|[^/]*\.spec\.[a-z]+)$"
    r"|^jarvis/(evals|bench)/.*\.json$|(^|/)[^/]*result[^/]*\.json$")              # benchmark data and results are not code
MAPPED = re.compile(r"\.(py|ps1|psm1|sh|ts|tsx|js|mjs|vbs|bat|cmd|json|ya?ml|toml|service|timer|conf|html)$|(^|/)(Caddyfile|Containerfile|Dockerfile)$")
NO_DESC = "(no description in the file yet)"


def tracked(root: Path = ROOT) -> list[str]:
    out = subprocess.run(["git", "ls-files", *ROOTS], cwd=root, capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.splitlines() if MAPPED.search(p) and not EXCLUDE.search(p) and not p.endswith("package-lock.json"))


def first_sentence(text: str, limit: int = 220) -> str:
    para = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    s = re.sub(r"\s+", " ", para).strip()
    m = re.search(r"(?<=[.!?])\s+(?=[A-Z(\"'`])", s)
    if m and m.start() >= 8:
        s = s[:m.start()]
    if len(s) > limit:
        s = s[:limit - 1].rsplit(" ", 1)[0] + "…"
    return s.replace("|", "\\|")


def _leading_hash_comment(lines: list[str]) -> str:
    buf: list[str] = []
    for ln in lines:
        t = ln.strip()
        if not buf and (t.startswith("#!") or not t or t.startswith("#Requires") or t.lower().startswith("# -*-")):
            continue
        if t.startswith("#") and not t.startswith("#>"):
            buf.append(t.lstrip("#").strip())
        else:
            break
    return " ".join(buf)


def describe(path: str, text: str) -> str:
    """One line saying what the file does, taken from the file itself ("" when it has none)."""
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else name
    lines = text.splitlines()
    if name in ("__init__.py", "__main__.py"):
        pkg = path.rsplit("/", 2)[-2]
        try:
            has_doc = bool(ast.get_docstring(ast.parse(text)))
        except SyntaxError:
            has_doc = False
        if not has_doc and name == "__main__.py":
            return f"Command-line entry point: `python -m` on the `{pkg}` package runs this."
        if not has_doc and not any(ln.strip() and not ln.lstrip().startswith("#") for ln in lines):
            return f"Marks `{pkg}` as a Python package (no code)."
    try:
        if ext == "py":
            doc = ast.get_docstring(ast.parse(text)) or ""
            return first_sentence(doc) if doc else first_sentence(_leading_hash_comment(lines))
        if ext in ("ps1", "psm1"):
            m = re.search(r"<#(.*?)#>", text, re.S)
            if m:
                body = re.sub(r"^\s*\.(SYNOPSIS|DESCRIPTION)\s*$", "", m.group(1), flags=re.M | re.I)
                return first_sentence(body)
            return first_sentence(_leading_hash_comment(lines))
        if ext in ("sh", "yaml", "yml", "toml", "conf", "Caddyfile", "Containerfile", "Dockerfile"):
            return first_sentence(_leading_hash_comment(lines))
        if ext in ("service", "timer"):
            m = re.search(r"^Description=(.+)$", text, re.M)
            return first_sentence(m.group(1)) if m else first_sentence(_leading_hash_comment(lines))
        if ext in ("ts", "tsx", "js", "mjs"):
            m = re.match(r"\s*(?:(?:\"use [a-z]+\";?|'use [a-z]+';?)\s*)?/\*\*?(.*?)\*/", text, re.S)
            if m:
                return first_sentence(re.sub(r"^\s*\*\s?", "", m.group(1), flags=re.M))
            buf = []
            for ln in lines:
                t = ln.strip()
                if t.startswith("//"):
                    buf.append(t.lstrip("/").strip())
                elif buf or t:
                    break
            return first_sentence(" ".join(buf))
        if ext in ("vbs",):
            buf = [ln.strip().lstrip("'").strip() for ln in lines[:12] if ln.strip().startswith("'")]
            return first_sentence(" ".join(buf))
        if ext in ("bat", "cmd"):
            buf = [re.sub(r"^(rem|::)\s?", "", ln.strip(), flags=re.I) for ln in lines[:12] if re.match(r"\s*(rem\b|::)", ln, re.I)]
            return first_sentence(" ".join(buf))
        if ext == "json":
            data = json.loads(text)
            if isinstance(data, dict):
                for key in ("_note", "about", "_about", "description", "_comment", "_doc", "note"):
                    if isinstance(data.get(key), str):
                        return first_sentence(data[key])
            return ""
        if ext == "html":
            m = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
            return first_sentence(m.group(1)) if m else ""
    except (SyntaxError, ValueError):
        return ""
    return ""


def folder_intro(folder: str, root: Path = ROOT) -> str:
    for name in ("README.md", "README.MD", "readme.md"):
        p = root / folder / name
        if p.is_file():
            text = re.sub(r"^#.*$", "", p.read_text(encoding="utf-8", errors="replace"), flags=re.M)
            text = re.sub(r"^\s*[-*>|].*$", "", text, flags=re.M)              # skip lists, quotes and tables
            return first_sentence(text, 300)
    return ""


def build_file_map(root: Path = ROOT, files: list[str] | None = None) -> str:
    files = tracked(root) if files is None else files
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    missing = 0
    for f in files:
        try:
            text = (root / f).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        d = describe(f, text) or NO_DESC
        missing += d == NO_DESC
        folder, _, name = f.rpartition("/")
        groups[folder].append((name, d))
    out = ["# JARVIS file map", "",
           "Generated by `jarvis/tools/make_docs.py` from the files themselves; do not edit by hand. Re-run it after adding or "
           "renaming files (`--check` tells you when this page is stale).", "",
           f"{len(files)} files in {len(groups)} folders. {missing} have no description in the file yet: give them a module "
           "docstring or a leading comment and re-run.", "",
           "Covered: the `jarvis/` tree and the plugins JARVIS added (" + ", ".join(r.split('/')[2] for r in ROOTS[1:]) + "). "
           "Not covered file by file: N.E.K.O's own tree, `jarvis/vendor`, `jarvis/third_party`, the Tauri shell subtree, tests and data.",
           ""]
    for folder in sorted(groups):
        out += [f"## `{folder}/`", ""]
        intro = folder_intro(folder, root)
        if intro:
            out += [intro, ""]
        out += ["| File | What it does |", "|---|---|"]
        out += [f"| `{name}` | {d} |" for name, d in groups[folder]]
        out.append("")
    return "\n".join(out)


MODEL_ARCHIVE = re.compile(r"(?i)\b(stt|tts|kws|asr|vad|wake|model)\b")
_FAMILY_SUFFIX = re.compile(r"-(model|config|tokenizer|special|added|pytorch|gliner|flan|vm\b).*$")


def _family(name: str) -> str:
    """Lock entry name -> model family: the first name part (piper-hi_in-rohan-medium-onnx -> piper, minicheck-roberta-large -> minicheck).
    Used only to split a registry row that lumps unrelated models together (the claim-checker row holds four checkers)."""
    return _FAMILY_SUFFIX.sub("", name).split("-", 1)[0]


def _size(n) -> str:
    if not isinstance(n, (int, float)) or n <= 0:
        return "?"
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{int(n)} B"


def build_models(lock_path: Path = LOCK) -> str:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    machines = lock.get("machines", {})
    by_family: dict[tuple[str, str], list[dict]] = defaultdict(list)
    tools: dict[str, list[dict]] = defaultdict(list)
    for e in lock.get("entries", []):
        reg = e.get("registry_name") or e["name"]
        if e.get("kind") == "model" or (e.get("kind") == "archive" and MODEL_ARCHIVE.search(reg)):
            by_family[(reg, _family(e["name"]))].append(e)
        else:
            tools[reg].append(e)
    families: dict[str, set] = defaultdict(set)
    for reg, fam in by_family:
        families[reg].add(fam)
    groups = {(f"{reg}: {fam}" if len(families[reg]) > 1 else reg): es for (reg, fam), es in by_family.items()}
    out = ["# JARVIS models", "",
           "Generated by `jarvis/tools/make_docs.py` from `jarvis/tools/jarvis.lock.json`; do not edit by hand. Every file is pinned "
           "by upstream revision and sha256 and fetched with `jarvis/tools/fetch.py`. Why each model was chosen, and the measured "
           "speeds and scores, are in `NEKO_MODEL_MATRIX.md` and `NEKO_95_MODEL_BENCHMARK.md`.", "",
           "Machines: " + "; ".join(f"`{k}` = {v}" for k, v in machines.items()) + ".", "",
           f"{len(groups)} models ({sum(len(v) for v in groups.values())} files).", "",
           "## Models", "", "| Model (role) | Files | Machines | Size | Licence | Optional |", "|---|---|---|---|---|---|"]
    for name in sorted(groups, key=lambda n: (n.lower().startswith("rejected"), n.lower())):
        es = groups[name]
        mach = sorted({m for e in es for m in e.get("machine", [])})
        total = sum(e.get("bytes") or 0 for e in es)
        lic = sorted({str(e.get("licence") or "?") for e in es})
        opt = "yes" if all(e.get("optional") for e in es) else ("some" if any(e.get("optional") for e in es) else "no")
        files = ", ".join(f"`{e['name']}`" for e in es[:4]) + (f" +{len(es) - 4} more" if len(es) > 4 else "")
        out.append(f"| {name.replace('|', '/')} | {files} | {', '.join(mach)} | {_size(total)} | "
                   f"{'; '.join(lic).replace('|', '/')} | {opt} |")
    if tools:
        out += ["", "## Runtimes and tools (pinned the same way)", "", "| Tool | Files | Machines | Licence |", "|---|---|---|---|"]
        for name in sorted(tools, key=str.lower):
            es = tools[name]
            mach = sorted({m for e in es for m in e.get("machine", [])})
            lic = sorted({str(e.get("licence") or "?") for e in es})
            files = ", ".join(f"`{e['name']}`" for e in es[:3]) + (f" +{len(es) - 3} more" if len(es) > 3 else "")
            out.append(f"| {name.replace('|', '/')} | {files} | {', '.join(mach)} | {'; '.join(lic).replace('|', '/')} |")
    unlocked = lock.get("not_locked", [])
    if unlocked:
        out += ["", "Not pinned (and why):", ""]
        out += [f"- **{u.get('registry_name', '?')}**: {first_sentence(u.get('unpinned_reason', ''), 400)}" for u in unlocked]
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if FILE_MAP.md or MODELS.md is out of date")
    args = ap.parse_args(argv)
    wanted = {FILE_MAP: build_file_map(), MODELS: build_models()}
    stale = [p for p, text in wanted.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
    if args.check:
        for p in stale:
            print(f"out of date: {p.relative_to(ROOT)} (run python jarvis/tools/make_docs.py)")
        return 1 if stale else 0
    for p in stale:
        p.write_text(wanted[p], encoding="utf-8", newline="\n")
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
