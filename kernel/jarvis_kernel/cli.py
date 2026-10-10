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
    sv = sub.add_parser("serve", help="run the node endpoint"); sv.add_argument("--bind", required=True); sv.add_argument("--port", type=int, default=8765)
    tg = sub.add_parser("telegram", help="run the Telegram bridge"); tg.add_argument("--providers", default=None)
    sub.add_parser("pair-code", help="make a one-time node pairing code")
    lp = sub.add_parser("llm-proxy", help="OpenAI-compatible model endpoint for agents (127.0.0.1, plus any --bind address)")
    lp.add_argument("--port", type=int, default=8790); lp.add_argument("--providers", default=None)
    lp.add_argument("--bind", action="append", default=[], help="extra listen address, e.g. the Tailscale IP")
    at = sub.add_parser("agent-token", help="issue a model-endpoint token for an agent and write it to a file (never printed)")
    at.add_argument("name"); at.add_argument("--out", required=True)
    at.add_argument("--lane", choices=["private", "public", "dashboard", "runner"], default="private")
    sub.add_parser("nodes", help="list nodes (no state)")
    an = sub.add_parser("analyze", help="report on a node from its collected state"); an.add_argument("node", nargs="?")
    a = sub.add_parser("audit"); a.add_argument("what", choices=["verify", "anchor"])
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

    if args.cmd == "check":
        d, aid = k.request(Action(args.tool, args.op, args.target, untrusted_session=args.untrusted))
        print(json.dumps({"level": d.name, "reason": d.reason, "approval_id": aid}))
    elif args.cmd == "approve":
        info = k.approval_info(args.id)
        if not info or info["state"] != "pending":
            print("refused (unknown, used or expired)", file=sys.stderr); return 1
        typed = _ask_console(
            f"\nApprove {info['level']} action? ({info['seconds_left']} s left)\n{json.dumps(info['action'], indent=2)}\n"
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
        if args.bind in ("0.0.0.0", "::", ""):
            print("refused: bind to one private address (the Tailscale IP), not all interfaces", file=sys.stderr)
            return 3
        serve(k, args.bind, args.port)
    elif args.cmd == "telegram":
        import os
        from . import gateway, telegram
        cfg = args.providers or os.path.join(os.path.dirname(__file__), "..", "..", "config", "providers.json")
        telegram.from_files(k, gateway.load(cfg)).run()
    elif args.cmd == "llm-proxy":
        import os
        from . import gateway, llm_proxy
        cfg = args.providers or os.path.join(os.path.dirname(__file__), "..", "..", "config", "providers.json")
        llm_proxy.serve(k, gateway.load(cfg), args.port, binds=["127.0.0.1"] + args.bind)
    elif args.cmd == "agent-token":
        import os
        from .llm_proxy import AgentTokens
        tok = AgentTokens(k).issue(args.name, args.lane)
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(tok)
        print("token for", args.name, "written to", args.out)
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
        from .policy import Action
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
            ok, msg = k.audit.verify(anchor)
            print(msg)
            return 0 if ok else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
