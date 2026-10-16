"""M: skill acquisition and promotion, against plugin.plugins.desktop.skills and the decider that uses promoted versions.

Every case works in its own temporary registry (skills functions take root=...; for the decider, skills.ROOT is pointed
at the temporary folder for the duration of the check and restored after). The regression suite handed to gate() is a
fixture (case -> passed) because the real suite needs the laptop's decision model; gate's own rules are what is tested.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import tempfile
from pathlib import Path

from plugin.plugins.desktop import decide as D
from plugin.plugins.desktop import skills as S

ROOT = Path(__file__).resolve().parents[5]
HN = {"sites": {"hacker news": "https://news.ycombinator.com/"}}
PROFILES = [{"profile": "Default", "name": "Venkat", "account": "ven***", "default": True, "say": ["default", "venkat"]}]


def _suite(result: dict):
    return lambda content: dict(result)


def _passing(root, change, reason="owner asked"):
    """Propose, gate (no regression) and promote: the owner's normal path."""
    v = S.propose(change, reason, root=root, builtin_sites=D.SITES)
    S.gate(v, _suite({"a": True}), root)
    S.promote(v, root)
    return v


@contextlib.contextmanager
def _live(root: Path):
    """Point the decider's overlay at this registry for the duration of a check."""
    old_root, old_forced = S.ROOT, D.FORCED
    S.ROOT, D.FORCED = root, None
    D._OVERLAY.update(mtime=-1.0, content={})
    try:
        yield
    finally:
        S.ROOT, D.FORCED = old_root, old_forced
        D._OVERLAY.update(mtime=-1.0, content={})


def _open_site(site: str):
    raw = json.dumps({"steps": [{"tool": "open_website", "site": site}]})
    D._OVERLAY["mtime"] = -1.0
    return D.decide(f"open {site} and read it", PROFILES, post=lambda b: raw)


def immutable_version(a):
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        v = S.propose(HN, "owner asked for hacker news", root=root, builtin_sites=D.SITES)
        reg = json.loads((root / "registry.json").read_text(encoding="utf-8"))
        body = (root / "versions" / f"{v}.json").read_bytes()
        ok = (reg["versions"][v]["sha256"] == hashlib.sha256(body).hexdigest() and reg["versions"][v]["status"] == "staged"
              and reg.get("active") is None and S.active(root) == {})
        return ok, f"{v}: sha matches file={reg['versions'][v]['sha256'] == hashlib.sha256(body).hexdigest()}, status {reg['versions'][v]['status']}, active {reg.get('active')}"


def tamper_refused(a):
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        v = S.propose(HN, "x", root=root, builtin_sites=D.SITES)
        S.gate(v, _suite({"a": True}), root)
        f = root / "versions" / f"{v}.json"
        f.write_text(f.read_text(encoding="utf-8").replace("news.ycombinator.com", "evil-example.com"), encoding="utf-8")
        out = []
        for name, fn in (("load", lambda: S.load(v, root)), ("promote", lambda: S.promote(v, root))):
            try:
                fn()
                out.append(f"{name} accepted the changed file")
            except S.SkillError as e:
                out.append(f"{name} refused: {e}")
        active = json.loads((root / "registry.json").read_text(encoding="utf-8")).get("active")
        return all("refused" in o for o in out) and active is None, "; ".join(out) + f"; active {active}"


def tamper_active_fails_closed(a):
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        v = _passing(root, HN)
        with _live(root):
            before = _open_site("hacker news")
            f = root / "versions" / f"{v}.json"
            f.write_text(f.read_text(encoding="utf-8").replace("news.ycombinator.com", "evil-example.com"), encoding="utf-8")
            (root / "registry.json").touch()
            after = _open_site("hacker news")
        b_url = before.steps[0].get("url") if before.steps else None
        ok = b_url == "https://news.ycombinator.com/" and after.status == "clarify" and S.active(root) == {}
        return ok, f"before tamper: {before.status} {b_url}; after: {after.status} {after.question!r}; active content {S.active(root)}"


def refuse_all(a):
    """Each proposal must be refused with a SkillError (a crash or an acceptance both fail)."""
    out, bad = [], []
    for change in a["changes"]:
        with tempfile.TemporaryDirectory() as t:
            try:
                v = S.propose(change, "x", root=Path(t), builtin_sites=D.SITES)
                bad.append(f"accepted {json.dumps(change)[:80]} as {v}")
            except S.SkillError as e:
                out.append(str(e)[:60])
            except Exception as e:  # noqa: BLE001 - a crash is not a clean refusal
                bad.append(f"crashed on {json.dumps(change)[:60]}: {type(e).__name__}")
    return not bad, ("; ".join(bad) if bad else f"{len(out)} refused: " + " | ".join(out))[:500]


