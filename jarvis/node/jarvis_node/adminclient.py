"""Ask the elevated admin service (adminsvc.py) to run one approved admin verb. Runs as the owner, not elevated.

    python -m jarvis_node.adminclient list
    python -m jarvis_node.adminclient run winget_install id=Git.Git
    python -m jarvis_node.adminclient run service_restart name=Spooler

`run` shows exactly what will happen, asks the owner to type the verb name, then asks for the admin passphrase with the terminal's
hidden prompt. It refuses when input is not a terminal, so a script or an agent cannot approve: the approval IS the passphrase,
and the service checks it against a key only an administrator can read. Values that came from web or tool content must be shown
and confirmed one by one with --tainted NAME (the card prints the value verbatim). The answer is read from the service's outbox;
"verified" means the read-only check after the command confirmed the result.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

from jarvis_node import adminctl as A
from jarvis_node import adminsvc as S

HEARTBEAT_MAX_AGE_S = 15


def service_alive(root: Path, clock=time.time) -> bool:
    try:
        return clock() - json.loads((root / "heartbeat.json").read_text(encoding="utf-8"))["at"] <= HEARTBEAT_MAX_AGE_S
    except (OSError, ValueError, KeyError):
        return False


def build_request(root: Path, verb: str, params: dict, passphrase: str, tainted=(), seen=(), ttl: int = 300, clock=time.time) -> dict:
    pub = json.loads((root / "public.json").read_text(encoding="utf-8"))
    key = S.derive_key(passphrase, pub["salt"], pub.get("kdf"))
    rid, expires = secrets.token_urlsafe(12), round(clock() + min(ttl, S.MAX_VALID_S), 3)
    msg = S.signed_message(rid, verb, params, expires, list(tainted), list(seen))
    return {"id": rid, "verb": verb, "params": params, "expires": expires, "tainted": list(tainted), "seen": list(seen),
            "proof": S.proof_of(key, msg)}


def submit(root: Path, request: dict) -> None:
    inbox = root / "inbox"
    tmp = inbox / f"{request['id']}.tmp"                     # the service reads only *.json, so it never sees a half-written file
    tmp.write_text(json.dumps(request), encoding="utf-8")
    os.replace(tmp, inbox / f"{request['id']}.json")


def wait_for(root: Path, rid: str, timeout_s: float = 600.0, clock=time.time, sleep=time.sleep) -> dict | None:
    path, end = root / "outbox" / f"{rid}.json", clock() + timeout_s
    while clock() < end:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        sleep(1)
    return None


def parse_values(pairs: list[str]) -> dict:
    out = {}
    for p in pairs:
        k, sep, v = p.partition("=")
        if not sep or not k:
            raise ValueError(f"write values as name=value, not {p!r}")
        out[k] = int(v) if re.fullmatch(r"-?(0|[1-9][0-9]*)", v) else v
    return out


def main(argv=None) -> int:
    import argparse
    import getpass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["list", "run"])
    ap.add_argument("verb", nargs="?")
    ap.add_argument("values", nargs="*")
    ap.add_argument("--tainted", action="append", default=[], help="a value that came from web or tool content (shown verbatim, confirmed)")
    args = ap.parse_args(argv)
    root = S.admin_dir()
    try:
        cat = A.load_catalogue(root / "catalogue.json")
    except (OSError, ValueError, KeyError):
        print("the admin service is not installed on this PC (run Install-JARVIS.ps1)", file=sys.stderr)
        return 2
    if args.cmd == "list":
        for name, spec in cat.items():
            print(f"{'approved' if spec.get('approved') is True else 'off':9} {name:26} {spec.get('summary', '')}")
        return 0
    if not args.verb or args.verb not in cat:
        print("unknown verb; see: list", file=sys.stderr)
        return 2
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("approval needs a person at a terminal: a script or an agent cannot approve", file=sys.stderr)
        return 2
    if not service_alive(root):
        print("the admin service is not running (task JarvisAdmin)", file=sys.stderr)
        return 2
    try:
        params = parse_values(args.values)
        A.render(args.verb, cat[args.verb], params)                 # same validation the service will do, before the owner is asked
    except (ValueError, A.AdminRefused) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print("\nJARVIS asks to run, as administrator:\n  " + A.describe(args.verb, cat[args.verb], params))
    for t in args.tainted:
        print(f"  value of {t} (from web or tool content), exactly: {params.get(t)!r}")
    if input(f"Type {args.verb} to approve, anything else cancels: ").strip() != args.verb:
        print("cancelled")
        return 1
    req = build_request(root, args.verb, params, getpass.getpass("Admin passphrase: "), args.tainted, args.tainted)
    submit(root, req)
    res = wait_for(root, req["id"])
    if res is None:
        print("no answer from the admin service yet; look in", root / "outbox")
        return 1
    print(f"{res.get('status')}: {res.get('said', '')} {res.get('reason', '')}".strip())
    return 0 if res.get("status") in ("verified", "done_unchecked") else 1


if __name__ == "__main__":
    sys.exit(main())
