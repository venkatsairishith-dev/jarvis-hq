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


def _all_interfaces(addr) -> bool:
    """True for an address that listens on every interface (0.0.0.0, ::, '', '*', 0, [::])."""
    import ipaddress
    import socket
    a = str(addr or "").strip().strip("[]")
    if a in ("", "*"):
        return True
    try:
        return ipaddress.ip_address(a).is_unspecified
    except ValueError:
        try:
            return socket.inet_aton(a) == b"\0\0\0\0"                 # "0", "0.0" and other short spellings
        except OSError:
            return False


def main(argv=None):
    p = argparse.ArgumentParser(prog="jarvis")
    p.add_argument("--data", help="data dir (default ~/.jarvis-kernel or $JARVIS_DATA)")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="classify an action (does not run it)")
    c.add_argument("tool"); c.add_argument("op"); c.add_argument("target", nargs="?", default="")
    c.add_argument("--untrusted", action="store_true")
    ap = sub.add_parser("approve", help="approve a pending request (interactive only)")
    ap.add_argument("id")
    rt = sub.add_parser("retention", help="bounded retention of private text in the kernel's stores (retention.py): show, change or run it")
    rt.add_argument("--show", action="store_true"); rt.add_argument("--run", action="store_true", help="apply the policy now")
    rt.add_argument("--dry-run", action="store_true", help="count what --run WOULD change, then change nothing")
    rt.add_argument("--off", action="store_true", help="turn retention off (nothing is removed)"); rt.add_argument("--on", action="store_true")
    rt.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="e.g. tasks_days=60, approvals_grace_hours=48")
    sub.add_parser("kill"); sub.add_parser("resume"); sub.add_parser("status")
    sv = sub.add_parser("serve", help="run the node endpoint"); sv.add_argument("--bind", required=True); sv.add_argument("--port", type=int, default=8765)
    tg = sub.add_parser("telegram", help="run the Telegram bridge"); tg.add_argument("--providers", default=None)
    sub.add_parser("pair-code", help="make a one-time node pairing code")
    lp = sub.add_parser("llm-proxy", help="OpenAI-compatible model endpoint for agents (127.0.0.1, plus any --bind address)")
    lp.add_argument("--port", type=int, default=8790); lp.add_argument("--providers", default=None)
    lp.add_argument("--bind", action="append", default=[], help="extra listen address, e.g. the Tailscale IP")
    at = sub.add_parser("agent-token", help="issue a model-endpoint token for an agent and write it to a file (never printed)")
    at.add_argument("name"); at.add_argument("--out", required=True)
    at.add_argument("--lane", choices=["private", "public", "dashboard", "runner", "owner"], default="private")
    sub.add_parser("set-approval-pin", help="set the PIN the owner's console needs to approve a YELLOW action over HTTP (console only)")
    sub.add_parser("nodes", help="list nodes (no state)")
    an = sub.add_parser("analyze", help="report on a node from its collected state"); an.add_argument("node", nargs="?")
    a = sub.add_parser("audit", help="verify the audit chain (against --anchor-file, else the newest stored anchor) or print its head")
    a.add_argument("what", choices=["verify", "anchor"])
    a.add_argument("--anchor-file")
    tr = sub.add_parser("trace", help="redacted trace of one task (read-only)"); tr.add_argument("task")
    tr.add_argument("--public", action="store_true", help="drop all free text"); tr.add_argument("--save", help="folder to write <task>.json")
    ta = sub.add_parser("trace-analyze", help="recurring failures and suggestions over recent tasks (read-only)")
    ta.add_argument("--days", type=float, default=7); ta.add_argument("--public", action="store_true")
    args = p.parse_args(argv)
    if args.cmd in ("trace", "trace-analyze"):     # own read-only connection: never opens the kernel's writer
        from . import traces
        db = traces.open_readonly(args.data)
        try:
            if args.cmd == "trace":
                t = traces.build(db, args.task, public=args.public)
                if not t:
                    print("no such task"); return 2
                print(f"saved {traces.save(t, args.save)}" if args.save else json.dumps(t, indent=1))
            else:
                print(json.dumps(traces.analyze(db, args.days, public=args.public), indent=1))
            return 0
        finally:
            db.close()
    k = Kernel(args.data)
    try:
        return _run(args, k)
    finally:
        k.db.close()                     # a caller that runs main() in-process (tests, tools) must not keep state.db open


