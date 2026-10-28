"""One-time, per-site grants for the browser agent (D64, 2026-10-26, owner: "log in by itself").

The kernel treats sign-in, accepting terms, installing, uploading and downloading as class C: always the owner's PIN. The owner
wants JARVIS to use a free web tool (for example a presentation generator) without a PIN for every click. This module is the
narrow, expiring exception: the owner confirms ONE approval card with the PIN in the console, and for that exact host (https only,
no sub-domains, no wildcards) and for the listed abilities only, the matching clicks run without a card until it expires.

    config/trusted_sites.json   {"sites": [{"host", "allowed": [...], "granted_at", "expires", "granted_by_pin": true, "approval",
                                            "request": {"hosts", "abilities", "days", "note"}, "seal"}]}

* A grant is created only by `Owner.site_grant_apply` after `Kernel.authorize` consumed an approval the owner confirmed with the PIN
  (never by voice, chat, a model or a plan: nothing else writes entries). Every entry carries a seal (HMAC-SHA256 with a key that
  only the kernel database holds) over its own fields, the approval id and the digest of the approved card. A hand-written or edited
  entry has no valid seal and is ignored (fail closed); an entry the owner deletes is simply gone (the safe direction needs no PIN).
* The file is console-only: it is in policy.SAFETY_COMPONENTS (an agent's write is RED) and in push_gate.PROTECTED.
* Abilities: signin_existing_session (sign in with the browser session the owner already has, including "Continue with Google /
  Microsoft"; never a password typed by JARVIS), signup (create an account with the owner's alias e-mail and a generated password
  that only the node ever types), accept_terms (terms and cookie consent clicks on that host), download_to_quarantine (a file from that
  host into the quarantine folder; pptx, pdf, png, jpg; size cap; never executed).
* Never covered, whatever the file says: payment pages, "upgrade" prompts, OAuth consent beyond sign-in (mail, drive, contacts),
  another host, a password manager, a bank, an AI chat site (their terms forbid automated use), an internal address.
"""
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import time

