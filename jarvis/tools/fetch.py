"""Fetch and verify JARVIS's pinned downloads (model weights, binaries, archives) listed in jarvis/tools/jarvis.lock.json.

    python jarvis/tools/fetch.py --list [--machine laptop]
    python jarvis/tools/fetch.py --check [--root DIR] [--machine M] [NAME ...]
    python jarvis/tools/fetch.py --fetch NAME [NAME ...] [--root DIR] [--allow-unpinned]

Windows and Linux, Python 3.11, standard library only. Public downloads only: no tokens, no auth headers.
- The data root is --root, else env JARVIS_DATA_ROOT, else D:\\jarvis (Windows) or /srv/jarvis (Linux) as in
  docs/05_OPERATIONS.md. Each entry's "dest" is a relative path under that root.
- --check hashes the files that are present and reports ok / missing / MISMATCH / unpinned. Exit 1 on any mismatch.
- --fetch downloads to "<dest>.part" (resumed with an HTTP Range request when the server allows it, restarted when it
  does not), checks bytes and sha256, then renames atomically onto dest. It refuses names that are not in the lock,
  refuses entries without a sha256 unless --allow-unpinned is given, and never replaces a file that is already there.
- It never runs, unpacks or marks executable anything it downloads ("unpack_to" in the lock is a note for the human).
Exit codes: 0 ok, 1 verification failed, 2 refused (unknown name, unpinned, bad lock, unsafe URL, no disk space).
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis.lock.json")
DEFAULT_ROOT = {"nt": r"D:\jarvis"}.get(os.name, "/srv/jarvis")
UA = {"User-Agent": "jarvis-fetch/1"}
CHUNK = 1024 * 1024
DISK_MARGIN = 1 << 30          # keep 1 GiB free after a download
OK, REFUSED, BAD = 0, 2, 1


class Refused(Exception):
    pass


def default_root() -> str:
    return os.environ.get("JARVIS_DATA_ROOT") or DEFAULT_ROOT


def load_lock(path: str = LOCK) -> dict:
    with open(path, encoding="utf-8") as f:
        lock = json.load(f)
    for e in lock.get("entries", []):
        safe_dest(e)
    return lock


def safe_dest(e: dict) -> str:
    """The entry's dest as a relative path that cannot leave the root."""
    d = e.get("dest") or ""
    parts = d.replace("\\", "/").split("/")
    if not d or d.startswith(("/", "\\")) or ":" in d or any(p in ("", ".", "..") for p in parts):
        raise Refused(f"{e.get('name')}: unsafe dest {d!r}")
    return os.path.join(*parts)


def _loopback(host: str | None) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def check_url(url: str) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme == "https" or (u.scheme == "http" and _loopback(u.hostname)):
        return
    raise Refused(f"refusing URL {url!r}: only https (or http to this machine) is allowed")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)              # no downgrade to plain http on a redirect (HF and GitHub redirect to CDNs)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def git_blob_sha1_of(path: str) -> str:
    """Git's object id of a file (SHA-1 of "blob <size>\\0" + content): what HF and GitHub publish for small non-LFS files."""
    h = hashlib.sha1(b"blob %d\0" % os.path.getsize(path))
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def verify(path: str, e: dict) -> tuple[str, str]:
    """(status, detail) for a present file: ok, MISMATCH, or unpinned (no sha256 in the lock)."""
    size = os.path.getsize(path)
    if e.get("bytes") is not None and size != e["bytes"]:
        return "MISMATCH", f"size {size}, lock says {e['bytes']}"
    if e.get("sha256"):
        got = sha256_of(path)
        return ("ok", "sha256 ok") if got == e["sha256"] else ("MISMATCH", f"sha256 {got}, lock says {e['sha256']}")
    if e.get("git_blob_sha1"):
        got = git_blob_sha1_of(path)
        if got != e["git_blob_sha1"]:
            return "MISMATCH", f"git blob sha1 {got}, lock says {e['git_blob_sha1']}"
        return "unpinned", "no sha256 upstream; git blob sha1 and size ok"
    return "unpinned", "no sha256 upstream; size ok, content not verifiable"


def select(lock: dict, names: list[str], machine: str | None = None) -> list[dict]:
    by = {e["name"]: e for e in lock["entries"]}
    unknown = [n for n in names if n not in by]
    if unknown:
        raise Refused("not in the lock: " + ", ".join(unknown) + " (see --list)")
    chosen = [by[n] for n in names] if names else list(lock["entries"])
    if machine:
        chosen = [e for e in chosen if machine in e.get("machine", [])]
    return chosen


def cmd_list(lock: dict, machine: str | None) -> int:
    for e in select(lock, [], machine):
        pin = (e["sha256"] or "")[:12] or "UNPINNED"
        opt = " (optional)" if e.get("optional") else ""
        print(f"{e['name']:46s} {e['kind']:7s} {','.join(e['machine']):22s} {e['bytes'] / 1e6:10.1f} MB  {pin}{opt}")
        print(f"{'':46s} -> {e['dest']}")
    for n in lock.get("not_locked", []):
        print(f"(not locked) {n['registry_name']}: {n['unpinned_reason']}")
    return OK


