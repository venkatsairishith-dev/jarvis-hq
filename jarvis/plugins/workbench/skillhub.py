"""The learned-skill lifecycle: search, inspect, import (untrusted), regression-test, disable, quarantine, roll back,
export, and usage metrics. Storage stays in ``library.py``; this module never edits a stored version.

    library/workflows/<name>/state.json     {"status": active|disabled|quarantined, "reason", "history": [...]}
    library/workflows/<name>/usage.jsonl    one line per sandbox reuse, regression run or real run: result + time
    library/imported/<name>/<sha12>/        an external SKILL.md as received + its rating; NEVER active by itself

Rules:
- Only an ``active`` skill whose current version is validated may be run (sandbox or real). Disabled and quarantined
  skills are refused with the reason.
- A regression run re-probes the current version on its own cases (plus any extra regression cases); a failure
  quarantines the skill and names the failing case. Re-enabling after quarantine needs a passing regression run.
- Rollback moves "current" to an older version only if that version's validation says every case passed.
- Duplicates: a new workflow whose steps are identical to an existing skill's current version is reported as a
  duplicate, not stored twice (``library.save_workflow`` calls ``find_duplicate``).
- Import: an external SKILL.md is untrusted text. It is read under Document Tools' rules, hashed, rated with the skill
  gate's own static rules, and stored as ``imported-unverified``. It is never activated here: a Hermes skill goes to the
  lab only through ``jarvis/skills/skillgate.py`` (allowlist + checks + kernel authorization), and a Workbench workflow
  only through learning (sandbox cases). Scripts, links and referenced files in it are listed, never fetched or run.
- Permissions: a skill's declared permissions come from its workflow (sandbox only). A skill that would need more
  (network, files, messages, code) is never learned automatically; it needs the owner (``learning.analyse`` lists it).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from . import safety, sandbox
from .library import NAME, LibraryError, base, load_workflow
from .projects import read_json, write_json

STATUSES = ("active", "disabled", "quarantined")
_URL = re.compile(r"https?://[^\s)\]>\"']+")


def _wdir(name: str) -> Path:
    if not NAME.match(name or ""):
        raise LibraryError("Unknown skill.")
    d = base() / "workflows" / name
    if not d.is_dir() or d.is_symlink():
        raise LibraryError("Unknown skill.")
    return d


def steps_hash(workflow: dict[str, Any]) -> str:
    steps = [{k: v for k, v in st.items() if k not in ("_sid", "note")} for st in workflow.get("steps") or []]
    return hashlib.sha256(json.dumps({"steps": steps, "outputs": workflow.get("outputs")}, sort_keys=True).encode()).hexdigest()


def state(name: str) -> dict[str, Any]:
    d = _wdir(name)
    st = read_json(d / "state.json", None)
    return st if isinstance(st, dict) else {"status": "active", "reason": "", "history": []}


def _set_state(name: str, status: str, reason: str) -> dict[str, Any]:
    if status not in STATUSES:
        raise LibraryError(f"status must be one of {', '.join(STATUSES)}")
    st = state(name)
    st["history"] = (st.get("history") or [])[-49:] + [{"at": round(time.time(), 3), "from": st.get("status"), "to": status,
                                                        "reason": safety.safe_for_output(reason, 300)}]
    st.update(status=status, reason=safety.safe_for_output(reason, 300))
    write_json(_wdir(name) / "state.json", st)
    return st


def runnable(name: str) -> dict[str, Any]:
    """The current validated version, or LibraryError saying why it may not run."""
    st = state(name)
    if st["status"] != "active":
        raise LibraryError(f"The skill '{name}' is {st['status']}: {st.get('reason') or 'no reason given'}.")
    wf = load_workflow(name)
    if not (wf.get("validation") or {}).get("all_cases_passed"):
        raise LibraryError("Only a validated skill can be run.")
    return wf


def record_use(name: str, kind: str, ok: bool | None, note: str = "") -> None:
    row = {"at": round(time.time(), 3), "kind": kind, "ok": ok, "note": safety.safe_for_output(note, 200)}
    with (_wdir(name) / "usage.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def metrics(name: str) -> dict[str, Any]:
    path = _wdir(name) / "usage.jsonl"
    rows = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines()[-1000:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    out: dict[str, Any] = {"uses": len(rows)}
    for kind in ("sandbox", "regression", "real"):
        ks = [r for r in rows if r.get("kind") == kind]
        out[kind] = {"runs": len(ks), "ok": sum(1 for r in ks if r.get("ok") is True),
                     "failed": sum(1 for r in ks if r.get("ok") is False), "unverified": sum(1 for r in ks if r.get("ok") is None)}
    out["last"] = rows[-1] if rows else None
    return out


def disable(name: str, reason: str) -> dict[str, Any]:
    return _set_state(name, "disabled", reason or "disabled by the owner")


def enable(name: str) -> dict[str, Any]:
    """Back to active. After a quarantine this needs a regression run that passes now."""
    st = state(name)
    if st["status"] == "quarantined":
        r = regress(name, quarantine_on_fail=False)
        if not r["passed"]:
            raise LibraryError(f"Still failing its regression cases ({r['failed_cases']}); it stays quarantined.")
    return _set_state(name, "active", "enabled by the owner")


def regress(name: str, extra_cases: list[dict[str, Any]] | None = None, quarantine_on_fail: bool = True) -> dict[str, Any]:
    wf = load_workflow(name)
    cases = list(wf.get("cases") or []) + list(read_json(_wdir(name) / "regression_cases.json", []) or []) + list(extra_cases or [])
    p = sandbox.probe(wf["workflow"], cases)
    failed = [d["case"] for d in p["detail"] if not d["passed"]] + (p.get("problems") or [])
    passed = bool(cases) and not failed and p["runnable"]
    record_use(name, "regression", passed, ", ".join(failed)[:200])
    out = {"name": name, "version": wf["version"], "cases": len(cases), "passed": passed, "failed_cases": failed}
    if not passed and quarantine_on_fail:
        out["state"] = _set_state(name, "quarantined", f"regression failed on {', '.join(failed)[:150] or 'no cases'}")
    return out


def add_regression_case(name: str, case: dict[str, Any]) -> int:
    """A case from a bug that was found: it is checked on every regression run from now on."""
    if not isinstance(case, dict) or not case.get("checks"):
        raise LibraryError("A regression case needs inputs and checks.")
    path = _wdir(name) / "regression_cases.json"
    rows = list(read_json(path, []) or [])
    rows.append({"name": safety.safe_for_output(case.get("name", f"regression-{len(rows) + 1}"), 60),
                 "inputs": {str(k)[:60]: str(v)[:20000] for k, v in (case.get("inputs") or {}).items()},
                 "checks": [c for c in case["checks"] if isinstance(c, dict)][:20]})
    write_json(path, rows)
    return len(rows)


def rollback(name: str, version: str) -> dict[str, Any]:
    if not re.fullmatch(r"v\d{1,4}", version or ""):
        raise LibraryError("Give a version like v1.")
    d = _wdir(name)
    val = read_json(d / version / "validation.json", None)
    if not isinstance(val, dict) or not val.get("all_cases_passed"):
        raise LibraryError(f"{version} was not validated; rollback refused.")
    prev = (d / "current").read_text(encoding="utf-8").strip() if (d / "current").is_file() else ""
    (d / "current").write_text(version, encoding="utf-8")
    _set_state(name, "active", f"rolled back from {prev or 'none'} to {version}")
    return {"name": name, "current": version, "previous": prev}


def find_duplicate(workflow: dict[str, Any], skip: str = "") -> str | None:
    h = steps_hash(workflow)
    root = base() / "workflows"
    if not root.is_dir():
        return None
    for w in sorted(root.iterdir()):
        if w.name == skip or not NAME.match(w.name) or not (w / "current").is_file():
            continue
        cur = (w / "current").read_text(encoding="utf-8").strip()
        wf = read_json(w / cur / "workflow.json", None)
        if isinstance(wf, dict) and steps_hash(wf) == h:
            return w.name
    return None


def search(query: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Local discovery: learned skills and imported (unverified) skills, matched on name, task and description words."""
    q = set(re.findall(r"[a-z]{3,}", str(query or "").lower()))
    out = []
    root = base() / "workflows"
    for w in sorted(root.iterdir()) if root.is_dir() else []:
        if not NAME.match(w.name):
            continue
        cur = (w / "current").read_text(encoding="utf-8").strip() if (w / "current").is_file() else ""
        prov = read_json(w / cur / "provenance.json", {}) if cur else {}
        st = read_json(w / "state.json", {}) or {}
        text = f"{w.name} {(prov or {}).get('task', '')}"
        score = len(q & set(re.findall(r"[a-z]{3,}", text.lower()))) if q else 1
        if score:
            out.append({"name": w.name, "kind": "learned", "current": cur or None, "status": st.get("status", "active"),
                        "task": (prov or {}).get("task", "")[:160], "score": score})
    imp = base() / "imported"
    for d in sorted(imp.iterdir()) if imp.is_dir() else []:
        for v in sorted(d.iterdir()) if d.is_dir() else []:
            meta = read_json(v / "meta.json", {}) or {}
            text = f"{meta.get('name', '')} {meta.get('description', '')}"
            score = len(q & set(re.findall(r"[a-z]{3,}", text.lower()))) if q else 1
            if score:
                out.append({"name": meta.get("name"), "kind": "imported", "status": meta.get("status"),
                            "rating": (meta.get("gate") or {}).get("tier"), "sha256": meta.get("sha256", "")[:12], "score": score})
    return sorted(out, key=lambda r: -r["score"])[:limit]


