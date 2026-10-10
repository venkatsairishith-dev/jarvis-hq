"""Secret scrubber for anything headed to a model, the vault or Telegram. Private keys block the send; tokens are redacted.

Two passes:
1. a small built-in regex pass (always on, cheap; used everywhere, including audit entries);
2. an optional "deep" pass that runs gitleaks (MIT, https://github.com/gitleaks/gitleaks), a maintained scanner with far
   more rules. It is used on the outbound paths (model calls, vault writes, Telegram replies). If the gitleaks binary is
   not installed the deep pass is skipped and the built-in pass still applies.
Findings contain rule names only, never secret values.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

BLOCK = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
REDACT = [
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("oci_ocid", re.compile(r"\bocid1\.[a-z0-9]+\.oc\d\.[a-z0-9-]*\.[a-z0-9]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("key_assignment", re.compile(r"(?i)\b([A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)\s*[=:]\s*['\"]?[^\s'\"]{8,}")),
]

_GL = {"path": None, "checked": False}


def gitleaks_path():
    """Find the gitleaks binary: $GITLEAKS_BIN, the tools folders used on this project's machines, then PATH."""
    if not _GL["checked"]:
        cands = [os.environ.get("GITLEAKS_BIN"), r"D:\jarvis\tools\gitleaks\bin\gitleaks.exe",
                 os.path.expanduser("~/jarvis/tools/gitleaks/bin/gitleaks"), shutil.which("gitleaks")]
        _GL["path"] = next((c for c in cands if c and os.path.isfile(c)), None)
        _GL["checked"] = True
    return _GL["path"]


def _gitleaks_secrets(text: str):
    """Return [(rule_id, secret_string)] found by gitleaks, or None if it is unavailable or failed."""
    exe = gitleaks_path()
    if not exe or not text.strip():
        return None
    try:
        with tempfile.TemporaryDirectory() as d:
            rep = os.path.join(d, "r.json")
            subprocess.run([exe, "stdin", "--no-banner", "--report-format", "json", "--report-path", rep, "--exit-code", "0"],
                           input=text, capture_output=True, text=True, encoding="utf-8", timeout=20,
                           creationflags=0x08000000 if os.name == "nt" else 0)   # no console window on Windows
            with open(rep, encoding="utf-8") as fh:
                found = json.load(fh) or []
        return [(f.get("RuleID", "gitleaks"), f.get("Secret") or f.get("Match") or "") for f in found]
    except Exception:
        return None


def scrub(text: str, deep: bool = False):
    """Return (clean_text, findings, blocked). findings is a list of kinds, never the values."""
    findings = [k for k, rx in BLOCK if rx.search(text)]
    if findings:
        return "", findings, True
    for kind, rx in REDACT:
        if kind == "key_assignment":
            text, n = rx.subn(lambda m: f"{m.group(1)}=[REDACTED]", text)
        else:
            text, n = rx.subn("[REDACTED]", text)
        if n:
            findings.append(kind)
    if deep:
        for rule, secret in (_gitleaks_secrets(text) or []):
            if len(secret) >= 6 and secret in text:
                text = text.replace(secret, "[REDACTED]")
                findings.append("gitleaks:" + rule)
    return text, findings, False
