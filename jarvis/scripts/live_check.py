"""Live checks of JARVIS's local services and plugins, against the RUNNING stack (not mocks).

Run with the agent environment's Python, from anywhere:
    D:\\jarvis\\tools\\agent-venv\\Scripts\\python.exe jarvis\\scripts\\live_check.py [--no-asr] [--no-model]

The plugins are loaded and run in this process (jarvis/pluginapi), exactly as the face's tool server runs them, against the real
local services (privacy gateway, speech server, local models, console). The tool server itself is checked over MCP.
What it does is deliberately safe: it only reads, or writes small clearly named files that it deletes again. It makes at most
one model call (--no-model skips it) and loads the speech-recognition model once (--no-asr skips it). It never prints a
secret: secret handling is checked by booleans only. Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

import loopback_net  # noqa: E402  loopback only, no proxy, no redirect (completion H)
import control_auth  # noqa: E402  the servers' master tokens (re-review M3: private reads need them)

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DATA = Path(os.environ.get("JARVIS_DATA_DIR", r"D:\jarvis\data"))
TOOLS_URL = "http://127.0.0.1:48970/mcp"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""), flush=True)
    return bool(ok)


def http(url: str, method: str = "GET", body=None, timeout: float = 20.0, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with loopback_net.urlopen(req, timeout=timeout) as r:
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


class Hosted:
    """The plugins, loaded and started once on a loop of their own; run_entry calls an entry like the tool server does."""

    def __init__(self):
        from jarvis.pluginapi.host import PluginHost
        self.host = PluginHost()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.host.load()
        self.call(self.host.start(), 120)

    def call(self, coro, timeout):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)


_HOSTED: Hosted | None = None


def hosted() -> Hosted:
    global _HOSTED
    if _HOSTED is None:
        _HOSTED = Hosted()
    return _HOSTED


def run_entry(plugin: str, entry: str, args: dict, wait: float = 90.0):
    """{"status": "succeeded" | "failed", "data": the entry's value, "error": text}."""
    from jarvis.pluginapi import Err
    try:
        res = hosted().call(hosted().host.invoke(f"{plugin}:{entry}", args, wait), wait + 15)
    except Exception as exc:                          # noqa: BLE001 - a surprise is a failed run, not a crashed check
        return {"status": "failed", "data": None, "error": f"{type(exc).__name__}: {str(exc)[:150]}"}
    if isinstance(res, Err):
        return {"status": "failed", "data": None, "error": str(getattr(res.error, "message", res.error))[:300]}
    return {"status": "succeeded", "data": res.value, "error": None}


# ---------------------------------------------------------------- checks
def servers():
    for name, url in (("local speech server", "http://127.0.0.1:48930/health"), ("privacy gateway", "http://127.0.0.1:48950/health"),
                      ("JARVIS console", "http://127.0.0.1:8799/api/health")):
        code, j = jget(url)
        check(f"server up: {name}", code == 200, f"HTTP {code}")


def tool_server():
    """The face's tool server over MCP: it answers, offers the JARVIS tools and the plugins' entries, and says JARVIS is ready."""
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def go():
        async with streamable_http_client(TOOLS_URL) as (r, w):
            async with ClientSession(r, w) as sess:
                await sess.initialize()
                names = sorted(t.name for t in (await sess.list_tools()).tools)
                ready = await sess.call_tool("jarvis_ready", {})
                return names, json.loads(ready.content[0].text)
    names, ready = asyncio.run(asyncio.wait_for(go(), 60))
    check("tool server: answers over MCP and offers the JARVIS tools", {"jarvis_do", "jarvis_ready", "jarvis_task_result"} <= set(names),
          f"{len(names)} tools")
    plugin_tools = [n for n in names if "__" in n]
    check("tool server: offers the plugins' entries", len(plugin_tools) >= 20, f"{len(plugin_tools)} plugin tools")
    check("tool server: JARVIS says it is ready", ready.get("ready") is True, str(ready.get("message"))[:100])


def privacy():
    """The privacy gateway is up, the plugins' agent model goes through it, and a made-up password leaves only as a placeholder."""
    code, _ = jget("http://127.0.0.1:48950/health")
    check("server up: privacy gateway", code == 200, f"HTTP {code}")
    from jarvis.pluginapi.models import agent_model
    base = agent_model()["base_url"]
    check("the plugins' agent model goes through the privacy gateway", "127.0.0.1:4895" in base, base)
    fake = "Zq7-made-up-Pw!"
    code, raw = http("http://127.0.0.1:48950/redact", method="POST", body={"text": f"my password = {fake} and mail a.b@example.com"})
    out = (json.loads(raw).get("text") if code == 200 else "") or ""
    check("privacy gateway: a password and an e-mail leave as placeholders", code == 200 and fake not in out
          and "a.b@example.com" not in out and "[PASSWORD_1]" in out, out[:80])
    # no cue word: only the PII model (GLiNER-PII) with the Jev-style decision model's confirmation finds these
    code, raw = http("http://127.0.0.1:48950/redact", method="POST", timeout=60,
                     body={"text": "the vpn login is k.rao and the passcode Blue7Whale#. What is 15% of 240?"})
    out = (json.loads(raw).get("text") if code == 200 else "") or ""
    _, h = jget("http://127.0.0.1:48950/health")
    check("privacy gateway: a secret with no cue word is hidden, a sum is left alone (PII model + Jev check)",
          code == 200 and "Blue7Whale" not in out and "15% of 240" in out and (h or {}).get("pii_model")
          and not ((h or {}).get("checks") or {}).get("jev_unreachable"), out[:90])


EXPECTED = {
    "jarvis_bridge": {"delegate_task", "task_result", "jarvis_status", "pending_approvals", "connector_overview"},
    "main_agent": {"plan_task", "run_task", "resume_run", "run_status", "stop_task"},
    "doc_tools": {"read_document", "write_document", "list_outputs"},
    "doc_reader": {"read_document"},
    "learn_vault": {"vault_status", "list_notes", "read_note", "search_notes", "save_note", "record_progress", "get_progress"},
    "local_team": {"ask_local", "solve_with_team", "team_status"},
    "deliverables": {"make_presentation", "make_report", "open_output", "show_in_folder", "start_slideshow", "regenerate",
                     "list_deliverables"},
    "research": {"research_topic"},
    "web_search": {"search"},
    # the desktop plugin hosts the 60 s PC monitor (crashes, low disk, services down, stuck PC tasks) and runs PC tasks
    "desktop": {"run_pc_task", "stop_pc_task", "check_pc", "recent_pc_alerts"},
}


def plugins():
    desc = {p["id"]: p for p in hosted().host.describe()} if hasattr(hosted().host, "describe") else {}
    check("plugin host: loaded", len(desc) >= 9, f"{len(desc)} plugins")
    for pid, want in EXPECTED.items():
        p = desc.get(pid) or {}
        have = {e["id"] if isinstance(e, dict) else e for e in p.get("entries", [])}
        check(f"plugin running: {pid}", p.get("status") == "running" and want <= have,
              f"status={p.get('status')} missing={sorted(want - have) or 'none'}")


def agent_key():
    """The agent model's key stays in a file: set up by reference, never printed."""
    path = os.environ.get("JARVIS_AGENT_KEY_FILE", "").strip()
    ok = bool(path) and Path(path).is_file()
    check("the agent model's key is in a file the environment points to (value never printed)", ok,
          "JARVIS_AGENT_KEY_FILE is not set" if not path else ("file exists" if ok else "file missing"))


def local_team(with_adviser: bool):
    """The local specialists answer through the plugin host (no quota); with the model path, one team run whose
    public adviser is reached through the privacy gateway."""
    s = run_entry("local_team", "team_status", {}, wait=30)
    d = s.get("data") or {}
    check("local team: local models, decision model and privacy gateway are up", d.get("local_models") == "up"
          and d.get("privacy_gateway") == "up" and not str(d.get("decision_model", "down")).startswith("down"),
          f"models {d.get('local_models')}, decision {d.get('decision_model')}, gateway {d.get('privacy_gateway')}")
    r = run_entry("local_team", "ask_local", {"task": "make my website's button turn red on hover", "specialist": "auto"}, wait=300)
    rd = r.get("data") or {}
    check("local team: the Jev decision model routes a request the keyword rules cannot", r["status"] == "succeeded"
          and rd.get("specialist") == "code" and str(rd.get("routed_by", "")).startswith("jev"), f"{rd.get('specialist')} via {rd.get('routed_by')}")
    m = run_entry("local_team", "ask_local", {"task": "What is 17 * 23? Give the final number."}, wait=300)
    md = m.get("data") or {}
    check("local team: the maths specialist answers 17 * 23 = 391 on this PC", m["status"] == "succeeded" and md.get("specialist") == "math"
          and "391" in str(md.get("answer")), f"{md.get('specialist')} via {md.get('routed_by')}, {md.get('tokens_per_second')} tok/s")
    c = run_entry("local_team", "ask_local", {"task": "Write a Python function add(a, b) that returns the sum. Code only."}, wait=300)
    cd = c.get("data") or {}
    check("local team: the code specialist writes the function", c["status"] == "succeeded" and cd.get("specialist") == "code"
          and "def add" in str(cd.get("answer")), f"{cd.get('specialist')}, {cd.get('tokens_per_second')} tok/s")
    if with_adviser:
        fake = "Kite-Lemon-5521"
        t = run_entry("local_team", "solve_with_team", {"task": f"Our home wifi key = {fake}. Plan three steps to let guests "
                                                                 "join safely with a printed QR card, then write the card text "
                                                                 "including the key.", "use_adviser": "always"}, wait=600)
        td = t.get("data") or {}
        check("local team: adviser saw a placeholder, the local answer has the real value",
              t["status"] == "succeeded" and td.get("adviser_used") and sum((td.get("hidden_from_adviser") or {}).values()) >= 1
              and fake in str(td.get("answer")),
              f"{td.get('specialist')}, adviser {td.get('adviser_used')}, hidden {td.get('hidden_from_adviser')}, "
              f"{td.get('note') or ''} {t.get('error') or ''}"[:200])


def deliverables():
    """Decks and reports through the plugin host (no model call): built, animated, validated, rendered, checked."""
    fix = Path(__file__).resolve().parents[1] / "plugins" / "deliverables" / "tests" / "fixtures"
    for entry, name in (("make_presentation", "deck"), ("make_report", "report")):
        spec = json.loads((fix / f"demo_{name}.json").read_text(encoding="utf-8"))
        r = run_entry("deliverables", entry, {"spec": spec}, wait=420)
        d = r.get("data") or {}
        q = d.get("qa") or {}
        failed = [k for k, v in (q.get("checks") or {}).items() if not v]
        check(f"deliverables: the demo {name} is made and passes its checks", r["status"] == "succeeded" and q.get("passed"),
              f"{d.get('kind')} {q.get('counts')} {('failed: ' + ', '.join(failed)) if failed else ''} {r.get('error') or ''}"[:200])
        made = Path(d.get("folder") or "")
        root = Path(r"D:\jarvis\outputs\deliverables").resolve()
        if made.name and root in made.resolve().parents:       # remove only the folder this check just made
            shutil.rmtree(made, ignore_errors=True)


def research():
    """One quick research run (web search + one public-model call through the privacy gateway): cited and checked."""
    r = run_entry("research", "research_topic", {"question": "What does the OWASP Top 10 cover?", "depth": "quick"}, wait=900)
    d = r.get("data") or {}
    st, c = d.get("stats") or {}, d.get("counts") or {}
    check("research: a quick run returns cited statements that its sources support", r["status"] == "succeeded"
          and c.get("sources_cited", 0) >= 1 and st.get("supported", 0) >= 1,
          f"{c.get('sources_cited')} sources, {st.get('supported')}/{st.get('cited')} supported, {d.get('seconds')} s {r.get('error') or ''}"[:200])


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
    hindi = "नमस्ते, आप कैसे हैं?"
    telugu = "నమస్కారం, మీరు ఎలా ఉన్నారు?"
    for lang, sample in (("Hindi", hindi), ("Telugu", telugu)):
        c2, r2 = http("http://127.0.0.1:48930/v1/audio/speech", method="POST",
                      body={"model": "jarvis-local", "input": sample, "voice": "auto", "response_format": "pcm"}, timeout=180)
        s2 = len(r2) / 2 / 24000 if c2 == 200 else 0
        check(f"local TTS: {lang} text is spoken with the voice chosen by script", c2 == 200 and 0.8 < s2 < 20, f"{s2:.1f}s")
    if code != 200 or not do_asr:
        return
    # Speech in: the local int8 recognizers through the speech server's transcription endpoint, round trip per language.
    try:
        import numpy as np
        rounds = (("en", text, ("paris", "france")),
                  ("hi", hindi, ("नमस्ते", "कैसे")),
                  ("te", telugu, ("నమస్కారం", "ఉన్నారు")))     # greeting word and closing word: one vowel in the middle word varies run to run
        for lang, sample, needles in rounds:
            c3, r3 = http("http://127.0.0.1:48930/v1/audio/speech", method="POST",
                          body={"model": "jarvis-local", "input": sample, "voice": lang, "response_format": "pcm"}, timeout=180)
            pcm = np.frombuffer(r3, dtype="<i2").astype("float32") / 32768.0
            n = int(len(pcm) * 16000 / 24000)
            a16 = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype("float32")
            wav = io.BytesIO()
            with wave.open(wav, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes((a16 * 32767).astype("<i2").tobytes())
            t0 = time.time()
            c4, got_raw = http(f"http://127.0.0.1:48930/v1/audio/transcriptions?language={lang}", method="POST", raw=wav.getvalue(),
                               headers={"Content-Type": "audio/wav"}, timeout=180)
            took = time.time() - t0
            try:
                got = str(json.loads(got_raw.decode("utf-8", "replace")).get("text") or "")
            except ValueError:
                got = ""
            check(f"local ASR ({lang}, int8): the spoken sentence is transcribed back",
                  c3 == 200 and c4 == 200 and all(x in got.lower() for x in needles), f"{took:.1f}s for {len(a16) / 16000:.1f}s of audio -> {got[:50]}")
    except Exception as exc:                                    # report, do not crash the whole run
        check("local ASR: the spoken sentence is transcribed back", False, f"{type(exc).__name__}: {str(exc)[:100]}")


def model_path():
    r = run_entry("main_agent", "plan_task", {"task": "Say hello and tell me one fun fact.", "mode": "chat"}, 180)
    steps = (r.get("data") or {}).get("steps", [])
    check("model path (the agent model via the privacy gateway): a chat-mode plan is one think step",
          r["status"] == "succeeded" and [s["agent"] for s in steps] == ["think"], f"{[s.get('agent') for s in steps]} {r.get('error') or ''}"[:120])
    bad = run_entry("main_agent", "plan_task", {"task": "anything", "mode": "root"}, 30)
    check("main agent: an unknown mode is refused", bad["status"] == "failed", "refused")


def robustness():
    """Checkpoint/resume surface, without a model call or any side effect."""
    r = run_entry("main_agent", "resume_run", {"run_id": "run-that-does-not-exist"}, 45)
    check("main agent: resuming an unknown run is refused", r["status"] == "failed" and "no task to resume" in str(r.get("error")).lower(),
          str(r.get("error"))[:80])
    s = run_entry("main_agent", "run_status", {}, 45)
    check("main agent: run_status answers (state or 'not run yet')", s["status"] in ("succeeded", "failed"), str(s.get("status")))


def watchdog():
    """The self-healing watchdog (scheduled every 10 minutes) must be alive, recent and not stuck on a problem it gave up on."""
    f = DATA / "logs" / "watchdog_status.json"
    if not f.is_file():
        check("watchdog: has reported", False, "no watchdog_status.json (install-watchdog.ps1)")
        return
    age = time.time() - f.stat().st_mtime
    st = json.loads(f.read_text(encoding="utf-8"))
    check("watchdog: reported within the last 25 minutes", age < 25 * 60, f"{age / 60:.0f} min ago")
    check("watchdog: nothing it gave up on", not st.get("gave_up_on"), str(st.get("gave_up_on") or "none"))


def jarvis():
    code, h = jget("http://127.0.0.1:8799/api/health", headers=control_auth.headers_for("console"))
    k = (h or {}).get("kernel", {})
    check("JARVIS: kernel reachable and authorized, kill switch off", code == 200 and k.get("reachable") and k.get("authorized") and not k.get("killed"), str({x: k.get(x) for x in ("reachable", "authorized", "killed")}))
    _, st = jget("http://127.0.0.1:8799/api/owner/state", timeout=30, headers=control_auth.headers_for("console"))
    check("JARVIS: owner state readable and not busy", bool(st) and st.get("busy") is False, f"busy={(st or {}).get('busy')}")
    _, v = jget("http://127.0.0.1:8799/api/owner/audit/verify", timeout=60, headers=control_auth.headers_for("console"))
    check("JARVIS: audit chain verifies", bool(v) and v.get("ok") is True, str((v or {}).get("message", ""))[:60])
    _, m = jget("http://127.0.0.1:8799/api/owner/models", timeout=30, headers=control_auth.headers_for("console"))
    nodes = (m or {}).get("nodes") or []
    if isinstance(nodes, dict):
        nodes = [nodes]
    n = next((x for x in nodes if isinstance(x, dict) and x.get("name") == "laptop"), nodes[0] if nodes and isinstance(nodes[0], dict) else {})
    check("JARVIS: laptop node online", bool(n.get("online")) and float(n.get("seconds_since_report", 999)) < 120, str(n))
    r = run_entry("jarvis_bridge", "jarvis_status", {}, 45)
    check("bridge: the plugins reach JARVIS (ready)", r["status"] == "succeeded" and (r.get("data") or {}).get("ready") is True, str(r.get("data")))
    a = run_entry("jarvis_bridge", "pending_approvals", {}, 45)
    ad = a.get("data") or {}
    check("bridge: pending approvals are readable (read-only)", a["status"] == "succeeded" and isinstance(ad.get("count"), int),
          f"count={ad.get('count')}")
    k = run_entry("jarvis_bridge", "connector_overview", {}, 45)
    kd = k.get("data") or {}
    leaked = any(x in json.dumps(kd) for x in ("secret_ref", "env_refs", "endpoint"))
    check("bridge: connector overview readable and free of references", k["status"] == "succeeded" and "connectors" in kd and not leaked,
          f"{len(kd.get('connectors', []))} connector(s)")
    _, c = jget("http://127.0.0.1:8799/api/owner/connectors", timeout=30, headers=control_auth.headers_for("console"))
    check("JARVIS: connector registry readable", bool(c) and "connectors" in c, f"{len((c or {}).get('connectors', []))} connector(s)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-asr", action="store_true")
    ap.add_argument("--no-model", action="store_true")
    a = ap.parse_args()
    def guard(label, fn, *args):
        try:
            fn(*args)
        except Exception as exc:                      # a surprise is a failed check, never a crashed run
            check(f"{label} (check could not complete)", False, f"{type(exc).__name__}: {str(exc)[:100]}")

    guard("servers", servers)
    guard("privacy", privacy)
    guard("tool server", tool_server)
    guard("plugins", plugins)
    guard("agent key", agent_key)
    guard("documents", documents)
    guard("vault", vault)
    guard("speech", speech, not a.no_asr)
    guard("local team", local_team, not a.no_model)
    guard("deliverables", deliverables)
    if not a.no_model:
        guard("research", research)
    if not a.no_model:
        guard("model path", model_path)
    guard("robustness", robustness)
    guard("watchdog", watchdog)
    guard("JARVIS", jarvis)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
