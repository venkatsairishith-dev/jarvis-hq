"""Deterministic permission policy. No model is consulted here.

Levels are ordered. The final level is max(rule floor, overlays, model suggestion),
so a model can raise a level but never lower it. Unknown actions fail closed (RED).
All names and paths are normalized before matching.
"""
import fnmatch
import hashlib
import json
import os
import unicodedata
import urllib.parse
from dataclasses import dataclass, field

GREEN, YELLOW_AUTO, YELLOW_APPROVAL, RED, DENY = range(5)
NAMES = ["GREEN", "YELLOW_AUTO", "YELLOW_APPROVAL", "RED", "DENY"]

# (tool, op patterns separated by |, level). First match wins. Anything unmatched is RED.
RULES = [
    ("fs", "read", GREEN),
    ("fs", "list", GREEN),
    ("state", "*", GREEN),
    ("git", "status|log|diff|branch_create", GREEN),
    ("tests", "run", GREEN),
    ("docs", "read", GREEN),
    ("pkg", "install_pinned_sandbox", YELLOW_AUTO),
    ("pkg", "install", YELLOW_APPROVAL),
    ("config", "write", YELLOW_APPROVAL),
    ("service", "restart_noncritical", YELLOW_APPROVAL),
    ("message", "send_routine", YELLOW_APPROVAL),
    ("fs", "write", YELLOW_APPROVAL),
    ("money", "*", RED),
    ("delete", "*", RED),
    ("credential", "*", RED),
    ("publish", "*", RED),
    ("egress", "*", RED),
    ("oci", "*", RED),
]

# Tools that send data out. Removed once a session has read untrusted content.
EGRESS_TOOLS = {"egress", "message", "publish", "browser_post", "http_post", "email", "upload"}
SECRET_COMPONENTS = {".jarvis", ".jarvis-kernel", "secrets.env", "keys.json", "protected.json", "id_ed25519", "id_rsa"}


def _name(s) -> str:
    return unicodedata.normalize("NFKC", str(s)).strip().lower()


def _leaves(x, out, keys=True):
    if isinstance(x, dict):
        for k, v in x.items():
            if keys:
                _leaves(k, out, keys)
            _leaves(v, out, keys)
    elif isinstance(x, (list, tuple, set)):
        for v in x:
            _leaves(v, out, keys)
    elif x is not None:
        out.append(str(x))
    return out


def _canon(s: str) -> list:
    """Return the normalized forms of one string: decoded, NFKC, forward slashes, lowercased, and (for
    path-like strings) the real path with '..', symlinks, junctions and 8.3 short names resolved."""
    forms = []
    t = str(s)
    for _ in range(2):  # decode up to two layers of %-encoding
        t = urllib.parse.unquote(t)
    t = unicodedata.normalize("NFKC", t).strip().replace("\\", "/")
    forms.append(t.lower())
    if "://" not in t and ("/" in t or t.startswith("~") or (len(t) > 1 and t[1] == ":")):
        try:
            forms.append(os.path.realpath(os.path.expanduser(t)).replace("\\", "/").lower())
        except (OSError, ValueError):
            pass
    return forms


def _is_secret_path(forms: list) -> bool:
    for f in forms:
        if "jarvis-secrets" in f:
            return True
        for comp in f.split("/"):
            if comp in SECRET_COMPONENTS or comp.endswith(".pem") or "jarvis-secrets" in comp:
                return True
    return False


@dataclass
class Action:
    tool: str
    op: str
    target: str = ""
    params: dict = field(default_factory=dict)
    lane: str = "private"
    untrusted_session: bool = False  # session already read web/files from outside

    def digest(self) -> str:
        blob = json.dumps(
            [_name(self.tool), _name(self.op), self.target, self.params, self.lane], sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def describe(self) -> dict:
        return {"tool": self.tool, "op": self.op, "target": self.target, "params": self.params, "lane": self.lane}


@dataclass
class Decision:
    level: int
    reason: str
    allowed: bool = False

    @property
    def name(self) -> str:
        return NAMES[self.level]

    @property
    def needs_approval(self) -> bool:
        return self.level in (YELLOW_APPROVAL, RED)

    @property
    def denied(self) -> bool:
        return self.level == DENY


def _rule_level(tool: str, op: str):
    for t, ops, level in RULES:
        if fnmatch.fnmatchcase(tool, t) and any(fnmatch.fnmatchcase(op, o) for o in ops.split("|")):
            return level, f"rule {t}.{ops}"
    return RED, "no rule: fail closed"


def decide(a: Action, suggested=None, protected=None) -> Decision:
    """Return the final decision. `suggested` is a model's opinion: it can only raise the level."""
    tool, op = _name(a.tool), _name(a.op)
    level, reason = _rule_level(tool, op)
    leaves = _leaves(a.target, []) + _leaves(a.params, [])
    forms = [f for lf in leaves for f in _canon(lf)]
    vals = [v for v in _leaves(a.target, [], False) + _leaves(a.params, [], False) if v]
    for group in (leaves, vals):  # catches a path split across params (with and without the key names between)
        forms += [f for j in ("", "/") for f in _canon(j.join(group))]
    if _is_secret_path(forms):
        level, reason = DENY, "touches a secrets path"
    if protected:
        flat = " ".join(forms).lower()
        if any(_name(p) and _name(p).replace("\\", "/") in flat for p in protected):
            level, reason = DENY, "touches a protected resource"
    if a.untrusted_session and tool in EGRESS_TOOLS:
        level, reason = DENY, "egress removed after untrusted content"
    if _name(a.lane) not in ("private", "public") or a.lane != _name(a.lane):
        level, reason = DENY, "unknown lane"
    if isinstance(suggested, int) and not isinstance(suggested, bool):
        suggested = max(GREEN, min(DENY, suggested))
        if suggested > level:
            level, reason = suggested, reason + " (raised by model)"
    return Decision(level, reason)
