"""Compare local decision/planning models on the CPU test server, one at a time, through the REAL pipeline.

    python jarvis/evals/quality95/bench/model_compare.py --fetch                      # download pinned files, SHA-256 checked
    python jarvis/evals/quality95/bench/model_compare.py --models qwen3-4b-2507,qwen3.5-2b [--sets dev,holdout2] [--repeat 1]

Each model is started as its own llama-server (JARVIS_LLAMA_SERVER; the build is read from its --version and saved with the
results; a candidate's "args" add server flags such as a quantised KV cache), then every case goes
through jarvis/plugins/desktop/decide.py (same prompt, JSON-schema grammar and deterministic rules as the laptop) and is
scored with eval_decision.score(). Measured per model: accuracy per set, JSON validity, cold start (process start to
first answer), prompt and generation time from llama.cpp's own timings, tokens/s, peak RSS, CPU seconds, timeouts.
Names in the requests are replaced by made-up ones with a matching synthetic profile list (as live_plans_dahl.py).

CPU numbers are THIS server's (Xeon Gold 6244, threads capped); laptop GPU latency is a different machine and is not
estimated from them. Models come only from candidates.json (pinned repo, file and Hugging Face SHA-256); a name that is
not there is refused. Results: results/models-<time>.json (key "models").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
for p in (ROOT, ROOT / "jarvis" / "evals" / "pc_control", HERE):
    sys.path.insert(0, str(p))
from jarvis.plugins.desktop import decide  # noqa: E402
import eval_decision  # noqa: E402
import live_plans_dahl as LP  # noqa: E402  (SETS, sanitize, PROFILES)

CANDIDATES = json.loads((HERE / "model_candidates.json").read_text(encoding="utf-8"))
MODEL_DIR = Path(os.environ.get("JARVIS_MODEL_DIR", "/models"))
SERVER = os.environ.get("JARVIS_LLAMA_SERVER", "/llama/llama-server")
PORT = int(os.environ.get("JARVIS_BENCH_PORT", "18080"))
THREADS = int(os.environ.get("JARVIS_BENCH_THREADS", "4"))


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch(name: str) -> Path:
    c = CANDIDATES[name]
    path = MODEL_DIR / c["file"]
    if path.exists() and sha256(path) == c["sha256"]:
        return path
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/{c['repo']}/resolve/{c['revision']}/{c['file']}"
    tmp = path.with_suffix(".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "jarvis-bench"}), timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    got = sha256(tmp)
    if got != c["sha256"]:
        tmp.unlink()
        raise RuntimeError(f"{name}: checksum mismatch ({got[:12]} != {c['sha256'][:12]}): not used")
    tmp.rename(path)
    return path


def rss_mb(pid: int) -> float:
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmHWM:"):
                return round(int(line.split()[1]) / 1024)
    except OSError:
        pass
    return 0.0


def cpu_s(pid: int) -> float:
    try:
        f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        return round((int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK"), 1)
    except (OSError, IndexError):
        return 0.0


def server_version() -> str:
    """`llama-server --version` as one line ("version: 0.7.0-dev (build 11714, commit c0fd78626); built with GNU 11.4.0 ..."),
    so a result file says which build produced it. A local source build reports "build 1"; its commit still identifies it."""
    try:
        out = subprocess.run([SERVER, "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return f"unknown ({type(e).__name__})"
    keep = [ln.strip() for ln in (out.stdout + out.stderr).splitlines() if ln.startswith(("version:", "built with"))]
    return "; ".join(keep) or "unknown"


def start(name: str):
    c = CANDIDATES[name]
    path = MODEL_DIR / c["file"]
    if not path.exists() or sha256(path) != c["sha256"]:
        raise RuntimeError(f"{name}: file missing or not the pinned one; run --fetch")
    args = [SERVER, "-m", str(path), "--host", "127.0.0.1", "--port", str(PORT), "--jinja", "--no-webui", "-t", str(THREADS),
            "-c", str(c.get("ctx", 2048)), "-np", "1"] + c.get("args", [])
    t0 = time.time()
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(600):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                if r.status == 200:
                    return proc, time.time() - t0
        except Exception:  # noqa: BLE001 - loading
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"{name}: llama-server exited with {proc.returncode}")
        time.sleep(0.5)
    proc.kill()
    raise RuntimeError(f"{name}: not ready after 300 s")


def make_post(timings: list):
    def post(body):
        body = dict(body)
        body.pop("model", None)
        r = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", data=json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=300) as resp:
            out = json.loads(resp.read())
        t = out.get("timings") or {}
        timings.append({"prompt_n": t.get("prompt_n"), "prompt_ms": t.get("prompt_ms"), "gen_n": t.get("predicted_n"),
                        "gen_ms": t.get("predicted_ms"), "gen_tps": t.get("predicted_per_second")})
        return str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    return post


def run(name: str, sets: list[str], repeat: int) -> dict:
    proc, load_s = start(name)
    timings: list = []
    post = make_post(timings)
    rows, lat = [], []
    try:
        t0 = time.time()
        first = decide.decide("open notepad and write hello", LP.PROFILES, model=name, post=post, timeout=300)
        cold_first = time.time() - t0
        for s in sets:
            cases = [{**c, "req": LP.sanitize(c["req"]), "context": LP.sanitize(c.get("context", ""))}
                     for c in json.loads(LP.SETS[s].read_text(encoding="utf-8"))]
            for rep in range(repeat):
                for c in cases:
                    t1 = time.time()
                    try:
                        p = decide.decide(c["req"], LP.PROFILES, model=name, post=post, timeout=300, context=c["context"])
                        ok, why = eval_decision.score(p, c["expect"])
                        status, json_ok = p.status, not (p.status == "fallback" and "JSON" in " ".join(p.problems))
                    except Exception as e:  # noqa: BLE001 - a timeout or crash is a failed case
                        ok, why, status, json_ok, p = False, f"{type(e).__name__}", "error", False, None
                    dt = time.time() - t1
                    if p is not None and p.model != "rule":
                        lat.append(dt)
                    rows.append({"set": s, "id": c["id"], "rep": rep, "ok": ok, "why": why[:120], "status": status,
                                 "json_ok": json_ok, "rule_path": p is not None and p.model == "rule", "s": round(dt, 2)})
        peak, cpu = rss_mb(proc.pid), cpu_s(proc.pid)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    model_rows = [r for r in rows if not r["rule_path"]]
    per_set = {s: f"{sum(r['ok'] for r in rows if r['set'] == s)}/{sum(1 for r in rows if r['set'] == s)}" for s in sets}
    gen = [t for t in timings if t.get("gen_tps")]
    lat.sort()
    return {"model": name, "repo": CANDIDATES[name]["repo"], "file": CANDIDATES[name]["file"], "sha256": CANDIDATES[name]["sha256"],
            "threads": THREADS, "args": CANDIDATES[name].get("args", []), "sets": per_set, "correct": sum(r["ok"] for r in rows), "total": len(rows),
            "model_path_correct": f"{sum(r['ok'] for r in model_rows)}/{len(model_rows)}",
            "json_valid": f"{sum(r['json_ok'] for r in model_rows)}/{len(model_rows)}",
            "errors": sum(r["status"] == "error" for r in rows), "load_s": round(load_s, 1), "cold_first_call_s": round(cold_first, 1),
            "first_call_status": first.status, "p50_s": round(statistics.median(lat), 2) if lat else None,
            "p95_s": round(lat[int(0.95 * (len(lat) - 1))], 2) if lat else None,
            "gen_tps_p50": round(statistics.median(t["gen_tps"] for t in gen), 1) if gen else None,
            "gen_tokens_p50": statistics.median(t["gen_n"] for t in gen) if gen else None,
            "prompt_ms_p50": round(statistics.median(t["prompt_ms"] for t in gen), 0) if gen else None,
            "peak_rss_mb": peak, "server_cpu_s": cpu,
            "failures": [{k: r[k] for k in ("set", "id", "why")} for r in rows if not r["ok"]]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="")
    ap.add_argument("--sets", default="plan_cases,dev,holdout,holdout2")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--fetch", action="store_true")
    a = ap.parse_args(argv)
    names = [m for m in a.models.split(",") if m] or list(CANDIDATES)
    unknown = [m for m in names if m not in CANDIDATES]
    if unknown:
        raise SystemExit(f"not in model_candidates.json (refused): {unknown}")
    if a.fetch:
        for m in names:
            print(m, fetch(m), flush=True)
        return
    out = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "host": "Xeon Gold 6244 CPU", "threads": THREADS,
           "llama_cpp": server_version(), "server": SERVER, "prompt_version": decide.PROMPT_VERSION, "sets": a.sets.split(","), "models": []}
    for m in names:
        try:
            r = run(m, a.sets.split(","), a.repeat)
        except Exception as e:  # noqa: BLE001 - one broken candidate must not stop the comparison
            r = {"model": m, "error": f"{type(e).__name__}: {e}"[:300]}
        out["models"].append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "failures"}), flush=True)
    res = HERE.parent / "results"
    res.mkdir(exist_ok=True)
    p = res / f"models-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("saved", p)


if __name__ == "__main__":
    main()
