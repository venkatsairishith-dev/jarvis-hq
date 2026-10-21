"""Neko 9.5, C1 V-echo-no-self-wake, acoustic and live (2026-10-16). Windows, neko-venv; speech server 48930 and the
wake listener 48945 running. AUDIBLE: the laptop says "Hey Neko" through its speakers 5 times.
    python jarvis\\tests\\neko_echo_live.py
Neko's own voice is made by the speech server (which marks Neko as speaking) and played through the speakers; the real
listener hears it through the real microphone. Pass: no wake, and the listener's "ignored while Neko was speaking" count
went up (it heard the keyword and held back). If neither count moves, the microphone did not pick the speech up:
inconclusive, recorded as not run.
Results: jarvis/evals/neko95/results/echo-<time>.json
"""
import io
import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import control_auth  # noqa: E402  the wake listener's /status needs its token (re-review M3)
import numpy as np
import sounddevice as sd

SPEECH = "http://127.0.0.1:48930"
STATUS = "http://127.0.0.1:48945/status"
TEXTS = ["Hey Neko, this is Neko speaking.", "Hey Neko. I will check that for you.", "Hey Neko, the song is playing now.",
         "Okay. Hey Neko is my name.", "Hey Neko, done."]


def counts():
    with urllib.request.urlopen(urllib.request.Request(STATUS, headers=control_auth.headers_for("wake")), timeout=5) as r:
        return json.loads(r.read()).get("counts") or {}


def say(text):
    req = urllib.request.Request(f"{SPEECH}/v1/audio/speech", data=json.dumps(
        {"model": "local", "input": text, "voice": "en", "response_format": "wav"}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    with wave.open(io.BytesIO(data)) as w:
        rate, pcm = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    sd.play(pcm, rate)
    sd.wait()


def main():
    before = counts()
    for t in TEXTS:
        say(t)
        time.sleep(2.5)
    time.sleep(3)
    after = counts()
    woke = after.get("detections", 0) - before.get("detections", 0)
    held = after.get("ignored_while_speaking", 0) - before.get("ignored_while_speaking", 0)
    if woke == 0 and held > 0:
        st = "pass"
    elif woke > 0:
        st = "fail"
    else:
        st = "not_run"
    detail = f"{len(TEXTS)} spoken 'Hey Neko' in Neko's own voice through the speakers: wakes {woke}, held back while speaking {held}" + (
        " (the microphone did not pick the keyword up: inconclusive)" if st == "not_run" else "")
    print(st.upper(), "V-echo-no-self-wake", detail)
    out = Path(__file__).resolve().parents[1] / "evals" / "neko95" / "results" / f"echo-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": [
        {"cap": "C1_voice", "id": "V-echo-no-self-wake", "status": st, "detail": detail}]}, indent=1), encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    sys.exit(main())
