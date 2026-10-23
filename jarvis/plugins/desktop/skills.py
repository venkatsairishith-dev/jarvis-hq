"""Controlled self-improvement for JARVIS's decision layer (2026-10-16). Windows.

JARVIS may PROPOSE small additions to what the decider knows; it can never promote them itself.
  * What a proposal may contain (anything else is refused): "sites" (a spoken name -> an https address; new names only,
    never replacing a built-in one) and "examples" (a request with the plan it should produce, added to the prompt).
    No tool, permission, approval, safety or prompt-rule change is expressible here at all.
  * Every version is an immutable file versions/v<N>.json with its sha256 in registry.json; a changed file is refused.
  * gate(): the staged version runs the regression suite (dev + holdout + plan cases through the real decider); it passes
    only if no case that passes on the active version fails, and the total is not lower. The report is saved.
  * promote() / rollback(): OWNER ONLY, from the command line (no plugin entry exists for them):
        D:\\jarvis\\tools\\agent-venv\\Scripts\\python.exe -m jarvis.plugins.desktop.skills status|promote v3|rollback
    candidates / propose-mined (learn.py): list or STAGE examples mined from PC tasks that worked three times with every step observed; staging never activates anything.
    promote needs a passed gate for that exact file hash. Each change is appended to audit.log.
Reused idea: Voyager-style skill admission (a skill is admitted only with a passing test); the store is plain files.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

from . import coordinator as coord

ROOT = coord.DATA / "plugins" / "decider_skills"
ALLOWED = {"sites", "examples"}
MAX_EXAMPLES = 12


class SkillError(Exception):
    pass


def _reg(root: Path) -> dict:
    """The registry; a missing one is empty. A damaged one is REFUSED, never silently reset: starting over would reuse
    version names and overwrite the history the owner rolls back to."""
    try:
        reg = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"versions": {}, "active": None, "history": []}
    except (OSError, ValueError) as e:
        raise SkillError(f"registry.json in {root} is damaged ({type(e).__name__}); refusing to change anything. "
                         "The version files are untouched; the owner must repair or restore the registry") from None
    if not isinstance(reg, dict) or not isinstance(reg.get("versions"), dict) or not isinstance(reg.get("history", []), list):
        raise SkillError(f"registry.json in {root} is damaged (unexpected shape); refusing to change anything")
    reg.setdefault("history", [])
    return reg


def _save_reg(root: Path, reg: dict):
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=root, prefix="registry.", suffix=".tmp")      # a temp file of its own per writer
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(reg, indent=1))
        os.replace(tmp, root / "registry.json")
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


_THREAD_LOCK = threading.Lock()


@contextlib.contextmanager
def _locked(root: Path):
    """One registry read-modify-write at a time: across threads (a process lock) and across processes (a file lock)."""
    root.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, open(root / "registry.lock", "a+b") as f:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)        # retries for about 10 s, then raises OSError
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


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
    with _locked(root):
        reg = _reg(root)
        base = reg.get("active")
        if base:                                              # a version builds on the active one
            prev = load(base, root)
            content = {"sites": {**prev["sites"], **content["sites"]},
                       "examples": (prev["examples"] + content["examples"])[-MAX_EXAMPLES:]}
        (root / "versions").mkdir(parents=True, exist_ok=True)
        on_disk = [p.stem for p in (root / "versions").glob("v*.json")]
        nums = [int(v[1:]) for v in [*reg["versions"], *on_disk] if re.fullmatch(r"v\d+", v)]
        ver = f"v{1 + max(nums or [0])}"
        body = json.dumps({"version": ver, "base": base, "reason": str(reason)[:300], "content": content}, indent=1, sort_keys=True)
        try:
            with (root / "versions" / f"{ver}.json").open("x", encoding="utf-8") as f:   # never overwrite a version file
                f.write(body)
        except FileExistsError:
            raise SkillError(f"versions/{ver}.json already exists; refusing to overwrite the history") from None
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
    try:
        ver = _reg(root).get("active")
        if not ver:
            return {}
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
    # a suite that ran no cases (model down, case files missing) is no evidence: it never passes
    passed = bool(staged) and not regressed and sum(staged.values()) >= sum(baseline.values())
    report = {"version": ver, "base": base, "sha256": reg["versions"][ver]["sha256"], "passed": passed, "regressed": regressed,
              "staged_pass": sum(staged.values()), "baseline_pass": sum(baseline.values()), "cases": len(staged), "time": time.time()}
    (root / "gates").mkdir(parents=True, exist_ok=True)
    (root / "gates" / f"{ver}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    with _locked(root):                                   # re-read: proposals may have arrived while the suite ran
        reg = _reg(root)
        reg["versions"][ver]["status"] = "gate_passed" if passed else "gate_failed"
        _save_reg(root, reg)
    _audit(root, f"gate {ver}: {'passed' if passed else 'FAILED'} ({report['staged_pass']} vs {report['baseline_pass']}; regressed {regressed})")
    return report


def promote(ver: str, root: Path = ROOT) -> str:
    with _locked(root):
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
    with _locked(root):
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
        elif cmd in ("candidates", "propose-mined"):                # learning layer 3 (learn.py): what worked often, staged as examples
            from . import learn
            if cmd == "candidates":
                print(json.dumps([{"request": c["request"], "runs": c["runs"], "tools": [x["tool"] for x in c["plan"]["steps"]]}
                                  for c in learn.candidates(coord.TASKS)], indent=1))
            else:
                print(json.dumps(learn.propose_mined(coord.TASKS), indent=1))
        else:
            reg = _reg(ROOT)
            print(json.dumps({"active": reg.get("active"), "versions": {k: v["status"] for k, v in reg["versions"].items()}}, indent=1))
    except SkillError as e:
        print("refused:", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