ABILITIES = ("signin_existing_session", "signup", "accept_terms", "download_to_quarantine")
DEFAULT_DAYS = 30
MAX_DAYS = 90
# D79 (2026-10-27): a grant can be for the browser on VM2 (`target: vm2`). The PC form is unchanged. A VM2 grant covers VM2 only and a PC grant covers the PC only; the
# VM2 profile holds sessions the owner logged in to himself through the takeover viewer, and a page that talks an agent into using them is the risk, so the grant is
# shorter (default 14 days, at most 30), has no signup (no node types a generated password there) and its card says so.
TARGETS = ("pc", "vm2")
VM2_ABILITIES = ("signin_existing_session", "accept_terms", "download_to_quarantine")
VM2_DEFAULT_DAYS = 14
VM2_MAX_DAYS = 30
MAX_HOSTS = 4
FILE = os.environ.get("JARVIS_TRUSTED_SITES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "trusted_sites.json")
HOST_RX = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")
DAY = 86400

# Set by the kernel (store.Kernel) and by tests: () -> the HMAC key (bytes), or None when there is none (nothing is trusted then).
SEAL_KEY = None
NOW = time.time
_lock = threading.RLock()
_cache = {"path": None, "mtime": None, "entries": []}


class GrantError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------------- hosts
def normalize_host(raw) -> str:
    """The exact host a grant is for, or raises GrantError with the reason. Accepts 'gamma.app' or 'https://gamma.app[/]'.
    Refuses: http, a port, a path, user info, a wildcard, an address, a single label, a non-ASCII (look-alike) name, an internal host,
    a bank / wallet / password manager (the kernel's sensitive list) and an AI chat site."""
    from . import policy
    s = str(raw or "").strip().lower()
    if not s:
        raise GrantError("no site named")
    if "://" in s:
        scheme, _, rest = s.partition("://")
        if scheme != "https":
            raise GrantError("only https sites can be trusted")
        s = rest
    if s.endswith("/"):
        s = s[:-1]
    if any(c in s for c in "/?#@:*\\ \t\r\n") or s.startswith(".") or s.endswith("."):
        raise GrantError("a grant is for one exact host name: no path, port, user name or wildcard")
    if not s.isascii():
        raise GrantError("the host must be plain ASCII (a look-alike letter could hide another site)")
    if not HOST_RX.match(s):
        raise GrantError("that is not a host name")
    if s.startswith("xn--") or ".xn--" in s:
        raise GrantError("an internationalized host name is not accepted: it can hide a look-alike")
    if policy.internal_host(s):
        raise GrantError("an internal address cannot be trusted")
    why = policy.sensitive_host(s)
    if why:
        raise GrantError("a bank, payment, broker, wallet or password-manager site is never trusted")
    if policy.ai_chat_host(s):
        raise GrantError("the terms of that AI chat site forbid automated use")
    return s


def clean_abilities(raw) -> list:
    got = [str(a).strip() for a in (raw or [])]
    bad = [a for a in got if a not in ABILITIES]
    if bad or not got:
        raise GrantError("abilities must be some of: " + ", ".join(ABILITIES))
    return [a for a in ABILITIES if a in got]


def clean_days(raw, target="pc") -> int:
    top, dflt = (VM2_MAX_DAYS, VM2_DEFAULT_DAYS) if target == "vm2" else (MAX_DAYS, DEFAULT_DAYS)
    try:
        n = int(raw if raw not in (None, "") else dflt)
    except (TypeError, ValueError):
        raise GrantError("days must be a number") from None
    if not 1 <= n <= top:
        raise GrantError(f"days must be from 1 to {top}")
    return n


def clean_target(raw) -> str:
    t = str(raw or "pc").strip().lower()
    if t not in TARGETS:
        raise GrantError("target must be pc or vm2")
    return t


def clean_request(hosts, abilities, days=None, note="", target="pc") -> dict:
    target = clean_target(target)
    hs = hosts if isinstance(hosts, (list, tuple)) else [hosts]
    out = []
    for h in hs:
        n = normalize_host(h)
        if n not in out:
            out.append(n)
    if not out or len(out) > MAX_HOSTS:
        raise GrantError(f"name from 1 to {MAX_HOSTS} hosts")
    abil = clean_abilities(abilities)
    if target == "vm2" and [a for a in abil if a not in VM2_ABILITIES]:
        raise GrantError("on VM2 a grant can only cover: " + ", ".join(VM2_ABILITIES))
    req = {"hosts": out, "abilities": abil, "days": clean_days(days, target), "note": " ".join(str(note or "").split())[:300]}
    if target == "vm2":
        req["target"] = "vm2"                          # absent for the PC, so every PC request, digest and seal is byte-for-byte what it was
    return req


def make_action(req: dict):
    """The policy Action an approval card stands for. Its digest is what the approval is bound to; the entries keep it."""
    from .policy import Action
    card = card_text(req)
    params = {"hosts": list(req["hosts"]), "abilities": list(req["abilities"]), "days": int(req["days"]), "note": req.get("note", "")}
    if req.get("target") == "vm2":
        params["target"] = "vm2"
    return Action("sitegrant", "grant", ", ".join(req["hosts"]), params, "private", False, owner_direct=True, card=card)


WHAT = {"signin_existing_session": "sign in with the Chrome session you already have there (Continue with Google or Microsoft), never typing a password",
        "signup": "create an account with your alias e-mail and a generated password that only the node types and keeps in the secrets folder",
        "accept_terms": "click the site's terms and cookie-consent buttons",
        "download_to_quarantine": "download files (pptx, pdf, png, jpg) from that site into the quarantine folder; they are checked and never run"}


def card_text(req: dict, now: float = None) -> dict:
    now = NOW() if now is None else now
    until = time.strftime("%Y-%m-%d", time.localtime(now + req["days"] * DAY))
    vm2 = req.get("target") == "vm2"
    if vm2:
        return {"what": ("Let JARVIS act on VM2 (the sandbox desktop), without asking for your PIN each time, ONLY on " + ", ".join(req["hosts"]) + " (https, exactly this host) until "
                         + until + " (" + str(req["days"]) + " days). It may: " + "; ".join(WHAT[a] for a in req["abilities"]) + ". It uses the sessions YOU logged in to on VM2 "
                         "through the takeover viewer; JARVIS never types or reads a password there."),
                "warning": f"This session lives on VM2. A page that talks an agent into acting could use it. That is why this grant expires in {req['days']} days and is for VM2 only. "
                           "Page text on VM2 is read by the lab's public-lane models: grant only sites you accept that for.",
                "never_covered": "payments, upgrade or purchase prompts, permission screens that give the site your mail, Drive or contacts, any other site, and CAPTCHA, 2FA codes or bot checks (you do those yourself, through the takeover viewer)",
                "sites": list(req["hosts"]), "abilities": list(req["abilities"]), "expires": until, "target": "vm2",
                **({"site_terms_note": req["note"]} if req.get("note") else {}),
                "approve": "Approve with your PIN in the JARVIS console. You can remove the grant at any time."}
    return {"what": ("Let JARVIS act on its own, without asking for your PIN each time, ONLY on " + ", ".join(req["hosts"]) + " (https, exactly this host) until "
                     + until + " (" + str(req["days"]) + " days). It may: " + "; ".join(WHAT[a] for a in req["abilities"]) + "."),
            "never_covered": "payments, upgrade or purchase prompts, permission screens that give the site your mail, Drive or contacts, any other site, and CAPTCHA, 2FA codes or bot checks (you do those yourself)",
            "sites": list(req["hosts"]), "abilities": list(req["abilities"]), "expires": until,
            **({"site_terms_note": req["note"]} if req.get("note") else {}),
            "approve": "Approve with your PIN in the JARVIS console. You can remove the grant at any time."}


# ---------------------------------------------------------------------------------------------------------------- the file
def _key():
    fn = SEAL_KEY
    try:
        k = fn() if fn else None
    except Exception:  # noqa: BLE001 - no key: nothing is trusted
        k = None
    return k if isinstance(k, (bytes, bytearray)) and len(k) >= 16 else None


def seal_of(entry: dict, key: bytes) -> str:
    body = {k: entry[k] for k in ("host", "allowed", "granted_at", "expires", "granted_by_pin", "approval", "digest", "request", "target") if k in entry}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hmac.new(key, blob.encode("utf-8"), hashlib.sha256).hexdigest()


def valid_entry(entry, key: bytes = None) -> bool:
    """Is this file entry a real grant: sealed by the kernel, consistent with the card that was approved, and well-formed?"""
    key = key or _key()
    if key is None or not isinstance(entry, dict):
        return False
    try:
        req = entry["request"]
        host = normalize_host(entry["host"])
        if host != entry["host"] or host not in req["hosts"] or entry["granted_by_pin"] is not True:
            return False
        target = clean_target(req.get("target", "pc"))
        if entry.get("target", "pc") != target:
            return False
        if list(entry["allowed"]) != clean_abilities(req["abilities"]) or int(entry["expires"]) != int(entry["granted_at"]) + clean_days(req["days"], target) * DAY:
            return False
        if entry["digest"] != make_action(clean_request(req["hosts"], req["abilities"], req["days"], req.get("note", ""), target)).digest():
            return False
        return hmac.compare_digest(str(entry.get("seal", "")), seal_of(entry, key))
    except (KeyError, TypeError, ValueError, GrantError):
        return False


def read_entries(path: str = None) -> list:
    """The entries in the file (not yet checked for seal or expiry). A missing or broken file is no entries."""
    path = path or FILE
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    sites = data.get("sites") if isinstance(data, dict) else None
    return [e for e in sites if isinstance(e, dict)] if isinstance(sites, list) else []


def _entries(path: str = None) -> list:
    path = path or FILE
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    key = _key()
    with _lock:
        c = _cache
        if c["path"] != path or c["mtime"] != (mtime, bool(key)):
            c.update(path=path, mtime=(mtime, bool(key)), entries=[e for e in read_entries(path) if valid_entry(e, key)])
        return c["entries"]


def invalidate() -> None:
    with _lock:
        _cache.update(path=None, mtime=None, entries=[])


def find(host, ability: str, now: float = None, path: str = None, target: str = "pc"):
    """The valid, unexpired grant for exactly this host and ability ON THIS TARGET (the PC unless asked for vm2), or None. `host` must already be a plain host (no scheme).
    A grant made for VM2 never answers a PC question, and the other way round."""
    if ability not in ABILITIES or not isinstance(host, str) or target not in TARGETS:
        return None
    h = host.strip().lower()
    now = NOW() if now is None else now
    for e in _entries(path):
        if e["host"] == h and ability in e["allowed"] and float(e["expires"]) > now and e.get("target", "pc") == target:
            return e
    return None


def active(now: float = None, path: str = None) -> list:
    now = NOW() if now is None else now
    return [{"host": e["host"], "allowed": list(e["allowed"]), "expires": int(e["expires"]), "granted_at": int(e["granted_at"]), "target": e.get("target", "pc")}
            for e in _entries(path) if float(e["expires"]) > now]


def write_entries(entries: list, path: str = None) -> None:
    """Atomic replace of the file (temp file in the same folder, then rename)."""
    path = os.path.abspath(path or FILE)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".trusted_sites.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"_note": NOTE, "sites": entries}, f, indent=1, ensure_ascii=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    invalidate()


NOTE = ("Console-only. Entries are created by the kernel after the owner confirms an approval card with the PIN, sealed with a key that only "
        "the kernel database holds; an entry typed by hand has no valid seal and is ignored. To remove a grant, delete its entry. "
        "See docs/03_DECISIONS.md D64.")


def add(req: dict, approval_id: str, key: bytes = None, now: float = None, path: str = None) -> list:
    """Write one sealed entry per host of an approved request. Returns the entries. The caller has just consumed the approval."""
    key = key or _key()
    if key is None:
        raise GrantError("no seal key: grants cannot be created here")
    now = int(NOW() if now is None else now)
    digest = make_action(req).digest()
    tgt = req.get("target", "pc")
    keep = [e for e in read_entries(path) if not (e.get("host") in req["hosts"] and e.get("target", "pc") == tgt) and valid_entry(e, key)]       # a new grant replaces the old one for that host and target
    made = []
    for h in req["hosts"]:
        e = {"host": h, "allowed": list(req["abilities"]), "granted_at": now, "expires": now + req["days"] * DAY, "granted_by_pin": True,
             "approval": str(approval_id), "digest": digest, "request": dict(req), **({"target": "vm2"} if tgt == "vm2" else {})}
        e["seal"] = seal_of(e, key)
        made.append(e)
    write_entries(keep + made, path)
    return made


def revoke(host, path: str = None) -> int:
    """Remove the grant(s) for this host. The safe direction: no PIN. Returns how many were removed."""
    h = normalize_host(host)
    entries = read_entries(path)
    left = [e for e in entries if e.get("host") != h]
    if len(left) != len(entries):
        write_entries([e for e in left if valid_entry(e)], path)
    return len(entries) - len(left)
