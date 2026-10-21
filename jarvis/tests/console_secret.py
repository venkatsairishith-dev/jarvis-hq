"""The JARVIS console refuses every request without a credential (audit SEC-D9; re-review M3, 2026-10-19). A program of the owner's
reads the console's MASTER token from `console.token`, the 0600 file the console writes in the per-user run folder
(jarvis/scripts/control_auth.py), and sends it in X-Jarvis-Console. The console's own page no longer carries any credential.
(A program of the same user can read the file too: it blocks other users, other sites and programs that cannot read it; the PIN stays
the boundary for approvals.) Live test scripts use this to talk to the console like the UI does."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import control_auth  # noqa: E402

CONSOLE = "http://127.0.0.1:8799"


def secret(console: str = CONSOLE) -> str:
    token = control_auth.read("console")
    if not token:
        raise RuntimeError(f"no console.token in {control_auth.run_dir()}: is the JARVIS console running on this machine?")
    return token


def headers(console: str = CONSOLE) -> dict:
    return {"X-Jarvis-Console": secret(console)}
