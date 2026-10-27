"""The owner's alias identity and the site passwords the NODE types (D64, 2026-10-26). Windows (the secrets folder), but pure Python.

Owner: "account creation with an email alias he configured; a generated strong password is typed by the NODE from a secrets-folder file
reference and is never shown to or passed through any model, log, screenshot or journal."

* `config/identity.json` (console-only, in the kernel's SAFETY_COMPONENTS): {"email_alias": "me+jarvis@example.com",
  "email_pattern": "me+{site}@example.com", "display_name": "Satish"}. `email_pattern` wins when present ({site} becomes the host's first label, so
  every site gets its own alias). Nothing here is a secret from the owner's point of view, but it is still only typed by reference.
* `D:\\jarvis-secrets\\sites\\<host>.cred`: a dotenv file (JARVIS_SITE, JARVIS_EMAIL, JARVIS_PASSWORD, CREATED) written by `new_cred` for ONE host,
  never overwritten, owner-only where the OS has modes. Claude, agents and plugins never read this folder (the kernel's secret-path rule is DENY
  for it); only this module does, inside the node, when a granted `type` job names `@cred:password` for the host the live page is on.
* A model, a plan and the journal only ever see the reference text (`@cred:password`, `@identity:email`) and a character count.
"""
import json
import os
import re
import secrets
import string
import time

HOME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
IDENTITY_FILE = os.environ.get("JARVIS_IDENTITY") or os.path.join(HOME, "config", "identity.json")
SECRETS_DIR = os.environ.get("JARVIS_SITE_SECRETS") or r"D:\jarvis-secrets\sites"
HOST_RX = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")
EMAIL_RX = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,120}\.[A-Za-z]{2,}$")
REF_RX = re.compile(r"^@(cred|identity):([a-z][a-z0-9_]{0,20})$")
SYMBOLS = "-_.!"


class CredError(Exception):
    pass


def _host(host: str) -> str:
    h = str(host or "").strip().lower()
    if not HOST_RX.match(h):
        raise CredError("not a plain host name")
    return h


def identity(host: str = "", path: str = None) -> dict:
    """{"email", "name"} for a site, from config/identity.json. CredError says what the owner has to add."""
    try:
        with open(path or IDENTITY_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise CredError("config/identity.json is missing or unreadable: the owner adds the alias e-mail he wants JARVIS to sign up with") from None
    if not isinstance(data, dict):
        raise CredError("config/identity.json is not an object")
    pattern, alias = str(data.get("email_pattern") or ""), str(data.get("email_alias") or "")
    if pattern and "{site}" in pattern and host:
        label = re.sub(r"[^a-z0-9]", "", _host(host).split(".")[0])[:20] or "site"
        email = pattern.replace("{site}", label)
    else:
        email = alias
    if not EMAIL_RX.match(email):
        raise CredError("config/identity.json has no valid alias e-mail (email_alias, or email_pattern with {site})")
    name = " ".join(str(data.get("display_name") or "").split())[:60]
    return {"email": email, "name": name}


def cred_path(host: str, folder: str = None) -> str:
    return os.path.join(folder or SECRETS_DIR, _host(host) + ".cred")


def _password(n: int = 24) -> str:
    alpha = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alpha) for _ in range(n - 3)) + "".join(secrets.choice(SYMBOLS) for _ in range(3))
        pw = "".join(secrets.SystemRandom().sample(pw, len(pw)))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw) and any(c in SYMBOLS for c in pw)
                and pw[0] not in SYMBOLS):
            return pw


def new_cred(host: str, folder: str = None, ident: dict = None) -> dict:
    """Make the password for this host once. Returns {created, email_hint, file} and NEVER the password. An existing file is kept as it is."""
    host = _host(host)
    folder = folder or SECRETS_DIR
    path = cred_path(host, folder)
    who = ident or identity(host)
    if os.path.isfile(path):
        return {"created": False, "email_hint": mask(who["email"]), "file": host + ".cred"}
    os.makedirs(folder, exist_ok=True)
    body = f"JARVIS_SITE={host}\nJARVIS_EMAIL={who['email']}\nJARVIS_PASSWORD={_password()}\nCREATED={time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    return {"created": True, "email_hint": mask(who["email"]), "file": host + ".cred"}


def mask(email: str) -> str:
    return re.sub(r"^(.{3}).*(@.*)$", r"\1***\2", email) if "@" in (email or "") else ""


def _read_dotenv(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            k, sep, v = line.rstrip("\r\n").partition("=")
            if sep and re.match(r"^[A-Z_]+$", k):
                out[k] = v
    return out


def resolve(ref: str, host: str, folder: str = None, path: str = None) -> str:
    """The text a reference stands for, for the node's own typing only. `@cred:password` / `@cred:email` come from <host>.cred (the host is the
    live page's host, checked by the caller); `@identity:email` / `@identity:name` from config/identity.json."""
    m = REF_RX.match(str(ref or ""))
    if not m:
        raise CredError("not a reference")
    kind, field = m.group(1), m.group(2)
    host = _host(host)
    if kind == "identity":
        if field not in ("email", "name"):
            raise CredError("unknown identity field")
        v = identity(host, path)[field]
        if not v:
            raise CredError("config/identity.json has no display_name")
        return v
    if field not in ("password", "email"):
        raise CredError("unknown credential field")
    try:
        data = _read_dotenv(cred_path(host, folder))
    except OSError:
        raise CredError("no password was made for this site yet") from None
    v = data.get("JARVIS_" + field.upper(), "")
    if not v:
        raise CredError("the credential file has no " + field)
    return v


def field_fits(kind_field: str, role: str, name: str) -> bool:
    """A reference goes only into a field whose accessible name fits it: a password into a password field, the alias e-mail into an e-mail or user
    name field, the name into a name field. A page cannot talk JARVIS into typing a password into some other box."""
    n = (name or "").lower()
    if kind_field.endswith(":password"):
        return role in ("textbox", "searchbox", "") and bool(re.search(r"pass(?:word|code)|pwd", n))
    if kind_field.endswith(":email"):
        return role in ("textbox", "searchbox", "combobox", "") and bool(re.search(r"e-?mail|user ?name|login|account", n))
    if kind_field.endswith(":name"):
        return role in ("textbox", "searchbox", "") and bool(re.search(r"\bname\b", n))
    return False
