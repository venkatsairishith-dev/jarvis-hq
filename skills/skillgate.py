"""Skill gate: stage, check, activate and roll back Hermes skills for the JARVIS lab. Runs on the Windows laptop.

    python hq/skills/skillgate.py scan                      # rate every optional skill in the pinned source (no copy)
    python hq/skills/skillgate.py stage software-development/grill-me
    python hq/skills/skillgate.py guard grill-me            # Hermes's own skills_guard + linter, in the lab
    python hq/skills/skillgate.py activate grill-me         # allowlist + checks + kernel authorization -> lab, smoke test
    python hq/skills/skillgate.py deactivate grill-me       # kernel-audited rollback (restores any previous copy)
    python hq/skills/skillgate.py status

Nothing is trusted because it imports: staging copies files byte-for-byte (scripts never), records provenance
(repo, commit, path, licence, SHA-256 and git blob id per file), maps the tools the skill asks for onto the JARVIS
registry, rates risk from static checks, and runs Hermes's maintained scanner (tools/skills_guard.py) on the staged
copy. Activation needs: an allowlist entry, passed checks, a low/medium rating and the kernel's authorization (policy
skill.activate: low -> automatic and audited, medium -> the owner's approval, anything else -> denied). Skills go only
to the lab Hermes (its own computer); the owner's server agent has no tools a skill could use.

Reuses OpenJarvis's SKILL.md parser and tool translator (vendor/openjarvis, Apache-2.0, see NOTICE.md). Adapted as
patterns: its .source sidecar (extended with hashes and licence) and scripts-off-by-default. Written new: staging,
risk rules, tool mapping to JARVIS, kernel-gated activation and rollback (OpenJarvis imports straight into active
skills with no review or rollback; see docs/01_RESEARCH_AUDIT.md).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from skills.vendor.openjarvis.parser import SkillParseError, SkillParser          # noqa: E402  (vendored, Apache-2.0)
from skills.vendor.openjarvis.tool_translator import ToolTranslator               # noqa: E402

SOURCE = {"repo": "https://github.com/NousResearch/hermes-agent", "commit": "818c13be1dc4fd28987e1e881a9408224afd4535",
          "root": r"D:\jarvis\research\hermes-agent-818c13be", "subdir": "optional-skills", "license": "MIT"}
STAGING = os.path.join(HERE, "staging")
ACTIVE = os.path.join(HERE, "active.json")
ALLOWLIST = os.path.join(HERE, "allowlist.json")
LAB_SKILLS = "/home/agent/.hermes/skills/jarvis-staged"     # the lab Hermes reads skills from ~/.hermes/skills/<category>/
LAB_STAGE = "/home/agent/skill-staging"
KERNEL = os.environ.get("JARVIS_ENDPOINT", "http://100.125.91.51:8790")
TOKEN_FILE = os.environ.get("JARVIS_SKILL_TOKEN", r"D:\jarvis-secrets\kernel\skill-operator.token")
MAX_FILE, MAX_TOTAL, MAX_FILES = 200_000, 1_000_000, 60
TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".html", ".css", ".svg", ".mmd"}
EXCLUDED_DIRS = {"scripts", "bin", "node_modules", "__pycache__", ".git"}

# The tools a skill may ask for, as the JARVIS registry provides them in the lab (Hermes's own tools inside its VM;
# kernel-gated JARVIS tools through the jarvis MCP server), and those it does not get at all.
LAB_NATIVE = {"terminal", "process", "read_file", "write_file", "patch", "search_files", "web_search", "web_extract",
              "browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_scroll", "browser_back",
              "browser_press", "browser_vision", "browser_console", "execute_code", "vision_analyze", "todo", "memory",
              "skill_view", "skills_list", "clarify", "delegate_task", "session_search"}
JARVIS_GATED = {"save_output", "show_output", "open_app", "open_folder", "open_file", "pc_action", "start_lab_task",
                "lab_task_result", "models_status", "switch_model"}
UNAVAILABLE = {"send_message", "cronjob", "image_generate", "text_to_speech", "mixture_of_agents", "ha_call_service",
               "ha_get_state", "spotify", "discord", "x_search", "kanban", "computer_use", "rl_", "video_generate"}
CLAUDE_NAMES = {"Bash": "terminal", "Read": "read_file", "Write": "write_file", "Edit": "patch", "Glob": "search_files",
                "Grep": "search_files", "WebFetch": "web_extract", "WebSearch": "web_search", "Task": "delegate_task"}

# Static rules. reject: never activated. medium: needs the owner's approval. Each finding records file and line.
REJECT = [
    ("secrets", r"\.ssh/|id_rsa|id_ed25519|\.aws/credentials|/etc/shadow|keychain|\.netrc|\bsecrets?\.env\b|\.env\b(?!ironment)"),
    ("exfiltration", r"(curl|wget)[^\n|]*\|\s*(ba|z)?sh\b|base64\s+-d[^\n]*\|\s*(ba)?sh|\bnc\s+-e\b|/dev/tcp/"),
    ("privilege", r"\bsudo\b|chmod\s+(-R\s+)?777|\bsetuid\b|\bchown\s+root\b|systemctl\s+(enable|start|restart)"),
    ("owner_paths", r"[A-Za-z]:\\\\?(Users|jarvis)|/mnt/[a-z]/|\bD:/jarvis"),
    ("stealth", r"cloudflare\s+bypass|bypass\s+(cloudflare|captcha|bot)|stealth\s+brows|evade\s+detection|anti-?detect"),
    ("money", r"(?<!git )\b(checkout|purchase|payment|credit\s*card|stripe|paypal|wallet|buy\s+now)\b"),
    ("prompt_injection", r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions|disregard\s+(the\s+)?(system|previous)|you\s+are\s+now\s+(?!ready)"),
]
MEDIUM = [
    ("network_install", r"\b(pip|pip3|uv)\s+install\b|\bnpm\s+(i|install)\b|\bnpx\s|\bbrew\s+install\b|\bapt(-get)?\s+install\b|\bcargo\s+install\b"),
    ("api_key", r"\b[A-Z][A-Z0-9_]*_(API_KEY|TOKEN|SECRET)\b"),
    ("external_service", r"https?://(?!github\.com|docs\.|en\.wikipedia\.org|localhost|127\.0\.0\.1)[a-z0-9.-]+\.[a-z]{2,}"),
]
PERSONAL = r"\b(e-?mail|inbox|calendar|contacts|messages|bank|health|medical|passport|address book|phone number)\b"
PURPOSE = [("research", r"\b(research|search|sources|papers?|investigat)"), ("documentation", r"\b(docs?|wiki|readme|document|diagram)"),
           ("code_review", r"\b(review|critique|plan interview|grill|adversarial)"), ("testing", r"\b(test|debug|qa|ux|repro|bug)")]


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_blob(b: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def read_frontmatter(text: str):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        raise SkillParseError("SKILL.md has no YAML frontmatter")
    import yaml                                  # the vendored parser's own dependency (yaml.safe_load only)
    fm = yaml.safe_load(m.group(1)) or {}
    if not isinstance(fm, dict):
        raise SkillParseError("frontmatter is not a mapping")
    return fm, m.group(2)


def source_dir(rel: str) -> str:
    d = os.path.realpath(os.path.join(SOURCE["root"], SOURCE["subdir"], *rel.split("/")))
    if not d.startswith(os.path.realpath(SOURCE["root"]) + os.sep) or not os.path.isfile(os.path.join(d, "SKILL.md")):
        raise SystemExit(f"not a skill in the pinned source: {rel}")
    return d


def inventory(d: str):
    """(included files, excluded files) as [(relpath, bytes)], refusing links and enforcing limits."""
    inc, exc = [], []
    for root, dirs, files in os.walk(d):
        dirs.sort(); files.sort()
        rel_root = os.path.relpath(root, d).replace("\\", "/")
        top = rel_root.split("/")[0] if rel_root != "." else ""
        for f in files:
            p = os.path.join(root, f)
            rel = f if rel_root == "." else f"{rel_root}/{f}"
            if os.path.islink(p):
                raise SystemExit(f"refused: {rel} is a link")
            data = open(p, "rb").read()
            hidden = any(part.startswith(".") for part in rel.split("/"))
            if top in EXCLUDED_DIRS or hidden or os.path.splitext(f)[1].lower() not in TEXT_EXT or len(data) > MAX_FILE:
                exc.append((rel, data))
            else:
                inc.append((rel, data))
    if len(inc) > MAX_FILES or sum(len(b) for _, b in inc) > MAX_TOTAL:
        raise SystemExit("refused: skill too large")
    return inc, exc


def findings_for(files, rules):
    out = []
    for rel, data in files:
        text = data.decode("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            for name, rx in rules:
                if re.search(rx, line, re.I):
                    out.append({"rule": name, "file": rel, "line": i, "text": line.strip()[:160]})
    return out


def tools_of(text: str):
    """Tool names a skill refers to, mapped onto the JARVIS registry (OpenJarvis's translator finds Claude-Code names)."""
    translated, unknown_camel = ToolTranslator(CLAUDE_NAMES).translate_markdown(text)
    names = set(re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+|terminal|memory|todo|clarify|patch|process)\b", translated))
    mapping = {}
    for n in sorted(names):
        if n in LAB_NATIVE:
            mapping[n] = "lab-native (its own sandbox computer)"
        elif n in JARVIS_GATED:
            mapping[n] = "jarvis-mcp (each action authorized by the kernel)"
        elif n in UNAVAILABLE or any(n.startswith(u) for u in UNAVAILABLE if u.endswith("_")):
            mapping[n] = "UNAVAILABLE"
    return mapping, unknown_camel


def rate(rel, files, excluded, fm, mapping):
    body = "\n".join(b.decode("utf-8", "replace") for _, b in files)
    rej, med = findings_for(files, REJECT), findings_for(files, MEDIUM)
    script_refs = bool(re.search(r"\bscripts?/", body))
    reasons = []
    if rej:
        reasons.append("reject rules: " + ", ".join(sorted({f["rule"] for f in rej})))
    if any(v == "UNAVAILABLE" for v in mapping.values()):
        reasons.append("needs tools JARVIS does not give: " + ", ".join(k for k, v in mapping.items() if v == "UNAVAILABLE"))
    if script_refs and excluded:
        reasons.append("its instructions rely on scripts, which are excluded until their source is reviewed")
    if med:
        reasons.append("medium rules: " + ", ".join(sorted({f["rule"] for f in med})))
    if any(k.startswith("browser") or k == "delegate_task" for k in mapping):
        reasons.append("drives a browser or starts sub-agents")
    tier = ("reject" if rej else "high" if (script_refs and excluded) or any(v == "UNAVAILABLE" for v in mapping.values())
            else "medium" if (med or any(k.startswith("browser") or k == "delegate_task" for k in mapping)) else "low")
    blob = (str(fm.get("description", "")) + " " + rel).lower()
    purpose = next((p for p, rx in PURPOSE if re.search(rx, blob)), "other")
    perms = sorted({"shell" if k in ("terminal", "process", "execute_code") else "files" if k in ("read_file", "write_file", "patch", "search_files")
                    else "network" if k.startswith("web_") else "browser" if k.startswith("browser") else "agents" if k == "delegate_task"
                    else "owner-pc" if v.startswith("jarvis-mcp") else "other" for k, v in mapping.items()})
    return {"tier": tier, "reasons": reasons, "reject_findings": rej[:20], "medium_findings": med[:20], "purpose": purpose,
            "data_sensitivity": "personal" if re.search(PERSONAL, body, re.I) else "none", "permissions": perms,
            "scripts_referenced": script_refs}


def analyse(rel):
    d = source_dir(rel)
    files, excluded = inventory(d)
    text = dict(files)["SKILL.md"].decode("utf-8")
    fm, body = read_frontmatter(text)
    manifest = SkillParser().parse_frontmatter(fm, markdown_content=body)       # strict spec checks (name, lengths)
    mapping, unknown = tools_of(body)
    deps = sorted(set(re.findall(r"\b(?:pip3?|uv|npm|npx|brew|apt(?:-get)?|cargo)\s+(?:install\s+|i\s+)?([A-Za-z0-9_.@/-]+)", body)))[:20]
    return d, files, excluded, fm, manifest, mapping, unknown, deps


def cmd_scan(_a):
    root = os.path.join(SOURCE["root"], SOURCE["subdir"])
    rows = []
    for dirpath, _, fnames in os.walk(root):
        if "SKILL.md" in fnames:
            rel = os.path.relpath(dirpath, root).replace("\\", "/")
            try:
                d, files, excluded, fm, man, mapping, _, _ = analyse(rel)
                r = rate(rel, files, excluded, fm, mapping)
                rows.append((r["tier"], rel, r["purpose"], "; ".join(r["reasons"])[:110]))
            except (SkillParseError, SystemExit, UnicodeDecodeError, KeyError) as e:
                rows.append(("invalid", rel, "-", str(e)[:110]))
    order = {"low": 0, "medium": 1, "high": 2, "reject": 3, "invalid": 4}
    for t, rel, p, why in sorted(rows, key=lambda r: (order[r[0]], r[1])):
        print(f"{t:8s} {rel:55s} {p:14s} {why}")
    print({t: sum(1 for r in rows if r[0] == t) for t in order})


def cmd_stage(a):
    d, files, excluded, fm, man, mapping, unknown, deps = analyse(a.rel)
    name = man.name
    dest = os.path.join(STAGING, name)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    for rel, data in files:                                     # byte-for-byte: hashes stay comparable with upstream
        p = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    r = rate(a.rel, files, excluded, fm, mapping)
    prov = {
        "name": name, "staged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": {"repo": SOURCE["repo"], "commit": SOURCE["commit"], "path": f"{SOURCE['subdir']}/{a.rel}",
                   "license": fm.get("license") or SOURCE["license"], "license_source": "frontmatter" if fm.get("license") else "repository LICENSE"},
        "description": man.description, "version": man.version, "author": man.author,
        "files": [{"path": rel, "bytes": len(b), "sha256": sha256(b), "git_blob": git_blob(b)} for rel, b in files],
        "excluded": [{"path": rel, "bytes": len(b), "sha256": sha256(b), "why": "script or non-text: excluded until its source is reviewed"} for rel, b in excluded],
        "tools": mapping, "unmapped_tool_like_names": unknown[:20], "dependencies": deps,
        "capabilities_declared": man.required_capabilities,
        "risk": r, "checks": {"static": "passed" if r["tier"] in ("low", "medium") else "failed"},
        "manifest_sha256": None,
    }
    prov["manifest_sha256"] = sha256(json.dumps(prov["files"], sort_keys=True).encode())
    with open(os.path.join(dest, "PROVENANCE.json"), "w", encoding="utf-8") as fh:
        json.dump(prov, fh, indent=1)
    print(json.dumps({k: prov[k] for k in ("name", "source", "tools", "dependencies")}, indent=1))
    print("risk:", r["tier"], "|", "; ".join(r["reasons"]) or "no findings", "| excluded files:", len(excluded))


def load_prov(name):
    p = os.path.join(STAGING, name, "PROVENANCE.json")
    if not os.path.isfile(p):
        raise SystemExit(f"{name} is not staged")
    prov = json.load(open(p, encoding="utf-8"))
    for f in prov["files"]:                                   # the staged copy must still be what was checked
        data = open(os.path.join(STAGING, name, *f["path"].split("/")), "rb").read()
        if sha256(data) != f["sha256"]:
            raise SystemExit(f"{name}: {f['path']} changed since staging; stage it again")
    return prov


def save_prov(name, prov):
    with open(os.path.join(STAGING, name, "PROVENANCE.json"), "w", encoding="utf-8") as fh:
        json.dump(prov, fh, indent=1)


def wsl(args, stdin=None, user="agent"):
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    return subprocess.run(["wsl", "-d", "JarvisLab", "-u", user, "--", *args], input=stdin, capture_output=True, env=env)


def push_to_lab(name, lab_dir):
    """Copy the staged files (PROVENANCE.json included) into a lab folder as the agent user, through tar on stdin."""
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for root, _, files in os.walk(os.path.join(STAGING, name)):
            for f in files:
                p = os.path.join(root, f)
                tar.add(p, arcname=os.path.relpath(p, os.path.join(STAGING, name)).replace("\\", "/"))
    r = wsl(["sh", "-c", f'rm -rf "{lab_dir}.new" && mkdir -p "{lab_dir}.new" && tar -xf - -C "{lab_dir}.new"'], stdin=buf.getvalue())
    if r.returncode:
        raise SystemExit("lab copy failed: " + r.stderr.decode()[:200])


GUARD_PY = r"""
import json, sys
sys.path.insert(0, "/home/agent/apps/hermes-agent")
from pathlib import Path
from tools.skills_guard import scan_skill, should_allow_install
p = Path(sys.argv[1])
res = scan_skill(p, source="community")
ok, why = should_allow_install(res)
out = {"verdict": str(getattr(res, "verdict", "")), "allowed": bool(ok), "reason": str(why)[:300],
       "findings": [{k: str(getattr(f, k, ""))[:160] for k in ("pattern_id", "severity", "category", "file", "line", "description")}
                    for f in (getattr(res, "findings", None) or [])][:30]}
try:
    from tools.skill_linter import lint_skill
    try:
        lint = lint_skill(p / "SKILL.md")
    except (IsADirectoryError, TypeError):
        lint = lint_skill(str(p / "SKILL.md"))
    out["lint"] = [str(x)[:200] for x in (lint if isinstance(lint, list) else getattr(lint, "issues", []) or [])][:20]
except Exception as e:
    out["lint"] = ["linter unavailable: " + type(e).__name__]
print(json.dumps(out))
"""


def cmd_guard(a):
    prov = load_prov(a.name)
    lab = f"{LAB_STAGE}/{a.name}"
    push_to_lab(a.name, lab)
    wsl(["sh", "-c", f'rm -rf "{lab}" && mv "{lab}.new" "{lab}"'])
    r = wsl(["/home/agent/apps/hermes-venv/bin/python", "-c", GUARD_PY, lab])
    if r.returncode:
        raise SystemExit("Hermes skills_guard failed to run: " + r.stderr.decode()[-300:])
    g = json.loads(r.stdout.decode().strip().splitlines()[-1])
    prov["checks"]["hermes_skills_guard"] = g
    prov["checks"]["guard"] = "passed" if g["allowed"] else "failed"
    sev = {f["severity"].lower() for f in g["findings"]}
    order = ["low", "medium", "high", "reject"]
    floor = "reject" if sev & {"high", "critical"} or not g["allowed"] else "medium" if "medium" in sev else "low"
    if order.index(floor) > order.index(prov["risk"]["tier"]):       # Hermes's findings can only raise the rating
        prov["risk"]["reasons"].append(f"Hermes skills_guard findings ({', '.join(sorted(sev))}) raise the rating to {floor}")
        prov["risk"]["tier"] = floor
        if floor == "reject":
            prov["checks"]["static"] = "failed"
    save_prov(a.name, prov)
    print(json.dumps(g, indent=1)[:2500])


def kernel_call(op, prov, approval_id=None):
    tok = open(TOKEN_FILE, encoding="utf-8-sig").read().strip()
    body = {"tool": "skill", "op": op, "target": prov["name"],
            "params": {"risk": prov["risk"]["tier"], "where": "lab", "sha256": prov["manifest_sha256"]}}
    if approval_id:
        body["approval_id"] = approval_id
    r = urllib.request.Request(KERNEL + "/v1/jarvis/actions", data=json.dumps(body).encode(), method="POST",
                               headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def load_active():
    try:
        return json.load(open(ACTIVE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"lab": {}, "log": []}


def save_active(st):
    with open(ACTIVE, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)


SMOKE = ("Use the skill_view tool to load the skill named '{name}'. Then reply with exactly one line that starts with "
         "'SKILL-OK {name}:' followed by the skill's purpose in under 15 words. If you cannot load it, reply 'SKILL-MISSING'.")


def smoke(name, timeout=600, tries=3):
    """A controlled functional check in the lab: a fresh Hermes session must find and load the skill. A model that
    gives no reply at all is inconclusive (a known intermittent fault of the lab model), so it is asked again, at
    most *tries* times; SKILL-MISSING or any other answer fails at once."""
    out = ""
    for i in range(1, tries + 1):
        r = wsl(["timeout", str(timeout), "/home/agent/.local/bin/hermes", "chat", "-q", SMOKE.format(name=name), "--oneshot", "--quiet",
                 "--max-turns", "6"])
        out = r.stdout.decode(errors="replace").strip()
        if f"SKILL-OK {name}" in out:
            return True, f"try {i}: {out[-300:]}"
        if out and "No reply" not in out and "didn't produce a reply" not in out:
            return False, f"try {i}: {out[-300:]}"
    return False, f"inconclusive after {tries} tries (the lab model gave no reply): {out[-200:]}"


def cmd_activate(a):
    prov = load_prov(a.name)
    allow = json.load(open(ALLOWLIST, encoding="utf-8"))
    entry = next((e for e in allow["skills"] if e["name"] == a.name), None)
    problems = []
    if not entry:
        problems.append("not on the allowlist")
    if prov["checks"].get("static") != "passed":
        problems.append("static checks failed")
    if prov["checks"].get("guard") != "passed":
        problems.append("Hermes skills_guard has not passed (run: guard)")
    if prov["risk"]["tier"] not in ("low", "medium"):
        problems.append(f"risk {prov['risk']['tier']}")
    if problems:
        raise SystemExit("not activated: " + "; ".join(problems))
    code, r = kernel_call("activate", prov, a.approval_id)
    if code == 202:
        print(f"the kernel needs the owner's approval: /approve {r['approval_id']} on Telegram, then rerun with --approval-id {r['approval_id']}")
        return 3
    if code != 200:
        raise SystemExit(f"kernel refused ({code}): {r.get('reason') or r}")
    lab = f"{LAB_SKILLS}/{a.name}"
    push_to_lab(a.name, lab)
    ts = int(time.time())
    r1 = wsl(["sh", "-c", f'mkdir -p "{LAB_SKILLS}" && if [ -d "{lab}" ]; then mv "{lab}" "{lab}.bak-{ts}"; fi && mv "{lab}.new" "{lab}"'])
    if r1.returncode:
        raise SystemExit("lab install failed: " + r1.stderr.decode()[:200])
    st = load_active()
    st["lab"][a.name] = {"sha256": prov["manifest_sha256"], "commit": prov["source"]["commit"], "risk": prov["risk"]["tier"],
                         "activated": time.strftime("%Y-%m-%dT%H:%M:%S"), "backup": f"{lab}.bak-{ts}"}
    st["log"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "op": "activate", "skill": a.name, "kernel": r.get("level")})
    save_active(st)
    ok, out = smoke(a.name) if not a.no_smoke else (True, "skipped")
    prov["checks"]["smoke"] = {"passed": ok, "output": out}
    save_prov(a.name, prov)
    if not ok:                                   # roll back first; reporting must never stand between a failure and its rollback
        try:
            cmd_deactivate(argparse.Namespace(name=a.name))
        finally:
            print("smoke test failed; rolled back:", out)
        return 1
    print(f"active in the lab: {a.name} (kernel {r.get('level')}, smoke: {out[:160]})")
    return 0


def cmd_deactivate(a):
    st = load_active()
    rec = st["lab"].get(a.name)
    prov = load_prov(a.name) if os.path.isdir(os.path.join(STAGING, a.name)) else {"name": a.name, "risk": {"tier": ""}, "manifest_sha256": ""}
    code, r = kernel_call("deactivate", prov)
    if code != 200:
        raise SystemExit(f"kernel refused ({code}): {r.get('reason') or r}")
    lab = f"{LAB_SKILLS}/{a.name}"
    backup = (rec or {}).get("backup", "")
    r1 = wsl(["sh", "-c", f'rm -rf "{lab}" && if [ -n "{backup}" ] && [ -d "{backup}" ]; then mv "{backup}" "{lab}"; fi; ls "{LAB_SKILLS}" 2>/dev/null'])
    st["lab"].pop(a.name, None)
    st["log"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "op": "deactivate", "skill": a.name, "restored_previous": bool(backup)})
    save_active(st)
    print(f"rolled back: {a.name}; lab jarvis-staged now holds: {r1.stdout.decode().split() or 'nothing'}")
    return 0


def cmd_status(_a):
    st = load_active()
    print(json.dumps(st["lab"], indent=1))
    r = wsl(["sh", "-c", f'ls "{LAB_SKILLS}" 2>/dev/null'])
    print("in the lab:", r.stdout.decode().split())


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):      # lab output carries emoji; a Windows console must not crash on it
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(prog="skillgate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    s = sub.add_parser("stage"); s.add_argument("rel")
    for c in ("guard", "deactivate"):
        sub.add_parser(c).add_argument("name")
    ac = sub.add_parser("activate"); ac.add_argument("name"); ac.add_argument("--approval-id"); ac.add_argument("--no-smoke", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    return {"scan": cmd_scan, "stage": cmd_stage, "guard": cmd_guard, "activate": cmd_activate, "deactivate": cmd_deactivate,
            "status": cmd_status}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
