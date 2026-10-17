"""Shared fixtures: no test reaches the internet. Upstream APIs (GitHub, Hugging Face, OSV, nodejs.org) are a fake server on
127.0.0.1, the kernel is the real one in a temp dir, and a guard refuses every connection that is not to this machine."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "jarvis" / "kernel"))


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    real = socket.create_connection

    def guard(address, *a, **k):
        host = address[0]
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise OSError(f"test tried to reach {host}: no internet in tests")
        return real(address, *a, **k)
    monkeypatch.setattr(socket, "create_connection", guard)


class Fake:
    """routes: {(METHOD, path with query): (status, json body)}; every request is recorded as (method, path, body)."""

    def __init__(self):
        self.routes, self.seen = {}, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                fake.seen.append((method, self.path, body))
                route = fake.routes.get((method, self.path))
                if route is None and method == "GET":
                    route = fake.routes.get((method, urllib.parse.urlsplit(self.path).path))
                code, obj = route if route else (404, {"message": "Not Found"})
                if callable(obj):
                    obj = obj(body)
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    @property
    def endpoints(self):
        return {"github": self.url, "hf": self.url, "osv": self.url, "nodejs": self.url}

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.close()


@pytest.fixture
def http(tmp_path):
    from jarvis.caretaker.detectors import Http
    return Http(cache_path=str(tmp_path / "cache.json"), intervals={"127.0.0.1": 0.0, "default": 0.0})


SHA_A = "a" * 64
SHA_B = "b" * 64


def gh_lock(tag="v1.0", sha=SHA_A, source="github-release"):
    return {"entries": [{"name": "tool-x-linux", "registry_name": "Tool X", "kind": "archive", "machine": ["linux-test"],
                         "url": f"https://github.com/o/toolx/releases/download/{tag}/x.tar.gz",
                         "revision": {"source": source, "repo": "o/toolx", "tag": tag, "asset": "x.tar.gz"},
                         "sha256": sha, "bytes": 10, "dest": "downloads/x.tar.gz", "optional": False}]}


def hf_lock(commit="1" * 40, sha=SHA_A):
    return {"entries": [{"name": "model-y", "registry_name": "Model Y GGUF", "kind": "model", "machine": ["laptop"],
                         "url": f"https://huggingface.co/org/y-GGUF/resolve/{commit}/y.gguf",
                         "revision": {"source": "huggingface", "repo": "org/y-GGUF", "commit": commit, "path": "y.gguf"},
                         "sha256": sha, "bytes": 10, "dest": "models/y.gguf", "optional": False}]}


def gh_routes(fake, latest_tag="v1.1", body="Fixes.", asset_digest=SHA_A):
    fake.routes[("GET", "/repos/o/toolx/releases/tags/v1.0")] = (200, {"tag_name": "v1.0", "published_at": "2026-01-01T00:00:00Z",
                                                                      "assets": [{"name": "x.tar.gz", "digest": "sha256:" + asset_digest}]})
    rel = {"tag_name": latest_tag, "name": f"Tool X {latest_tag}", "published_at": "2026-05-01T00:00:00Z", "body": body,
           "html_url": f"https://github.com/o/toolx/releases/tag/{latest_tag}"}
    other = {"tag_name": "v0.0.9-other-product", "name": "another product line", "published_at": "2026-06-01T00:00:00Z"}
    pre = {"tag_name": "v9.9", "prerelease": True}
    fake.routes[("GET", "/repos/o/toolx/releases?per_page=50")] = (200, [other, pre, rel, {"tag_name": "v1.0"}])


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


def bench_result(path, cases, area=None, idsel=None):
    counts = {s: sum(1 for c in cases if c["status"] == s) for s in ("pass", "fail", "error", "deferred")}
    return write_json(path, {"bench_version": 2, "time": "x", "host": "h", "selection": {"area": area, "id": idsel},
                             "counts": counts, "cases": cases})


os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
