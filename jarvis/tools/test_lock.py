"""Checks the download lock (jarvis/tools/jarvis.lock.json) and the fetcher (jarvis/tools/fetch.py).

No test touches the internet: downloads come from a local http.server on 127.0.0.1, and a guard refuses any other
connection. Run in the neko-test image (repo mounted at /src):
    python -m pytest jarvis/tools/test_lock.py -q -p no:cacheprovider
"""
from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
import re
import socket
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "jarvis.lock.json"
REGISTRY_PATH = HERE / "registry.json"
_spec = importlib.util.spec_from_file_location("jarvis_fetch", HERE / "fetch.py")
fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch)

HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
KINDS = {"model", "binary", "archive"}
REQUIRED = ("name", "registry_name", "kind", "machine", "url", "revision", "filename", "sha256", "bytes", "licence", "dest",
            "optional", "source_evidence")


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    """Any connection that is not to this machine fails the test."""
    real = socket.create_connection

    def guarded(address, *a, **k):
        if address[0] not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"test tried to reach the internet: {address}")
        return real(address, *a, **k)
    monkeypatch.setattr(socket, "create_connection", guarded)
    for var in ("no_proxy", "NO_PROXY"):                               # a proxy setting must not route the local server
        monkeypatch.setenv(var, "127.0.0.1,localhost")


@pytest.fixture(scope="module")
def lock():
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def registry():
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- lock schema

def test_every_entry_has_the_required_fields(lock):
    assert lock["entries"], "empty lock"
    for e in lock["entries"]:
        missing = [k for k in REQUIRED if k not in e]
        assert not missing, (e.get("name"), missing)
        assert e["kind"] in KINDS, e["name"]
        assert isinstance(e["optional"], bool), e["name"]
        assert isinstance(e["bytes"], int) and e["bytes"] > 0, e["name"]
        assert e["machine"] and set(e["machine"]) <= set(lock["machines"]), e["name"]
        assert e["source_evidence"], e["name"]


def test_names_are_unique_and_cli_friendly(lock):
    names = [e["name"] for e in lock["entries"]]
    assert len(names) == len(set(names))
    for n in names:
        assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*", n), n


def test_registry_name_exists_in_registry(lock, registry):
    known = {r["name"] for r in registry}
    for e in lock["entries"]:
        assert e["registry_name"] in known, (e["name"], e["registry_name"])
    for n in lock["not_locked"]:
        assert n["registry_name"] in known, n


def test_every_download_recommendation_is_locked_or_explained(lock, registry):
    wanted = {r["name"] for r in registry if r.get("packaging_recommendation", "").startswith("lockfile-pinned download")}
    assert len(wanted) >= 25
    covered = {e["registry_name"] for e in lock["entries"]}
    explained = {n["registry_name"] for n in lock["not_locked"] if n.get("unpinned_reason")}
    assert not wanted - covered - explained, sorted(wanted - covered - explained)


def test_hashes_are_pinned_or_explained(lock):
    for e in lock["entries"]:
        if e["sha256"] is None:
            assert e.get("unpinned_reason"), e["name"]
            if "git_blob_sha1" in e:
                assert HEX40.match(e["git_blob_sha1"]), e["name"]
        else:
            assert HEX64.match(e["sha256"]), e["name"]


def test_urls_and_revisions_are_exact(lock):
    for e in lock["entries"]:
        assert e["url"].startswith("https://"), e["name"]
        rev = e["revision"]
        if rev["source"] == "huggingface":
            assert HEX40.match(rev["commit"]), e["name"]                 # a commit, never "main"
            assert f"/resolve/{rev['commit']}/" in e["url"], e["name"]
        elif rev["source"] == "github-release":
            assert rev["tag"] and rev["asset"], e["name"]
            assert e["url"].endswith("/" + rev["asset"]) and f"/download/{rev['tag']}/" in e["url"], e["name"]
        assert "/main/" not in e["url"] and "/latest/" not in e["url"], e["name"]


def test_dest_is_relative_and_inside_the_root(lock):
    for e in lock["entries"]:
        fetch.safe_dest(e)
        assert not os.path.isabs(e["dest"])
    for bad in ("/etc/x", "..\\x", "a/../../x", "C:/x", "", "a//b"):
        with pytest.raises(fetch.Refused):
            fetch.safe_dest({"name": "t", "dest": bad})


