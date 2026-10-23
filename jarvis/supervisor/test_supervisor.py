"""Supervisor tests (manifest, health, systemd, and the process-tree acceptance check).
Run: python -m pytest jarvis/supervisor -q -p no:cacheprovider"""
from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import health  # noqa: E402
import manifest  # noqa: E402
import core as supervisor  # noqa: E402
import systemd  # noqa: E402

PY = sys.executable
SLEEP = [PY, "-c", "import time; time.sleep(120)"]


def comp(name="a", **kw):
    d = {"name": name, "argv": SLEEP}
    d.update(kw)
    return d


def test_manifest_accepts_loopback_and_orders_dependencies():
    comps = manifest.parse({"components": [comp("b", depends_on=["a"]), comp("a", bind={"host": "127.0.0.1", "port": 8000})]})
    assert [c.name for c in manifest.order(comps)] == ["a", "b"]


def test_manifest_refuses_wide_bind_secret_env_cycle_and_bad_names():
    with pytest.raises(manifest.ManifestError):
        manifest.parse({"components": [comp(bind={"host": "0.0.0.0", "port": 8000})]})        # the acceptance case
    with pytest.raises(manifest.ManifestError):
        manifest.parse({"components": [comp(env={"K": "ghp_" + "a1b2c3d4e5f6g7h8i9j0"})]})
    with pytest.raises(manifest.ManifestError):
        manifest.parse({"components": [comp("a", depends_on=["b"]), comp("b", depends_on=["a"])]})
    with pytest.raises(manifest.ManifestError):
        manifest.parse({"components": [comp("Bad Name")]})
    with pytest.raises(manifest.ManifestError):
        manifest.parse({"components": [comp("a", depends_on=["zzz"])]})


def test_allowed_host_passes_only_when_listed():
    ok = manifest.parse({"allowed_hosts": ["100.125.91.51"], "components": [comp(bind={"host": "100.125.91.51", "port": 8765})]})
    assert ok[0].host == "100.125.91.51"
    with pytest.raises(manifest.ManifestError):
        manifest.parse({"components": [comp(bind={"host": "100.125.91.51", "port": 8765})]})


def test_file_reference_env_is_allowed():
    assert manifest.parse({"components": [comp(env={"KEY": "file:providers/groq.key"})]})[0].env["KEY"].startswith("file:")


def test_health_tcp_and_http_against_a_real_local_server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conn.recv(1024)
            conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        assert health.tcp_ok("127.0.0.1", port)
        assert health.http_ok("127.0.0.1", port)           # 401 counts as up
        assert health.wait_healthy("http", "127.0.0.1", port, timeout_s=3)
    finally:
        srv.close()
    assert not health.tcp_ok("127.0.0.1", port)
    assert not health.wait_healthy("tcp", "127.0.0.1", port, timeout_s=0.5)


def test_systemd_unit_is_hardened_and_orders_dependencies():
    comps = manifest.parse({"components": [comp("a"), comp("b", depends_on=["a"], restart="on-failure", env={"MODE": "x y"})]})
    units = systemd.render_all(comps)
    assert set(units) == {"jarvis-a.service", "jarvis-b.service"}
    b = units["jarvis-b.service"]
    assert "Requires=jarvis-a.service" in b and "Restart=on-failure" in b and "NoNewPrivileges=yes" in b
    assert "Environment='MODE=x y'" in b and "JARVIS_BOOT_TOKEN_FILE=" in b


def test_supervisor_starts_and_stops_components_and_never_puts_the_token_in_env(tmp_path):
    comps = manifest.parse({"components": [comp("a")]})
    sup = supervisor.Supervisor(comps, tmp_path / "run")
    sup.start()
    try:
        assert sup.status() == {"a": "running"}
        assert (tmp_path / "run" / "boot.token").read_text(encoding="utf-8") == sup.token
    finally:
        sup.stop()
    assert sup.status()["a"].startswith("exited")


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        import os
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_killing_the_supervisor_leaves_no_orphans(tmp_path):
    """Acceptance (P1): kill the shell hard; the children must die with it (Job Object on Windows)."""
    harness = tmp_path / "harness.py"
    harness.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(HERE)!r})
        import jobobject
        tree = jobobject.ProcessTree()
        kids = [tree.start([sys.executable, "-c", "import time; time.sleep(120)"]) for _ in range(2)]
        print(" ".join(str(k.pid) for k in kids), flush=True)
        time.sleep(120)
    """), encoding="utf-8")
    p = subprocess.Popen([PY, str(harness)], stdout=subprocess.PIPE, text=True)
    try:
        pids = [int(x) for x in p.stdout.readline().split()]
        assert len(pids) == 2 and all(_pid_alive(x) for x in pids)
        p.kill()                       # hard kill: no cleanup code in the harness runs
        p.wait(timeout=10)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and any(_pid_alive(x) for x in pids):
            time.sleep(0.3)
        survivors = [x for x in pids if _pid_alive(x)]
        if survivors and sys.platform != "win32":
            pytest.skip("POSIX fallback only kills on a clean stop; the Job Object guarantee is Windows-specific")
        assert survivors == []
    finally:
        p.kill()


def test_laptop_manifest_parses_is_loopback_only_and_matches_jarvis_start():
    comps = manifest.load(HERE / "manifests" / "laptop.json")
    assert [c.name for c in manifest.order(comps)][-1] == "face-tools"          # started last: it depends on the other two
    assert all(c.host == "127.0.0.1" for c in comps)
    start = (HERE.parent / "scripts" / "jarvis-start.ps1").read_text(encoding="utf-8")
    for c in comps:
        assert str(c.port) in start, f"{c.name}: port {c.port} not in jarvis-start.ps1"
    env = next(c for c in comps if c.name == "face-tools").env
    for k in env:
        assert k in start, f"{k} is not set by jarvis-start.ps1"
