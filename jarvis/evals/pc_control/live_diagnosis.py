"""Category D: diagnosis and recovery on controlled copies of the Neko sample app (one throwaway folder per case under
D:\\jarvis\\tests\\neko-variants, deleted afterwards). Real error dialogs, read by the node's diagnostics; the plugin's
local diagnosis; real repairs with backups (the owner's approval is SIMULATED here: the kernel's approval is tested
separately). Windows. Run with the neko-venv Python (it imports both the plugin core and the node modules).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "jarvis" / "node"))
from plugin.plugins.desktop import core  # noqa: E402
from jarvis_node import diag, repair, winbackend as W  # noqa: E402

SRC = Path(r"D:\jarvis\tests\neko-sample-app\NekoSampleApp.exe")
ROOT = Path(r"D:\jarvis\tests\neko-variants")
ROWS = []


def case(cid, name, fn):
    t0 = time.time()
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {str(e)[:200]}"
    ROWS.append({"id": cid, "name": name, "ok": bool(ok), "detail": str(detail)[:300], "seconds": round(time.time() - t0, 2)})
    print(f"[{'PASS' if ok else 'FAIL'}] {cid} {name}: {str(detail)[:170]}", flush=True)


def variant(name, config):
    d = ROOT / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    shutil.copy2(SRC, d / "NekoSampleApp.exe")
    if config is not None:
        (d / "config.json").write_text(config, encoding="ascii")
    return d


def run_and_read(d):
    """Start the copy, read its windows (local), dismiss its dialog, return (windows, diagnosis)."""
    subprocess.run(["taskkill", "/IM", "NekoSampleApp.exe", "/F"], capture_output=True)
    subprocess.Popen([str(d / "NekoSampleApp.exe")], cwd=str(d))
    time.sleep(3)
    r = diag.run({"op": "window_text", "target": "NekoSampleApp"})
    wins = json.load(open(r["local_result"], encoding="utf-8")) if r.get("local_result") else []
    if r.get("local_result"):
        os.remove(r["local_result"])
    d_ = core.diagnose(wins, [], [], app_dir=str(d))
    if any(w.get("dialog") for w in wins):
        try:
            W.click("NekoSampleApp|OK", {})
        except Exception:  # noqa: BLE001
            pass
    time.sleep(1)
    subprocess.run(["taskkill", "/IM", "NekoSampleApp.exe", "/F"], capture_output=True)
    return wins, d_


def main():
    old_backups = repair.BACKUPS
    repair.BACKUPS = str(ROOT / "_backups")
    try:
        d1 = variant("typo", '{\n  "name": "neko sample",\n  "port": 80x80\n}\n')
        w, dg = run_and_read(d1)
        case("D01", "a controlled error message is read and its cause verified in the file", lambda: (
            dg["verified"] and "line 3" in dg["cause"] and dg["dialog_seen"], dg["cause"]))
        case("D02", "the least disruptive fix is proposed (one value, not the whole file)", lambda: (
            (dg.get("fix") or {}).get("args") == {"find": '"port": 80x80', "replace": '"port": 8080'}, dg.get("fix")))
        fix = {}
        case("D03", "the approved fix is applied with a backup first", lambda: (lambda r: (fix.update(r) or (r["ok"] and r["backup"]), r))(
            repair.edit_file(str(d1 / "config.json"), '"port": 80x80', '"port": 8080')))
        case("D04", "after the fix the app starts without the error", lambda: (lambda wd: (
            not any(x.get("dialog") for x in wd[0]) and any("running on port 8080" in x.get("title", "") for x in wd[0]), [x.get("title") for x in wd[0]]))(
            run_and_read(d1)))
        case("D05", "rolling back restores the original file and the error returns", lambda: (lambda r: (
            r["ok"] and "80x80" in (d1 / "config.json").read_text() and run_and_read(d1)[1]["verified"], r))(repair.restore(fix.get("backup", ""))))
        case("D06", "a repair whose text is not there changes nothing and says so", lambda: (lambda r: (
            not r["ok"] and "0 times" in r["error"] and "80x80" in (d1 / "config.json").read_text(), r["error"]))(
            repair.edit_file(str(d1 / "config.json"), '"port": 1234', '"port": 8080')))
        case("D07", "a file that needs administrator rights is refused (never elevated)", lambda: (lambda r: (
            not r["ok"] and "only files" in r["error"], r["error"]))(repair.edit_file(r"C:\Program Files\Common Files\neko-test.json", "a", "b")))
        d2 = variant("missing-config", None)
        w2, dg2 = run_and_read(d2)
        case("D08", "a missing file is diagnosed without inventing a fix", lambda: (
            dg2["dialog_seen"] and dg2.get("fix") is None and "config" in json.dumps(w2).lower(), dg2["cause"][:120]))
        d3 = variant("port-string", '{"name": "neko sample", "port": "8080"}')
        w3, dg3 = run_and_read(d3)
        case("D09", "a plausible cause it cannot check stays a hypothesis", lambda: (
            dg3["dialog_seen"] and not dg3["verified"] and dg3.get("fix") is None, dg3["cause"][:120]))
        d4 = variant("missing-dll", '{"name": "neko sample", "port": 8080, "plugin": "neko_plugin.dll"}')
        w4, dg4 = run_and_read(d4)
        case("D10", "a known missing dependency is identified and verified absent", lambda: (
            dg4.get("dependency") == "neko_plugin.dll" and dg4["verified"], dg4["cause"]))
        (d4 / "neko_plugin.dll").write_bytes(b"test")            # the dependency put in place (an empty test file)
        case("D11", "with the dependency present the error is gone", lambda: (lambda wd: (
            not any(x.get("dialog") for x in wd[0]), [x.get("title") for x in wd[0]]))(run_and_read(d4)))
        d5 = variant("healthy", '{"name": "neko sample", "port": 8080}')
        case("D12", "a healthy app is not 'fixed': no error found", lambda: (lambda wd: (
            not wd[1]["dialog_seen"] and wd[1].get("fix") is None and not wd[1]["verified"], wd[1]["cause"]))(run_and_read(d5)))
        case("D13", "a crash record alone is a hypothesis", lambda: (lambda dg_: (not dg_["verified"] and "hypothesis" in dg_["cause"], dg_["cause"]))(
            core.diagnose([], [], [{"app": "x.exe", "kind": "crash", "module": "y.dll", "code": "c0000005"}])))
        case("D14", "a broken backup is not restored (the repair stops safely)", lambda: _tampered(d1))
        case("T-port-conflict", "port taken by another program: holder verified, app moved to a free port, then it runs", _port_conflict)
        case("T-disk-space", "not enough disk space: verified against the drive's real free space, no invented fix", _disk_space)
        case("T-service-down", "a needed local service is down: verified nothing listens; once it runs, the app starts", _service_down)
    finally:
        subprocess.run(["taskkill", "/IM", "NekoSampleApp.exe", "/F"], capture_output=True)
        repair.BACKUPS = old_backups
        shutil.rmtree(ROOT, ignore_errors=True)
    out = HERE / "results" / f"diagnosis-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"cases": ROWS}, indent=1), encoding="utf-8")
    print("SUMMARY", sum(r["ok"] for r in ROWS), "of", len(ROWS), "->", out)


def _listen(port=0):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen(5)
    return s, s.getsockname()[1]


def _port_conflict():
    holder, port = _listen()                                  # "another program" holding the port: this test itself
    try:
        d = variant("port-taken", json.dumps({"name": "neko sample", "port": port, "listen": True}))
        _, dg = run_and_read(d)
        fix = dg.get("fix") or {}
        if not (dg["verified"] and f"port {port}" in dg["cause"] and fix.get("action") == "edit_file"):
            return False, f"diagnosis: {dg['cause']}; fix {fix}"
        r = repair.edit_file(fix["target"], fix["args"]["find"], fix["args"]["replace"])
        wins, after = run_and_read(d)
        new = fix["args"]["replace"].split(":")[-1].strip()
        runs = any(f"running on port {new}" in x.get("title", "") for x in wins) and not any(x.get("dialog") for x in wins)
        return r["ok"] and runs, f"{dg['cause']}; fix {fix['args']}; after the fix: {[x.get('title') for x in wins]}"
    finally:
        holder.close()


def _disk_space():
    import psutil
    free_mb = psutil.disk_usage(str(ROOT)[:3]).free // 2**20
    d = variant("no-space", json.dumps({"name": "neko sample", "port": 8080, "needs_mb": int(free_mb + 100000)}))
    _, dg = run_and_read(d)
    ok = dg["verified"] and dg.get("fix") is None and dg.get("drive") == str(ROOT)[:2] and dg.get("needs_mb") == free_mb + 100000
    return ok, f"{dg['cause']}; fix {dg.get('fix')}; advice: {dg.get('advice')}"


def _service_down():
    probe, port = _listen()                                   # a free port: bound once, then released
    probe.close()
    d = variant("service-down", json.dumps({"name": "neko sample", "port": 8080, "service_port": port}))
    _w, dg = run_and_read(d)
    if not (dg["verified"] and f"127.0.0.1:{port}" in dg["cause"] and dg.get("fix") is None):
        return False, f"diagnosis: {dg['cause']}"
    svc, _p = _listen(port)                                   # the service is started (here: a listener on that port)
    try:
        wins, after = run_and_read(d)
        return not any(x.get("dialog") for x in wins), f"{dg['cause']}; with the service up: {[x.get('title') for x in wins]}"
    finally:
        svc.close()


def _tampered(d1):
    r = repair.edit_file(str(d1 / "config.json"), '"port": 80x80', '"port": 8080')
    (Path(repair.BACKUPS) / r["backup"] / "file.bak").write_text("evil", encoding="utf-8")
    back = repair.restore(r["backup"])
    return (not back["ok"] and "does not match" in back["error"] and '"port": 8080' in (d1 / "config.json").read_text()), back["error"]


if __name__ == "__main__":
    main()