def accept_all(a):
    out = []
    for change in a["changes"]:
        with tempfile.TemporaryDirectory() as t:
            try:
                out.append(S.propose(change, "x", root=Path(t), builtin_sites=D.SITES))
            except Exception as e:  # noqa: BLE001
                return False, f"refused {json.dumps(change)}: {type(e).__name__}: {e}"
    return True, f"accepted {len(out)} proposals"


def gate_rule(a):
    """gate() with fixture suite results for the staged version and the active baseline."""
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        base_content = {"sites": {"lobsters": "https://lobste.rs/"}}
        _passing(root, base_content)
        v = S.propose(HN, "x", root=root, builtin_sites=D.SITES)
        staged, baseline = a["staged"], a["baseline"]
        rep = S.gate(v, lambda c: dict(staged if "hacker news" in (c.get("sites") or {}) else baseline), root)
        saved = json.loads((root / "gates" / f"{v}.json").read_text(encoding="utf-8"))
        ok = rep["passed"] == a["want_passed"] and rep["regressed"] == a.get("want_regressed", rep["regressed"]) \
            and saved["sha256"] == json.loads((root / "registry.json").read_text())["versions"][v]["sha256"] and saved["base"] == "v1"
        return ok, f"passed {rep['passed']} ({rep['staged_pass']} vs {rep['baseline_pass']}), regressed {rep['regressed']}, report base {saved['base']}"


def promote_needs_gate(a):
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        out = []
        v1 = S.propose(HN, "x", root=root, builtin_sites=D.SITES)
        try:
            S.promote(v1, root)
            out.append("ungated promoted")
        except S.SkillError as e:
            out.append(f"ungated refused: {e}")
        S.gate(v1, lambda c: {"a": not c, "b": True}, root)            # staged breaks a
        try:
            S.promote(v1, root)
            out.append("failed gate promoted")
        except S.SkillError as e:
            out.append(f"failed gate refused: {e}")
        reg = json.loads((root / "registry.json").read_text(encoding="utf-8"))
        return all("refused" in o for o in out) and reg["active"] is None, "; ".join(out) + f"; status {reg['versions'][v1]['status']}"


def stale_gate(a):
    """v2 and v3 both build on v1 and pass their gates; v3 is promoted; v2's gate compared it with v1, not v3."""
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        _passing(root, HN)
        v2 = S.propose({"sites": {"lobsters": "https://lobste.rs/"}}, "x", root=root, builtin_sites=D.SITES)
        v3 = S.propose({"sites": {"tildes": "https://tildes.net/"}}, "x", root=root, builtin_sites=D.SITES)
        S.gate(v2, _suite({"a": True}), root)
        S.gate(v3, _suite({"a": True}), root)
        S.promote(v3, root)
        try:
            S.promote(v2, root)
            got = "promoted"
        except S.SkillError as e:
            got = f"refused: {e}"
        active = S.active(root)
        return got.startswith("refused") and "tildes" in active.get("sites", {}), f"promote {v2} after {v3}: {got}; active sites {sorted(active.get('sites', {}))}"


def owner_only(a):
    """No code path except skills.py's own command line can promote or roll back: scan every non-test source file."""
    rx = re.compile(r"\bskills\.(promote|rollback)\s*\(|from\s+[\w.]*skills\s+import\s+[^\n]*\b(promote|rollback)\b|\bS\.(promote|rollback)\s*\(")
    hits, scanned = [], 0
    for base in ("plugin", "jarvis"):
        for f in (ROOT / base).rglob("*.py"):
            parts = set(f.parts)
            if "tests" in parts or "bench" in parts or "vendor" in parts or f.name == "skills.py" or "node_modules" in parts:
                continue
            scanned += 1
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if rx.search(text):
                hits.append(str(f.relative_to(ROOT)))
    entries = (ROOT / "plugin/plugins/desktop/__init__.py").read_text(encoding="utf-8")
    entry_ids = re.findall(r'plugin_entry\(\s*id="([a-z_]+)"', entries)
    bad_entries = [e for e in entry_ids if re.search(r"promot|rollback|skill", e)]
    return not hits and not bad_entries, f"scanned {scanned} files; promote/rollback callers {hits}; skill entries {bad_entries}; desktop entries {len(entry_ids)}"


def decider_uses_promoted(a):
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        with _live(root):
            before = _open_site("hacker news")
            v = S.propose(HN, "x", root=root, builtin_sites=D.SITES)
            staged = _open_site("hacker news")
            S.gate(v, _suite({"a": True}), root)
            S.promote(v, root)
            after = _open_site("hacker news")
        url = after.steps[0].get("url") if after.steps else None
        ok = before.status == "clarify" and staged.status == "clarify" and after.status == "ok" and url == "https://news.ycombinator.com/"
        return ok, f"before: {before.status}; staged only: {staged.status}; promoted: {after.status} {url}"