def test_existing_pins_are_kept(lock):
    """Pins already in the repo (model_candidates.json, Containerfile, wake_word_bundle.py) appear unchanged."""
    cands = json.loads((HERE.parent / "evals/neko95/bench/model_candidates.json").read_text(encoding="utf-8"))
    by_sha = {e["sha256"]: e for e in lock["entries"] if e["sha256"]}
    for c in cands.values():
        assert c["sha256"] in by_sha, c["file"]
        e = by_sha[c["sha256"]]
        assert e["revision"]["commit"] == c["revision"] and e["bytes"] == c["bytes"], c["file"]
    for pinned in ("551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",     # gitleaks linux_x64, Containerfile
                   "cc2f601cee82d9a5a233302fe307abc600727035fd7d2c3a3571755989e95008",     # node linux-x64, Containerfile
                   "68447f4fbc67e70eee3a93961f36e81e98f47aef73ce7e7ca00885c6cd3616a6"):    # N.E.K.O KWS model
        assert pinned in by_sha


# ---------------------------------------------------------------- fetch.py against a local server

class _Server:
    def __init__(self, files: dict[str, bytes], honour_range: bool = True):
        self.files, self.honour_range, self.requests = files, honour_range, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.requests.append((self.path, self.headers.get("Range"), outer.on_request()))
                body = outer.files.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                rng = self.headers.get("Range")
                if rng and outer.honour_range:
                    start = int(rng.split("=")[1].split("-")[0])
                    part = body[start:]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                else:
                    part = body
                    self.send_response(200)
                self.send_header("Content-Length", str(len(part)))
                self.end_headers()
                self.wfile.write(part)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.on_request = lambda: None
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server({})
    yield s
    s.close()


