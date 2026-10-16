"""Deterministic permission policy. No model is consulted here.

Levels are ordered. The final level is max(rule floor, overlays, model suggestion),
so a model can raise a level but never lower it. Unknown actions fail closed (RED).
All names and paths are normalized before matching.
"""
import fnmatch
import hashlib
import json
import os
import re
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
    ("browser", "open|snapshot|extract", GREEN),
    ("browser", "click|type|select|press", YELLOW_APPROVAL),
    ("browser", "download|upload", RED),
    # Agent actions (owner defaults 2026-10-09): outputs go only to the laptop's outputs folder; real PC control
    # (ui.launch/click/type below) always needs the owner's approval; model switching stays inside the token's lane.
    ("output", "save|show", YELLOW_AUTO),
    ("lab", "task", YELLOW_AUTO),
    ("models", "status", GREEN),
    ("models", "prefer", YELLOW_AUTO),
    ("ui", "snapshot|list_windows", GREEN),
    ("ui", "screenshot", YELLOW_AUTO),     # the owner's own side at once; a lab agent needs approval (raised in decide below)
    ("ui", "launch|focus|click|type|key|scroll|click_at|minimize|maximize|restore", YELLOW_APPROVAL),
    # Opening things on the laptop (2026-10-10): the owner's own agent (private lane) at once; a lab agent (public
    # lane) only with the owner's approval and never a folder listing (raised in decide below).
    ("pc", "open_app|open_folder|open_file|list_folder", YELLOW_AUTO),
    # Outcome tasks (2026-10-10): agents record and update their own tasks; dispatching work to the lab is automatic
    # (the dispatch itself refuses private keys and, from the private lane, needs an explicit "may be shared").
    ("task", "create|update|get|dispatch", YELLOW_AUTO),
    ("lab", "dispatch", YELLOW_AUTO),
    # Skills (2026-10-10): a staged, checked skill; the level depends on its recorded risk and where it goes (decide below).
    # Durable memory (2026-10-11): each agent reads and changes only its own entries (owner = its token).
    ("memory", "list|get|resolve|render", GREEN),
    ("memory", "add|update|forget", YELLOW_AUTO),
    ("skill", "activate", YELLOW_AUTO),
    ("skill", "deactivate", YELLOW_AUTO),
    # Owner console (2026-10-11): automations and agent definitions are the owner's own records; running them goes
    # through the same job and lab policy as anything else, and nothing unattended may need an approval each time.
    ("automation", "create|update|delete|pause|resume|run", YELLOW_AUTO),
    ("agent_def", "create|update|delete|activate|pause|test", YELLOW_AUTO),
    # Connectors (2026-10-11): the OWNER'S risk class for the tool decides (decide below); a plain call is at most YELLOW_AUTO.
    ("connector", "list|describe|manage|result", GREEN),
    ("connector", "call", YELLOW_AUTO),
    # Neko's PC control (owner decision 2026-10-16): the owner's own requests run ordinary actions at once (owner_direct,
    # decide below); playing media in the browser is ordinary. Read-only diagnostics of the owner's PC are GREEN for the
    # owner's side. Repairs change the PC: approval, except putting a backed-up file back (the safe direction).
    ("browser", "play", YELLOW_AUTO),
    ("media", "state", GREEN),
    ("diag", "processes|app_errors|crashes|services|disk|network|app_info|window_text", GREEN),
    ("repair", "restore", YELLOW_AUTO),
    ("repair", "restart_app|edit_file|install", YELLOW_APPROVAL),
    ("money", "*", RED),
    ("delete", "*", RED),
    ("credential", "*", RED),
    ("publish", "*", RED),
    ("egress", "*", RED),
    ("oci", "*", RED),
]

