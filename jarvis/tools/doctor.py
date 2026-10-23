"""Posture self-check, `jarvis doctor` (plan P9 step 7): one command that says whether this machine's JARVIS is set up safely.

Checks, all offline and read-only:
  listeners   every JARVIS port listens only on loopback or Tailscale; other all-interface listeners are listed for review
  manifests   the supervisor manifests parse (binds, no secret-looking env values)
  licences    the licence gate over registry.json passes
  secrets     no key-like file names or key-shaped strings in tracked config; the secrets folder is outside the repo and,
              on Linux, private to its owner (only its permissions are looked at, never its files)
  egress      config/egress.json names a proxy on loopback or Tailscale
  providers   no enabled provider sends cleartext off the machine, carries an inline key, or is paid
  skills      every staged skill still matches its recorded hashes, and every active one matches its staged copy
  docs        FILE_MAP.md and MODELS.md are current

Findings name files and settings, never values: a leaked key is reported by file, not printed.

    python jarvis/tools/doctor.py [--json]          exit code 0 = no FAIL, 1 = at least one FAIL
Runs on Windows (netstat) and Linux (/proc/net); other systems skip the listener check.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
JARVIS = ROOT / "jarvis"
if str(ROOT) not in sys.path:                     # run as a script from any folder
    sys.path.insert(0, str(ROOT))

TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
# Ports JARVIS itself listens on (jarvis/scripts/watchdog.py SERVERS, neko-start.ps1, the kernel); manifests add theirs.
JARVIS_PORTS = {48911: "N.E.K.O main", 48912: "N.E.K.O memory", 48915: "N.E.K.O agent", 48916: "plugins", 48930: "local speech",
                48940: "widget", 48945: "wake listener", 48950: "privacy gateway", 48952: "planner gateway", 8799: "JARVIS console",
                8080: "local models", 8091: "Verdict", 8790: "kernel", 8642: "Hermes API"}
SECRET_NAME = re.compile(r"(^|/)(\.env(\.(?!example$|sample$)[^/]*)?|id_(rsa|ed25519|ecdsa|dsa)|[^/]+\.(pem|key|p12|pfx|token|keystore))$", re.I)
SECRET_SHAPE = re.compile(r"\b(sk-(ant-)?[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}"
                          r"|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,})")
CONFIG_DIRS = ("jarvis/config", "jarvis/supervisor/manifests", "jarvis/privacy", "jarvis/deploy", "jarvis/local_models")
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".service", ".env", ".txt", ".ps1", ".sh"}
LEVELS = ("ok", "skip", "warn", "fail")


@dataclass
class Finding:
    check: str
    level: str          # ok | skip | warn | fail
    detail: str


# ---- listeners ------------------------------------------------------------------------------------------------------------------------

def _hex_ip(h: str) -> str:
    raw = bytes.fromhex(h)
    if len(raw) == 4:
        return str(ipaddress.IPv4Address(raw[::-1]))
    words = b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))   # /proc/net/tcp6 stores four little-endian words
    return str(ipaddress.IPv6Address(words))


def parse_proc_net(text: str) -> list[tuple[str, int]]:
    """Listening sockets from /proc/net/tcp or tcp6 (state 0A)."""
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) > 3 and parts[3] == "0A":
            ip, port = parts[1].split(":")
            out.append((_hex_ip(ip), int(port, 16)))
    return out


def parse_netstat(text: str) -> list[tuple[str, int]]:
    """Listening TCP sockets from Windows `netstat -ano` (any language: the state word is not relied on, the zero remote port is)."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "TCP":
            continue
        local, remote = parts[1], parts[2]
        if not (remote.endswith(":0") or remote.endswith("]:0")):
            continue
        host, _, port = local.rpartition(":")
        try:
            out.append((host.strip("[]").split("%")[0], int(port)))
        except ValueError:
            continue
    return out


