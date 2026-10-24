"""The elevated half of the admin broker (plan item 1, P4 step 5): the process that runs as administrator on the owner's PC.

"A second admin" is delivered as a small elevated service that can do only what the owner approved, one typed verb at a time
(jarvis/config/admin_verbs.json, run by adminctl.Broker), never as an admin shell and never as an unrestricted account. This
module is what the installer registers as the scheduled task "JarvisAdmin" (run level Highest, started at logon).

Trust boundary: everything this process believes lives in one folder that ordinary (not elevated) processes can read but not
change, C:\\ProgramData\\JARVIS\\admin (the installer sets the permissions; the code itself is copied to C:\\Program Files\\JARVIS\\admin
and run by a machine-wide Python with -I, so an agent that can write to D:\\jarvis cannot swap the code for its own):

    catalogue.json   the verbs and which of them the owner approved (only `approve-verbs`, run elevated, changes `approved`)
    private/         verifier.json (the key derived from the owner's admin passphrase) and lock.json: not even readable by
                     ordinary processes
    public.json      the salt for that key (readable: the client needs it)
    ledger.jsonl     request ids already used (one-time approvals)
    transcript.jsonl one line per request: what ran, exit codes, the check that confirmed it
    inbox/           ordinary processes may create files here, nothing else
    outbox/          the service's answers, readable by ordinary processes

A request is a small JSON file in inbox/. It is accepted only if all of these hold, checked here and again by adminctl.Broker:
  - it carries a proof, HMAC-SHA256 under the passphrase-derived key, over (request id, verb, values, expiry, taint lists). The
    passphrase is typed by the owner at a terminal (adminclient.py refuses without one), so a process running as the owner
    but without the passphrase can queue a request and have it refused, never run;
  - the verb is approved in catalogue.json and every value passes its type (adminctl);
  - the expiry is at most ten minutes ahead and the id was never used before;
  - five wrong proofs in fifteen minutes lock the service for fifteen minutes.
The hard-deny list in adminctl (Defender, UAC, firewall, boot, users, credentials, log and shadow-copy deletion, disk wiping,
encoded or downloaded commands) applies on top, even to a verb the owner approved.

    python adminsvc.py run                 the service loop (the scheduled task runs this)
    python adminsvc.py set-passphrase      choose the admin passphrase (elevated, interactive)
    python adminsvc.py approve-verbs       go through the verbs one by one (elevated, interactive)
    python adminsvc.py sync-catalogue      take new or changed verbs from the shipped file; changed verbs become unapproved again
    python adminsvc.py status
Standard library only (it runs under a bare, machine-wide Python).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_node import adminctl as A  # noqa: E402

KDF = {"n": 2 ** 15, "r": 8, "p": 1, "dklen": 32, "maxmem": 2 ** 26}
MIN_PASSPHRASE = 10
MAX_REQUEST_BYTES = 16 * 1024
MAX_VALID_S = 600
LOCK_AFTER, LOCK_WINDOW_S, LOCK_FOR_S = 5, 900, 900
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
KEEP_OUTBOX_S = 86400


def admin_dir() -> Path:
    env = os.environ.get("JARVIS_ADMIN_DIR")
    return Path(env) if env else Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "JARVIS" / "admin"


def derive_key(passphrase: str, salt_hex: str, params: dict | None = None) -> bytes:
    p = {**KDF, **(params or {})}
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=p["n"], r=p["r"], p=p["p"],
                          dklen=p["dklen"], maxmem=p["maxmem"])


def signed_message(request_id: str, verb: str, params: dict, expires: float, tainted: list, seen: list) -> bytes:
    return json.dumps({"id": request_id, "verb": verb, "params": params, "expires": expires, "tainted": sorted(tainted),
                       "seen": sorted(seen)}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def proof_of(key: bytes, msg: bytes) -> str:
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def passphrase_problem(passphrase: str) -> str:
    if len(passphrase) < MIN_PASSPHRASE:
        return f"use at least {MIN_PASSPHRASE} characters (the proof of an approval can be guessed offline by anyone who sees one)"
    if len(set(passphrase)) < 5:
        return "use more different characters"
    return ""


def set_passphrase(root: Path, passphrase: str) -> None:
    problem = passphrase_problem(passphrase)
    if problem:
        raise ValueError(problem)
    salt = os.urandom(16).hex()
    key = derive_key(passphrase, salt)
    _write_atomic(root / "private" / "verifier.json", json.dumps({"key": key.hex()}))
    _write_atomic(root / "public.json", json.dumps({"salt": salt, "kdf": {k: KDF[k] for k in ("n", "r", "p", "dklen")}}))


def sync_catalogue(shipped: dict[str, dict], current: dict[str, dict] | None) -> tuple[dict[str, dict], list[str]]:
    """New catalogue = the shipped verbs; a verb the owner approved keeps its approval only if nothing but the wording changed."""
    out, reset = {}, []
    exec_keys = ("argv", "check", "check_expect", "params", "restore_point", "timeout_s")
    for name, spec in shipped.items():
        new = {**spec, "approved": False}
        old = (current or {}).get(name)
        if old and old.get("approved") is True:
            if all(old.get(k) == spec.get(k) for k in exec_keys):
                new["approved"] = True
            else:
                reset.append(name)
        out[name] = new
    return out, reset


class Service:
    def __init__(self, root: Path | None = None, runner: A.Runner = A.default_runner, clock: Callable[[], float] = time.time):
        self.root = Path(root) if root else admin_dir()
        self.runner, self.clock = runner, clock
        self.inbox, self.outbox = self.root / "inbox", self.root / "outbox"
        for d in (self.inbox, self.outbox):
            d.mkdir(parents=True, exist_ok=True)

    # ---- lockout ---------------------------------------------------------------------------------------------------------
    def _state(self) -> dict:
        try:
            return json.loads((self.root / "private" / "lock.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"fails": [], "until": 0}

    def _fail(self) -> None:
        now, st = self.clock(), self._state()
        st["fails"] = [t for t in st.get("fails", []) if now - t < LOCK_WINDOW_S] + [now]
        if len(st["fails"]) >= LOCK_AFTER:
            st["until"], st["fails"] = now + LOCK_FOR_S, []
        _write_atomic(self.root / "private" / "lock.json", json.dumps(st))

    def locked(self) -> bool:
        return self._state().get("until", 0) > self.clock()

    # ---- one request -----------------------------------------------------------------------------------------------------
    def _answer(self, rid: str, result: dict) -> dict:
        result = {"request_id": rid, **result}
        if ID_RE.match(rid or ""):
            _write_atomic(self.outbox / f"{rid}.json", json.dumps(result, ensure_ascii=False))
        return result

    def process(self, raw: bytes) -> dict:
        try:
            data = json.loads(raw.decode("utf-8"))
            rid, verb, params = data["id"], data["verb"], data["params"]
            expires, proof = float(data["expires"]), str(data["proof"])
            tainted, seen = list(data.get("tainted", [])), list(data.get("seen", []))
            ok_types = (isinstance(rid, str) and isinstance(verb, str) and isinstance(params, dict)
                        and all(isinstance(k, str) and isinstance(v, (str, int)) and not isinstance(v, bool) for k, v in params.items())
                        and all(isinstance(x, str) for x in tainted + seen))
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return self._answer("", {"status": "refused", "reason": "not a valid request"})
        if not ok_types or not ID_RE.match(rid):
            return self._answer("", {"status": "refused", "reason": "not a valid request"})
        if self.locked():
            return self._answer(rid, {"status": "refused", "reason": "locked for a while after wrong passphrases"})
        try:
            key = bytes.fromhex(json.loads((self.root / "private" / "verifier.json").read_text(encoding="utf-8"))["key"])
        except (OSError, ValueError, KeyError):
            return self._answer(rid, {"status": "refused", "reason": "no admin passphrase is set yet (run set-passphrase elevated)"})
        if not hmac.compare_digest(proof_of(key, signed_message(rid, verb, params, expires, tainted, seen)), proof):
            self._fail()
            return self._answer(rid, {"status": "refused", "reason": "wrong passphrase or altered request"})
        if expires > self.clock() + MAX_VALID_S:
            return self._answer(rid, {"status": "refused", "reason": "the approval may be valid for ten minutes at most"})
        try:
            broker = A.Broker(self.root / "ledger.jsonl", A.load_catalogue(self.root / "catalogue.json"), self.runner, self.clock,
                              self.root / "transcript.jsonl")
            result = broker.run(A.AdminRequest(rid, verb, params, A.digest(verb, params), expires, tainted, seen))
        except A.AdminRefused as e:
            result = {"status": "refused", "verb": verb, "reason": str(e)}
        except (OSError, ValueError, KeyError) as e:
            result = {"status": "refused", "verb": verb, "reason": f"the admin catalogue could not be read ({type(e).__name__})"}
        result = {k: v for k, v in result.items() if k != "request_id"}
        return self._answer(rid, result)

    # ---- the inbox -------------------------------------------------------------------------------------------------------
    def poll_once(self) -> int:
        done = 0
        for p in sorted(self.inbox.glob("*.json")):
            try:
                if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_REQUEST_BYTES:
                    raw = b""
                else:
                    raw = p.read_bytes()
            except OSError:
                raw = b""
            try:
                p.unlink()
            except OSError:
                continue
            self.process(raw)
            done += 1
        self._sweep()
        _write_atomic(self.root / "heartbeat.json", json.dumps({"at": self.clock(), "locked": self.locked()}))
        return done

    def _sweep(self) -> None:
        for p in self.outbox.glob("*.json"):
            try:
                if self.clock() - p.stat().st_mtime > KEEP_OUTBOX_S:
                    p.unlink()
            except OSError:
                pass

    def run_forever(self, every: float = 1.0) -> None:
        while True:
            self.poll_once()
            time.sleep(every)


# ---- interactive commands (elevated) ---------------------------------------------------------------------------------------
def is_elevated() -> bool:
    if os.name != "nt":
        return True
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def approve_verbs(root: Path, ask: Callable[[str], str] = input, out: Callable[[str], None] = print) -> dict[str, bool]:
    cat = A.load_catalogue(root / "catalogue.json")
    out("Approve each admin verb JARVIS may run as administrator. Nothing here can turn off Defender, UAC or the firewall, change boot")
    out("settings, create users or touch credentials: those are refused in code even if you say yes. Press Enter to keep the current answer.")
    for name, spec in cat.items():
        now = spec.get("approved") is True
        a = ask(f"  {name:26} {spec.get('summary', '')}  [{'Y/n' if now else 'y/N'}] ").strip().lower()
        if a in ("y", "yes"):
            spec["approved"] = True
        elif a in ("n", "no"):
            spec["approved"] = False
    _write_atomic(root / "catalogue.json", json.dumps({"_note": "Installed copy. Only approve-verbs (elevated) changes `approved`.",
                                                       "verbs": cat}, indent=1))
    return {n: s["approved"] is True for n, s in cat.items()}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["run", "set-passphrase", "approve-verbs", "sync-catalogue", "status"])
    ap.add_argument("--shipped", help="the shipped admin_verbs.json (sync-catalogue)")
    args = ap.parse_args(argv)
    root = admin_dir()
    if args.cmd != "status" and not is_elevated():
        print("run this elevated (as administrator)", file=sys.stderr)
        return 2
    if args.cmd == "run":
        Service(root).run_forever()
    elif args.cmd == "set-passphrase":
        import getpass
        if not sys.stdin.isatty():
            print("the passphrase is typed at a terminal, never piped", file=sys.stderr)
            return 2
        one, two = getpass.getpass("New admin passphrase: "), getpass.getpass("Again: ")
        if one != two:
            print("they differ", file=sys.stderr)
            return 1
        try:
            set_passphrase(root, one)
        except ValueError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 1
        print("admin passphrase set")
    elif args.cmd == "approve-verbs":
        if not sys.stdin.isatty():
            print("verbs are approved at a terminal", file=sys.stderr)
            return 2
        res = approve_verbs(root)
        print(f"{sum(res.values())} of {len(res)} verbs approved")
    elif args.cmd == "sync-catalogue":
        shipped = A.load_catalogue(Path(args.shipped) if args.shipped else A.CATALOGUE)
        try:
            current = A.load_catalogue(root / "catalogue.json")
        except (OSError, ValueError, KeyError):
            current = None
        new, reset = sync_catalogue(shipped, current)
        _write_atomic(root / "catalogue.json", json.dumps({"_note": "Installed copy. Only approve-verbs (elevated) changes `approved`.",
                                                           "verbs": new}, indent=1))
        print(f"{len(new)} verbs; approval dropped for changed verbs: {', '.join(reset) or 'none'}")
    else:
        try:
            cat = A.load_catalogue(root / "catalogue.json")
            n = sum(1 for s in cat.values() if s.get("approved") is True)
            print(f"catalogue : {n} of {len(cat)} verbs approved")
        except (OSError, ValueError, KeyError):
            print("catalogue : not installed")
        print("passphrase:", "set" if (root / "private" / "verifier.json").exists() else "NOT set")
        try:
            hb = json.loads((root / "heartbeat.json").read_text(encoding="utf-8"))
            print(f"service   : last beat {int(time.time() - hb['at'])} s ago" + (" (locked)" if hb.get("locked") else ""))
        except (OSError, ValueError, KeyError):
            print("service   : not running")
    return 0


if __name__ == "__main__":
    sys.exit(main())
