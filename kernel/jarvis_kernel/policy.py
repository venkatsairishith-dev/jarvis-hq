"""Deterministic permission policy. No model is consulted here.

Levels are ordered. The final level is max(rule floor, overlays, model suggestion),
so a model can raise a level but never lower it. Unknown actions fail closed (RED).
"""
import fnmatch
import hashlib
import json
from dataclasses import dataclass, field

GREEN, YELLOW_AUTO, YELLOW_APPROVAL, RED, DENY = range(5)
NAMES = ["GREEN", "YELLOW_AUTO", "YELLOW_APPROVAL", "RED", "DENY"]

# (tool glob, op glob, level). First match wins. Anything unmatched is RED.
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
EGRESS_TOOLS = {"egress", "message", "publish", "browser_post", "http_post"}
SECRET_MARKERS = ("jarvis-secrets", ".jarvis/", "secrets.env", ".pem", "id_ed25519", "id_rsa", "keys.json")


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
            [self.tool, self.op, self.target, self.params, self.lane], sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(blob.encode()).hexdigest()


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


def _norm(s: str) -> str:
    return s.replace("\\", "/").lower()


def _rule_level(a: Action):
    for tool, op, level in RULES:
        if fnmatch.fnmatch(a.tool, tool) and any(fnmatch.fnmatch(a.op, o) for o in op.split("|")):
            return level, f"rule {tool}.{op}"
    return RED, "no rule: fail closed"


def decide(a: Action, suggested: int | None = None, protected: set | None = None) -> Decision:
    """Return the final decision. `suggested` is a model's opinion: it can only raise the level."""
    level, reason = _rule_level(a)
    hay = _norm(a.target) + " " + _norm(json.dumps(a.params, default=str))
    if any(m in hay for m in SECRET_MARKERS):
        level, reason = DENY, "touches a secrets path"
    if protected and any(p and p in a.target + json.dumps(a.params, default=str) for p in protected):
        level, reason = DENY, "touches a protected resource"
    if a.untrusted_session and a.tool in EGRESS_TOOLS:
        level, reason = DENY, "egress removed after untrusted content"
    if a.lane not in ("private", "public"):
        level, reason = DENY, "unknown lane"
    if suggested is not None and suggested > level:
        level, reason = suggested, reason + " (raised by model)"
    return Decision(level, reason)
