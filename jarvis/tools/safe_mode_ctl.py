"""Safe mode switch for the OWNER at a console (D71): turn the flag file on or off, or say whether safe mode is on.

    python jarvis/tools/safe_mode_ctl.py status          is it on, and why (environment variable, flag file), and what it changes
    python jarvis/tools/safe_mode_ctl.py on              make <data>\\safe_mode.json; JARVIS falls back to its shipped defaults (jarvis/safe_mode.py)
    python jarvis/tools/safe_mode_ctl.py off             remove the file (the environment variable JARVIS_SAFE_MODE, if set, still keeps it on)

`on` and `off` need a real terminal: they ask for a typed "yes" and refuse when there is no keyboard behind them (a script, a pipe, an agent's shell).
Nothing under plugins/, bridge/, node/ or the face imports this file, the model has no tool that runs it, and the file it writes is console-only for
agents (the kernel's SAFETY_COMPONENTS, the push gate's PROTECTED list). Learned data is never deleted by either switch.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from jarvis import safe_mode  # noqa: E402

EFFECTS = (
    "learned lessons and learned site-playbook repairs are ignored (kept on disk, not deleted) and nothing new is learned",
    "the owner's auto-click list is not used: those on-screen buttons wait for the owner",
    "per-site browser grants are not used: a site needs its card and PIN again",
    "the persistent UI Automation worker is off (PowerShell serves)",
    "speed shortcuts that skip a model are off",
    "the local-model preference is off (the provider chain keeps its configured order)",
    "every safety gate stays exactly as it is",
)


def status() -> dict:
    env = os.environ.get("JARVIS_SAFE_MODE", "").strip().lower() in safe_mode._ON
    path = safe_mode.flag_path()
    try:
        file_on = path.exists()
    except OSError:
        file_on = False
    return {"active": env or file_on, "environment_variable": env, "flag_file": file_on, "path": str(path)}


def turn_on(path: Path | None = None, now=time.time) -> Path:
    p = Path(path) if path else safe_mode.flag_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"on": True, "set": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now())), "by": "owner at the console"}, indent=1),
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


def turn_off(path: Path | None = None) -> bool:
    """True when a file was removed."""
    p = Path(path) if path else safe_mode.flag_path()
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False


def main(argv=None, *, is_tty=lambda: sys.stdin.isatty() and sys.stdout.isatty(), ask=input, out=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("status", "on", "off"))
    args = ap.parse_args(argv)
    if args.action == "status":
        s = status()
        out(f"safe mode: {'ON' if s['active'] else 'off'}   (environment variable: {'on' if s['environment_variable'] else 'off'}; flag file: "
            f"{'present' if s['flag_file'] else 'absent'} at {s['path']})")
        if s["active"]:
            for e in EFFECTS:
                out("  - " + e)
        return 0
    if not is_tty():
        out("refused: safe mode is switched by the owner at a keyboard (this is not an interactive terminal)")
        return 2
    answer = ask(f"Safe mode {args.action.upper()}: type yes to confirm: ").strip().lower()
    if answer != "yes":
        out("not changed")
        return 1
    if args.action == "on":
        out(f"safe mode is ON ({turn_on()})")
        for e in EFFECTS:
            out("  - " + e)
    else:
        out("safe mode flag file removed" if turn_off() else "there was no flag file")
        if os.environ.get("JARVIS_SAFE_MODE", "").strip().lower() in safe_mode._ON:
            out("the environment variable JARVIS_SAFE_MODE is still set in this shell and keeps safe mode on for programs started from it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
