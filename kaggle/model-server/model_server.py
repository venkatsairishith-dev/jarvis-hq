"""JARVIS model server on Kaggle (owner rule: Kaggle/Colab host models only; agents and tests run on the laptop).

Runs as a private Kaggle script with GPU and internet. Serves open models with Ollama and exposes them through ngrok on the
owner's static domain. ngrok refuses every caller except the JARVIS server's public address, so only the JARVIS model
endpoint (lane checks, scrubbing, audit) can use it. Traffic passes ngrok, so JARVIS treats it as public lane.
The ngrok token comes from Kaggle Secrets (NGROK_AUTHTOKEN, attached to this notebook by the owner); it is never printed.
Stops by itself after RUN_HOURS to save the weekly GPU quota.
"""
import json
import os
import subprocess
import time
import urllib.request

NGROK_DOMAIN = "letdown-campsite-ecology.ngrok-free.dev"   # the owner's free ngrok dev domain (not a secret)
VM_IP = "129.159.236.212"                       # hermes-agent-vm public egress address
MODELS = ["gpt-oss:20b"]
CONTEXT = 32768
RUN_HOURS = 8
IDLE_MINUTES = 90        # stop early when nobody has used the models for this long


def sh(cmd, timeout=3600):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()[-600:]


def up(url):
    try:
        return urllib.request.urlopen(url, timeout=5).read().decode()
    except Exception:
        return None


def main():
    print("check: gpu", bool(sh("command -v nvidia-smi")), "| internet", bool(up("https://ollama.com")),
          "| secrets service configured", any(k.startswith("KAGGLE_USER_SECRETS") for k in os.environ), flush=True)
    # The ngrok token comes from the owner's PRIVATE dataset (runs started through the Kaggle API do not get editor
    # secrets); Kaggle Secrets is the fallback. Fail fast without it: no point pulling models.
    import glob
    found = glob.glob("/kaggle/input/**/ngrok_authtoken.txt", recursive=True)
    ngrok_token = open(found[0]).read().strip() if found else None
    if not ngrok_token:
        try:
            from kaggle_secrets import UserSecretsClient
            ngrok_token = UserSecretsClient().get_secret("NGROK_AUTHTOKEN")
        except Exception as e:
            print("ngrok token not available (dataset jarvis-ngrok or secret NGROK_AUTHTOKEN):", type(e).__name__, flush=True)
            raise SystemExit(2)
    print("ngrok token: found", "(dataset)" if found else "(secret)", flush=True)
    print(sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader"), flush=True)
    if not sh("command -v ollama"):
        print(sh("apt-get -qq install -y zstd >/dev/null 2>&1; curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tail -2"), flush=True)
    env = {**os.environ, "OLLAMA_HOST": "0.0.0.0:11434",   # loopback-only Ollama refuses the tunnel's Host header; the container has no other inbound path "OLLAMA_KEEP_ALIVE": "-1", "OLLAMA_CONTEXT_LENGTH": str(CONTEXT),
           "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_NUM_PARALLEL": "1"}
    ollama = subprocess.Popen(["ollama", "serve"], env=env, stdout=open("/kaggle/working/ollama.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        if up("http://127.0.0.1:11434/api/version"):
            break
        time.sleep(1)
    print("ollama", up("http://127.0.0.1:11434/api/version"), flush=True)
    for m in MODELS:
        print("pull", m, sh(f"ollama pull {m} 2>&1 | tail -1"), flush=True)
    warm = urllib.request.Request("http://127.0.0.1:11434/api/generate", headers={"Content-Type": "application/json"},
                                  data=json.dumps({"model": MODELS[0], "prompt": "hi", "stream": False, "options": {"num_predict": 4}}).encode())
    urllib.request.urlopen(warm, timeout=900).read()
    ps = json.loads(up("http://127.0.0.1:11434/api/ps"))["models"]
    print("loaded", [(p["name"], p.get("context_length"), f"{100 * p['size_vram'] // p['size']}% GPU") for p in ps], flush=True)

    if not sh("command -v ngrok"):
        sh("curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc > /etc/apt/trusted.gpg.d/ngrok.asc && "
           "echo 'deb https://ngrok-agent.s3.amazonaws.com buster main' > /etc/apt/sources.list.d/ngrok.list && "
           "apt-get -qq update >/dev/null && apt-get -qq install -y ngrok >/dev/null")
    print(sh("ngrok version"), flush=True)
    with open("/kaggle/working/policy.yml", "w") as fh:
        fh.write(f"on_http_request:\n  - expressions:\n      - \"conn.client_ip != '{VM_IP}'\"\n"
                 f"    actions:\n      - type: deny\n        config:\n          status_code: 403\n")
    nenv = {**os.environ, "NGROK_AUTHTOKEN": ngrok_token}
    ngrok_token = None
    ngrok = subprocess.Popen(["ngrok", "http", "11434", "--url", f"https://{NGROK_DOMAIN}", "--traffic-policy-file",
                              "/kaggle/working/policy.yml", "--log", "/kaggle/working/ngrok.log", "--log-format", "json"],
                             env=nenv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    nenv = None
    time.sleep(8)
    print("tunnel", "up" if ngrok.poll() is None else "FAILED", f"https://{NGROK_DOMAIN}", flush=True)

    end = time.time() + RUN_HOURS * 3600
    last_count, last_change = -1, time.time()
    while time.time() < end and ngrok.poll() is None and ollama.poll() is None:
        time.sleep(300)
        try:   # requests served so far, from the ngrok agent's local API
            t = json.loads(up("http://127.0.0.1:4040/api/tunnels") or "{}").get("tunnels") or [{}]
            count = ((t[0].get("metrics") or {}).get("http") or {}).get("count", 0)
        except ValueError:
            count = last_count
        if count != last_count:
            last_count, last_change = count, time.time()
        idle = (time.time() - last_change) / 60
        print(time.strftime("%H:%M"), "alive, requests", count, f"idle {idle:.0f} min", flush=True)
        if idle >= IDLE_MINUTES:
            print("no requests for", IDLE_MINUTES, "minutes: stopping to save GPU quota", flush=True)
            break
    ngrok.terminate(); ollama.terminate()
    print("stopped", flush=True)


main()
