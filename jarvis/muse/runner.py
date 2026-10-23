"""Background runner for page watches (plan P7 step 9): checks due watches, saves after every check, files notices in the inbox and
pings the phone without content.

Durability: the watches file is changed only under a short file lock, re-read before every save and merged by watch id, so a watch the
console adds or deletes while a slow fetch runs is neither lost nor brought back. Notices are delivered before the watch is saved; if the
process dies in between, the re-check produces the same notice and the inbox drops the duplicate. A second runner refuses to start (an
OS lock that the system releases when the process dies, so a crash leaves no stale lock).

Fetching: every URL (and every redirect target) must pass jarvis/egress/ssrf_guard.check_url first; the request then goes through the
egress proxy named in jarvis/config/egress.json, http(s) only, at most MAX_BYTES, text types only, and HTML is reduced to its visible
text. The page text is data for the diff, never instructions.

    python -m jarvis.muse.runner --watches D:\\jarvis\\N.E.K.O-data\\muse\\watches.json --inbox D:\\jarvis\\N.E.K.O-data\\muse\\inbox.jsonl
        [--push-topic-file D:\\jarvis-secrets\\ntfy.topic] [--once]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterator

from jarvis.egress.ssrf_guard import check_url
from jarvis.muse import watch as W
from jarvis.muse.notify import Inbox, Notifier, NtfyPush

MAX_BYTES = 2_000_000
FETCH_TIMEOUT_S = 20
MAX_REDIRECTS = 3
MAX_SLEEP_S = 60
TEXT_TYPES = ("text/", "application/xhtml+xml", "application/json", "application/xml")
EGRESS_CONFIG = Path(__file__).resolve().parents[1] / "config" / "egress.json"


class RunnerBusy(RuntimeError):
    pass


class FetchRefused(RuntimeError):
    pass


# ---- locks ----------------------------------------------------------------------------------------------------------------------------

def _try_lock(f) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(f) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def file_lock(path: str | Path, wait_s: float = 10.0) -> Iterator[None]:
    """Short exclusive lock around a read-modify-write of the watches file (the console uses the same lock)."""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as f:
        deadline = time.monotonic() + wait_s
        while not _try_lock(f):
            if time.monotonic() > deadline:
                raise RunnerBusy("watches file is locked by another writer")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(f)


def update_watches(path: str | Path, change: Callable[[list[W.Watch]], list[W.Watch]]) -> list[W.Watch]:
    """Read, change and atomically save the watches file under the lock. For the console: add, delete, resume a watch."""
    with file_lock(path):
        watches = change(W.load(path))
        W.save(path, watches)
        return watches


# ---- fetching -------------------------------------------------------------------------------------------------------------------------

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "ul", "ol", "header",
             "footer", "main", "nav", "aside", "pre", "blockquote", "dd", "dt", "td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    p.close()
    lines = (" ".join(ln.split()) for ln in "".join(p.out).splitlines())
    return "\n".join(ln for ln in lines if ln)


def egress_proxy(config: Path = EGRESS_CONFIG) -> str:
    try:
        proxy = json.loads(config.read_text(encoding="utf-8"))["proxy"]
    except (OSError, ValueError, KeyError):
        raise FetchRefused("no egress proxy configured; watches never fetch directly") from None
    if not str(proxy).startswith("http://"):
        raise FetchRefused("egress proxy must be an http:// address")
    return proxy


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):            # redirects are followed by hand so each target is checked first
        return None


def make_fetch(proxy: str, check: Callable[[str], Any] = check_url) -> Callable[[str], str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}), _NoRedirect)

    def fetch(url: str) -> str:
        for _ in range(MAX_REDIRECTS + 1):
            verdict = check(url)
            if not verdict.ok:
                raise FetchRefused(verdict.reason)
            req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-watch/1", "Accept": "text/html,text/plain;q=0.9"})
            try:
                resp = opener.open(req, timeout=FETCH_TIMEOUT_S)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                    url = urllib.parse.urljoin(url, e.headers["Location"])
                    continue
                raise
            with resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if not ctype.startswith(TEXT_TYPES):
                    raise FetchRefused("not a text page")
                body = resp.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise FetchRefused("page too large")
                text = body.decode(resp.headers.get_content_charset() or "utf-8", "replace")
                return html_to_text(text) if "html" in ctype else text
        raise FetchRefused("too many redirects")

    return fetch


# ---- runner ---------------------------------------------------------------------------------------------------------------------------

class WatchRunner:
    def __init__(self, watches_path: str | Path, notifier: Notifier, fetch: Callable[[str], str],
                 clock: Callable[[], float] = time.time):
        self.path = Path(watches_path)
        self.notifier, self.fetch, self.clock = notifier, fetch, clock
        self.stop = threading.Event()
        self._singleton = None

    def acquire(self) -> None:
        """Only one runner per watches file; the OS releases the lock if this process dies."""
        lock_path = Path(str(self.path) + ".runner.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(lock_path, "a+")
        if not _try_lock(f):
            f.close()
            raise RunnerBusy("another watch runner is already running")
        self._singleton = f

    def release(self) -> None:
        if self._singleton is not None:
            _unlock(self._singleton)
            self._singleton.close()
            self._singleton = None

    def _store(self, checked: W.Watch, before: dict[str, Any]) -> None:
        def merge(current: list[W.Watch]) -> list[W.Watch]:
            # Deleted meanwhile: stays deleted. Edited meanwhile (paused, new interval): the edit wins and the next pass re-checks.
            return [checked if w.id == checked.id and w.to_dict() == before else w for w in current]
        update_watches(self.path, merge)

    def run_once(self) -> int:
        """Check every due watch once. Returns the number of new notices filed."""
        with file_lock(self.path):
            due = W.Scheduler(W.load(self.path)).due(self.clock())
        new = 0
        for w in due:
            if self.stop.is_set():
                break
            before = json.loads(json.dumps(w.to_dict()))
            notices = W.Scheduler([w]).run_check(w, self.fetch, self.clock())
            new += self.notifier.deliver(notices)
            self._store(w, before)
        return new

    def next_wake(self) -> float:
        active = [w.next_check for w in W.load(self.path) if w.state == "active"]
        return min(active) if active else self.clock() + MAX_SLEEP_S

    def run_forever(self) -> None:
        self.acquire()
        try:
            while not self.stop.is_set():
                self.run_once()
                self.stop.wait(max(1.0, min(MAX_SLEEP_S, self.next_wake() - self.clock())))
        finally:
            self.release()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--watches", required=True)
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--push-topic-file", help="file holding the ntfy topic URL (keep it in the secrets folder)")
    ap.add_argument("--once", action="store_true", help="check due watches once and exit")
    args = ap.parse_args(argv)
    push = NtfyPush(args.push_topic_file) if args.push_topic_file else None
    notifier = Notifier(Inbox(args.inbox), push)
    runner = WatchRunner(args.watches, notifier, make_fetch(egress_proxy()))
    try:
        if args.once:
            runner.acquire()
            try:
                print(f"{runner.run_once()} new notice(s)")
            finally:
                runner.release()
        else:
            runner.run_forever()
    except RunnerBusy as e:
        print(e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    for err in notifier.push_errors:
        print(f"push: {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
