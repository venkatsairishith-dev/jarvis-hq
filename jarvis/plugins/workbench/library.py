"""What JARVIS has learned: validated workflows (as skills) and evidence-supported lessons.

    <WORKBENCH_DIR>/library/
        workflows/<name>/v<N>/   workflow.json, cases.json, SKILL.md, validation.json, provenance.json
        workflows/<name>/current (text: the adopted version, only ever a validated one)
        lessons.jsonl            one lesson per line: cue, lesson, evidence, confidence, sources, version, limitations,
                                 validation; scrubbed; refused if it carries client material
        learning/<id>.json       the full record of one learning attempt (stages, evidence table, ledger)

Rules: a workflow version is written once and never changed; "current" moves only to a version whose validation says
every case passed; a lesson must name the evidence it rests on (an observed contrast), and is refused if it repeats a
run of 8 or more words from any text the caller marks as private (a client project's sources) or holds an e-mail
address, a phone number or a secret.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

from . import safety
from .projects import read_json, root, write_json

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d -]{8,14}\d)(?!\d)")


class LibraryError(Exception):
    pass


def base() -> Path:
    return root() / "library"


def _shingles(text: str, n: int = 8) -> set[str]:
    w = re.findall(r"\w+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def lesson_problems(lesson: dict[str, Any], private_texts: list[str] | None = None) -> list[str]:
    probs = []
    blob = " ".join(str(lesson.get(k, "")) for k in ("cue", "lesson", "evidence", "limitations"))
    if not str(lesson.get("evidence", "")).strip():
        probs.append("a lesson needs the evidence it rests on")
    if _EMAIL.search(blob) or _PHONE.search(blob):
        probs.append("a lesson may not hold an e-mail address or a phone number")
    _, hits = safety.scrub(blob)
    if hits:
        probs.append("a lesson may not hold a secret")
    if private_texts:
        mine = _shingles(blob)
        if any(mine & _shingles(t) for t in private_texts):
            probs.append("a lesson may not repeat client material")
    return probs


def add_lesson(lesson: dict[str, Any], *, sources: list[str] | None = None, version: str = "", limitations: str = "",
               validation: dict[str, Any] | None = None, private_texts: list[str] | None = None) -> dict[str, Any]:
    row = {"id": f"L-{secrets.token_hex(4)}", "at": round(time.time(), 3),
           "cue": safety.safe_for_output(lesson.get("cue", ""), 300), "lesson": safety.safe_for_output(lesson.get("lesson", ""), 500),
           "evidence": safety.safe_for_output(lesson.get("evidence", ""), 800),
           "confidence": str(lesson.get("confidence", "low"))[:10], "family": str(lesson.get("family", ""))[:10],
           "sources": [safety.safe_for_output(s, 300) for s in (sources or [])][:10], "version": str(version)[:40],
           "limitations": safety.safe_for_output(limitations, 400), "validation": validation or {}}
    probs = lesson_problems(row, private_texts)
    if probs:
        raise LibraryError("; ".join(probs))
    base().mkdir(parents=True, exist_ok=True)
    with (base() / "lessons.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def lessons(query: str = "", limit: int = 10) -> list[dict[str, Any]]:
    path = base() / "lessons.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    if not query:
        return rows[-limit:]
    q = set(re.findall(r"[a-z]{4,}", query.lower()))
    scored = sorted(rows, key=lambda r: -len(q & set(re.findall(r"[a-z]{4,}", (r.get("cue", "") + " " + r.get("lesson", "")).lower()))))
    return [r for r in scored[:limit] if q & set(re.findall(r"[a-z]{4,}", (r.get("cue", "") + " " + r.get("lesson", "")).lower()))]


def save_workflow(name: str, workflow: dict[str, Any], cases: list[dict[str, Any]], skill_md: str,
                  validation: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    if not NAME.match(name or ""):
        raise LibraryError("A workflow name is 2-41 lowercase letters, digits and dashes.")
    from .skillhub import find_duplicate
    dup = find_duplicate(workflow, skip=name)
    if dup:                                             # the same steps already exist as another skill: reuse, do not store twice
        return {"name": dup, "version": (base() / "workflows" / dup / "current").read_text(encoding="utf-8").strip(),
                "adopted": False, "duplicate_of": dup}
    d = base() / "workflows" / name
    d.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(p.name[1:]) for p in d.glob("v*") if p.name[1:].isdigit()] or [0])
    v = d / f"v{n}"
    v.mkdir()
    write_json(v / "workflow.json", workflow)
    write_json(v / "cases.json", cases)
    (v / "SKILL.md").write_text(skill_md, encoding="utf-8")
    write_json(v / "validation.json", validation)
    write_json(v / "provenance.json", provenance)
    adopted = validation.get("status") == "validated" and validation.get("all_cases_passed") is True
    if adopted:
        (d / "current").write_text(f"v{n}", encoding="utf-8")
    return {"name": name, "version": f"v{n}", "adopted": adopted}


def load_workflow(name: str, version: str = "") -> dict[str, Any]:
    if not NAME.match(name or ""):
        raise LibraryError("Unknown workflow.")
    d = base() / "workflows" / name
    ver = version or ((d / "current").read_text(encoding="utf-8").strip() if (d / "current").is_file() else "")
    if not ver or not re.fullmatch(r"v\d{1,4}", ver) or not (d / ver).is_dir():
        raise LibraryError("That workflow has no validated version.")
    out = {k: read_json(d / ver / f"{k}.json", None) for k in ("workflow", "cases", "validation", "provenance")}
    out.update(name=name, version=ver, skill_md=(d / ver / "SKILL.md").read_text(encoding="utf-8"))
    return out


def list_workflows() -> list[dict[str, Any]]:
    d = base() / "workflows"
    if not d.is_dir():
        return []
    out = []
    for w in sorted(d.iterdir()):
        if w.is_dir() and NAME.match(w.name):
            cur = (w / "current").read_text(encoding="utf-8").strip() if (w / "current").is_file() else ""
            prov = read_json(w / cur / "provenance.json", {}) if cur else {}
            out.append({"name": w.name, "current": cur or None, "task": (prov or {}).get("task", "")[:160],
                        "versions": len([p for p in w.glob("v*") if p.is_dir()])})
    return out


def save_record(record: dict[str, Any]) -> str:
    rid = record.get("id") or f"learn-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    record["id"] = rid
    write_json(base() / "learning" / f"{rid}.json", record)
    return rid


def load_record(rid: str) -> dict[str, Any]:
    if not re.fullmatch(r"learn-\d{8}-\d{6}-[0-9a-f]{4}", str(rid or "")):
        raise LibraryError("Unknown learning record.")
    data = read_json(base() / "learning" / f"{rid}.json", None)
    if not isinstance(data, dict):
        raise LibraryError("Unknown learning record.")
    return data
