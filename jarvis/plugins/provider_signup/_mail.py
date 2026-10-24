"""Verification mail, safely: only mail whose sender domain is on the provider's list AND that passed DKIM, only a short code or a link
on that same domain. Everything else in the mail is dropped; the text is untrusted and is never returned."""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
import time
from email.utils import parseaddr
from urllib.parse import urlsplit

CODE_RE = re.compile(r"(?<![A-Za-z0-9])(\d{4,8})(?![A-Za-z0-9])")
LINK_RE = re.compile(r"https://[^\s<>\"')\]]+")
KEYWORDS = re.compile(r"(?i)verif|confirm|activate|code|sign.?in|log.?in")


def domain_ok(host: str, allowed: list[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in allowed)


def _dkim_passed(msg, allowed: list[str]) -> bool:
    """Gmail adds Authentication-Results; accept only a dkim=pass whose signing domain is one of the allowed ones."""
    for h in msg.get_all("Authentication-Results", []):
        for m in re.finditer(r"dkim=pass[^;]*?header\.(?:d|i)=@?([A-Za-z0-9.-]+)", str(h)):
            if domain_ok(m.group(1), allowed):
                return True
    return False


def _body(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    return re.sub(r"<[^>]+>", " ", text) if part.get_content_type() == "text/html" else text


def extract(raw: bytes, allowed: list[str]) -> dict | None:
    """{'code', 'link', 'subject'} from one raw message, or None when the sender, the signature or the content do not qualify."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(str(msg.get("From", "")))[1].rpartition("@")[2]
    if not domain_ok(sender, allowed) or not _dkim_passed(msg, allowed):
        return None
    subject = " ".join(str(msg.get("Subject", "")).split())[:120]
    raw_text = ""
    part = msg.get_body(preferencelist=("html", "plain"))
    if part is not None:
        raw_text = part.get_content()
    text = _body(msg)
    links = [u.rstrip(".,;") for u in LINK_RE.findall(raw_text + " " + text)]
    links = [u for u in links if domain_ok(urlsplit(u).hostname or "", allowed) and KEYWORDS.search(u + " " + subject + " " + text[:400])]
    code = CODE_RE.search(subject) or CODE_RE.search(text[:2000])
    if not links and not code:
        return None
    return {"code": code.group(1) if code else "", "link": links[0] if links else "", "subject": subject}


def fetch_latest(host: str, user: str, password: str, allowed: list[str], since_minutes: int = 15, imap_cls=imaplib.IMAP4_SSL) -> dict | None:
    """Newest qualifying message in the inbox from the last `since_minutes`. Read-only (EXAMINE), nothing is marked or deleted."""
    cutoff = time.time() - since_minutes * 60
    conn = imap_cls(host)
    try:
        conn.login(user, password)
        conn.select("INBOX", readonly=True)
        since = time.strftime("%d-%b-%Y", time.gmtime(cutoff - 86400))
        _, data = conn.search(None, "SINCE", since)
        for num in reversed((data[0] or b"").split()[-30:]):
            _, parts = conn.fetch(num, "(INTERNALDATE BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
            got = extract(raw, allowed)
            if got:
                return got
        return None
    finally:
        try:
            conn.logout()
        except Exception:
            pass