def system_listeners() -> list[tuple[str, int]] | None:
    if sys.platform.startswith("linux"):
        found = []
        for name in ("tcp", "tcp6"):
            try:
                found += parse_proc_net(Path("/proc/net", name).read_text())
            except OSError:
                pass
        return found
    if os.name == "nt":
        try:
            text = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=30).stdout
            text += subprocess.run(["netstat", "-ano", "-p", "TCPv6"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return parse_netstat(text)
    return None


def reach(ip: str) -> str:
    """loopback | tailscale | all | lan | public"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "public"
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    if a.is_loopback:
        return "loopback"
    if a.is_unspecified:
        return "all"
    if a in (TAILSCALE_V6 if a.version == 6 else TAILSCALE_V4):
        return "tailscale"
    if a.is_private or a.is_link_local:
        return "lan"
    return "public"


def manifest_ports() -> dict[int, str]:
    ports = {}
    for f in sorted((JARVIS / "supervisor" / "manifests").glob("*.json")):
        try:
            for c in json.loads(f.read_text(encoding="utf-8")).get("components", []):
                port = (c.get("bind") or {}).get("port")
                if isinstance(port, int):
                    ports[port] = c.get("name", f.stem)
        except (OSError, ValueError, AttributeError):
            continue
    return ports


def check_listeners(listeners: list[tuple[str, int]] | None, ours: dict[int, str]) -> list[Finding]:
    if listeners is None:
        return [Finding("listeners", "skip", "cannot list sockets on this system")]
    out = []
    exposed = sorted({(p, ip, reach(ip)) for ip, p in listeners if p in ours and reach(ip) not in ("loopback", "tailscale")})
    for port, ip, r in exposed:
        where = {"all": "every interface", "lan": "the local network", "public": "a public address"}[r]
        out.append(Finding("listeners", "fail", f"{ours[port]} (port {port}) listens on {where} ({ip}); it must be loopback or Tailscale"))
    others = sorted({p for ip, p in listeners if p not in ours and reach(ip) in ("all", "lan", "public")})
    if others:
        shown = ", ".join(map(str, others[:20])) + (" ..." if len(others) > 20 else "")
        out.append(Finding("listeners", "warn", f"{len(others)} other port(s) reachable from the network: {shown}. System services often "
                                                "do this; look up any you do not recognise (netstat -ano shows the process id)"))
    running = sorted({p for _, p in listeners if p in ours})
    if not exposed:
        out.append(Finding("listeners", "ok", f"{len(running)} JARVIS port(s) listening, all on loopback or Tailscale"))
    return out


# ---- configuration --------------------------------------------------------------------------------------------------------------------

def check_manifests() -> list[Finding]:
    from jarvis.supervisor import manifest
    files = sorted((JARVIS / "supervisor" / "manifests").glob("*.json"))
    if not files:
        return [Finding("manifests", "skip", "no supervisor manifests")]
    out = []
    for f in files:
        try:
            n = len(manifest.load(f))
            out.append(Finding("manifests", "ok", f"{f.name}: {n} component(s), binds and env checked"))
        except (manifest.ManifestError, OSError, ValueError, KeyError) as e:
            out.append(Finding("manifests", "fail", f"{f.name}: {e}"))
    return out


def check_licences() -> list[Finding]:
    from jarvis.tools import licence_gate
    try:
        entries = json.loads(licence_gate.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [Finding("licences", "fail", f"registry.json unreadable ({type(e).__name__})")]
    problems = licence_gate.check_registry(entries, "personal")
    if problems:
        return [Finding("licences", "fail", p) for p in problems]
    return [Finding("licences", "ok", f"{len(entries)} registry entries, 0 violations")]


def tracked_files() -> list[str] | None:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p] if r.returncode == 0 else None


def default_secrets_dir() -> Path:
    if os.environ.get("JARVIS_SECRETS_DIR"):
        return Path(os.environ["JARVIS_SECRETS_DIR"])
    return Path(r"D:\jarvis-secrets") if os.name == "nt" else Path.home() / ".jarvis"


def check_secrets(files: list[str] | None, read: Callable[[str], str], secrets_dir: Path) -> list[Finding]:
    out = []
    if files is None:
        out.append(Finding("secrets", "skip", "not a git checkout: tracked files not scanned"))
    else:
        named = [f for f in files if SECRET_NAME.search(f) and "/tests/" not in f and not f.startswith("tests/")]
        out += [Finding("secrets", "fail", f"{f} is tracked and named like a key file; move it to the secrets folder") for f in named]
        shaped = []
        for f in files:
            if f.startswith(CONFIG_DIRS) and Path(f).suffix.lower() in CONFIG_EXT:
                try:
                    if SECRET_SHAPE.search(read(f)):
                        shaped.append(f)
                except (OSError, UnicodeDecodeError):
                    continue
        out += [Finding("secrets", "fail", f"{f} holds a key-shaped string (value not shown); replace it with a file: reference")
                for f in shaped]
        if not named and not shaped:
            out.append(Finding("secrets", "ok", "no key files or key-shaped strings in tracked config"))
    try:
        exists = secrets_dir.is_dir()
    except OSError:
        exists = False
    if not exists:
        out.append(Finding("secrets", "skip", "secrets folder not found on this machine"))
        return out
    try:
        secrets_dir.resolve().relative_to(ROOT.resolve())
        out.append(Finding("secrets", "fail", "the secrets folder is inside the repo; move it out"))
    except ValueError:
        out.append(Finding("secrets", "ok", "secrets folder is outside the repo"))
    if os.name == "posix" and secrets_dir.stat().st_mode & 0o077:
        out.append(Finding("secrets", "fail", f"secrets folder is readable by other users (chmod 700 {secrets_dir})"))
    return out


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _local(host: str) -> bool:
    return host == "localhost" or reach(host) in ("loopback", "tailscale")


def check_egress(path: Path) -> list[Finding]:
    try:
        proxy = str(json.loads(path.read_text(encoding="utf-8"))["proxy"])
    except (OSError, ValueError, KeyError):
        return [Finding("egress", "fail", f"{path.name} has no proxy: page fetches would have no checkpoint")]
    if not proxy.startswith("http://") or not _local(_host(proxy)):
        return [Finding("egress", "fail", "the egress proxy must be http:// on loopback or a Tailscale address")]
    return [Finding("egress", "ok", "egress proxy is on loopback/Tailscale")]


def check_providers(path: Path) -> list[Finding]:
    try:
        providers = json.loads(path.read_text(encoding="utf-8"))["providers"]
    except (OSError, ValueError, KeyError):
        return [Finding("providers", "fail", f"{path.name} unreadable")]
    out = []
    enabled = [p for p in providers if p.get("enabled")]
    for p in providers:
        name = p.get("name", "?")
        for k, v in p.items():
            if k != "key_file" and isinstance(v, str) and SECRET_SHAPE.search(v):      # model names are long and mixed too
                out.append(Finding("providers", "fail", f"{name}: field {k!r} looks like an inline key; use key_file"))
    for p in enabled:
        name, url = p.get("name", "?"), str(p.get("base_url") or "")
        if url.startswith("http://") and not _local(_host(url)):
            out.append(Finding("providers", "fail", f"{name}: cleartext http:// to {_host(url)}; prompts would cross the network unencrypted"))
        if p.get("paid"):
            out.append(Finding("providers", "warn", f"{name} is paid and enabled; the owner's rule is $0 unless approved"))
        if re.search(r"(ngrok|trycloudflare|loca\.lt|serveo)", _host(url)):
            out.append(Finding("providers", "warn", f"{name} goes through a public tunnel ({_host(url)}); its address changes and anyone can sit behind it"))
    if not out:
        out.append(Finding("providers", "ok", f"{len(enabled)} enabled provider(s): encrypted or local, no inline keys, none paid"))
    return out


def check_skills(skills_dir: Path) -> list[Finding]:
    staging = skills_dir / "staging"
    if not staging.is_dir():
        return [Finding("skills", "skip", "no staged skills")]
    out, manifests, rejected = [], {}, set()
    for prov_path in sorted(staging.glob("*/PROVENANCE.json")):
        name = prov_path.parent.name
        try:
            prov = json.loads(prov_path.read_text(encoding="utf-8"))
            manifests[name] = prov.get("manifest_sha256")
            if (prov.get("risk") or {}).get("tier") == "reject":
                rejected.add(name)              # only the record of why is kept; its files are deliberately absent
                continue
            bad = [f["path"] for f in prov["files"]
                   if hashlib.sha256((prov_path.parent / f["path"]).read_bytes()).hexdigest() != f["sha256"]]
        except FileNotFoundError as e:
            out.append(Finding("skills", "fail", f"{name}: a recorded file is missing ({Path(e.filename).name})"))
            continue
        except (OSError, ValueError, KeyError, TypeError) as e:
            out.append(Finding("skills", "fail", f"{name}: provenance unreadable ({type(e).__name__})"))
            continue
        if bad:
            out.append(Finding("skills", "fail", f"{name}: changed since it was checked ({', '.join(bad)}); stage it again"))
    try:
        active = json.loads((skills_dir / "active.json").read_text(encoding="utf-8")).get("lab", {})
    except (OSError, ValueError):
        active = {}
    for name, rec in active.items():
        if name in rejected:
            out.append(Finding("skills", "fail", f"{name} is active although the skill gate rejected it"))
        elif manifests.get(name) != rec.get("sha256"):
            out.append(Finding("skills", "fail", f"{name} is active but differs from its staged, checked copy"))
    if not out:
        out.append(Finding("skills", "ok", f"{len(manifests) - len(rejected)} staged skill(s) match their hashes ({len(rejected)} rejected, "
                                                f"kept as records only); {len(active)} active, all as checked"))
    return out


def check_docs() -> list[Finding]:
    try:
        r = subprocess.run([sys.executable, str(JARVIS / "tools" / "make_docs.py"), "--check"], capture_output=True, text=True,
                           timeout=120, cwd=ROOT)
    except (OSError, subprocess.SubprocessError) as e:
        return [Finding("docs", "skip", f"make_docs not run ({type(e).__name__})")]
    if r.returncode == 0:
        return [Finding("docs", "ok", "FILE_MAP.md and MODELS.md are current")]
    return [Finding("docs", "warn", "FILE_MAP.md or MODELS.md is out of date: run python jarvis/tools/make_docs.py")]


# ---- runner ---------------------------------------------------------------------------------------------------------------------------

def run_all() -> list[Finding]:
    files = tracked_files()
    checks: list[Callable[[], Iterable[Finding]]] = [
        lambda: check_listeners(system_listeners(), {**JARVIS_PORTS, **manifest_ports()}),
        check_manifests,
        check_licences,
        lambda: check_secrets(files, lambda f: (ROOT / f).read_text(encoding="utf-8"), default_secrets_dir()),
        lambda: check_egress(JARVIS / "config" / "egress.json"),
        lambda: check_providers(JARVIS / "config" / "providers.json"),
        lambda: check_skills(JARVIS / "skills"),
        check_docs,
    ]
    out: list[Finding] = []
    for check in checks:
        try:
            out += list(check())
        except Exception as e:                      # one broken check must not hide the others
            out.append(Finding(getattr(check, "__name__", "check").replace("check_", ""), "fail", f"check crashed ({type(e).__name__})"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output for the console")
    args = ap.parse_args(argv)
    findings = run_all()
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=1))
    else:
        for f in sorted(findings, key=lambda f: -LEVELS.index(f.level)):
            print(f"{f.level.upper():5} {f.check:10} {f.detail}")
        counts = {lv: sum(1 for f in findings if f.level == lv) for lv in LEVELS}
        print(f"\n{counts['fail']} fail, {counts['warn']} warn, {counts['ok']} ok, {counts['skip']} skipped")
    return 1 if any(f.level == "fail" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
