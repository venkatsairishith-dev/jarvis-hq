"""Gmail and Calendar DRAFT helpers with reviewed writes.

This module only ever creates or updates drafts (Gmail) and tentative events
(Calendar, with no invitations). It deliberately has NO delivery capability:
nothing here calls a delivery method on the service object.  Callers pass an
already-built ``service`` object; this module never builds credentials or
touches the network itself.

Write helpers return receipt-like dicts holding ids and counts only, never
bodies, subjects or addresses, so they are safe for an audit log.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime
from email.message import EmailMessage
from email import policy
from email.utils import getaddresses, parseaddr
from typing import Any

MAX_RECIPIENTS = 20
MAX_SUBJECT = 300
MAX_BODY_BYTES = 200 * 1024
MAX_SUMMARY = 200

_ADDR_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


class BadMessage(ValueError):
    """The message failed validation."""


class Unsupported(ValueError):
    """The requested feature is intentionally not supported."""


class BadEvent(ValueError):
    """The event failed validation."""


class GoogleError(RuntimeError):
    """A service call failed; carries only the exception class name."""


def _no_ctl(name: str, value: str) -> None:
    if any(c in value for c in "\r\n\0"):
        raise BadMessage(f"control character in {name}")


def _check_address(addr: str, exc: type[Exception]) -> str:
    if not isinstance(addr, str):
        raise exc("address must be a string")
    if any(c in addr for c in "\r\n\0"):
        raise exc("control character in address")
    _, parsed = parseaddr(addr)
    if not parsed or not _ADDR_RE.match(parsed):
        raise exc("invalid address")
    # parseaddr only reads the FIRST address of "a@b.com, evil@x.com", but the whole string would go into the header: exactly one address allowed.
    if len(getaddresses([addr])) != 1:
        raise exc("exactly one address per entry")
    return addr


def _check_addresses(addrs: list[str], what: str, exc: type[Exception]) -> list[str]:
    if isinstance(addrs, str):
        raise exc(f"{what} must be a list")
    return [_check_address(a, exc) for a in addrs]


def build_message(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    reply_to_message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: Any = None,
) -> dict[str, str]:
    """Build a Gmail API message dict ``{"raw": ..., "threadId"?}``."""
    if attachments:
        raise Unsupported("attachments are not supported")
    cc = cc or []
    if not to:
        raise BadMessage("at least one recipient required")
    if len(to) + len(cc) > MAX_RECIPIENTS:
        raise BadMessage("too many recipients")
    for name, value in (("subject", subject), ("in_reply_to", in_reply_to),
                        ("references", references)):
        if value is not None:
            if not isinstance(value, str):
                raise BadMessage(f"{name} must be a string")
            _no_ctl(name, value)
    to = _check_addresses(to, "to", BadMessage)
    cc = _check_addresses(cc, "cc", BadMessage)
    if len(subject) > MAX_SUBJECT:
        raise BadMessage("subject too long")
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise BadMessage("body invalid or too large")

    msg = EmailMessage(policy=policy.SMTP)
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body, charset="utf-8")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    out: dict[str, str] = {"raw": raw}
    if reply_to_message_id:
        _no_ctl("reply_to_message_id", reply_to_message_id)
        out["threadId"] = reply_to_message_id
    return out


def _wrap(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - deliberately reduced to class name
        raise GoogleError(type(e).__name__) from None


class DraftGuard:
    """Holds the draft rules: create and update only, no delete, no delivery."""

    def save_draft(self, service: Any, message: dict) -> dict[str, Any]:
        """Create a Gmail draft; return ids only."""
        res = _wrap(lambda: service.users().drafts().create(
            userId="me", body={"message": message}).execute())
        return self._receipt("draft_create", res)

    def update_draft(self, service: Any, draft_id: str, message: dict) -> dict[str, Any]:
        """Replace the content of an existing draft; return ids only."""
        res = _wrap(lambda: service.users().drafts().update(
            userId="me", id=draft_id, body={"id": draft_id, "message": message}).execute())
        return self._receipt("draft_update", res)

    @staticmethod
    def _receipt(op: str, res: dict) -> dict[str, Any]:
        m = res.get("message", {}) or {}
        return {"op": op, "draft_id": res.get("id"),
                "message_id": m.get("id"), "thread_id": m.get("threadId")}


def unanswered_query(days: int = 7, label_ids: tuple[str, ...] = ("INBOX",)) -> dict[str, Any]:
    """Params for ``threads().list`` finding recent inbox threads."""
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365:
        raise ValueError("days out of range")
    return {"userId": "me", "q": f"in:inbox newer_than:{days}d -from:me",
            "labelIds": list(label_ids)}


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def find_unanswered(service: Any, days: int = 7, max_threads: int = 25) -> list[dict[str, Any]]:
    """Return threads whose last message is not from the user. Read-only."""
    params = unanswered_query(days)
    params["maxResults"] = max_threads

    def run() -> list[dict[str, Any]]:
        me = service.users().getProfile(userId="me").execute().get(
            "emailAddress", "").lower()
        listed = service.users().threads().list(**params).execute()
        found = []
        for t in (listed.get("threads") or [])[:max_threads]:
            th = service.users().threads().get(
                userId="me", id=t["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]).execute()
            msgs = th.get("messages") or []
            if not msgs:
                continue
            last = msgs[-1]
            sender = parseaddr(_header(last, "From"))[1].lower()
            if sender and sender != me:
                found.append({"thread_id": t["id"], "message_id": last.get("id"),
                              "from": _header(last, "From"),
                              "subject": _header(last, "Subject"),
                              "date": _header(last, "Date")})
        return found

    return _wrap(run)


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise BadEvent("invalid datetime") from None


def build_event(summary: str, start_iso: str, end_iso: str,
                attendees: list[str] | None = None, description: str = "",
                timezone: str = "UTC", location: str = "") -> dict[str, Any]:
    """Build a tentative Calendar event dict with no conference data."""
    if not summary or len(summary) > MAX_SUMMARY:
        raise BadEvent("summary invalid")
    start, end = _parse_dt(start_iso), _parse_dt(end_iso)
    if (start.tzinfo is None) != (end.tzinfo is None):
        raise BadEvent("mixed naive and aware datetimes")
    if not start < end:
        raise BadEvent("start must be before end")
    attendees = attendees or []
    if len(attendees) > MAX_RECIPIENTS:
        raise BadEvent("too many attendees")
    attendees = _check_addresses(attendees, "attendees", BadEvent)
    event: dict[str, Any] = {
        "summary": summary,
        "description": description,
        "location": location,
        "status": "tentative",
        "start": {"dateTime": start_iso, "timeZone": timezone},
        "end": {"dateTime": end_iso, "timeZone": timezone},
        "reminders": {"useDefault": True},
    }
    if attendees:
        event["attendees"] = [{"email": a} for a in attendees]
    return event


def save_event_draft(service: Any, event: dict, allow_attendees: bool = False, **options: Any) -> dict[str, Any]:
    """Insert a tentative event without notifying anyone; return ids only.

    sendUpdates="none" sends no e-mail, but Google still puts the event on an invited attendee's calendar, where they can see it. So an event
    with attendees is refused unless the caller says allow_attendees=True (a deliberate, reviewed choice), not just "a draft"."""
    if options:
        raise BadEvent("no options may be overridden")
    if event.get("conferenceData") is not None:
        raise BadEvent("conferenceData not allowed")
    if event.get("attendees") and allow_attendees is not True:
        raise BadEvent("attendees would see the event on their calendars even without e-mail; pass allow_attendees=True after review")
    res = _wrap(lambda: service.events().insert(
        calendarId="primary", body=event, sendUpdates="none").execute())
    return {"op": "event_create", "event_id": res.get("id"),
            "html_link": res.get("htmlLink"),
            "attendee_count": len(event.get("attendees", []))}
