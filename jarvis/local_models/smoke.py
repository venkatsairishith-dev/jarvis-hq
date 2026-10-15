"""Ask each local specialist one question through llama-swap and report time, speed and the answer. Windows; any Python.

    python smoke.py [--base http://127.0.0.1:8080/v1]

Local only (no Dahl, no quota). The first call to a model includes loading it.
"""
import argparse
import json
import time
import urllib.request

CASES = [
    ("code", "Write a Python function is_palindrome(s) that ignores case and spaces. Code only.",
     lambda a: "def is_palindrome" in a),
    ("math", "What is 17 * 23 + 144 / 12? Give the final number.", lambda a: "403" in a),
    ("general", "In one sentence: why does the sky look blue?", lambda a: "scatter" in a.lower()),
]


def ask(base, model, prompt, max_tokens=300):
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0.2, "messages": [{"role": "user", "content": prompt}],
            "chat_template_kwargs": {"enable_thinking": False}}
    t0 = time.time()
    r = json.load(urllib.request.urlopen(urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                                                headers={"Content-Type": "application/json"}), timeout=300))
    dt = time.time() - t0
    msg = r["choices"][0]["message"]
    timings = r.get("timings") or {}
    return (msg.get("content") or "").strip(), dt, timings.get("predicted_per_second")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080/v1")
    a = ap.parse_args()
    ok_all = True
    for model, prompt, ok in CASES:
        try:
            ans, dt, tps = ask(a.base, model, prompt)
            good = ok(ans)
        except Exception as e:                                  # noqa: BLE001
            ans, dt, tps, good = f"{type(e).__name__}: {e}", 0, None, False
        ok_all &= good
        tps_s = f"{tps:.0f} tok/s" if tps else "-"
        print(f"[{'PASS' if good else 'FAIL'}] {model:8s} {dt:5.1f}s {tps_s:>10s} | {ans[:110]!r}", flush=True)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
