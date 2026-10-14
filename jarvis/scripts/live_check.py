"""Live checks of the N.E.K.O side of this system, against the RUNNING servers (not mocks).

Run with the N.E.K.O environment's Python, from anywhere:
    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe jarvis\\scripts\\live_check.py [--no-asr] [--no-model] [--no-browser]

What it does is deliberately safe: it only reads, or writes small clearly named files that it deletes again. It makes at most
one model call (--no-model skips it) and loads the speech-recognition model once (--no-asr skips it). It never prints a
secret: secret handling is checked by booleans only. Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA = Path(os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data"))
PLUGIN_URL = "http://127.0.0.1:48916"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""), flush=True)
    return bool(ok)


def http(url: str, method: str = "GET", body=None, timeout: float = 20.0, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"} if data is not None else {}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return 0, str(e).encode()


def jget(url: str, **kw):
    code, raw = http(url, **kw)
    try:
        return code, json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return code, None


def run_entry(plugin: str, entry: str, args: dict, wait: float = 90.0):
    code, r = jget(f"{PLUGIN_URL}/runs", method="POST", body={"plugin_id": plugin, "entry_id": entry, "args": args})
    if code != 200 or not r:
        return {"status": f"http {code}", "data": None, "error": "could not start the run"}
    run_id = r.get("run_id")
    end = time.time() + wait
    status = {}
    while time.time() < end:
        time.sleep(0.5)
        _, status = jget(f"{PLUGIN_URL}/runs/{run_id}")
        if status and status.get("status") not in ("queued", "running"):
            break
    _, ex = jget(f"{PLUGIN_URL}/runs/{run_id}/export")
    data = None
    for it in (ex or {}).get("items", []):
        if it.get("json"):
            data = it["json"].get("data")
    err = (status or {}).get("error")
    return {"status": (status or {}).get("status"), "data": data, "error": (err or {}).get("message") if err else None}


# ---------------------------------------------------------------- checks
def servers():
    for name, url in (("N.E.K.O main", "http://127.0.0.1:48911/health"), ("memory server", "http://127.0.0.1:48912/health"),
                      ("agent server", "http://127.0.0.1:48915/health"), ("plugin runtime", f"{PLUGIN_URL}/health"),
                      ("local speech server", "http://127.0.0.1:48930/health"), ("JARVIS console", "http://127.0.0.1:8799/api/health")):
        code, j = jget(url)
        check(f"server up: {name}", code == 200, f"HTTP {code}")


EXPECTED = {
    "jarvis_bridge": {"delegate_task", "task_result", "jarvis_status"},
    "main_agent": {"plan_task", "run_task", "run_status", "stop_task"},
    "doc_tools": {"read_document", "write_document", "list_outputs"},
    "learn_vault": {"vault_status", "list_notes", "read_note", "search_notes", "save_note", "record_progress", "get_progress"},
}


def plugins():
    code, j = jget(f"{PLUGIN_URL}/plugins")
    by_id = {p["id"]: p for p in (j or {}).get("plugins", [])}
    check("plugin registry", code == 200 and len(by_id) >= 14, f"{len(by_id)} plugins registered")
    for pid, want in EXPECTED.items():
        p = by_id.get(pid) or {}
        have = {e["id"] for e in p.get("entries", [])}
        check(f"plugin running: {pid}", p.get("status") == "running" and want <= have,
              f"status={p.get('status')} missing={sorted(want - have) or 'none'}")


def secrets_hygiene():
    cfg = DATA / "config" / "core_config.json"
    ok_file = cfg.is_file()
    raw = {}
    if ok_file:
        raw = json.loads(cfg.read_text(encoding="utf-8"))
    keyed = {k: v for k, v in raw.items() if k.lower().endswith("apikey") and v}
    plain = [k for k, v in keyed.items() if not str(v).startswith("file:")]
    check("config holds key references only (no plaintext key)", ok_file and bool(keyed) and not plain,
          f"{len(keyed)} key fields, {len(plain)} plaintext")
    probe = ("from utils.config_manager import get_config_manager as g\n"
             "c = g().get_model_api_config('agent')\n"
             "k = str(c.get('api_key') or '')\n"
             "print('RESOLVED', bool(k), 'REFERENCE_LEAK', k.startswith('file:'), 'URL', c.get('base_url'))\n")
    env = dict(os.environ, PYTHONUTF8="1", NEKO_STORAGE_SELECTED_ROOT=str(DATA), NEKO_SECRET_REF_ROOTS=r"D:\jarvis-secrets")
    repo = Path(__file__).resolve().parents[2]
    p = subprocess.run([sys.executable, "-I", "-c", probe], cwd=str(repo), env=env, capture_output=True, text=True, timeout=180)
    line = next((l for l in p.stdout.splitlines() if l.startswith("RESOLVED")), "")
    check("the key reference resolves in N.E.K.O's config layer (value never printed)",
          "RESOLVED True" in line and "REFERENCE_LEAK False" in line, line.replace("URL", "endpoint"))


def documents():
    name = f"livecheck-{int(time.time())}"
    w = run_entry("doc_tools", "write_document", {"name": name, "content": "# Live check\n\n- one\n- two\n", "format": "md"}, 45)
    path = (w.get("data") or {}).get("path", "")
    check("doc_tools: write a document", w["status"] == "succeeded" and path.endswith(".md"), path)
    if path:
        r = run_entry("doc_tools", "read_document", {"path": path}, 45)
        check("doc_tools: read it back", r["status"] == "succeeded" and "Live check" in str((r.get("data") or {}).get("text", "")))
        try:
            os.remove(path)
        except OSError:
            pass
    bad = run_entry("doc_tools", "read_document", {"path": r"D:\jarvis-secrets\providers\dahl.key"}, 45)
    check("doc_tools: a secret file is refused", bad["status"] == "failed" and "protected" in str(bad.get("error")), "refused")
    out = run_entry("doc_tools", "read_document", {"path": r"C:\Windows\System32\drivers\etc\hosts"}, 45)
    check("doc_tools: a file outside the allowed folders is refused", out["status"] == "failed", "refused")


def vault():
    note = f"livecheck/{int(time.time())}.md"
    marker = f"zebrafish{int(time.time())}"
    s = run_entry("learn_vault", "save_note", {"path": note, "content": f"# Check\n\nThe {marker} is a test word.\n"}, 45)
    check("learn_vault: save a note", s["status"] == "succeeded", note)
    q = run_entry("learn_vault", "search_notes", {"query": marker}, 45)
    hits = (q.get("data") or {}).get("hits", [])
    check("learn_vault: search finds it", q["status"] == "succeeded" and any(h["path"] == note for h in hits), f"{len(hits)} hit(s)")
    dup = run_entry("learn_vault", "save_note", {"path": note, "content": "again"}, 45)
    check("learn_vault: no overwrite", dup["status"] == "failed", "refused")
    esc = run_entry("learn_vault", "save_note", {"path": "../escape.md", "content": "x"}, 45)
    check("learn_vault: path traversal refused", esc["status"] == "failed", "refused")
    root = Path(os.environ.get("LEARN_VAULT_DIR", r"D:\jarvis\learn-vault"))
    shutil.rmtree(root / "livecheck", ignore_errors=True)         # only the folder this check created


def speech(do_asr: bool):
    text = "The capital of France is Paris."
    code, raw = http("http://127.0.0.1:48930/v1/audio/speech", method="POST",
                     body={"model": "jarvis-local", "input": text, "voice": "en", "response_format": "pcm"}, timeout=180)
    secs = len(raw) / 2 / 24000 if code == 200 else 0
    check("local TTS: spoken audio returned", code == 200 and 1.0 < secs < 20, f"{secs:.1f}s of 24 kHz audio")
    if code != 200 or not do_asr:
        return
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        pcm = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
        n = int(len(pcm) * 16000 / 24000)
        audio = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype("float32")
        os.environ.setdefault("HF_HOME", r"D:\jarvis\cache\hf")
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segs, _ = model.transcribe(audio, language="en", beam_size=1)
        got = " ".join(s.text.strip() for s in segs)
        check("local ASR: the spoken sentence is transcribed back", "paris" in got.lower() and "france" in got.lower(), got[:80])
    except Exception as exc:                                    # report, do not crash the whole run
        check("local ASR: the spoken sentence is transcribed back", False, type(exc).__name__)


def model_path():
    r = run_entry("main_agent", "plan_task", {"task": "Say hello and tell me one fun fact.", "mode": "chat"}, 180)
    steps = (r.get("data") or {}).get("steps", [])
    check("model path (Dahl via N.E.K.O): a chat-mode plan is one think step",
          r["status"] == "succeeded" and [s["agent"] for s in steps] == ["think"], f"{[s.get('agent') for s in steps]} {r.get('error') or ''}"[:120])
    bad = run_entry("main_agent", "plan_task", {"task": "anything", "mode": "root"}, 30)
    check("main agent: an unknown mode is refused", bad["status"] == "failed", "refused")


def jarvis():
    code, h = jget("http://127.0.0.1:8799/api/health")
    k = (h or {}).get("kernel", {})
    check("JARVIS: kernel reachable and authorized, kill switch off", code == 200 and k.get("reachable") and k.get("authorized") and not k.get("killed"), str({x: k.get(x) for x in ("reachable", "authorized", "killed")}))
    _, st = jget("http://127.0.0.1:8799/api/owner/state", timeout=30)
    check("JARVIS: owner state readable and not busy", bool(st) and st.get("busy") is False, f"busy={(st or {}).get('busy')}")
    _, v = jget("http://127.0.0.1:8799/api/owner/audit/verify", timeout=60)
    check("JARVIS: audit chain verifies", bool(v) and v.get("ok") is True, str((v or {}).get("message", ""))[:60])
    _, m = jget("http://127.0.0.1:8799/api/owner/models", timeout=30)
    nodes = (m or {}).get("nodes") or []
    if isinstance(nodes, dict):
        nodes = [nodes]
    n = next((x for x in nodes if isinstance(x, dict) and x.get("name") == "laptop"), nodes[0] if nodes and isinstance(nodes[0], dict) else {})
    check("JARVIS: laptop node online", bool(n.get("online")) and float(n.get("seconds_since_report", 999)) < 120, str(n))
    r = run_entry("jarvis_bridge", "jarvis_status", {}, 45)
    check("bridge: N.E.K.O reaches JARVIS (ready)", r["status"] == "succeeded" and (r.get("data") or {}).get("ready") is True, str(r.get("data")))
    _, c = jget("http://127.0.0.1:8799/api/owner/connectors", timeout=30)
    check("JARVIS: connector registry readable", bool(c) and "connectors" in c, f"{len((c or {}).get('connectors', []))} connector(s)")


def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        check("browser checks", False, "playwright not installed")
        return
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        page = b.new_page(viewport={"width": 1366, "height": 900})
        bad, errors = [], []
        page.on("response", lambda r: bad.append(f"{r.status} {r.url[-60:]}") if r.status >= 400 and "127.0.0.1" in r.url else None)
        page.on("pageerror", lambda e: errors.append(str(e)[:80]))
        page.goto("http://127.0.0.1:48911/", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)
        lang = page.evaluate("document.documentElement.lang")
        check("N.E.K.O web UI loads in English without failed requests or script errors",
              lang == "en" and not bad and not errors, f"lang={lang} failed={bad[:3]} errors={errors[:2]}")
        for pid, needles in (("jarvis_bridge", ("Give JARVIS a task", "Status")), ("main_agent", ("New task", "Current task"))):
            page.goto(f"{PLUGIN_URL}/ui/plugins/{pid}", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)
            text = ""
            for f in page.frames[1:]:
                try:
                    text += " " + f.inner_text("body")
                except Exception:
                    pass
            check(f"plugin manager panel renders: {pid}", all(n in text for n in needles), f"{len(page.frames) - 1} hosted frame(s)")
        b.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-asr", action="store_true")
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    def guard(label, fn, *args):
        try:
            fn(*args)
        except Exception as exc:                      # a surprise is a failed check, never a crashed run
            check(f"{label} (check could not complete)", False, f"{type(exc).__name__}: {str(exc)[:100]}")

    guard("servers", servers)
    guard("plugins", plugins)
    guard("secrets", secrets_hygiene)
    guard("documents", documents)
    guard("vault", vault)
    guard("speech", speech, not a.no_asr)
    if not a.no_model:
        guard("model path", model_path)
    guard("JARVIS", jarvis)
    if not a.no_browser:
        guard("browser", browser)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
