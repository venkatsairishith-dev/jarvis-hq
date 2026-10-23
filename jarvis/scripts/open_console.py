"""Open the JARVIS console in the browser with a ONE-TIME launch code. Standard library only.

    python open_console.py                 the console   http://127.0.0.1:8799/
    python open_console.py --print-url     print the URL (it holds a code that works once, for 60 s) instead of opening it

The servers no longer put any credential in `GET /` (re-review M3, 2026-10-19): this launcher reads the server's master token from its
0600 file in the per-user run folder (scripts/control_auth.py), asks it for a one-time code (`POST /auth/code`) and opens
`http://127.0.0.1:PORT/#code=...`. The fragment is never sent to a server; the login page exchanges it for an HttpOnly session
cookie and removes it from the address bar. A program of the same OS user can do the same: this is for the owner's own use.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import control_auth  # noqa: E402
import loopback_net  # noqa: E402  loopback only, no proxy, no redirect (completion H)

SERVERS = {"console": (8799, "/")}


def launch_url(server: str = "console", directory: str | None = None, opener=loopback_net.urlopen) -> str:
    """The URL with a fresh one-time code; raises RuntimeError with a plain reason when the server is not running or refuses."""
    port, path = SERVERS[server]
    name = server
    headers = control_auth.headers_for(name, directory)
    if not headers:
        raise RuntimeError(f"no {name}.token in {directory or control_auth.run_dir()}: is the {name} running (it writes the file at start)?")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/auth/code", data=b"{}", method="POST", headers={**headers, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=10) as r:
            code = json.loads(r.read().decode("utf-8")).get("code")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"the {name} refused the token (HTTP {e.code}): it restarted? Start it again, then retry.") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise RuntimeError(f"the {name} is not answering on port {port}") from None
    if not code:
        raise RuntimeError(f"the {name} gave no launch code")
    return f"http://127.0.0.1:{port}{path}#code={code}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--server", choices=sorted(SERVERS), default="console")
    ap.add_argument("--print-url", action="store_true")
    a = ap.parse_args(argv)
    try:
        url = launch_url(a.server)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    if a.print_url:
        print(url)
    else:
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
