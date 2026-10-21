"""The console's only way to the kernel: the owner plane, with the owner token read from its file on every call.

The token is never returned to the browser, logged or put in an error message. A missing token, an unreachable server
or a refused request each become a clear, typed error the UI can show.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import loopback_net  # noqa: E402  loopback only, no proxy, no redirect (completion H)

PREFIX = "/v1/jarvis/owner/"


class KernelError(Exception):
    def __init__(self, msg, code=502, body=None):
        super().__init__(msg)
        self.code, self.body = code, body


class Kernel:
    def __init__(self, cfg):
        self.cfg = cfg

    def _open(self, req, timeout):
        """Loopback rules (no proxy, no redirect, no credentials in the address) plus exactly the configured kernel host, nothing else."""
        host = (urllib.parse.urlsplit(self.cfg.kernel_url).hostname or "").lower()
        return loopback_net.urlopen(req, timeout=timeout, allow_hosts=(host,) if host else ())

    def _token(self):
        try:
            with open(self.cfg.token_file, encoding="utf-8-sig") as fh:
                t = fh.read().strip()
        except OSError:
            raise KernelError("no owner token on this computer: the kernel owner plane has not been paired yet", 503)
        if not t:
            raise KernelError("the owner token file is empty", 503)
        return t

    def request(self, method, name, body=None, timeout=20):
        """(status, json) for /v1/jarvis/owner/<name>. Raises KernelError only when the kernel cannot be reached."""
        url = self.cfg.kernel_url + PREFIX + name.lstrip("/")
        data = json.dumps(body if body is not None else {}).encode() if method == "POST" else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": "Bearer " + self._token(), "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with self._open(req, timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b"{}")
            except ValueError:
                payload = {"error": f"kernel answered HTTP {e.code}"}
            if e.code in (401, 403):
                payload = {"error": "the kernel refused the owner token (rotate it at the kernel console)"} if e.code == 401 else payload
            return e.code, payload
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise KernelError(f"cannot reach the kernel at {self.cfg.kernel_url}: {type(e).__name__}", 502)
        finally:
            self.last_ms = round((time.time() - t0) * 1000)

    def stream(self, since: int, timeout=330):
        """An open urllib response of the event stream (the caller reads lines and closes it)."""
        req = urllib.request.Request(self.cfg.kernel_url + PREFIX + f"stream?since={int(since)}",
                                     headers={"Authorization": "Bearer " + self._token(), "Accept": "text/event-stream"})
        try:
            return self._open(req, timeout)
        except urllib.error.HTTPError as e:
            raise KernelError(f"the kernel refused the event stream (HTTP {e.code})", e.code)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise KernelError(f"cannot reach the kernel: {type(e).__name__}", 502)
