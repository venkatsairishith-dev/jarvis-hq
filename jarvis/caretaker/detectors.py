"""Caretaker detectors: deterministic checks over the tools JARVIS is built from. Standard library only, no model calls.

Each detector returns (findings, notes). A finding is
    {id, tool (registry name), kind, severity, evidence, suggested_action}
plus, where it applies, "untrusted" (text copied from outside: release notes, advisory summaries; it is data and is only
ever quoted, never followed) and "owner_only" (true when a pull request cannot fix it: a tampered file on a machine, a
leaked secret, an upstream asset that changed under the same tag). The id is a stable hash of the tool, the kind and the
key evidence, so the same issue has the same id on every run (a pin that is behind upstream is one issue until the pin
changes, however many releases follow).

Kinds:
  upstream_release  lock entries pinned to a GitHub release / repo tag, a Hugging Face revision or a Node.js release,
                    compared with upstream metadata (GitHub and HF APIs, nodejs.org/dist/index.json)
  advisory          OSV (api.osv.dev querybatch) for the PyPI packages of the uv.lock groups jarvis / jarvis-dev (with
                    their dependencies), the registry's pip tools, and the npm lock of jarvis/ui
  lock_drift        jarvis/tools/fetch.py's own check (imported, not shelled out) for one machine and data root
  test_failure      fail and error cases of the newest FULL benchmark v2 run (results/bench-*.json)
  secret_scan       gitleaks over JARVIS's own code (jarvis/, jarvis/plugins/), when the binary is present (secrets are never read back:
                    only rule, file and line are kept)

Network: only HTTPS (or plain http to this machine, for tests), with timeouts, a per-host rate limit and a response
cache. Nothing private is sent: OSV gets package names and versions, GitHub and Hugging Face get the public repo names
already in the lock. The HTTP endpoints are injected, so tests run against a fake server on 127.0.0.1.

Custom code, justified in docs/01_RESEARCH_AUDIT.md: Dependabot/Renovate do not know the lock's Hugging Face revisions
and GitHub release asset digests and would need GitHub Actions (off on jarvis-hq); osv-scanner covers only the
advisory part and is one more binary to pin. These checks reuse fetch.py and the kernel's task path instead.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "jarvis" / "tools"
BENCH_RESULTS = REPO / "jarvis" / "evals" / "quality95" / "results"
SEVERITIES = ("info", "low", "medium", "high", "critical")
KINDS = ("upstream_release", "advisory", "lock_drift", "test_failure", "secret_scan")
UNTRUSTED_MAX = 1200                 # characters of outside text kept per source
DEFAULT_ENDPOINTS = {"github": "https://api.github.com", "hf": "https://huggingface.co", "osv": "https://api.osv.dev",
                     "nodejs": "https://nodejs.org"}
UI_TOOL_PREFIX = "JARVIS UI stack"
# Registry wording -> PyPI name, where the registry's name for a pip tool is not the package name itself.
PIP_ALIASES = {"dbos-transact-py": "dbos", "presidio": "presidio-analyzer", "pytorch": "torch", "piper": "piper-tts",
               "openwakeword": "openwakeword", "beautifulsoup4": "beautifulsoup4", "oci": "oci-cli"}


def sev_rank(s: str) -> int:
    return SEVERITIES.index(s) if s in SEVERITIES else 0


def finding_id(tool: str, kind: str, key) -> str:
    blob = json.dumps([tool, kind, key], sort_keys=True, separators=(",", ":"), default=str)
    return "cf-" + hashlib.sha256(blob.encode()).hexdigest()[:20]


def make_finding(tool, kind, severity, key, evidence, suggested_action, untrusted=None, owner_only=False) -> dict:
    """key: the part of the evidence that identifies the issue (stable across runs); evidence may hold more."""
    f = {"id": finding_id(tool, kind, key), "tool": tool, "kind": kind, "severity": severity, "evidence": evidence,
         "suggested_action": suggested_action}
    if untrusted:
        f["untrusted"] = [{"source": str(u["source"])[:80], "text": str(u["text"])[:UNTRUSTED_MAX]} for u in untrusted if u.get("text")]
    if owner_only:
        f["owner_only"] = True
    return f


def _load_fetch():
    spec = importlib.util.spec_from_file_location("jarvis_fetch_caretaker", TOOLS / "fetch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- HTTP: injected endpoints, timeouts, rate limit, cache
class Offline(Exception):
    pass


class Http:
    """JSON over HTTPS with a timeout, a minimum interval per host, one retry on 429/5xx and an on-disk cache (TTL).

    Only https, or http to a loopback address (the tests' fake servers). Redirects are re-checked the same way."""

    def __init__(self, cache_path=None, ttl=6 * 3600, timeout=20, intervals=None, offline=False, max_bytes=16 << 20,
                 clock=time.time, sleep=time.sleep):
        self.cache_path, self.ttl, self.timeout, self.offline, self.max_bytes = cache_path, ttl, timeout, offline, max_bytes
        self.intervals = {"api.github.com": 1.0, "default": 0.3, **(intervals or {})}
        self.clock, self.sleep = clock, sleep
        self.last = {}
        self.calls = 0                      # real network requests (cache hits are not counted)
        self.cache = {}
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    self.cache = json.load(f)
            except (OSError, ValueError):
                self.cache = {}
        fetch = _load_fetch()
        self._check_url = fetch.check_url
        self._opener = urllib.request.build_opener(fetch._SafeRedirect)

    def save(self):
        if not self.cache_path:
            return
        now = self.clock()
        keep = {k: v for k, v in self.cache.items() if now - v.get("t", 0) < self.ttl}
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(keep, f)
        os.replace(tmp, self.cache_path)

    def get_json(self, url):
        return self._req("GET", url, None)

    def post_json(self, url, body):
        return self._req("POST", url, body)

    def _req(self, method, url, body):
        data = json.dumps(body, sort_keys=True).encode() if body is not None else None
        key = method + " " + url + ("" if data is None else " " + hashlib.sha256(data).hexdigest())
        hit = self.cache.get(key)
        if hit and self.clock() - hit.get("t", 0) < self.ttl:
            return 200, hit["v"]
        if self.offline:
            raise Offline(url)
        self._check_url(url)
        host = urllib.parse.urlsplit(url).hostname or ""
        for attempt in (0, 1):
            gap = self.intervals.get(host, self.intervals["default"])
            wait = self.last.get(host, 0) + gap - self.clock()
            if wait > 0:
                self.sleep(wait)
            self.last[host] = self.clock()
            self.calls += 1
            headers = {"User-Agent": "jarvis-caretaker/1", "Accept": "application/json"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with self._opener.open(req, timeout=self.timeout) as r:
                    raw = r.read(self.max_bytes + 1)
                    if len(raw) > self.max_bytes:
                        return 0, {"error": "response too large"}
                    v = json.loads(raw or b"null")
                    self.cache[key] = {"t": self.clock(), "v": v}
                    return 200, v
            except urllib.error.HTTPError as e:
                if e.code in (429, 502, 503, 504) and attempt == 0:
                    try:
                        ra = float(e.headers.get("Retry-After") or 2)
                    except ValueError:
                        ra = 2
                    self.sleep(min(max(ra, 0.5), 10))
                    continue
                try:
                    v = json.loads(e.read(65536) or b"{}")
                except ValueError:
                    v = {}
                return e.code, v if isinstance(v, dict) else {}
            except (urllib.error.URLError, OSError, ValueError) as e:
                return 0, {"error": type(e).__name__}
        return 0, {"error": "retry failed"}


def _ver_tuple(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


def _clip(s, n=UNTRUSTED_MAX):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + " ...[cut]"


# ---------------------------------------------------------------- upstream_release
def detect_upstream_release(lock: dict, http: Http, endpoints=None):
    ep = {**DEFAULT_ENDPOINTS, **(endpoints or {})}
    findings, notes, groups = [], [], {}
    for e in lock.get("entries", []):
        rv = e.get("revision") or {}
        src, tool = rv.get("source"), e.get("registry_name") or e.get("name")
        if src in ("github-release", "github-repo") and rv.get("repo") and rv.get("tag"):
            groups.setdefault(("gh", tool, rv["repo"], rv["tag"]), []).append(e)
        elif src == "huggingface" and rv.get("repo") and rv.get("commit"):
            groups.setdefault(("hf", tool, rv["repo"], rv["commit"]), []).append(e)
        elif src == "nodejs-dist" and rv.get("version"):
            groups.setdefault(("node", tool, "nodejs/node", rv["version"]), []).append(e)
        else:
            notes.append(f"upstream_release: {e.get('name')}: no metadata API for source {src!r}; not covered")
    for (kind, tool, repo, pin), entries in sorted(groups.items()):
        try:
            if kind == "gh":
                f, n = _gh(tool, repo, pin, entries, http, ep["github"])
            elif kind == "hf":
                f, n = _hf(tool, repo, pin, entries, http, ep["hf"])
            else:
                f, n = _node(tool, pin, entries, http, ep["nodejs"])
        except Offline:
            f, n = [], [f"upstream_release: {repo}: skipped (offline)"]
        findings += f
        notes += n
    return findings, notes


def _gh(tool, repo, tag, entries, http, base):
    names = sorted(e["name"] for e in entries)
    code, pinned = http.get_json(f"{base}/repos/{repo}/releases/tags/{urllib.parse.quote(tag)}")
    if code == 404:
        if entries[0]["revision"].get("source") == "github-repo":
            pinned = {}                       # a plain tag may have no release object; compare tags below
        else:
            return [make_finding(tool, "upstream_release", "high", {"repo": repo, "tag": tag, "problem": "release gone"},
                                 {"repo": repo, "pinned_tag": tag, "lock_entries": names, "problem": "the pinned release no longer exists upstream"},
                                 "Tell the owner: the pinned version was removed upstream. Keep the local copy; do not re-pin blindly.",
                                 owner_only=True)], []
    elif code != 200:
        return [], [f"upstream_release: {repo}: GitHub answered {code} for tag {tag}; not checked"]
    findings = []
    assets = {a.get("name"): a for a in (pinned or {}).get("assets") or []}
    for e in entries:
        an = e["revision"].get("asset")
        if not an or not assets:
            continue
        a = assets.get(an)
        if a is None:
            findings.append(make_finding(tool, "upstream_release", "high", {"entry": e["name"], "tag": tag, "problem": "asset gone"},
                                         {"repo": repo, "pinned_tag": tag, "asset": an, "problem": "the pinned asset is no longer in the release"},
                                         "Tell the owner: the pinned asset was removed upstream. Keep the local copy.", owner_only=True))
            continue
        dig = str(a.get("digest") or "")
        if dig.startswith("sha256:") and e.get("sha256") and dig[7:] != e["sha256"]:
            findings.append(make_finding(tool, "upstream_release", "high", {"entry": e["name"], "tag": tag, "upstream": dig[7:]},
                                         {"repo": repo, "pinned_tag": tag, "asset": an, "lock_sha256": e["sha256"], "upstream_sha256": dig[7:],
                                          "problem": "the asset changed upstream under the same tag"},
                                         "Tell the owner: an asset was replaced under the same tag (possible supply-chain change). "
                                         "Do not re-pin it; keep the verified local copy.", owner_only=True))
    if not re.search(r"\d", tag):
        return findings, [f"upstream_release: {repo}: tag {tag!r} is not a version; only its asset digests were checked"]
    # Newest release whose tag follows the pinned tag's scheme (b11669 -> b<digits>, v1.0.152 -> v<d>.<d>.<d>): a repo's
    # "latest" can be another product line (llama.cpp marked a v0.7.0 release latest while its builds are b-tags).
    scheme = re.compile("^" + re.sub(r"\d+", lambda m: r"\d+", re.escape(tag)) + "$")
    code, rels = http.get_json(f"{base}/repos/{repo}/releases?per_page=50")
    if code != 200 or not isinstance(rels, list):
        return findings, [f"upstream_release: {repo}: release list answered {code}; not checked"]
    pre_ok = bool((pinned or {}).get("prerelease"))       # llama.cpp marks its b-builds prerelease: then they count
    same = [r for r in rels if isinstance(r, dict) and not r.get("draft") and (pre_ok or not r.get("prerelease"))
            and scheme.match(str(r.get("tag_name") or ""))]
    if not same:
        return findings, [f"upstream_release: {repo}: no recent release follows the pinned tag scheme of {tag!r}; not compared"]
    latest = max(same, key=lambda r: _ver_tuple(r["tag_name"]))
    lt = str(latest["tag_name"])
    if _ver_tuple(lt) > _ver_tuple(tag):
        findings.append(make_finding(
            tool, "upstream_release", "medium", {"repo": repo, "pinned": tag, "behind": True},     # one issue per stale pin
            {"source": "github", "repo": repo, "pinned_tag": tag, "latest_tag": lt, "latest_published": latest.get("published_at"),
             "latest_url": latest.get("html_url"), "lock_entries": names},
            f"Update {tool} from {tag} to {lt}: the lock entries (url, tag, revision, sha256 and bytes from "
            "the maintainers' asset digests) and the registry's pinned_version and pin locations. Hashes come only from "
            "upstream metadata; nothing is run.",
            untrusted=[{"source": f"GitHub release notes {repo} {lt}", "text": _clip(f"{latest.get('name') or ''}\n{latest.get('body') or ''}")}]))
    return findings, []


def _hf(tool, repo, commit, entries, http, base):
    kind = "datasets" if any("/datasets/" in str(e.get("url")) for e in entries) else "models"
    code, info = http.get_json(f"{base}/api/{kind}/{repo}/revision/main?blobs=true")
    if code in (401, 403):
        return [], [f"upstream_release: {repo}: gated or private on Hugging Face ({code}); not checked"]
    if code == 404:
        return [make_finding(tool, "upstream_release", "medium", {"repo": repo, "problem": "repo gone"},
                             {"repo": repo, "pinned_commit": commit, "problem": "the Hugging Face repo is no longer reachable"},
                             "Tell the owner: the upstream repo is gone or renamed. Keep the local copy.", owner_only=True)], []
    if code != 200 or not isinstance(info, dict):
        return [], [f"upstream_release: {repo}: Hugging Face answered {code}; not checked"]
    main = str(info.get("sha") or "")
    if not main or main == commit:
        return [], []
    sib = {s.get("rfilename"): s for s in info.get("siblings") or [] if isinstance(s, dict)}
    files, changed = [], []
    for e in sorted(entries, key=lambda x: x["name"]):
        path = e["revision"].get("path")
        s = sib.get(path)
        lfs = (s or {}).get("lfs") or {}
        up = lfs.get("sha256") or lfs.get("oid")
        if s is None:
            st = "missing at main"
        elif up and e.get("sha256"):
            st = "same" if up == e["sha256"] else "changed"
        elif not up and e.get("git_blob_sha1") and s.get("blobId"):
            st = "same" if s["blobId"] == e["git_blob_sha1"] else "changed"
        else:
            st = "unknown"
        files.append({"entry": e["name"], "path": path, "status": st, **({"main_sha256": up} if up and st == "changed" else {})})
        if st in ("changed", "missing at main"):
            changed.append({"path": path, "status": st, "main": up or (s or {}).get("blobId")})
    ev = {"source": "huggingface", "repo": repo, "pinned_commit": commit, "main_commit": main, "files": files}
    if changed:
        return [make_finding(tool, "upstream_release", "medium", {"repo": repo, "pinned": commit, "changed": sorted(c["path"] for c in changed)}, ev,
                             f"Re-pin {repo}: the pinned file(s) changed or moved on upstream. Update the revision, url, "
                             "sha256 and bytes in jarvis/tools/jarvis.lock.json from the Hugging Face API only, and the registry "
                             "pin locations; keep the old values in the PR body. Only adopt the new weights if the mapped tests "
                             "and bench areas show no regression.")], []
    return [make_finding(tool, "upstream_release", "info", {"repo": repo, "pinned": commit, "files": "unchanged"}, ev,
                         "Nothing to do: the repo moved on but the pinned files are byte-identical upstream.")], []


def _node(tool, version, entries, http, base):
    code, idx = http.get_json(f"{base}/dist/index.json")
    if code != 200 or not isinstance(idx, list):
        return [], [f"upstream_release: node: index answered {code}; not checked"]
    major = _ver_tuple(version)[:1]
    newer = [r for r in idx if isinstance(r, dict) and _ver_tuple(r.get("version"))[:1] == major
             and _ver_tuple(r.get("version")) > _ver_tuple(version)]
    if not newer:
        return [], []
    top = max(newer, key=lambda r: _ver_tuple(r["version"]))
    sec = [r["version"] for r in newer if r.get("security")]
    return [make_finding(tool, "upstream_release", "medium" if sec else "low", {"pinned": version, "behind": True, "security": bool(sec)},
                         {"source": "nodejs-dist", "pinned": version, "latest_same_major": top["version"], "security_releases_since": sec[:10],
                          "lock_entries": sorted(e["name"] for e in entries)},
                         f"Update Node.js from {version} to {top['version']} (same major): the lock entry (url, version, sha256 "
                         "from SHASUMS256.txt, bytes) and the places the registry lists.")], []


# ---------------------------------------------------------------- advisory (OSV)
def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def _uv_index(repo: Path):
    """The packages of a uv.lock at the repository root, when there is one (the repository has none since 2026-10-23: the pins are
    the jarvis/tools/requirements files)."""
    try:
        d = tomllib.loads((repo / "uv.lock").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    by = {}
    for p in d.get("package", []):
        by.setdefault(_norm(p["name"]), []).append(p)
    return by


def _closure(by: dict, roots):
    """(name, version) of the roots and everything they depend on, by uv.lock's dependency lists."""
    seen, out, todo = set(), set(), [(r, None) for r in roots]
    while todo:
        name, ver = todo.pop()
        name = _norm(name)
        for p in by.get(name, []):
            if ver is not None and p.get("version") != ver:
                continue
            k = (name, p.get("version"))
            if k in seen:
                continue
            seen.add(k)
            if (p.get("source") or {}).get("registry"):
                out.add(k)
            for dep in p.get("dependencies") or []:
                todo.append((dep["name"], dep.get("version")))
    return out


def _req_pins(repo: Path):
    pins = {}
    for f in sorted((repo / "jarvis" / "tools" / "requirements").glob("*.txt")):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.+\-]+)", line)
            if m:
                pins.setdefault(_norm(m.group(1)), set()).add(m.group(2))
    return pins


def pypi_packages(repo: Path, registry: list):
    """[(name, version, registry tool)] for the jarvis / jarvis-dev groups (with dependencies) and the registry's pip tools."""
    by = _uv_index(repo)
    root = next((p for ps in by.values() for p in ps if (p.get("source") or {}).get("editable") == "." or (p.get("source") or {}).get("virtual") == "."), None)
    groups = (root or {}).get("dev-dependencies") or {}
    pip_tools = [r for r in registry if str(r.get("integration", "")).lower().startswith("pip")]
    req = _req_pins(repo)
    known = set(by) | set(req)
    tool_of, notes = {}, []
    for r in pip_tools:
        words = {_norm(w) for w in re.split(r"[^A-Za-z0-9_.\-]+", r["name"]) if w}
        names = {PIP_ALIASES.get(w, w) for w in words} & known
        if not names:
            notes.append(f"advisory: registry pip tool {r['name']!r}: no PyPI package found in the requirements files; not covered")
        for n in names:
            tool_of.setdefault(n, r["name"])
    out = {}
    for g in ("jarvis", "jarvis-dev"):
        for dep in groups.get(g, []):
            d = _norm(dep["name"])
            via = tool_of.get(d) or f"uv group {g} ({d})"     # a dependency is reported under the tool that pulls it in
            for k in _closure(by, [d]):
                out.setdefault(k, tool_of.get(k[0], via))
    for n, tool in tool_of.items():
        for p in by.get(n, []):
            if (p.get("source") or {}).get("registry"):
                out.setdefault((n, p["version"]), tool)
        for v in req.get(n, ()):
            out.setdefault((n, v), tool)
    for n, versions in req.items():                        # every pinned requirement is checked, under its registry tool when it has one
        for v in versions:
            out.setdefault((n, v), tool_of.get(n, f"requirements pin ({n})"))
    return sorted((n, v, t) for (n, v), t in out.items()), notes


def npm_packages(repo: Path, registry: list):
    path = repo / "jarvis" / "ui" / "package-lock.json"
    if not path.exists():
        return [], ["advisory: jarvis/ui/package-lock.json not found; npm not checked"]
    tool = next((r["name"] for r in registry if r["name"].startswith(UI_TOOL_PREFIX)), "JARVIS UI (npm)")
    lock = json.loads(path.read_text(encoding="utf-8"))
    out = set()
    for key, p in (lock.get("packages") or {}).items():
        if not key or not isinstance(p, dict) or not p.get("version") or p.get("link"):
            continue
        name = p.get("name") or key.rsplit("node_modules/", 1)[-1]
        out.add((name, p["version"], tool))
    return sorted(out), []


OSV_SEV = {"CRITICAL": "critical", "HIGH": "high", "MODERATE": "medium", "MEDIUM": "medium", "LOW": "low"}


def detect_advisory(repo: Path, registry: list, http: Http, endpoints=None, batch=500):
    ep = {**DEFAULT_ENDPOINTS, **(endpoints or {})}
    pypi, n1 = pypi_packages(repo, registry)
    npm, n2 = npm_packages(repo, registry)
    pkgs = [("PyPI", n, v, t) for n, v, t in pypi] + [("npm", n, v, t) for n, v, t in npm]
    notes = n1 + n2 + [f"advisory: {len(pypi)} PyPI and {len(npm)} npm package versions sent to OSV (names and versions only)"]
    hits = {}
    try:
        for i in range(0, len(pkgs), batch):
            chunk = pkgs[i:i + batch]
            q = {"queries": [{"package": {"name": n, "ecosystem": eco}, "version": v} for eco, n, v, _ in chunk]}
            code, r = http.post_json(f"{ep['osv']}/v1/querybatch", q)
            if code != 200:
                notes.append(f"advisory: OSV querybatch answered {code}; {len(chunk)} packages not checked")
                continue
            for pk, res in zip(chunk, (r or {}).get("results") or []):
                ids = sorted({v["id"] for v in (res or {}).get("vulns") or [] if isinstance(v, dict) and v.get("id")})
                if (res or {}).get("next_page_token"):
                    notes.append(f"advisory: {pk[1]} {pk[2]}: more advisories than one OSV page; only the first page used")
                if ids:
                    hits[pk] = ids
        findings, details = [], {}
        for (eco, name, ver, tool), ids in sorted(hits.items()):
            advs, top = [], "info"
            for vid in ids:
                if vid not in details:
                    c, d = http.get_json(f"{ep['osv']}/v1/vulns/{urllib.parse.quote(vid)}")
                    details[vid] = d if c == 200 and isinstance(d, dict) else {"id": vid}
                d = details[vid]
                if d.get("withdrawn"):
                    continue
                sev = OSV_SEV.get(str((d.get("database_specific") or {}).get("severity", "")).upper(), "medium")
                fixed = sorted({ev["fixed"] for a in d.get("affected") or [] if _norm((a.get("package") or {}).get("name", "")) == _norm(name)
                                for rg in a.get("ranges") or [] for ev in rg.get("events") or [] if ev.get("fixed")}, key=_ver_tuple)
                advs.append({"id": vid, "aliases": sorted(d.get("aliases") or [])[:5], "severity": sev, "fixed": fixed[:5],
                             "_summary": d.get("summary") or ""})
                top = sev if sev_rank(sev) > sev_rank(top) else top
            if not advs:
                continue
            fix = sorted({f for a in advs for f in a["fixed"] if _ver_tuple(f) > _ver_tuple(ver)}, key=_ver_tuple)
            # the version that fixes every advisory with a known fix: the highest of each advisory's lowest fix above ours
            per = [min((x for x in a["fixed"] if _ver_tuple(x) > _ver_tuple(ver)), key=_ver_tuple, default=None) for a in advs]
            target = max((x for x in per if x), key=_ver_tuple, default=None)
            unfixed = [a["id"] for a, x in zip(advs, per) if x is None]
            where = ("jarvis/ui/package.json and package-lock.json" if eco == "npm"
                     else "the jarvis/tools/requirements files (wherever it is pinned)")
            findings.append(make_finding(
                tool, "advisory", top, {"ecosystem": eco, "package": name, "version": ver, "advisories": [a["id"] for a in advs]},
                {"ecosystem": eco, "package": name, "version": ver, "used_by": tool,
                 "advisories": [{k: v for k, v in a.items() if not k.startswith("_")} for a in advs], "fixed_versions": fix[:5],
                 "fixes_all_known": target, "no_fix_known": unfixed},
                f"Upgrade package:{name} from {ver} to {target or 'a fixed version (see the advisories)'}, the lowest version that fixes every "
                f"advisory with a known fix, in {where}; or state why the vulnerable code path is not used"
                + (f" (no fix is known yet for {', '.join(unfixed[:3])})" if unfixed else "") + ". Do not upgrade unrelated packages.",
                untrusted=[{"source": f"OSV advisory summaries for {name} {ver}",
                            "text": _clip("\n".join(f"{a['id']}: {a['_summary']}" for a in advs))}]))
        return findings, notes
    except Offline:
        return [], notes + ["advisory: skipped (offline)"]


# ---------------------------------------------------------------- lock_drift (fetch.py's own check)
def detect_lock_drift(machine, root, lock_path=None):
    fetch = _load_fetch()
    lock = fetch.load_lock(str(lock_path or fetch.LOCK))
    findings, counts = [], {"ok": 0, "missing": 0, "MISMATCH": 0, "unpinned": 0}
    for e in fetch.select(lock, [], machine):
        path = os.path.join(root, fetch.safe_dest(e))
        if os.path.isfile(path):
            status, detail = fetch.verify(path, e)
        else:
            status, detail = "missing", "optional" if e.get("optional") else "not present"
        counts[status] += 1
        tool = e.get("registry_name") or e["name"]
        if status == "MISMATCH":
            findings.append(make_finding(tool, "lock_drift", "high", {"machine": machine, "entry": e["name"], "detail": detail},
                                         {"machine": machine, "entry": e["name"], "dest": e["dest"], "status": status, "detail": detail},
                                         "Tell the owner: the file on this machine does not match the lock (tampered, corrupted or "
                                         "replaced). Move it aside and fetch again with jarvis/tools/fetch.py --fetch; never re-pin "
                                         "the lock to a local file.", owner_only=True))
        elif status == "missing" and not e.get("optional") and machine:
            findings.append(make_finding(tool, "lock_drift", "low", {"machine": machine, "entry": e["name"], "status": "missing"},
                                         {"machine": machine, "entry": e["name"], "dest": e["dest"], "status": status},
                                         f"Fetch it on {machine}: python jarvis/tools/fetch.py --fetch {e['name']}", owner_only=True))
    return findings, [f"lock_drift: machine {machine or 'all'}, root {root}: " + ", ".join(f"{v} {k.lower()}" for k, v in counts.items())]


# ---------------------------------------------------------------- test_failure (benchmark v2)
def detect_test_failure(results_dir=None):
    results_dir = Path(results_dir or BENCH_RESULTS)
    for path in sorted(results_dir.glob("bench-*.json"), reverse=True):     # names carry the time: newest first
        try:
            r = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sel = r.get("selection") or {}
        if not isinstance(r.get("cases"), list) or sel.get("area") or sel.get("id"):
            continue                                                     # only full runs
        findings = []
        for c in r["cases"]:
            if c.get("status") not in ("fail", "error"):
                continue
            risk = str(c.get("risk") or "medium")
            sev = {"critical": "critical", "high": "high"}.get(risk, "medium")
            area = str(c.get("area") or "?")
            findings.append(make_finding(
                f"JARVIS benchmark v2, area {area}", "test_failure", sev, {"case": c.get("id"), "status": c.get("status")},
                {"result_file": path.name, "case": c.get("id"), "area": area, "title": c.get("title"), "risk": risk,
                 "status": c.get("status"), "dims": c.get("dims")},
                f"Find why bench case {c.get('id')} {c.get('status')}s and fix the code under test. Never change the case, its "
                "expectation, the rubric or the scorer to make it pass.",
                untrusted=[{"source": f"bench observation {c.get('id')}", "text": _clip(c.get("observed"))}]))
        return findings, [f"test_failure: {path.name}: {r.get('counts')}"]
    return [], [f"test_failure: no full benchmark result in {results_dir}"]


# ---------------------------------------------------------------- secret_scan (gitleaks)
SECRET_SCAN_PATHS = ("jarvis",)    # JARVIS's own code (the plugins and the face are inside it)


def detect_secret_scan(repo: Path, gitleaks=None, timeout=900, paths=SECRET_SCAN_PATHS):
    """gitleaks dir over `paths` (relative to repo; None or () = the whole tree). The repo's .gitleaksignore applies."""
    exe = gitleaks or shutil.which("gitleaks")
    if not exe:
        return [], ["secret_scan: skipped: the gitleaks binary is not installed here"]
    rows, scanned = [], []
    for sub in (paths or (".",)):
        target = (Path(repo) / sub).resolve()
        if not target.exists():
            continue
        scanned.append(sub)
        with tempfile.TemporaryDirectory() as td:
            rep = os.path.join(td, "report.json")
            cmd = [exe, "dir", str(target), "--no-banner", "--redact", "--report-format", "json", "--report-path", rep,
                   "--exit-code", "0", "--log-level", "error", "--max-target-megabytes", "5"]
            try:
                p = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return [], [f"secret_scan: gitleaks timed out after {timeout} s"]
            if p.returncode != 0:
                return [], [f"secret_scan: gitleaks exited {p.returncode}: {p.stderr.strip()[:200]}"]
            try:
                with open(rep, encoding="utf-8") as f:
                    rows += json.load(f) or []
            except (OSError, ValueError):
                pass
    findings = []
    for r in rows:                                   # the secret itself (Secret, Match) is never read into a finding
        fn = str(r.get("File", ""))
        rel = os.path.relpath(fn, str(Path(repo).resolve())) if os.path.isabs(fn) else fn
        findings.append(make_finding("jarvis-hq repository (gitleaks scan)", "secret_scan", "high",
                                     {"file": rel, "rule": r.get("RuleID"), "line": r.get("StartLine")},
                                     {"file": rel, "rule": r.get("RuleID"), "line": r.get("StartLine")},
                                     "Owner: check this line. If it is a real secret, revoke and rotate it first, then remove it; "
                                     "if it is a reviewed fake fixture, add its fingerprint to .gitleaksignore.", owner_only=True))
    return findings, [f"secret_scan: gitleaks dir over {', '.join(scanned) or 'nothing'}: {len(rows)} finding(s)"]