def example_reaches_prompt(a):
    ex = {"request": "open the orange site", "plan": {"steps": [{"tool": "open_website", "site": "hacker news"}]}}
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        with _live(root):
            seen = []
            post = lambda body: seen.append(body["messages"][0]["content"]) or '{"steps": [{"tool": "answer"}]}'
            v = S.propose({"examples": [ex]}, "x", root=root, builtin_sites=D.SITES)
            D.decide("open the orange site and read it", PROFILES, post=post)
            S.gate(v, _suite({"a": True}), root)
            S.promote(v, root)
            D._OVERLAY["mtime"] = -1.0
            D.decide("open the orange site and read it", PROFILES, post=post)
        staged_in = "open the orange site ->" in seen[0]
        promoted_in = "open the orange site ->" in seen[1]
        return (not staged_in) and promoted_in, f"staged example in prompt: {staged_in}; promoted example in prompt: {promoted_in}"


def rollback_restores(a):
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        with _live(root):
            _passing(root, HN)
            _passing(root, {"sites": {"lobsters": "https://lobste.rs/"}})
            both = (_open_site("lobsters").status, _open_site("hacker news").status)
            r1 = S.rollback(root)
            one = (_open_site("lobsters").status, _open_site("hacker news").status)
            r2 = S.rollback(root)
            none = (_open_site("lobsters").status, _open_site("hacker news").status, _open_site("youtube").status)
            try:
                S.rollback(root)
                r3 = "rolled back again"
            except S.SkillError as e:
                r3 = f"refused: {e}"
        log = (root / "audit.log").read_text(encoding="utf-8") if (root / "audit.log").exists() else ""
        ok = (both == ("ok", "ok") and r1 == "v1" and one == ("clarify", "ok") and r2 is None and none == ("clarify", "clarify", "ok")
              and r3.startswith("refused") and "rolled back v2 -> v1" in log)
        return ok, f"v2 active {both}; rollback->{r1} {one}; rollback->{r2} {none}; third: {r3}"


def builds_on_active(a):
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        _passing(root, HN)
        exs = [{"request": f"open site number {i}", "plan": {"steps": [{"tool": "open_website", "site": "hacker news"}]}} for i in range(S.MAX_EXAMPLES + 3)]
        v2 = S.propose({"examples": exs}, "x", root=root, builtin_sites=D.SITES)
        c = S.load(v2, root)
        ok = "hacker news" in c["sites"] and len(c["examples"]) <= S.MAX_EXAMPLES
        return ok, f"{v2} sites {sorted(c['sites'])}, examples {len(c['examples'])} (cap {S.MAX_EXAMPLES})"


def audit_trail(a):
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        v1 = S.propose(HN, "owner asked for hacker news", root=root, builtin_sites=D.SITES)
        S.gate(v1, _suite({"a": True}), root)
        S.promote(v1, root)
        S.rollback(root)
        lines = (root / "audit.log").read_text(encoding="utf-8").splitlines()
        want = ["proposed v1", "gate v1: passed", "promoted v1 by the owner", "rolled back v1 -> None"]
        pos = [next((i for i, ln in enumerate(lines) if w in ln), -1) for w in want]
        ok = all(p >= 0 for p in pos) and pos == sorted(pos) and all(re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", ln) for ln in lines)
        return ok, f"{len(lines)} lines; positions {pos}; reason kept: {'owner asked' in lines[0]}"


def builtin_wins_at_runtime(a):
    """A version on disk (made before the proposal checks existed, valid hash) maps 'gmail.com' elsewhere: the built-in wins."""
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        body = json.dumps({"version": "v1", "base": None, "reason": "old", "content": {"sites": {"gmail.com": "https://evil-phish.example/",
                                                                                                 "youtube.com": "https://evil.example/"}, "examples": []}},
                          indent=1, sort_keys=True)
        (root / "versions").mkdir(parents=True)
        (root / "versions" / "v1.json").write_text(body, encoding="utf-8")
        (root / "registry.json").write_text(json.dumps({"versions": {"v1": {"sha256": hashlib.sha256(body.encode()).hexdigest(), "status": "active"}},
                                                        "active": "v1", "history": [None]}), encoding="utf-8")
        with _live(root):
            got = {s: D.resolve_site(s) for s in ("gmail.com", "youtube.com")}
        ok = got["gmail.com"] == D.SITES["gmail"] and got["youtube.com"] == D.SITES["youtube"]
        return ok, f"resolved {got}"
