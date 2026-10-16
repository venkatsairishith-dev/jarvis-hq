"""Controlled self-improvement for Neko's decision layer (2026-10-16). Windows.

Neko may PROPOSE small additions to what the decider knows; it can never promote them itself.
  * What a proposal may contain (anything else is refused): "sites" (a spoken name -> an https address; new names only,
    never replacing a built-in one) and "examples" (a request with the plan it should produce, added to the prompt).
    No tool, permission, approval, safety or prompt-rule change is expressible here at all.
  * Every version is an immutable file versions/v<N>.json with its sha256 in registry.json; a changed file is refused.
  * gate(): the staged version runs the regression suite (dev + holdout + plan cases through the real decider); it passes
    only if no case that passes on the active version fails, and the total is not lower. The report is saved.
  * promote() / rollback(): OWNER ONLY, from the command line (no plugin entry exists for them):
        D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe -m plugin.plugins.desktop.skills status|promote v3|rollback
    promote needs a passed gate for that exact file hash. Each change is appended to audit.log.
Reused idea: Voyager-style skill admission (a skill is admitted only with a passing test); the store is plain files.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

from . import coordinator as coord

ROOT = coord.DATA / "neko" / "decider_skills"
ALLOWED = {"sites", "examples"}
MAX_EXAMPLES = 12


class SkillError(Exception):
    pass


def _reg(root: Path) -> dict:
    try:
        return json.loads((root / "registry.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"versions": {}, "active": None, "history": []}


def _save_reg(root: Path, reg: dict):
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / "registry.json.tmp"
    tmp.write_text(json.dumps(reg, indent=1), encoding="utf-8")
    tmp.replace(root / "registry.json")


def _audit(root: Path, what: str):
    with (root / "audit.log").open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {what}\n")


PRIVATE_HOST = re.compile(r"^(localhost|127\.|10\.|0\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)|\.(local|internal|lan|home|localhost)$", re.I)


def _site_key(name: str) -> str:
    """The name as the decider looks it up (decide.resolve_site): without 'website/site/the' and a '.com' ending."""
    low = re.sub(r"(?i)\b(website|site|web site|the)\b", "", name).strip()
    return " ".join(low.removesuffix(".com").split())


def check(change: dict, builtin_sites: dict) -> dict:
    """Validate a proposed change; returns the normalized content or raises SkillError."""
    if not isinstance(change, dict) or not change or set(change) - ALLOWED:
        raise SkillError(f"a proposal may only contain {sorted(ALLOWED)}")
    sites, examples = change.get("sites") or {}, change.get("examples") or []
    if not isinstance(sites, dict) or not isinstance(examples, list):
        raise SkillError("sites must be a mapping of name -> address and examples a list")
    out = {"sites": {}, "examples": []}
    for name, url in sites.items():
        n = " ".join(str(name).lower().split())
        if not re.fullmatch(r"[a-z0-9][a-z0-9 .'-]{1,40}", n):
            raise SkillError(f"bad site name {name!r}")
        if n in builtin_sites or _site_key(n) in builtin_sites:
            raise SkillError(f"'{n}' is built in and cannot be replaced")
        if re.search(r"\.[a-z]{2,}$", n):               # "gmail.com" is an address the owner says outright; it opens itself
            raise SkillError(f"'{n}' is a web address, not a name; it cannot point somewhere else")
        host = (urllib.parse.urlsplit(str(url)).hostname or "") if isinstance(url, str) else ""
        if not re.fullmatch(r"https://[a-z0-9.-]+\.[a-z]{2,}(/[\w./%?=&-]*)?", str(url), re.I) or not host or PRIVATE_HOST.search(host):
            raise SkillError(f"site {name!r} must be a public https address")
        out["sites"][n] = str(url)
    from . import decide
    fields = set(decide.SCHEMA["properties"]["steps"]["items"]["properties"])
    for ex in examples[:MAX_EXAMPLES]:
        if not isinstance(ex, dict):
            raise SkillError("an example needs a short request and a plan {'steps': [...]}")
        req, plan = str(ex.get("request", "")).strip(), ex.get("plan")
        if not req or len(req) > 200 or not isinstance(plan, dict) or not isinstance(plan.get("steps"), list) or not plan["steps"]:
            raise SkillError("an example needs a short request and a plan {'steps': [...]}")
        if re.search(r"[\x00-\x1f\x7f\u2028\u2029]", req):     # a line break would add a line (a rule) to the decider's prompt
            raise SkillError("an example request must be one plain line")
        for st in plan["steps"]:
            if not isinstance(st, dict) or st.get("tool") not in decide.TOOLS:
                raise SkillError(f"an example uses an unknown tool {st.get('tool') if isinstance(st, dict) else st!r}")
            if set(st) - fields:
                raise SkillError(f"an example step has fields the decider does not have: {sorted(set(st) - fields)}")
        out["examples"].append({"request": req, "plan": plan})
    if not out["sites"] and not out["examples"]:
        raise SkillError("the proposal is empty")
    return out


def propose(change: dict, reason: str, root: Path = ROOT, builtin_sites: dict | None = None) -> str:
    """Stage a new version (immutable). Anyone may propose; nothing changes until the owner promotes it."""
    if builtin_sites is None:
        from . import decide
        builtin_sites = decide.SITES
    content = check(change, builtin_sites)
    reg = _reg(root)
    base = reg.get("active")
    if base:                                              # a version builds on the active one
        prev = load(base, root)
        content = {"sites": {**prev["sites"], **content["sites"]},
                   "examples": (prev["examples"] + content["examples"])[-MAX_EXAMPLES:]}
    n = 1 + max([int(v[1:]) for v in reg["versions"]] or [0])
    ver = f"v{n}"
    body = json.dumps({"version": ver, "base": base, "reason": str(reason)[:300], "content": content}, indent=1, sort_keys=True)
    (root / "versions").mkdir(parents=True, exist_ok=True)
    (root / "versions" / f"{ver}.json").write_text(body, encoding="utf-8")
    reg["versions"][ver] = {"sha256": hashlib.sha256(body.encode()).hexdigest(), "status": "staged", "created": time.time(), "base": base}
    _save_reg(root, reg)
    _audit(root, f"proposed {ver} (base {base}): {str(reason)[:120]}")
    return ver


def load(ver: str, root: Path = ROOT) -> dict:
    reg = _reg(root)
    meta = reg["versions"].get(ver)
    if not meta:
        raise SkillError(f"no version {ver}")
    body = (root / "versions" / f"{ver}.json").read_text(encoding="utf-8")
    if hashlib.sha256(body.encode()).hexdigest() != meta["sha256"]:
        raise SkillError(f"{ver} was changed after it was made; refusing it")
    return json.loads(body)["content"]


def active(root: Path = ROOT) -> dict:
    """The content the decider uses now ({} when nothing is promoted or the active file fails its hash)."""
    ver = _reg(root).get("active")
    if not ver:
        return {}
    try:
        return load(ver, root)
    except (SkillError, OSError, ValueError):
        return {}


def gate(ver: str, run_suite, root: Path = ROOT) -> dict:
    """run_suite(content) -> {case_id: passed}. Passes only without a regression against the active version."""
    reg = _reg(root)
    if ver not in reg["versions"]:
        raise SkillError(f"no version {ver}")
    staged = run_suite(load(ver, root))
    base = reg.get("active")
    baseline = run_suite(load(base, root) if base else {})
    regressed = sorted(c for c, ok in baseline.items() if ok and not staged.get(c, False))
    passed = not regressed and sum(staged.values()) >= sum(baseline.values())
    report = {"version": ver, "base": base, "sha256": reg["versions"][ver]["sha256"], "passed": passed, "regressed": regressed,
              "staged_pass": sum(staged.values()), "baseline_pass": sum(baseline.values()), "cases": len(staged), "time": time.time()}
    (root / "gates").mkdir(parents=True, exist_ok=True)
    (root / "gates" / f"{ver}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    reg["versions"][ver]["status"] = "gate_passed" if passed else "gate_failed"
    _save_reg(root, reg)
    _audit(root, f"gate {ver}: {'passed' if passed else 'FAILED'} ({report['staged_pass']} vs {report['baseline_pass']}; regressed {regressed})")
    return report


def promote(ver: str, root: Path = ROOT) -> str:
    reg = _reg(root)
    meta = reg["versions"].get(ver)
    if not meta:
        raise SkillError(f"no version {ver}")
    try:
        rep = json.loads((root / "gates" / f"{ver}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SkillError(f"{ver} has not been through the regression gate") from None
    if not rep.get("passed") or rep.get("sha256") != meta["sha256"]:
        raise SkillError(f"{ver} did not pass the gate (or changed since)")
    if rep.get("base") != reg.get("active"):              # the gate compared it with another active version
        raise SkillError(f"{ver} was gated against {rep.get('base')}, but {reg.get('active')} is active now; run the gate again")
    load(ver, root)                                       # hash check
    reg["history"].append(reg.get("active"))
    reg["active"] = ver
    meta["status"] = "active"
    _save_reg(root, reg)
    _audit(root, f"promoted {ver} by the owner")
    return ver


def rollback(root: Path = ROOT) -> str | None:
    reg = _reg(root)
    if not reg["history"]:
        raise SkillError("nothing to roll back to")
    old = reg.get("active")
    reg["active"] = reg["history"].pop()
    if old:
        reg["versions"][old]["status"] = "rolled_back"
    _save_reg(root, reg)
    _audit(root, f"rolled back {old} -> {reg['active']} by the owner")
    return reg["active"]


def _cli(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    try:
        if cmd == "promote":
            print("active:", promote(argv[2]))
        elif cmd == "rollback":
            print("active:", rollback())
        else:
            reg = _reg(ROOT)
            print(json.dumps({"active": reg.get("active"), "versions": {k: v["status"] for k, v in reg["versions"].items()}}, indent=1))
    except SkillError as e:
        print("refused:", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
