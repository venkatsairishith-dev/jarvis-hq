"""Secret scrubber for anything headed to a model or the vault. Private keys block the send; tokens are redacted."""
import re

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


def scrub(text: str):
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
    return text, findings, False
