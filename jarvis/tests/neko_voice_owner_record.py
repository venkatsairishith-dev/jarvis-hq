"""The owner's real "Hey Neko" session (2026-10-16). Windows, neko-venv; the wake listener (48945) keeps running.
RUN BY THE OWNER, at the laptop, in a terminal:
    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe jarvis\\tests\\neko_voice_owner_record.py

What it does, with the owner's consent (typed YES):
  * for each distance (0.5 m, then 1.5 m) it asks for "Hey Neko" 10 times, one prompt every 5 seconds, and records the
    microphone for that session to D:\\jarvis\\outputs\\neko\\voice-owner\\<time>\\<distance>.wav (16 kHz mono). The audio
    stays on this laptop; it is the owner's voice and is only used to measure and tune the wake word offline
    (jarvis\\wake\\eval_owner.py). Delete the folder at any time.
  * meanwhile it reads the live listener's detection count, so the LIVE recall is measured too (no synthetic audio).
  * per prompt it stores the loudness (dBFS) of the speech and of the room, so a near-silent take is visible.
Results: the WAVs + session.json next to them; a summary row in jarvis/evals/neko95/results/voice-owner-<time>.json
"""
import json
import math
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

RATE = 16000
OUT = Path(r"D:\jarvis\outputs\neko\voice-owner") / time.strftime("%Y%m%d-%H%M%S")
STATUS = "http://127.0.0.1:48945/status"
PROMPTS, GAP = 10, 5.0


def dbfs(x: np.ndarray) -> float:
    rms = math.sqrt(float(np.mean(np.square(x.astype(np.float64))))) if len(x) else 0.0
    return round(20 * math.log10(max(rms, 1.0) / 32768.0), 1)


def detections() -> int | None:
    try:
        with urllib.request.urlopen(STATUS, timeout=3) as r:
            s = json.loads(r.read())
        return int((s.get("counts") or {}).get("detections", 0))
    except Exception:  # noqa: BLE001
        return None


def session(label: str) -> dict:
    print(f"\n=== {label}: stand or sit about {label} from the laptop. Speak normally, as you would to a person.")
    input("Press Enter when you are in place (recording starts after a 3-second countdown)... ")
    for n in (3, 2, 1):
        print(n, flush=True)
        time.sleep(1)
    chunks = []
    stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", callback=lambda d, f, t, s: chunks.append(d.copy()))
    before = detections()
    prompts = []
    t0 = time.time()
    with stream:
        time.sleep(2.0)                                           # room tone first
        for i in range(PROMPTS):
            at = time.time() - t0
            print(f'  ({i + 1}/{PROMPTS})  say: "Hey Neko"', flush=True)
            prompts.append(round(at, 2))
            time.sleep(GAP)
    after = detections()
    audio = np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, dtype=np.int16)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{label.replace(' ', '').replace('.', '_')}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())
    room = dbfs(audio[: int(1.8 * RATE)])
    loud = []
    for p in prompts:                                             # the loudest 0.8 s inside each prompt window
        seg = audio[int(p * RATE): int((p + GAP - 0.5) * RATE)]
        win = int(0.8 * RATE)
        best = max((dbfs(seg[k:k + win]) for k in range(0, max(1, len(seg) - win), int(0.1 * RATE))), default=-99.0)
        loud.append(best)
    live = (after - before) if (before is not None and after is not None) else None
    print(f"  live wakes counted by the listener: {live} of {PROMPTS}; room {room} dBFS; speech peaks {loud}")
    return {"label": label, "wav": str(path), "prompts_s": prompts, "gap_s": GAP, "room_dbfs": room, "speech_dbfs": loud,
            "live_wakes": live}


def main():
    print(__doc__.split("What it does")[0])
    print("This records YOUR VOICE to", OUT.parent, "(on this laptop only) to measure and tune 'Hey Neko'.")
    if input('Type YES to agree and start, anything else to stop: ').strip() != "YES":
        print("Stopped. Nothing was recorded.")
        return 1
    res = [session("0.5 m"), session("1.5 m")]
    (OUT / "session.json").write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "sessions": res}, indent=1), encoding="utf-8")
    rows = []
    for s, cid in zip(res, ("V-real-recall-0.5m", "V-real-recall-1.5m")):
        quiet = sum(1 for x in s["speech_dbfs"] if x < s["room_dbfs"] + 6)
        if s["live_wakes"] is None:
            st, det = "not_run", "the listener's count could not be read"
        elif quiet > PROMPTS // 2:
            st, det = "not_run", f"{quiet} of {PROMPTS} takes were within 6 dB of the room: inconclusive"
        else:
            rate = s["live_wakes"] / PROMPTS
            st = "pass" if rate >= (0.95 if cid.endswith("0.5m") else 0.85) else "fail"
            det = f"live recall {s['live_wakes']}/{PROMPTS}; room {s['room_dbfs']} dBFS; quiet takes {quiet}"
        rows.append({"cap": "C1_voice", "id": cid, "status": st, "detail": det + f"; audio {s['wav']}"})
    out = Path(__file__).resolve().parents[1] / "evals" / "neko95" / "results" / f"voice-owner-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}, indent=1), encoding="utf-8")
    print("\nSaved:", OUT, "and", out)
    for r in rows:
        print(f"  {r['id']}: {r['status']} ({r['detail']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