def _git_blob_sha1(b: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def _lock_file(tmp_path, entries) -> str:
    p = tmp_path / "test.lock.json"
    p.write_text(json.dumps({"schema_version": 1, "machines": {"laptop": ""}, "entries": entries, "not_locked": []}))
    return str(p)


def _entry(server, name, body, sha=None, **kw):
    path = f"/{name}.bin"
    server.files[path] = body
    e = {"name": name, "registry_name": "x", "kind": "model", "machine": ["laptop"], "url": server.base + path,
         "revision": {"source": "test"}, "filename": f"{name}.bin", "bytes": len(body), "licence": "MIT",
         "dest": f"models/t/{name}.bin", "optional": False, "source_evidence": ["test"],
         "sha256": hashlib.sha256(body).hexdigest() if sha is None else sha}
    e.update(kw)
    return e


BODY = os.urandom(3 * 1024 * 1024 + 17)      # spans several read chunks


def test_fetch_downloads_verifies_and_renames(server, tmp_path):
    lk = _lock_file(tmp_path, [_entry(server, "good", BODY)])
    root = tmp_path / "root"
    assert fetch.main(["--fetch", "good", "--root", str(root), "--lock", lk]) == 0
    final = root / "models/t/good.bin"
    assert final.read_bytes() == BODY
    assert not Path(str(final) + ".part").exists()


def test_rename_is_atomic(server, tmp_path, monkeypatch):
    """While bytes arrive the final path does not exist; it appears in one os.replace from the .part file."""
    lk = _lock_file(tmp_path, [_entry(server, "atomic", BODY)])
    root = tmp_path / "root"
    final = root / "models/t/atomic.bin"
    server.on_request = lambda: final.exists()
    calls, real_replace = [], os.replace

    def spy(src, dst):
        calls.append((str(src), str(dst), os.path.exists(dst)))
        return real_replace(src, dst)
    monkeypatch.setattr(fetch.os, "replace", spy)
    assert fetch.main(["--fetch", "atomic", "--root", str(root), "--lock", lk]) == 0
    assert [r[2] for r in server.requests] == [False]                  # final absent while downloading
    assert calls == [(str(final) + ".part", str(final), False)]
    assert final.read_bytes() == BODY


def test_sha_mismatch_is_refused_and_nothing_is_left(server, tmp_path):
    lk = _lock_file(tmp_path, [_entry(server, "bad", BODY, sha="0" * 64)])
    root = tmp_path / "root"
    assert fetch.main(["--fetch", "bad", "--root", str(root), "--lock", lk]) == 1
    final = root / "models/t/bad.bin"
    assert not final.exists()
    assert not Path(str(final) + ".part").exists()


def test_size_mismatch_is_refused(server, tmp_path):
    e = _entry(server, "short", BODY)
    server.files["/short.bin"] = BODY[:-1]                              # server sends one byte less, same name
    lk = _lock_file(tmp_path, [e])
    root = tmp_path / "root"
    assert fetch.main(["--fetch", "short", "--root", str(root), "--lock", lk]) == 1
    assert not (root / "models/t/short.bin").exists()


def test_unknown_name_is_refused_before_any_download(server, tmp_path, capsys):
    lk = _lock_file(tmp_path, [_entry(server, "good", BODY)])
    assert fetch.main(["--fetch", "good", "evil", "--root", str(tmp_path / "root"), "--lock", lk]) == 2
    assert "evil" in capsys.readouterr().err
    assert server.requests == []
    assert not (tmp_path / "root").exists()


def test_unpinned_needs_the_flag(server, tmp_path):
    body = b'{"labels": ["a", "b"]}\n'
    e = _entry(server, "cfg", body, sha=None, unpinned_reason="test", git_blob_sha1=_git_blob_sha1(body))
    e["sha256"] = None
    lk = _lock_file(tmp_path, [e])
    root = tmp_path / "root"
    assert fetch.main(["--fetch", "cfg", "--root", str(root), "--lock", lk]) == 2
    assert server.requests == [] and not root.exists()
    assert fetch.main(["--fetch", "cfg", "--root", str(root), "--lock", lk, "--allow-unpinned"]) == 0
    assert (root / "models/t/cfg.bin").read_bytes() == body


def test_unpinned_still_checks_git_blob_sha1(server, tmp_path):
    e = _entry(server, "cfg2", b"real content\n", sha=None, unpinned_reason="test", git_blob_sha1="1" * 40)
    e["sha256"] = None
    lk = _lock_file(tmp_path, [e])
    root = tmp_path / "root"
    assert fetch.main(["--fetch", "cfg2", "--root", str(root), "--lock", lk, "--allow-unpinned"]) == 1
    assert not (root / "models/t/cfg2.bin").exists()


def test_check_detects_a_tampered_file(server, tmp_path, capsys):
    lk = _lock_file(tmp_path, [_entry(server, "good", BODY), _entry(server, "absent", b"zz")])
    root = tmp_path / "root"
    assert fetch.main(["--fetch", "good", "--root", str(root), "--lock", lk]) == 0
    assert fetch.main(["--check", "--root", str(root), "--lock", lk]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^ok\s+good", out, re.M) and re.search(r"^missing\s+absent", out, re.M)
    final = root / "models/t/good.bin"
    data = bytearray(final.read_bytes())
    data[len(data) // 2] ^= 0xFF                                        # same size, one byte changed
    final.write_bytes(bytes(data))
    assert fetch.main(["--check", "--root", str(root), "--lock", lk]) == 1
    assert re.search(r"^MISMATCH\s+good\s+sha256", capsys.readouterr().out, re.M)


def test_never_overwrites_an_existing_file(server, tmp_path):
    lk = _lock_file(tmp_path, [_entry(server, "good", BODY)])
    root = tmp_path / "root"
    final = root / "models/t/good.bin"
    final.parent.mkdir(parents=True)
    final.write_bytes(BODY)
    assert fetch.main(["--fetch", "good", "--root", str(root), "--lock", lk]) == 0
    assert server.requests == []                                        # a good file is not downloaded again
    final.write_bytes(b"something else")
    assert fetch.main(["--fetch", "good", "--root", str(root), "--lock", lk]) == 1
    assert final.read_bytes() == b"something else" and server.requests == []


@pytest.mark.parametrize("honour_range", [True, False])
def test_resume_or_restart(tmp_path, honour_range):
    s = _Server({}, honour_range=honour_range)
    try:
        lk = _lock_file(tmp_path, [_entry(s, "big", BODY)])
        root = tmp_path / "root"
        part = root / "models/t/big.bin.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(BODY[:1000])
        assert fetch.main(["--fetch", "big", "--root", str(root), "--lock", lk]) == 0
        assert (root / "models/t/big.bin").read_bytes() == BODY and not part.exists()
        assert s.requests[0][1] == "bytes=1000-"
    finally:
        s.close()


def test_root_defaults(monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_ROOT", "/data/x")
    assert fetch.default_root() == "/data/x"
    monkeypatch.delenv("JARVIS_DATA_ROOT")
    assert fetch.default_root() == (r"D:\jarvis" if os.name == "nt" else "/srv/jarvis")


def test_only_https_or_loopback_urls():
    fetch.check_url("https://huggingface.co/x")
    fetch.check_url("http://127.0.0.1:8000/x")
    for bad in ("http://example.com/x", "file:///etc/passwd", "ftp://h/x"):
        with pytest.raises(fetch.Refused):
            fetch.check_url(bad)


def test_fetcher_never_executes_downloads():
    src = (HERE / "fetch.py").read_text(encoding="utf-8")
    for forbidden in ("subprocess", "os.system", "os.exec", "os.startfile", "os.chmod", "tarfile", "zipfile", "Authorization"):
        assert forbidden not in src, forbidden
