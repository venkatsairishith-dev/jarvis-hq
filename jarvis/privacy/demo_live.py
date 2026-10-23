"""One live call to show the privacy gateway working end to end (costs one small provider request). Windows; agent-venv.

    D:\\jarvis\\tools\\agent-venv\\Scripts\\python.exe jarvis\\privacy\\demo_live.py      (from the repo root)

Uses the plugins' agent-model settings (jarvis/pluginapi/models.py: JARVIS_AGENT_BASE_URL, JARVIS_AGENT_MODEL,
JARVIS_AGENT_KEY_FILE; the key is read from its file and never printed). Sends a made-up password, prints what the public model
received (the gateway's preview of the same text) and the reply as the caller gets it (placeholder restored locally).
"""
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from jarvis.pluginapi.models import agent_model  # noqa: E402

FAKE = "Blue-Mango-7731"
MAIL = "ravi.demo@example.com"
TEXT = (f"my wifi password = {FAKE} (keep it private). My e-mail is {MAIL}. "
        "Write a one-line e-mail signature for me that includes my e-mail address.")

cfg = agent_model()
base, model = str(cfg.get("base_url") or ""), str(cfg.get("model") or "")
if "127.0.0.1:48950" not in base:
    sys.exit(f"the agent model does not go through the gateway (endpoint {base}); nothing sent")

preview = json.load(urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:48950/redact", data=json.dumps({"text": TEXT}).encode(), headers={"Content-Type": "application/json"}), timeout=30))
req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=json.dumps({
    "model": model, "max_tokens": 800, "messages": [{"role": "user", "content": TEXT}]}).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + str(cfg.get("api_key") or "")})
reply = json.load(urllib.request.urlopen(req, timeout=180))["choices"][0]["message"]["content"] or ""
answer = re.sub(r"(?s)<think>.*?</think>", "", reply).strip()

print("model            :", model)
print("you typed        :", TEXT)
print("provider received:", preview["text"])
print("reply to caller  :", answer[:200])
print("RESULT", "the provider never saw the password:", FAKE not in preview["text"], "| nor the e-mail:", MAIL not in preview["text"],
      "| e-mail restored in the reply:", MAIL in answer)
