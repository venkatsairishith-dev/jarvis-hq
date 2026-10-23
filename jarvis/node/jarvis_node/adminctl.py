"""Admin broker core (plan item 1, P4 step 5): the only way JARVIS runs anything as administrator on the owner's PC.

"Full PC rights" is delivered as a catalogue of typed admin verbs (jarvis/config/admin_verbs.json), not a raw admin shell. Each
verb is a fixed program and argument list with validated values in fixed positions. The broker refuses, independently of the
kernel and in this order:
  1. a verb the owner has not approved in the catalogue (every shipped verb starts unapproved);
  2. a value that fails its type (winget id, service name, plain text, choice, bounded number, folder under a listed root);
  3. a protected service (Defender, firewall, security centre, event log, ...) for any service verb;
  4. a rendered command matching the hard-DENY list (Defender, UAC or firewall off, boot settings, new users or admins,
     credential stores, shadow-copy or log deletion, disk wiping), even if someone adds such a verb to the catalogue;
  5. a request whose digest is not the one the owner approved, that has expired, or that was already used (one-time ids);
  6. a value that came from web or tool content which the owner has not seen verbatim on the approval card.
Then: a restore point first when the verb asks for one, the command (no shell, a timeout), the read-only check, and a
transcript line for the audit log. "Done" is reported only when the check confirms it.

The approval itself (RED: the owner's PIN at the kernel; a second confirmation in the plan) and the elevated process that
imports this module (a Windows service or JEA endpoint started by the installer) are outside this file; the runner is injected.

    python -m jarvis_node.adminctl --list          the catalogue and what is approved
"""
from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

CATALOGUE = Path(__file__).resolve().parents[2] / "config" / "admin_verbs.json"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{1,80}$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
TEXT_RE = re.compile(r"^[A-Za-z0-9 .,_()-]{1,200}$")
PROTECTED_SERVICES = {s.lower() for s in (
    "WinDefend", "WdNisSvc", "WdFilter", "WdBoot", "WdNisDrv", "Sense", "SecurityHealthService", "wscsvc", "mpssvc", "BFE",
    "EventLog", "SamSs", "LSM", "VaultSvc", "KeyIso", "CryptSvc", "TrustedInstaller", "wuauserv", "UsoSvc", "BITS", "AppIDSvc",
    "Netlogon", "gpsvc", "RpcSs", "DcomLaunch")}
HARD_DENY = re.compile(
    r"(?i)Set-MpPreference|DisableRealtime|DisableAntiSpyware|DisableBehavior|bcdedit|bootcfg|EnableLUA|ConsentPromptBehavior"
    r"|advfirewall\s+set|Set-NetFirewallProfile|firewall\s+set\s+opmode|\bnet(\.exe)?\s+(user|localgroup)\b|New-LocalUser"
    r"|Add-LocalGroupMember|\\SAM\b|\\SECURITY\b|\bLSA\b|lsass|mimikatz|vssadmin\s+delete|wmic\s+shadowcopy|wevtutil\s+cl"
    r"|Clear-EventLog|cipher\s+/w|\bformat(\.com)?\s+[a-z]:|diskpart|Remove-Item\s.*-Recurse|reg(\.exe)?\s+(add|delete)"
    r"|Invoke-Expression|\biex\b|DownloadString|Start-BitsTransfer|certutil\s.*-urlcache|-EncodedCommand")
POWERSHELL = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
MAX_OUTPUT = 4000
Runner = Callable[[list[str], int], tuple[int, str]]               # (argv, timeout_s) -> (exit code, output)


class AdminRefused(RuntimeError):
    pass


@dataclass
class AdminRequest:
    request_id: str
    verb: str
    params: dict[str, Any]
    approved_digest: str               # the digest on the approval card the owner confirmed
    expires: float
    tainted: list[str] = field(default_factory=list)          # params whose value came from web or tool content
    seen_verbatim: list[str] = field(default_factory=list)    # tainted params the owner saw verbatim on the card