# Tools that send data out. Removed once a session has read untrusted content.
EGRESS_TOOLS = {"egress", "message", "publish", "browser_post", "http_post", "email", "upload"}
SENSITIVE_APPS = re.compile(
    r"1password|bitwarden|keepass|lastpass|dashlane|password manager|credential manager|metamask|\bwallet\b|paypal|\bbank|netbanking|\bupi\b|\bbroker", re.I)
# Files that hold the rules, hooks and settings the agents run under: changing them is console-only (at least RED).
SAFETY_COMPONENTS = {"claude.md", "settings.json", "settings.local.json", ".claude", "hooks.json", "providers.json", "policy.py", "audit.py", "store.py"}
# Credential and secret stores that must never be read, copied or sent by an agent (matched on the normalized path).
SECRET_PATTERNS = re.compile(
    r"(^|/)\.env(\.[\w.-]+)?(/|$)"                                   # .env, .env.local, .env.production
    r"|(^|/)\.(aws|ssh|gnupg|kube|azure)(/|$)"                        # cloud and key directories
    r"|(^|/)\.(npmrc|pypirc|netrc|pgpass|git-credentials|s3cfg|boto|my\.cnf|envrc)$|(^|/)_netrc$"
    r"|kubeconfig|(^|/)\.git/config$|(^|/)\.docker/config\.json$"
    r"|(^|/)(secrets?|credentials?|service-account[\w-]*)\.(ya?ml|json|db|txt|csv)$"
    r"|terraform\.(tfstate|tfvars)"
    r"|logins\.json|key[34]\.db|(^|/)cookies(\.sqlite)?$|login data|web data|local state"   # browser password and cookie stores
    r"|wallet\.dat|(^|/)etc/(shadow|gshadow)|\.(p12|pfx|kdbx|jks|keystore)$|(^|/)id_(dsa|ecdsa|rsa|ed25519)(\.pub)?$"
    r"|recovery[ _-]?codes?|(^|/)authy|(^|/)\.config/gcloud(/|$)",
    re.I)
# Logs and shell history: reading them is fine, changing or deleting them is tampering (at least RED).
LOG_TAMPER = re.compile(r"(^|/)(auth\.log|syslog|secure|wtmp|btmp|lastlog|\.bash_history|\.zsh_history|consolehost_history\.txt|journal)(/|$)", re.I)
WRITE_OPS = re.compile(r"write|edit|delete|remove|rename|move|commit|truncate|overwrite|append|chmod|rm\b", re.I)
SECRET_COMPONENTS = {".jarvis", ".jarvis-kernel", "secrets.env", "keys.json", "protected.json", "id_ed25519", "id_rsa"}
# What the owner may ask for directly without an approval each time (owner_direct): looking, opening, clicking, typing,
# keys, scrolling. A click or key whose words could pay, send, delete, install, sign out or accept is raised back to approval.
ORDINARY_OPS = {"ui": {"launch", "focus", "click", "type", "key", "scroll", "click_at", "minimize", "maximize", "restore"},
                "browser": {"click", "type", "select", "press"}}
CONSEQUENTIAL = re.compile(
    r"\b(pay|payment|buy|purchase|order|checkout|check out|place order|subscribe|donate|send|post|publish|share|tweet|reply|"
    r"comment|submit|confirm|delete|remove|erase|trash|wipe|format|uninstall|install|sign ?out|log ?out|sign ?in|log ?in|"
    r"unsubscribe|transfer|withdraw|deposit|accept|agree|allow|grant|authori[sz]e|reset|upload|download|save card|add card|"
    r"change password|close account|deactivate)\b", re.I)
MESSAGING_APPS = re.compile(r"whatsapp|telegram|slack|teams|discord|outlook|thunderbird|signal|skype|messenger|mail\b", re.I)
RISKY_KEYS = {"alt+f4", "ctrl+w", "ctrl+shift+w", "delete", "shift+delete", "ctrl+shift+delete", "ctrl+d", "ctrl+enter", "ctrl+shift+enter"}


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


