"""Client projects: one folder each, nothing shared between them.

    <WORKBENCH_DIR>/projects/<project_id>/
        project.json        name, created, provider policy (local only unless the owner authorised a provider)
        sources/            copies of the files the owner supplied (manifest.json: name, kind, sha256, size, added)
        extracted/          segments.json: text pieces with their source and position (line, message, timestamp, page)
        requirements.json   + requirements.md      evidence-referenced requirements, conflicts, questions
        plan.json           + plan.md              tasks, dependencies, milestones, tools
        prompts/            generated prompts (versioned)
        answers.json        the owner's answers to open questions
        deliverables/       what was produced, with validation.json
        log.jsonl           what happened (scrubbed; never file contents)

Isolation rules (tested): a project id is a short slug; every path is resolved and must stay inside its own project
folder; symlinks are refused; nothing here takes a path in another project. Removing a project is not offered.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import safety

PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
MAX_SOURCE_BYTES = 200 * 1024 * 1024
MAX_SOURCES = 200


class ProjectError(Exception):
    pass


def root() -> Path:
    env = os.environ.get("WORKBENCH_DIR", "").strip()
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path(r"D:\jarvis\workbench")
    return Path.home() / ".jarvis" / "workbench"


def _projects_dir() -> Path:
    return root() / "projects"


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", unicodedata_fold(name).lower()).strip("-")[:40].strip("-")
    if len(s) < 2:
        raise ProjectError("Give the project a name with at least two letters or digits.")
    return s


def unicodedata_fold(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")


def write_json(path: Path, data: Any) -> None:
    """Atomic: a crash leaves the old file or the new one, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Project:
    """A handle on one project folder. Every path it hands out is inside that folder."""

    def __init__(self, project_id: str):
        if not isinstance(project_id, str) or not PROJECT_ID.match(project_id):
            raise ProjectError("Unknown project id (use the id that create_project returned).")
        self.id = project_id
        base = _projects_dir()
        self.dir = base / project_id
        if self.dir.is_symlink():
            raise ProjectError("That project folder is a link; it was not opened.")

    # ---- paths
    def path(self, *parts: str) -> Path:
        """A path inside this project. Refuses '..', absolute parts and links that lead elsewhere."""
        for p in parts:
            if not isinstance(p, str) or not p or os.path.isabs(p) or ".." in Path(p).parts or "\x00" in p:
                raise ProjectError("That path is not inside the project.")
        target = self.dir.joinpath(*parts)
        real_root = self.dir.resolve()
        real = target.resolve()
        if real != real_root and real_root not in real.parents:
            raise ProjectError("That path is not inside the project.")
        probe = target
        while probe != self.dir and probe != probe.parent:
            if probe.is_symlink():
                raise ProjectError("That path goes through a link; it was not used.")
            probe = probe.parent
        return target

    def exists(self) -> bool:
        return (self.dir / "project.json").is_file()

    def meta(self) -> dict[str, Any]:
        data = read_json(self.dir / "project.json", None)
        if not isinstance(data, dict):
            raise ProjectError("No such project.")
        return data

    def policy(self) -> dict[str, Any]:
        return dict(self.meta().get("policy") or {"ai_providers": safety.LOCAL_ONLY})

    def log(self, event: str, **fields: Any) -> None:
        row = {"ts": round(time.time(), 3), "event": event}
        for k, v in fields.items():
            row[k] = safety.safe_for_output(v, 300) if isinstance(v, str) else v
        self.path("log.jsonl").parent.mkdir(parents=True, exist_ok=True)
        with self.path("log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ---- sources
    def manifest(self) -> list[dict[str, Any]]:
        data = read_json(self.path("sources", "manifest.json"), [])
        return data if isinstance(data, list) else []

    def add_source(self, src: Path, kind: str, role_hint: str = "") -> dict[str, Any]:
        """Copy one file into sources/ (never move or change the original). Same content twice is added once."""
        if src.is_symlink():
            raise ProjectError(f"{src.name} is a link; give the file itself.")
        size = src.stat().st_size
        if size > MAX_SOURCE_BYTES:
            raise ProjectError(f"{src.name} is larger than {MAX_SOURCE_BYTES // (1024 * 1024)} MB.")
        rows = self.manifest()
        if len(rows) >= MAX_SOURCES:
            raise ProjectError(f"A project holds at most {MAX_SOURCES} files.")
        digest = sha256_file(src)
        for r in rows:
            if r.get("sha256") == digest:
                return dict(r, duplicate=True)
        n = len(rows) + 1
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", src.name)[-80:].lstrip(".") or "file"
        name = f"{n:03d}-{safe}"
        dest = self.path("sources", name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        row = {"id": f"F{n}", "file": name, "original_name": safety.safe_for_output(src.name, 120), "kind": kind,
               "sha256": digest, "bytes": size, "added": round(time.time(), 3)}
        if role_hint:
            row["role_hint"] = role_hint[:40]
        rows.append(row)
        write_json(self.path("sources", "manifest.json"), rows)
        self.log("source_added", source=row["id"], kind=kind, bytes=size)
        return row

    def source_path(self, source_id: str) -> Path:
        for r in self.manifest():
            if r.get("id") == source_id:
                return self.path("sources", str(r["file"]))
        raise ProjectError(f"No source {source_id} in this project.")


def create(name: str, client: str = "", owner_names: list[str] | None = None) -> Project:
    pid = slug(name)
    base = _projects_dir()
    base.mkdir(parents=True, exist_ok=True)
    candidate, n = pid, 2
    while True:
        try:
            (base / candidate).mkdir()           # atomic: two creates with one name never share a folder
            break
        except FileExistsError:
            candidate = f"{pid[:36]}-{n}"
            n += 1
    project = Project(candidate)
    write_json(project.path("project.json"), {
        "id": candidate, "name": safety.safe_for_output(name, 120), "client": safety.safe_for_output(client, 120),
        "owner_names": [safety.safe_for_output(x, 60) for x in (owner_names or []) if str(x).strip()][:8],
        "created": round(time.time(), 3), "policy": {"ai_providers": safety.LOCAL_ONLY}})
    project.log("created")
    return project


def list_projects() -> list[dict[str, Any]]:
    out = []
    base = _projects_dir()
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        if d.is_dir() and not d.is_symlink() and PROJECT_ID.match(d.name):
            meta = read_json(d / "project.json", None)
            if isinstance(meta, dict):
                out.append({"id": d.name, "name": meta.get("name", ""), "client": meta.get("client", ""),
                            "sources": len(read_json(d / "sources" / "manifest.json", []) or [])})
    return out
