"""Laptop node agent: pairs once, then reports outbound to the kernel. Read-only; it never runs commands from the kernel."""
import argparse
import json
import os
import socket
import sys
import time
import urllib.request

from .collector import collect

SECRETS = r"D:\jarvis-secrets"
TOKEN_FILE = os.path.join(SECRETS, "node_token.txt")
DEFAULT_URL = "http://100.125.91.51:8765"  # kernel, reachable only over Tailscale


def _post(url, path, obj, token=None):
    req = urllib.request.Request(url + path, data=json.dumps(obj).encode(), method="POST", headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def pair(url, code_file, name):
    code = open(code_file).read().strip()
    tok = _post(url, "/pair", {"code": code, "name": name})["token"]
    with open(TOKEN_FILE, "w") as f:
        f.write(tok)
    print("paired; token saved to", TOKEN_FILE)  # the token itself is never printed


def run(url, name, once=False, collect_every=60, beat=15):
    token = open(TOKEN_FILE).read().strip()
    state, last_collect = {}, 0.0
    while True:
        try:
            if time.time() - last_collect >= collect_every or not state:
                state, last_collect = collect(), time.time()
            r = _post(url, "/report", {"state": state}, token)
            if r.get("killed"):
                state, last_collect = {}, 0.0  # kill switch on: stop collecting until it is lifted
                print("kernel kill switch is on; paused", file=sys.stderr)
        except Exception as e:
            print("report failed:", type(e).__name__, file=sys.stderr)
        if once:
            return
        time.sleep(beat)


def main():
    p = argparse.ArgumentParser(prog="jarvis-node")
    p.add_argument("cmd", choices=["pair", "run", "once"])
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--name", default=socket.gethostname().lower())
    p.add_argument("--code-file")
    a = p.parse_args()
    if a.cmd == "pair":
        pair(a.url, a.code_file, a.name)
    else:
        run(a.url, a.name, once=(a.cmd == "once"))


if __name__ == "__main__":
    main()