def inspect(name: str) -> dict[str, Any]:
    wf = load_workflow(name)
    return {"name": name, "version": wf["version"], "state": state(name), "metrics": metrics(name),
            "validation": wf.get("validation"), "provenance": wf.get("provenance"), "skill_md": wf.get("skill_md", "")[:20000],
            "cases": len(wf.get("cases") or []), "steps": len((wf.get("workflow") or {}).get("steps") or []),
            "permissions": ["sandbox only: text and table operations on the given inputs; no network, no files outside, "
                            "no processes"], "versions": sorted(p.name for p in _wdir(name).glob("v*") if p.is_dir())}


def export(name: str) -> dict[str, Any]:
    """One self-contained JSON bundle (workflow, cases, SKILL.md, validation, provenance, state, metrics) with a hash."""
    wf = load_workflow(name)
    bundle = {"format": "jarvis-workbench-skill/1", "name": name, "version": wf["version"], "workflow": wf["workflow"],
              "cases": wf["cases"], "regression_cases": read_json(_wdir(name) / "regression_cases.json", []) or [],
              "skill_md": wf["skill_md"], "validation": wf["validation"], "provenance": wf["provenance"],
              "state": state(name), "metrics": metrics(name), "exported": round(time.time(), 3)}
    body = json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=1)
    out = base() / "exports" / f"{name}-{wf['version']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return {"file": str(out), "sha256": hashlib.sha256(body.encode()).hexdigest(), "bytes": len(body)}


