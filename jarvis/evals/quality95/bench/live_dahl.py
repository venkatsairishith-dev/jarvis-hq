"""Live provider-routing checks against Dahl (public lane) through the REAL kernel gateway and model proxy.

    JARVIS_SECRETS=<dir with providers/dahl.key> python jarvis/evals/quality95/bench/live_dahl.py [--repeat 5]

Only the enabled dahl-* entries of jarvis/config/providers.json are used (every other provider is switched off for the
run, so nothing else is called). Every prompt is synthetic: Dahl is public-lane only and never gets private data.
The provider key is never printed; the checks look for it in every response body, header, error and audit row.
Results: results/live-dahl-<time>.json (key "cases", so the v1 scorer never reads it). Owner-authorised 2026-10-16.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "jarvis" / "kernel"))
from jarvis_kernel import gateway  # noqa: E402
from jarvis_kernel.llm_proxy import make_handler  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402

FAKE_SECRET = "ghp_" + "Zx9FAKEsecretFAKEsecretFAKE00000000"   # synthetic, must never reach the provider


def dahl_providers():
    provs = gateway.load(str(ROOT / "jarvis" / "config" / "providers.json"))
    keep = [p for p in provs if p.name.startswith("dahl") and getattr(p, "enabled", True)]
    return keep


class Run:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.provs = dahl_providers()
        H, tokens = make_handler(self.k, self.provs)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.pub = tokens.issue("bench-public", "public")
        self.priv = tokens.issue("bench-private", "private")
        key_path = next((p.key_path() for p in self.provs if getattr(p, "key_file", None)), None)
        self.key = Path(key_path).read_text(encoding="utf-8").strip() if key_path and os.path.exists(key_path) else ""
        self.seen_text: list[str] = []

    def call(self, body, tok):
        r = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(r, timeout=320) as resp:
                raw, status, hdrs = resp.read(), resp.status, dict(resp.headers)
        except urllib.error.HTTPError as e:
            raw, status, hdrs = e.read(), e.code, dict(e.headers)
        dt = time.perf_counter() - t0
        self.seen_text.append(raw.decode("utf-8", "replace") + json.dumps(hdrs))
        return status, raw, dt

    def audit(self):
        return [json.loads(p) if p.startswith("{") else p for (p,) in self.k.db.execute("SELECT payload FROM audit")]

    def close(self):
        self.srv.shutdown()
        self.k.db.close()
        self.tmp.cleanup()


def _content(raw):
    try:
        m = json.loads(raw)["choices"][0]["message"]
        return (m.get("content") or ""), m.get("tool_calls")
    except (ValueError, KeyError, IndexError, TypeError):
        return "", None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=5)
    a = ap.parse_args(argv)
    R = Run()
    rows = []

    def rec(cid, title, ok, observed, dims, risk="high"):
        rows.append({"id": cid, "area": "J", "title": title, "risk": risk, "dims": dims,
                     "status": "pass" if ok else "fail", "observed": str(observed)[:400]})
        print(("PASS " if ok else "FAIL ") + cid, title, "|", str(observed)[:160], flush=True)

    try:
        names = [p.name for p in R.provs]
        rec("JL-001", "Only the public-lane Dahl entries are in play", names and all(p.lane == "public" for p in R.provs) and bool(R.key),
            f"providers {names}, key file present {bool(R.key)}", ["routing", "security"], "critical")
        # 1. a public task is answered (repeat for latency)
        lat, toks, served = [], [], []
        for i in range(a.repeat):
            s, raw, dt = R.call({"model": "x", "max_tokens": 64, "messages": [{"role": "user", "content": f"Synthetic check {i}: what is 17 + 25? Answer with the number only."}]}, R.pub)
            c, _ = _content(raw)
            lat.append(dt)
            try:
                toks.append(json.loads(raw).get("usage") or {})
            except ValueError:
                pass
            served.append((s, "42" in c))
        ok = sum(1 for s, good in served if s == 200 and good)
        rec("JL-002", "Public task answered correctly through the proxy", ok == a.repeat, f"{ok}/{a.repeat} correct; statuses {[s for s, _ in served]}",
            ["routing", "reliability"])
        rec("JL-003", "Public-lane latency p50 under 20 s", statistics.median(lat) < 20,
            f"p50 {statistics.median(lat):.2f} s, max {max(lat):.2f} s over {len(lat)} calls", ["speed", "routing"], "medium")
        used = [p for p in R.audit() if isinstance(p, dict) and p.get("provider", "").startswith("dahl")]
        rec("JL-004", "Calls are audited with the provider that served them", len(used) >= a.repeat,
            f"{len(used)} audited dahl calls; providers {sorted({p.get('provider') for p in used})}", ["routing", "security"])
        tot = [t.get("total_tokens", 0) for t in toks if t]
        rec("JL-005", "Token usage is reported per call", len(tot) == a.repeat and all(tot),
            f"total_tokens per call {tot}", ["resources"], "medium")
        # 2. private lane never reaches Dahl (fails closed)
        before = len([p for p in R.audit() if isinstance(p, dict) and p.get("provider", "").startswith("dahl") and p.get("lane") == "private"])
        s, raw, _ = R.call({"model": "x", "max_tokens": 16, "messages": [{"role": "user", "content": "Synthetic private-lane probe."}]}, R.priv)
        after = len([p for p in R.audit() if isinstance(p, dict) and p.get("provider", "").startswith("dahl") and p.get("lane") == "private"])
        rec("JL-006", "A private-lane task never goes to Dahl (fails closed)", s != 200 and after == before == 0,
            f"status {s}; private dahl calls {after}", ["routing", "security"], "critical")
        # 3. a synthetic secret in the prompt is scrubbed before it leaves
        s, raw, _ = R.call({"model": "x", "max_tokens": 200, "messages": [{"role": "user", "content": f"Repeat this string exactly and nothing else: {FAKE_SECRET}"}]}, R.pub)
        c, _ = _content(raw)
        rec("JL-007", "A secret in the prompt is scrubbed before Dahl sees it", s == 200 and FAKE_SECRET not in c and "FAKEsecret" not in c,
            f"status {s}; reply contains the secret: {FAKE_SECRET in c}; reply starts {c[-80:]!r}", ["security", "routing"], "critical")
        # 4. tool calls relayed
        tools = [{"type": "function", "function": {"name": "get_weather", "description": "Weather for a city",
                                                   "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
        s, raw, _ = R.call({"model": "x", "max_tokens": 400, "tools": tools, "messages": [{"role": "user", "content": "Use the tool: what is the weather in Lisbon?"}]}, R.pub)
        c, tc = _content(raw)
        args = {}
        try:
            args = json.loads(tc[0]["function"]["arguments"]) if tc else {}
        except (ValueError, KeyError, TypeError):
            pass
        rec("JL-008", "Tool call comes back well-formed", s == 200 and bool(tc) and tc[0]["function"]["name"] == "get_weather" and "lisbon" in str(args).lower(),
            f"status {s}; tool_calls {json.dumps(tc)[:200] if tc else None}", ["routing"])
        # 5. streaming
        r = urllib.request.Request(R.url + "/v1/chat/completions", method="POST", headers={"Content-Type": "application/json", "Authorization": "Bearer " + R.pub},
                                   data=json.dumps({"model": "x", "stream": True, "max_tokens": 32, "messages": [{"role": "user", "content": "Say hello."}]}).encode())
        t0 = time.perf_counter()
        first = None
        chunks = 0
        try:
            with urllib.request.urlopen(r, timeout=320) as resp:
                for line in resp:
                    if line.startswith(b"data:") and first is None:
                        first = time.perf_counter() - t0
                    chunks += line.startswith(b"data:")
                    R.seen_text.append(line.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            R.seen_text.append(e.read().decode("utf-8", "replace"))
        rec("JL-009", "Streaming works; first chunk time measured", chunks > 1 and first is not None,
            f"{chunks} data chunks, first after {first if first is None else round(first, 2)} s", ["routing", "speed"], "medium")
        # 6. bad token refused without reaching Dahl
        s, raw, _ = R.call({"model": "x", "messages": [{"role": "user", "content": "x"}]}, "not-a-token")
        rec("JL-010", "A request without a valid agent token is refused (401)", s == 401, f"status {s}", ["security"], "critical")
        # 7. the agent cannot pick a model outside the provider's pattern
        s, raw, _ = R.call({"model": "deepseek-ai/DeepSeek-V4-Flash-0731", "max_tokens": 16, "messages": [{"role": "user", "content": "Reply OK."}]}, R.pub)
        bodies = [p for p in R.audit() if isinstance(p, dict) and p.get("provider") == "dahl-deepseek"]
        rec("JL-011", "A disabled model cannot be reached by naming it", not bodies, f"status {s}; dahl-deepseek calls {len(bodies)}", ["routing", "security"])
        # 8. the key never appears anywhere the agent or the owner can read
        everything = "\n".join(R.seen_text) + json.dumps(R.audit())
        rec("JL-012", "The provider key appears in no reply, header, stream, error or audit row", bool(R.key) and R.key not in everything,
            f"searched {len(everything)} characters", ["security"], "critical")
    finally:
        R.close()
    counts = {s: sum(r["status"] == s for r in rows) for s in ("pass", "fail")}
    print(json.dumps(counts))
    out = HERE.parent / "results" / f"live-dahl-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "providers": [p.name for p in dahl_providers()],
                               "repeat": a.repeat, "counts": counts, "cases": rows}, indent=1), encoding="utf-8")
    print("saved", out)
    return 0 if not counts["fail"] else 1


if __name__ == "__main__":
    sys.exit(main())
