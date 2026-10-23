"""Notifications for JARVIS's background work (plan P7 step 9): a local inbox that keeps the full notice, and push pings that
carry no content.

Every notice (a watch hit, a paused watch, later: a finished task, an approval waiting) goes into the inbox, a JSON Lines file on the
laptop that only JARVIS and the console read. A push to the phone only says that something is waiting ("JARVIS: 2 new
notices"), never what: no page text, no URL, no title, no amounts. The research behind this (unified UI notes): push services and
Telegram are not end-to-end encrypted, and a notification button must never approve anything, so the phone opens JARVIS to read
and decide.

Push channel: ntfy (Apache-2.0/GPL-2.0 server, plain HTTP API; self-hosted or ntfy.sh). Its topic URL is a secret (anyone who knows
it can read the pings), so it is read from a file under the secrets folder at send time, never logged or put in an error. Pings are
rate-limited and coalesced. The transport is injectable; the default uses urllib with no redirects and no environment proxy.

    python -m jarvis.muse.notify --inbox D:\\jarvis\\N.E.K.O-data\\muse\\inbox.jsonl --list
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

MAX_PUSH_PER_HOUR = 6
PUSH_TIMEOUT_S = 10
MAX_NOTICE_BYTES = 20_000          # one inbox line; a notice carrying a huge diff is cut, never the file
Transport = Callable[[str, bytes, dict[str, str]], int]       # (url, body, headers) -> HTTP status


class NotifyError(RuntimeError):
    pass


def notice_key(notice: dict[str, Any]) -> str:
    """Stable id for one notice, so a retried check never files or pings it twice."""
    basis = json.dumps({k: notice.get(k) for k in ("kind", "watch", "summary", "diff", "reason", "key")}, sort_keys=True, default=str)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


class Inbox:
    """Append-only JSON Lines store of notices. Reading it never needs the network."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()

    def _rows(self) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        rows = []
        for ln in lines:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue                              # a torn last line after a crash is skipped, not fatal
        return rows

    def add(self, notice: dict[str, Any]) -> dict[str, Any] | None:
        """File a notice; returns the stored row, or None when the same notice was already filed."""
        key = notice.get("key") or notice_key(notice)
        with self._lock:
            if any(r.get("key") == key for r in self._rows()):
                return None
            row = {"key": key, "at": self.clock(), "read": False, "notice": notice}
            line = json.dumps(row, ensure_ascii=False, default=str)
            if len(line.encode("utf-8")) > MAX_NOTICE_BYTES:      # too big: drop the diff (the watch history keeps it), then the rest
                cut = {k: notice.get(k) for k in ("kind", "watch", "summary", "reason") if k in notice}
                cut["diff"] = [{"op": "note", "text": "diff too large for the inbox; open the watch to see it"}]
                row["notice"] = cut
                line = json.dumps(row, ensure_ascii=False, default=str)
                if len(line.encode("utf-8")) > MAX_NOTICE_BYTES:
                    row["notice"] = {"kind": notice.get("kind"), "watch": str(notice.get("watch"))[:200], "summary": "notice too large"}
                    line = json.dumps(row, ensure_ascii=False, default=str)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            return row

    def unread(self) -> list[dict[str, Any]]:
        read = {r["key"] for r in self._rows() if r.get("mark") == "read"}
        return [r for r in self._rows() if "notice" in r and r["key"] not in read]

    def mark_read(self, key: str) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "mark": "read", "at": self.clock()}) + "\n")


def _default_transport(url: str, body: bytes, headers: dict[str, str]) -> int:
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):          # a redirect could carry the ping somewhere else
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=PUSH_TIMEOUT_S) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


@dataclass
class NtfyPush:
    """Content-free pings to an ntfy topic. topic_file holds the full topic URL, e.g. https://ntfy.example/abc123 ."""

    topic_file: str | Path
    transport: Transport = _default_transport
    clock: Callable[[], float] = time.time
    max_per_hour: int = MAX_PUSH_PER_HOUR
    _sent: list[float] = field(default_factory=list)
    _pending: int = 0

    def _topic(self) -> str:
        try:
            url = Path(self.topic_file).read_text(encoding="utf-8").strip()
        except OSError:
            raise NotifyError("push topic file is missing or unreadable") from None
        if not url.startswith("https://") or any(c.isspace() for c in url):
            raise NotifyError("push topic must be one https:// URL")
        return url

    def ping(self, count: int = 1) -> bool:
        """Say that `count` new notices wait in JARVIS. Coalesces when over the hourly limit; returns True when a ping went out."""
        now = self.clock()
        self._sent = [t for t in self._sent if now - t < 3600]
        self._pending += count
        if len(self._sent) >= self.max_per_hour:
            return False
        n, self._pending = self._pending, 0
        body = f"JARVIS: {n} new notice{'s' if n != 1 else ''}. Open JARVIS to read {'them' if n != 1 else 'it'}.".encode()
        headers = {"Title": "JARVIS", "Tags": "bell", "Priority": "default"}   # no Click/Actions: the phone opens JARVIS itself
        try:
            status = self.transport(self._topic(), body, headers)
        except NotifyError:
            self._pending += n
            raise
        except Exception as e:                          # the error text could contain the topic URL: report the kind only
            self._pending += n
            raise NotifyError(f"push failed ({type(e).__name__})") from None
        if not 200 <= status < 300:
            self._pending += n
            raise NotifyError(f"push refused with HTTP {status}")
        self._sent.append(now)
        return True


class Notifier:
    """File every notice in the inbox; ping the phone (content-free) for the new ones."""

    def __init__(self, inbox: Inbox, push: NtfyPush | None = None):
        self.inbox, self.push = inbox, push
        self.push_errors: list[str] = []

    def deliver(self, notices: list[dict[str, Any]]) -> int:
        new = [n for n in notices if self.inbox.add(n) is not None]
        if new and self.push is not None:
            try:
                self.push.ping(len(new))
            except NotifyError as e:                    # the inbox already holds them; a failed ping loses nothing
                self.push_errors.append(str(e))
        return len(new)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--list", action="store_true", help="print unread notices (kind, watch, summary)")
    args = ap.parse_args(argv)
    for r in Inbox(args.inbox).unread():
        n = r["notice"]
        print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['at']))}  {n.get('kind')}  {n.get('watch')}  {n.get('summary', n.get('reason', ''))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
