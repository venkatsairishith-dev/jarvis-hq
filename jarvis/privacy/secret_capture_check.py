"""Does a secret on screen reach the provider? A spare privacy gateway (port 48997, the code in this repo) forwards to a LOCAL capture server (48996) instead of a provider; the raw
Notepad text goes in as a tool result, as the face would send it. Only request BODIES are recorded; headers (the caller's key) are never read or printed. Fake secrets only."""
import json, os, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

CAP = []

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        CAP.append(body)
        out = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "capture", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

srv = HTTPServer(("127.0.0.1", 48996), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

prov = {"cooldown_seconds": 60, "providers": [{"name": "capture", "base_url": "http://127.0.0.1:48996/v1", "enabled": True, "max_concurrent": 4, "queue_wait_s": 5, "model": "capture-model"}]}
pf = os.path.join(tempfile.gettempdir(), "providers-capture.json")
json.dump(prov, open(pf, "w"))
env = dict(os.environ, JARVIS_LATENCY_LOG="off")
gw = subprocess.Popen([r"D:\jarvis\tools\privacy-venv\Scripts\python.exe", r"D:\jarvis\hq\jarvis\privacy\gateway.py", "--port", "48997", "--providers", pf],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=r"D:\jarvis\hq")
try:
    for _ in range(90):
        try:
            if urllib.request.urlopen("http://127.0.0.1:48997/health", timeout=3).status == 200:
                break
        except Exception:
            time.sleep(1)
    SECRETS = ["Hunter2-Zebra!", "4111 1111 1111 1111", "4111111111111111", "sk-test-ABCDEF0123456789abcdef0123456789", "ABCDEF0123456789abcdef0123456789"]
    PERSONAL = ["Zorblat Quimby", "Zorblat", "Quimby", "77413", "83521"]
    raw = "password: Hunter2-Zebra!\ncard 4111 1111 1111 1111\napi key sk-test-ABCDEF0123456789abcdef0123456789\norder 77413 for Zorblat Quimby, room 83521"
    def ask(messages):
        CAP.clear()
        req = urllib.request.Request("http://127.0.0.1:48997/v1/chat/completions", data=json.dumps({"model": "x", "messages": messages, "stream": False}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer jarvis-local"})
        try:
            urllib.request.urlopen(req, timeout=120).read()
        except Exception as e:
            print("   request error:", type(e).__name__)
        return CAP[-1] if CAP else ""
    cases = {
        "A. raw text as a TOOL RESULT (what would happen if the plugin did not scrub)": [
            {"role": "user", "content": "read the notepad window"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_window_text", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": raw}],
        "B. raw text typed in the USER message": [{"role": "user", "content": "what does my notepad say?\n" + raw}],
    }
    for title, msgs in cases.items():
        body = ask(msgs)
        print("==", title, "| bytes forwarded:", len(body))
        leaked = [s for s in SECRETS if s in body]
        print("   secrets that reached the provider side:", leaked or "NONE")
        print("   personal values that reached it:", [p for p in PERSONAL if p in body] or "NONE")
        text = ""
        try:
            for m in json.loads(body).get("messages", []):
                c = m.get("content")
                if m.get("role") in ("tool", "user") and isinstance(c, str): text = c
        except Exception:
            pass
        print("   what the provider received:", " | ".join(text.replace("\n", " / ").split())[:420])
finally:
    gw.terminate()
    try: gw.wait(10)
    except Exception: gw.kill()
    srv.shutdown()