def internal_host(host: str) -> bool:
    """True for localhost, private/loopback/link-local/CGNAT (Tailscale) addresses and internal-looking names.
    Handles decimal, octal, hex and IPv4-mapped IPv6 forms. Does not resolve DNS (the browser layer re-checks resolved IPs)."""
    import ipaddress
    import socket
    h = host.strip().strip("[]").rstrip(".").lower()
    if not h:
        return True
    if h == "localhost" or h.endswith((".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".ts.net")):
        return True
    cand = None
    try:
        cand = ipaddress.ip_address(h)
    except ValueError:
        try:  # inet_aton accepts decimal (2130706433), hex (0x7f.1) and octal (0177.0.0.1) IPv4 spellings
            cand = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(h)))
        except OSError:
            return False
    if isinstance(cand, ipaddress.IPv6Address) and cand.ipv4_mapped:
        cand = cand.ipv4_mapped
    return (cand.is_private or cand.is_loopback or cand.is_link_local or cand.is_reserved or cand.is_multicast
            or cand.is_unspecified or cand in ipaddress.ip_network("100.64.0.0/10"))


def _internal_url(leaf: str) -> bool:
    s = urllib.parse.unquote(str(leaf)).strip()
    if re.match(r"^(javascript|file|data|vbscript|ftp|chrome|about|view-source|blob):", s, re.I):
        return True  # none of these are valid targets for the browser tool
    if "://" not in s:
        return False
    parts = urllib.parse.urlsplit(s)
    if parts.scheme.lower() not in ("http", "https"):
        return True  # file:, ftp:, javascript: and friends are not allowed targets for the browser tool
    return internal_host(parts.hostname or "")