def _run(args, k):
    if args.cmd == "check":
        d, aid = k.request(Action(args.tool, args.op, args.target, untrusted_session=args.untrusted))
        print(json.dumps({"level": d.name, "reason": d.reason, "approval_id": aid}))
    elif args.cmd == "retention":
        from . import retention as R
        changes = {}
        for kv in args.set:
            key, _, val = kv.partition("=")
            changes[key.strip()] = (val.strip().lower() == "true") if isinstance(R.DEFAULTS.get(key.strip()), bool) else (int(val) if val.strip().isdigit() else val)
        if args.off:
            changes["enabled"] = False
        if args.on:
            changes["enabled"] = True
        try:
            cfg = R.save_settings(k.db, changes) if changes else R.settings(k.db)
        except ValueError as e:
            print("refused:", e, file=sys.stderr); return 1
        out = {"settings": cfg}
        if args.dry_run:
            out["dry_run"] = R.Retention(k).sweep("cli", dry_run=True)
        if args.run:
            out["run"] = R.Retention(k).sweep("cli")
        print(json.dumps(out, indent=1))
    elif args.cmd == "approve":
        info = k.approval_info(args.id)
        if not info or info["state"] != "pending":
            print("refused (unknown, used or expired)", file=sys.stderr); return 1
        if k.killed():
            print("refused: the kill switch is on (lift it first; denying still works)", file=sys.stderr); return 1
        if info.get("incomplete") or info.get("blocked"):
            print("refused: this card is cut off or has invisible/control characters, so it cannot be approved; deny it", file=sys.stderr); return 1
        typed = _ask_console(
            f"\nApprove {info['level']} action? ({info['seconds_left']} s left)\nRequested by: {json.dumps(info.get('requester') or 'the owner')}\n"
            f"{json.dumps(info['action'], indent=2)}\n"
            f"Type the id again to approve {args.id}: "
        )
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
        typed = _ask_console("Type RESUME to lift the kill switch: ")
        if typed is None:
            print("refused: lifting the kill switch needs a real interactive terminal", file=sys.stderr); return 3
        if typed.strip() != "RESUME":
            return 1
        k.resume(); print("kill switch off")
    elif args.cmd == "status":
        print(json.dumps({"killed": k.killed(), "audit_head": k.audit.anchor()}))
    elif args.cmd == "serve":
        from .nodes import serve
        if _all_interfaces(args.bind):
            print("refused: bind to one private address (the Tailscale IP), not all interfaces", file=sys.stderr)
            return 3
        serve(k, args.bind, args.port)
    elif args.cmd == "telegram":
        import os
        from . import gateway, telegram
        cfg = args.providers or os.path.join(os.path.dirname(__file__), "..", "..", "config", "providers.json")
        if telegram.disabled():
            k.audit.append("tg_disabled", {"why": "config/telegram.off"})
            print("Telegram is switched off (config/telegram.off); the bridge does not run.")
            k.db.close()
            return 0
        telegram.from_files(k, gateway.load(cfg)).run()
    elif args.cmd == "llm-proxy":
        import os
        if any(_all_interfaces(b) for b in args.bind):        # audit SEC-D10: the same rule as `serve`
            print("refused: --bind takes one private address (the Tailscale IP), not all interfaces", file=sys.stderr)
            return 3
        from . import gateway, llm_proxy
        cfg = args.providers or os.path.join(os.path.dirname(__file__), "..", "..", "config", "providers.json")
        llm_proxy.serve(k, gateway.load(cfg), args.port, binds=["127.0.0.1"] + args.bind)
    elif args.cmd == "agent-token":
        import os
        from .llm_proxy import AgentTokens
        tok = AgentTokens(k).issue(args.name, args.lane)
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        if os.name != "nt":
            os.fchmod(fd, 0o600)                 # O_CREAT's mode is ignored for a file that already exists: tighten it first
        with os.fdopen(fd, "w") as fh:
            fh.write(tok)
        print("token for", args.name, "written to", args.out)
    elif args.cmd == "set-approval-pin":
        import getpass
        from .owner import Owner, OwnerError
        from .tasks import Tasks
        from .jobs import Jobs
        if not sys.stdin.isatty():
            print("refused: the PIN is typed at a real terminal", file=sys.stderr); return 3
        a, b = getpass.getpass("New approval PIN (6-64 characters): "), getpass.getpass("Again: ")
        if a != b:
            print("the two entries differ", file=sys.stderr); return 1
        try:
            Owner(k, Tasks(k), Jobs(k)).set_pin(a)
        except OwnerError as e:
            print("refused:", e, file=sys.stderr); return 1
        print("approval PIN set")
    elif args.cmd == "pair-code":
        from .nodes import Nodes
        print(Nodes(k).new_code())
    elif args.cmd == "nodes":
        from .nodes import Nodes
        for n in Nodes(k).list():
            print(f"{n['name']}: {'online' if n['online'] else 'offline'}, last report {n['seconds_since_report']} s ago")
    elif args.cmd == "analyze":
        from .nodes import Nodes
        from .analyze import build, unmatched_numbers
        d = k.authorize(Action("state", "read", args.node or "any-node"))
        if not d.allowed:
            print("refused:", d.reason); return 3
        ns = [n for n in Nodes(k).list() if not args.node or n["name"] == args.node]
        if not ns:
            print("no node has reported yet"); return 1
        for n in ns:
            text = build(n)
            bad = unmatched_numbers(text, n) if n["state"] else []
            print(text)
            print(f"[check] numbers not found in collected state: {bad or 'none'}")
            k.audit.append("analyze", {"node": n["name"], "unmatched_numbers": bad})
    elif args.cmd == "audit":
        if args.what == "anchor":
            out = json.dumps(k.audit.anchor())
            if args.anchor_file:
                open(args.anchor_file, "w").write(out)
            print(out)
        else:
            anchor = None
            if args.anchor_file:
                try:
                    anchor = json.load(open(args.anchor_file))
                except (OSError, ValueError):
                    print("refused: anchor file unreadable or not JSON", file=sys.stderr); return 2
            else:                                   # the newest anchor the kernel stored outside state.db (audit SEC-D8)
                from .audit import ANCHOR_FILE, latest_anchor
                anchor, _ = latest_anchor(k.dir / ANCHOR_FILE)
            ok, msg = k.audit.verify(anchor)
            if anchor is None:
                msg += " (no anchor yet: a cut-off tail cannot be detected)"
            elif not args.anchor_file:
                msg += f" (checked against the stored anchor at seq {anchor['seq']})"
            print(msg)
            return 0 if ok else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
