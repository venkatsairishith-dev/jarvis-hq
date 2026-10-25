r"""Can the local chat models pick the right tool and arguments for the simple goals of the face (2026-10-26)? Direct calls to llama-swap with a small face-like tool list.

    python tool_call_check.py ling-heretic granite-abliterated [--runs 5]

Cases (the first step of each goal only; the desktop plugin's own decider does the rest):
  open     "Open Notepad and write a shopping list: milk, eggs, bread." -> desktop__run_pc_action with action open_app and target Notepad
  screen   "What is on my screen right now?"                            -> desktop__whats_on_screen
  weather  "What's the weather in Paris?"                               -> get_weather with a location holding Paris
A run is right when a tool call with that name (and arguments) comes back as valid JSON. Numbers only; results/toolcall-<date>.json.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = [
    {"type": "function", "function": {"name": "desktop__run_pc_action", "description": "Operate a Windows app. action: open_app (target = Start-menu name), focus, click, type, key. target: the app or 'process|element'. text: the text or key.",
                                      "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["open_app", "focus", "click", "type", "key"]}, "target": {"type": "string"}, "text": {"type": "string"}}, "required": ["action", "target"]}}},
    {"type": "function", "function": {"name": "desktop__whats_on_screen", "description": "Which window is in front and which others are open (app and title only). Read-only.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_weather", "description": "The current weather of a place.", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}},
]
CASES = {
    "open": ("Open Notepad and write a shopping list: milk, eggs, bread.", lambda n, a: n == "desktop__run_pc_action" and a.get("action") == "open_app" and "notepad" in str(a.get("target", "")).lower()),
    "screen": ("What is on my screen right now?", lambda n, a: n == "desktop__whats_on_screen"),
    "weather": ("What's the weather in Paris?", lambda n, a: n == "get_weather" and "paris" in str(a.get("location", "")).lower()),
}
SYSTEM = "You are JARVIS, the owner's assistant. Use the tools to do what the owner asks. Call a tool whenever one fits; answer in plain words only when none does."


def ask(url: str, model: str, text: str) -> tuple[str, dict, float, str]:
    body = {"model": model, "temperature": 0.3, "max_tokens": 200, "tools": TOOLS, "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]}
    t = time.time()
    req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        msg = json.loads(r.read())["choices"][0]["message"]
    secs = time.time() - t
    calls = msg.get("tool_calls") or []
    if not calls:
        return "", {}, secs, (msg.get("content") or "")[:80]
    fn = calls[0]["function"]
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except ValueError:
        return fn.get("name", ""), {"_bad_json": True}, secs, ""
    return fn.get("name", ""), args, secs, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--runs", type=int, default=5)
    a = ap.parse_args()
    rows = []
    for m in a.models:
        for case, (text, ok) in CASES.items():
            right, bad_json, secs, said = 0, 0, [], []
            for _ in range(a.runs):
                name, args, s, content = ask(a.url, m, text)
                secs.append(s)
                bad_json += 1 if args.get("_bad_json") else 0
                right += 1 if name and ok(name, args) else 0
                if not name:
                    said.append(content)
            row = {"model": m, "case": case, "right": right, "of": a.runs, "bad_json": bad_json, "median_s": round(sorted(secs)[len(secs) // 2], 1), "answered_without_a_tool": said[:2]}
            rows.append(row)
            print(json.dumps(row), flush=True)
    res = HERE / "results"
    res.mkdir(exist_ok=True)
    path = res / f"toolcall-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}, indent=1), encoding="utf-8")
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