def _is_secret_path(forms: list) -> bool:
    for f in forms:
        if "jarvis-secrets" in f or SECRET_PATTERNS.search(f):
            return True
        for comp in re.split(r"[/\s]+", f):  # path pieces AND words (window titles like "keys.json - Notepad")
            comp = comp.strip("'\"()[]{},;:<>")
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
    # Set only by the owner plane (Owner.pc) for what the owner asked for: never read from a node or agent request.
    owner_direct: bool = False

    def digest(self) -> str:
        parts = [_name(self.tool), _name(self.op), self.target, self.params, self.lane] + (["owner_direct"] if self.owner_direct else [])
        blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def describe(self, limit: int = 200) -> dict:
        """What goes into the audit log (limit 200) and approval prompts (a larger limit): strings are scrubbed for
        secrets and truncated, and a truncated value says so."""
        from .scrub import scrub

        def clean(x):
            if isinstance(x, str):
                s, _, blocked = scrub(x)
                return "[BLOCKED: private key]" if blocked else (s if len(s) <= limit else s[:limit] + "...[truncated]")
            if isinstance(x, dict):
                return {str(k): clean(v) for k, v in x.items()}
            if isinstance(x, (list, tuple)):
                return [clean(v) for v in x]
            return x
        params = self.params
        if _name(self.tool) == "connector" and limit <= 500 and isinstance(params, dict) and isinstance(params.get("args"), dict):
            # The audit chain records that a connector call happened and its shape, not what was in it: the arguments are the
            # owner's content. The approval prompt (a larger limit) still shows them in full, which is where they matter.
            a = params["args"]
            params = dict(params, args={"_keys": sorted(str(k)[:40] for k in a)[:20], "_chars": len(json.dumps(a, default=str))})
        return {"tool": clean(self.tool), "op": clean(self.op), "target": clean(self.target), "params": clean(params), "lane": self.lane}


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
    if (a.owner_direct and op in ORDINARY_OPS.get(tool, ()) and _name(a.lane) == "private" and not a.untrusted_session
            and level == YELLOW_APPROVAL):
        level, reason = YELLOW_AUTO, "the owner asked for this directly (ordinary PC or browser action)"
        words = " ".join(_leaves(a.target, [], False) + _leaves(a.params, [], False))
        key = _name((a.params or {}).get("text", "")) if op in ("key", "press") else ""
        if CONSEQUENTIAL.search(words):
            level, reason = YELLOW_APPROVAL, "this could pay, send, delete, install, sign in or out, or accept something: the owner approves it"
        elif key in RISKY_KEYS or (key == "enter" and (MESSAGING_APPS.search(words) or (tool == "browser" and "search" not in words.lower()))):
            level, reason = YELLOW_APPROVAL, "this key can close, delete or send: the owner approves it"
    if tool in ("diag", "media") and _name(a.lane) == "public" and level < YELLOW_APPROVAL:
        level, reason = YELLOW_APPROVAL, "a lab agent reading the owner's PC state needs the owner's approval (it reaches a public-lane model)"
    leaves = _leaves(a.target, []) + _leaves(a.params, [])
    forms = [f for lf in leaves for f in _canon(lf)]
    vals = [v for v in _leaves(a.target, [], False) + _leaves(a.params, [], False) if v]
    for group in (leaves, vals):  # catches a path split across params (with and without the key names between)
        forms += [f for j in ("", "/") for f in _canon(j.join(group))]
    if _is_secret_path(forms):
        level, reason = DENY, "touches a secrets path"
    if tool in ("fs", "config", "git", "delete", "service", "repair") and WRITE_OPS.search(op) and level < RED:
        pieces = {c.strip("'\"()[]{},;:<>") for f in forms for c in re.split(r"[/\s]+", f)}
        if pieces & SAFETY_COMPONENTS:
            level, reason = RED, "changes the rules, hooks or settings the agents run under (console only)"
    if WRITE_OPS.search(op) and level < RED and any(LOG_TAMPER.search(f) for f in forms):
        level, reason = RED, "changes a system log or shell history (console only)"
    if tool in ("ui", "browser", "pc") and SENSITIVE_APPS.search(" ".join(forms)):
        level, reason = DENY, "UI or browser on a password manager, wallet or banking app"
    if tool == "pc" and _name(a.lane) == "public" and level < DENY:
        if op == "list_folder":
            level, reason = DENY, "a lab agent may not read the owner's folders (the names would reach a public-lane model)"
        elif level < YELLOW_APPROVAL:
            level, reason = YELLOW_APPROVAL, "a lab agent acting on the owner's PC needs the owner's approval"
    if tool == "ui" and op in ("snapshot", "list_windows", "screenshot") and _name(a.lane) == "public" and level < DENY:
        level, reason = max(level, YELLOW_APPROVAL), "a lab agent looking at the owner's desktop needs the owner's approval (titles reach a public-lane model)"
    if tool == "skill" and op == "activate" and level < DENY:
        p = a.params or {}
        risk, where = _name(p.get("risk")), _name(p.get("where"))
        if _name(a.lane) != "private":
            level, reason = DENY, "only the owner's side may activate skills (a lab agent may not grant itself skills)"
        elif risk not in ("low", "medium"):
            level, reason = DENY, f"a skill rated '{risk or 'unrated'}' is never activated"
        elif risk == "medium" or where != "lab":
            level, reason = YELLOW_APPROVAL, "a medium-risk skill, or one for the owner's own agent, needs the owner's approval"
    if tool == "connector" and op == "call" and level < DENY:
        p = a.params or {}
        risk = _name(p.get("risk"))
        if risk == "read":
            pass                                              # runs at once for a lane that may use the connector
        elif risk in ("write", "send"):
            level, reason = max(level, YELLOW_APPROVAL), f"a connector tool that changes or sends ({risk}) needs the owner's approval of exactly these arguments"
        else:
            level, reason = DENY, "a connector tool that is not classified as read, write or send is refused"
        if _name(a.lane) == "public" and p.get("public_ok") is not True:
            level, reason = DENY, "this connector is not available to the public lane"
    if tool == "browser" and any(_internal_url(lf) for lf in leaves):
        level, reason = DENY, "browser pointed at an internal address"
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