def digest(verb: str, params: dict) -> str:
    """What an approval binds: the verb and its exact values."""
    return hashlib.sha256(json.dumps({"verb": verb, "params": params}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_catalogue(path: Path = CATALOGUE) -> dict[str, dict]:
    verbs = json.loads(path.read_text(encoding="utf-8"))["verbs"]
    for name, v in verbs.items():                 # a dangerous template is refused when the catalogue loads, not only when run
        for argv in (v.get("argv", []), v.get("check", [])):
            if HARD_DENY.search(" ".join(argv)):
                raise AdminRefused(f"catalogue verb {name} matches the hard-deny list")
    return verbs


def _check_value(name: str, spec: dict, value: Any, verb: str) -> str:
    kind = spec.get("type")
    if kind == "id" and isinstance(value, str) and ID_RE.match(value):
        return value
    if kind == "service" and isinstance(value, str) and SERVICE_RE.match(value):
        if value.lower() in PROTECTED_SERVICES:
            raise AdminRefused(f"{value} is a protected Windows service: JARVIS never stops, restarts or reconfigures it")
        return value
    if kind == "text" and isinstance(value, str) and TEXT_RE.match(value) and len(value) <= int(spec.get("max", 200)):
        return value
    if kind == "choice" and isinstance(value, str) and value in spec.get("values", {}):
        return str(spec["values"][value])
    if kind == "int" and isinstance(value, int) and not isinstance(value, bool) and spec.get("min", 0) <= value <= spec.get("max", 0):
        return str(value)
    if kind == "path" and isinstance(value, str):
        p = ntpath.normpath(value.strip())
        roots = [ntpath.normpath(r) for r in spec.get("roots", [])]
        if (not re.search(r"[\"'`$;&|<>%*?\r\n]", p) and ntpath.isabs(p) and ".." not in p.split("\\")
                and any(p.lower() == r.lower() or p.lower().startswith(r.lower().rstrip("\\") + "\\") for r in roots)):
            return p
        raise AdminRefused(f"{verb}: {name} must be a folder under {', '.join(roots) or 'an allowed root'}")
    raise AdminRefused(f"{verb}: {name} is not a valid {kind}")


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render(verb: str, spec: dict, params: dict) -> tuple[list[str], list[str] | None, str | None]:
    """(command, check command or None, check pattern or None) with every value validated and placed."""
    expected = set(spec.get("params", {}))
    if set(params) != expected:
        raise AdminRefused(f"{verb} takes exactly: {', '.join(sorted(expected)) or 'no values'}")
    values = {k: _check_value(k, spec["params"][k], params[k], verb) for k in expected}

    def fill(argv: list[str]) -> list[str]:
        ps = bool(argv) and os.path.basename(argv[0]).lower() in POWERSHELL
        out = []
        for tok in argv:
            m = re.fullmatch(r"\{(\w+)\}", tok)
            if m:
                v = values[m.group(1)]
                out.append(_ps_literal(v) if ps else v)       # PowerShell joins -Command arguments into script text
            elif "{" in tok:
                raise AdminRefused(f"{verb}: a value may only be a whole argument")
            else:
                out.append(tok)
        return out

    cmd = fill(spec["argv"])
    check = fill(spec["check"]) if spec.get("check") else None
    expect = None
    if spec.get("check_expect"):
        expect = re.sub(r"\{(\w+)\}", lambda m: re.escape(values[m.group(1)]), spec["check_expect"])
    for argv in [cmd] + ([check] if check else []):
        if HARD_DENY.search(" ".join(argv)):
            raise AdminRefused(f"{verb}: the command matches the hard-deny list")
    return cmd, check, expect


def describe(verb: str, spec: dict, params: dict) -> str:
    """The plain-language line for the approval card (values verbatim)."""
    text = spec.get("summary", verb)
    for k, v in params.items():
        text = text.replace("{" + k + "}", str(v))
    extras = []
    if spec.get("restore_point"):
        extras.append("a restore point is created first")
    if spec.get("undo"):
        extras.append(f"can be undone with {spec['undo']}")
    return text + (f" ({'; '.join(extras)})" if extras else "")


def default_runner(argv: list[str], timeout_s: int) -> tuple[int, str]:
    if os.name != "nt":
        raise AdminRefused("the admin broker runs only on Windows")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, creationflags=flags, shell=False)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class Broker:
    def __init__(self, ledger: str | Path, catalogue: dict[str, dict] | None = None, runner: Runner = default_runner,
                 clock: Callable[[], float] = time.time, transcript: str | Path | None = None):
        self.catalogue = catalogue if catalogue is not None else load_catalogue()
        self.ledger, self.runner, self.clock = Path(ledger), runner, clock
        self.transcript = Path(transcript) if transcript else self.ledger.with_name("admin_transcript.jsonl")

    def _used(self) -> set[str]:
        try:
            return {json.loads(x)["request_id"] for x in self.ledger.read_text(encoding="utf-8").splitlines() if x.strip()}
        except (OSError, ValueError, KeyError):
            return set()

    def _record(self, path: Path, row: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def validate(self, req: AdminRequest) -> tuple[dict, list[str], list[str] | None, str | None]:
        spec = self.catalogue.get(req.verb)
        if spec is None:
            raise AdminRefused(f"{req.verb} is not an admin verb JARVIS has (no raw admin shell)")
        if spec.get("approved") is not True:
            raise AdminRefused(f"{req.verb} is not approved by the owner in the admin catalogue")
        cmd, check, expect = render(req.verb, spec, req.params)
        if digest(req.verb, req.params) != req.approved_digest:
            raise AdminRefused("these are not the values the owner approved")
        if self.clock() > req.expires:
            raise AdminRefused("the approval has expired; approve it again")
        unseen = [p for p in req.tainted if p in req.params and p not in req.seen_verbatim]
        if unseen:
            raise AdminRefused(f"{', '.join(unseen)} came from web or tool content and the owner has not seen it verbatim")
        if req.request_id in self._used():
            raise AdminRefused("this approval was already used")
        return spec, cmd, check, expect

    def run(self, req: AdminRequest) -> dict:
        spec, cmd, check, expect = self.validate(req)
        self._record(self.ledger, {"request_id": req.request_id, "at": self.clock(), "verb": req.verb})   # one-time, before acting
        steps, started = [], self.clock()

        def step(name: str, argv: list[str], timeout: int) -> tuple[int, str]:
            try:
                rc, out = self.runner(argv, timeout)
            except subprocess.TimeoutExpired:
                rc, out = -1, "timed out"
            except AdminRefused:
                raise
            except Exception as e:                       # the kind only: output could hold anything
                rc, out = -2, type(e).__name__
            steps.append({"step": name, "argv": argv, "exit": rc, "output": str(out)[-MAX_OUTPUT:]})
            return rc, str(out)

        result = {"request_id": req.request_id, "verb": req.verb, "params": req.params, "said": describe(req.verb, spec, req.params)}
        if spec.get("restore_point"):
            rp = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Checkpoint-Computer", "-Description",
                  _ps_literal(f"JARVIS before {req.verb}"), "-RestorePointType", "MODIFY_SETTINGS"]
            rc, _ = step("restore_point", rp, 600)
            if rc != 0:                                  # no restore point, no change
                result.update(status="refused", reason="the restore point could not be created, so nothing was changed")
                return self._finish(result, steps, started)
        rc, _ = step("command", cmd, int(spec.get("timeout_s", 300)))
        if rc != 0:
            result.update(status="failed", reason=f"{req.verb} exited with {rc}")
            return self._finish(result, steps, started)
        if check:
            crc, out = step("check", check, 120)
            ok = crc == 0 and (expect is None or re.search(expect, out, re.I) is not None)
            result.update(status="verified" if ok else "unverified",
                          reason="" if ok else "the command ran but the check did not confirm the result")
        else:
            result.update(status="done_unchecked", reason="this verb has no read-only check; say what ran, not that it worked")
        return self._finish(result, steps, started)

    def _finish(self, result: dict, steps: list[dict], started: float) -> dict:
        row = {**result, "steps": steps, "seconds": round(self.clock() - started, 1), "at": self.clock()}
        self._record(self.transcript, row)
        return row


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    if args.list:
        for name, spec in load_catalogue().items():
            print(f"{'APPROVED' if spec.get('approved') is True else 'not approved':12}  {name:26}  {spec.get('summary', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