def cmd_check(lock: dict, root: str, names: list[str], machine: str | None) -> int:
    counts = {"ok": 0, "missing": 0, "MISMATCH": 0, "unpinned": 0}
    for e in select(lock, names, machine):
        path = os.path.join(root, safe_dest(e))
        if os.path.isfile(path):
            status, detail = verify(path, e)
        else:
            status, detail = "missing", "optional" if e.get("optional") else "not present"
        counts[status] += 1
        print(f"{status:9s} {e['name']:46s} {detail}", flush=True)
    print(f"\nroot {root}: " + ", ".join(f"{v} {k.lower()}" for k, v in counts.items()))
    return BAD if counts["MISMATCH"] else OK


def _download(url: str, part: str, total: int | None) -> None:
    """Append to part from where it stops (Range); start again when the server ignores the range."""
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if total is not None and have > total:
        os.remove(part)
        have = 0
    if total is not None and have == total:
        return
    headers = dict(UA)
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        r = _OPENER.open(urllib.request.Request(url, headers=headers), timeout=120)
    except urllib.error.HTTPError as ex:
        if ex.code == 416 and have:        # range not satisfiable: start over
            os.remove(part)
            return _download(url, part, total)
        raise
    with r:
        mode = "ab"
        if have and r.status != 206:      # server sent the whole file: restart instead of appending
            have, mode = 0, "wb"
        t0 = last = time.time()
        with open(part, mode) as out:
            while True:
                buf = r.read(CHUNK)
                if not buf:
                    break
                out.write(buf)
                have += len(buf)
                if time.time() - last > 15:
                    last = time.time()
                    rate = have / max(last - t0, 1e-3) / 1e6
                    print(f"    {have / 1e6:.0f}/{(total or 0) / 1e6:.0f} MB ({rate:.1f} MB/s)", flush=True)
            out.flush()
            os.fsync(out.fileno())


def fetch_one(e: dict, root: str, allow_unpinned: bool) -> int:
    name = e["name"]
    if not e.get("sha256") and not allow_unpinned:
        raise Refused(f"{name}: no sha256 in the lock ({e.get('unpinned_reason', 'unpinned')}); "
                      "pass --allow-unpinned to fetch it anyway")
    check_url(e["url"])
    path = os.path.join(root, safe_dest(e))
    if os.path.exists(path):
        status, detail = verify(path, e)
        if status == "MISMATCH":
            print(f"BAD       {name}: {path} is already there and does not match ({detail}); "
                  "it is left untouched: move it away and run again")
            return BAD
        print(f"present   {name}: {detail}")
        return OK
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    need = max((e.get("bytes") or 0) - have, 0)
    free = shutil.disk_usage(os.path.dirname(path)).free
    if need and free < need + DISK_MARGIN:
        raise Refused(f"{name}: {free / 1e9:.1f} GB free, needs {need / 1e9:.1f} GB plus a {DISK_MARGIN / 1e9:.0f} GB margin")
    print(f"fetching  {name} ({(e.get('bytes') or 0) / 1e6:.1f} MB){' resuming at %d' % have if have else ''}", flush=True)
    try:
        _download(e["url"], part, e.get("bytes"))
    except (urllib.error.URLError, OSError) as ex:   # HTTPError is a URLError; the .part stays for a resume
        print(f"BAD       {name}: download failed ({ex}); run again to resume")
        return BAD
    status, detail = verify(part, e)
    if status == "MISMATCH":
        os.remove(part)                    # never keep a bad download, never let it become the final file
        print(f"BAD       {name}: download rejected ({detail}); nothing was installed")
        return BAD
    os.replace(part, path)                 # atomic on the same volume: dest is either absent or complete
    print(f"ok        {name}: {detail} -> {path}")
    return OK


def cmd_fetch(lock: dict, root: str, names: list[str], allow_unpinned: bool) -> int:
    chosen = select(lock, names)           # refuses unknown names before anything is downloaded
    if not allow_unpinned:
        bad = [e["name"] for e in chosen if not e.get("sha256")]
        if bad:
            raise Refused("no sha256 in the lock for: " + ", ".join(bad) + " (pass --allow-unpinned to fetch anyway)")
    rc = OK
    for e in chosen:
        rc = max(rc, fetch_one(e, root, allow_unpinned))
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="list the lock entries")
    g.add_argument("--check", action="store_true", help="verify the files present under the root")
    g.add_argument("--fetch", action="store_true", help="download and verify the named entries")
    ap.add_argument("names", nargs="*", metavar="NAME")
    ap.add_argument("--root", help=f"data root (default: env JARVIS_DATA_ROOT, else {DEFAULT_ROOT})")
    ap.add_argument("--machine", help="only entries for this machine (laptop, linux-test, oracle-vm, wsl-lab, other-profile)")
    ap.add_argument("--allow-unpinned", action="store_true", help="also fetch entries whose sha256 is null")
    ap.add_argument("--lock", default=LOCK, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = a.root or default_root()
    try:
        lock = load_lock(a.lock)
        if a.list:
            return cmd_list(lock, a.machine)
        if a.check:
            return cmd_check(lock, root, a.names, a.machine)
        if not a.names:
            raise Refused("--fetch needs at least one NAME (see --list)")
        return cmd_fetch(lock, root, a.names, a.allow_unpinned)
    except Refused as ex:
        print(f"REFUSED: {ex}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