def import_skill_md(path_str: str) -> dict[str, Any]:
    """An external SKILL.md (from Hermes's hub, a registry, a download): read, hashed, rated, stored quarantined."""
    from . import learning
    from .ingest import check_readable
    real = check_readable(path_str)
    if real.name.lower() != "skill.md" and real.suffix.lower() != ".md":
        raise LibraryError("Give a SKILL.md (Markdown) file.")
    raw = real.read_bytes()
    if len(raw) > 200_000:
        raise LibraryError("That skill file is larger than 200 KB.")
    text = raw.decode("utf-8", "replace")
    sha = hashlib.sha256(raw).hexdigest()
    fm = {}
    m = re.match(r"(?s)^---\r?\n(.*?)\r?\n---\r?\n", text)   # a SKILL.md saved by a Windows editor has CRLF line ends
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip() in ("name", "description", "version", "license", "author") and v.strip():
                fm[k.strip()] = safety.safe_for_output(v.strip().strip("'\""), 300)
    name = re.sub(r"[^a-z0-9-]+", "-", (fm.get("name") or real.parent.name or "imported").lower()).strip("-")[:40] or "imported"
    gate = learning.gate(name, text)
    findings = {
        "links": sorted(set(_URL.findall(text)))[:30],
        "scripts_referenced": sorted(set(re.findall(r"\bscripts?/[\w./-]+", text)))[:30],
        "shell_blocks": len(re.findall(r"```(?:bash|sh|shell|zsh|powershell|ps1|cmd)\b", text, re.I)),
        "addressed_to_ai": bool(safety.addressed_to_ai(text)),
    }
    d = base() / "imported" / name / sha[:12]
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md.untrusted").write_bytes(raw)
    meta = {"name": name, "description": fm.get("description", ""), "version": fm.get("version", ""),
            "license": fm.get("license", ""), "sha256": sha, "source_file": real.name, "imported": round(time.time(), 3),
            "gate": gate, "findings": findings, "status": "imported-unverified",
            "next": ("Not active. For the lab Hermes: stage and activate with jarvis/skills/skillgate.py (allowlist, checks, kernel "
                     "authorization). For a Workbench workflow: learn it with example cases (learn_task).")}
    write_json(d / "meta.json", meta)
    return meta
