"""`jarvis` command line. Approvals need an interactive terminal, so a script or model cannot approve."""
import argparse
import json
import sys

from .policy import Action, NAMES
from .store import Kernel, Killed


def _ask_console(prompt: str):
    """Read one line from the real console, never from stdin, so pipes and scripts cannot answer.
    Returns None when there is no console."""
    import os
    path = "CONIN$" if os.name == "nt" else "/dev/tty"
    try:
        tty_in = open(path, "r")
        tty_out = open("CONOUT$" if os.name == "nt" else "/dev/tty", "w")
    except OSError:
        return None
    try:
        tty_out.write(prompt)
        tty_out.flush()
        line = tty_in.readline()
    except (OSError, EOFError):
        return None
    finally:
        tty_in.close(); tty_out.close()
    return line or None


def main(argv=None):
    p = argparse.ArgumentParser(prog="jarvis")
    p.add_argument("--data", help="data dir (default ~/.jarvis-kernel or $JARVIS_DATA)")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="classify an action (does not run it)")
    c.add_argument("tool"); c.add_argument("op"); c.add_argument("target", nargs="?", default="")
    c.add_argument("--untrusted", action="store_true")
    ap = sub.add_parser("approve", help="approve a pending request (interactive only)")
    ap.add_argument("id")
    sub.add_parser("kill"); sub.add_parser("resume"); sub.add_parser("status")
    a = sub.add_parser("audit"); a.add_argument("what", choices=["verify", "anchor"])
    a.add_argument("--anchor-file")
    args = p.parse_args(argv)
    k = Kernel(args.data)

    if args.cmd == "check":
        d, aid = k.request(Action(args.tool, args.op, args.target, untrusted_session=args.untrusted))
        print(json.dumps({"level": d.name, "reason": d.reason, "approval_id": aid}))
    elif args.cmd == "approve":
        typed = _ask_console(f"Type the id again to approve {args.id}: ")
        if typed is None:
            print("refused: approvals need a real interactive terminal", file=sys.stderr)
            return 3
        if typed.strip() != args.id:
            return 1
        ok = k.approve(args.id)
        print("approved" if ok else "refused (unknown, used or expired)")
        return 0 if ok else 1
    elif args.cmd == "kill":
        k.kill("cli"); print("kill switch ON")
    elif args.cmd == "resume":
        k.resume(); print("kill switch off")
    elif args.cmd == "status":
        print(json.dumps({"killed": k.killed(), "audit_head": k.audit.anchor()}))
    elif args.cmd == "audit":
        if args.what == "anchor":
            out = json.dumps(k.audit.anchor())
            if args.anchor_file:
                open(args.anchor_file, "w").write(out)
            print(out)
        else:
            anchor = json.load(open(args.anchor_file)) if args.anchor_file else None
            ok, msg = k.audit.verify(anchor)
            print(msg)
            return 0 if ok else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
