"""Offline check of the Hey Neko wake word with N.E.K.O's real detector and synthesized speech. Windows, neko-venv.

    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe jarvis\\wake\\eval_wake.py [--thresholds 0.15,0.2,0.25,0.3]

Clips are made by the local speech server (Kokoro English, Piper Hindi and Telugu voices), then varied: faster/slower with
a pitch change (as a different speaker would sound) and with background noise. Positives say "Hey Neko"; negatives are
near misses and ordinary sentences. This measures the detector on synthetic voices only: a real voice in a real room is
the owner's spoken test (docs/neko/NEKO_OPERATIONS.md). Clips are written under D:\\jarvis\\outputs\\neko\\wake-eval.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wake_listener as wl  # noqa: E402

OUT = Path(r"D:\jarvis\outputs\neko\wake-eval")
POSITIVE = [("en", "Hey Neko."), ("en", "Hey Neko, open notepad."), ("en", "Hey Neko, what is the time?"),
            ("en", "Hey Neko, play a Kalyani song on YouTube."), ("en", "Hey, Neko. Are you there?"),
            ("hi", "हे नेको"), ("hi", "हे नेको, नोटपैड खोलो"), ("te", "హే నెకో"), ("te", "హే నెకో, యూట్యూబ్ తెరువు")]
NEGATIVE = [("en", "Hey Nico, how are you?"), ("en", "Hello there, nice to meet you."), ("en", "Hey Siri, set a timer."),
            ("en", "That is a pain in the neck, honestly."), ("en", "Open notepad please."), ("en", "Hey, I am making some coffee."),
            ("en", "Hey Noah, come here."), ("en", "Nature documentaries are relaxing in the evening."),
            ("en", "Can you check my email and the calendar for tomorrow?"), ("en", "Hey Mekko, the meeting moved to Monday."),
            ("en", "Okay, I will call you back in ten minutes."), ("en", "Hey, nice car. Is it new?"),
            ("hi", "आज मौसम बहुत अच्छा है"), ("hi", "मुझे बाज़ार जाना है"), ("te", "ఈ రోజు వాతావరణం బాగుంది"), ("te", "నేను ఇంటికి వెళ్తున్నాను")]


def synth(lang: str, text: str, path: Path) -> bytes:
    if not path.exists():
        import urllib.request
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import loopback_net      # no proxy, loopback only, no redirect (completion H)
        req = urllib.request.Request(f"{wl.SPEECH}/v1/audio/speech", data=json.dumps(
            {"model": "local", "input": text, "voice": lang, "response_format": "wav"}).encode(), headers={"Content-Type": "application/json"})
        with loopback_net.urlopen(req, timeout=120) as r:
            path.write_bytes(r.read())
    return wl.read_wav_16k(str(path))


def variants(pcm: bytes, seed: int):
    """original, 8% faster and higher, 8% slower and lower, original with white noise at 20 dB and at 15 dB SNR."""
    import numpy as np
    from scipy.signal import resample_poly
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    out = {"orig": x}
    out["fast"] = resample_poly(x, 25, 27)     # played at 16 kHz: shorter and higher
    out["slow"] = resample_poly(x, 27, 25)
    rng = np.random.default_rng(seed)
    p = float(np.mean(x ** 2)) or 1.0
    out["noise20"] = x + rng.normal(0, (p / 10 ** 2.0) ** 0.5, len(x)).astype(np.float32)   # a quiet room with a fan
    out["noise15"] = x + rng.normal(0, (p / 10 ** 1.5) ** 0.5, len(x)).astype(np.float32)   # loud: a stress case
    # 20 dB quieter over a microphone's own hiss (-60 dBFS): the level the laptop microphone measured from its speaker
    out["quiet"] = x * 0.1 + rng.normal(0, 32768 * 10 ** -3, len(x)).astype(np.float32)
    lead = np.zeros(int(0.5 * wl.RATE), np.float32)          # half a second of quiet before the speech, as in a room
    return {k: np.clip(np.concatenate([lead, v]), -32768, 32767).astype("<i2").tobytes() for k, v in out.items()}


async def detect(det, pcm: bytes, epoch: int) -> bool:
    """Raw audio straight to the detector. (An automatic gain in front of it was tried on 2026-10-16: 33 instead of 37
    detections, the same false alarms, no help on quiet clips. The listener only raises the COMMAND's level.)"""
    from main_logic.voice_input.activation.contracts import ActivationGeneration, AudioFrame
    gen = ActivationGeneration("eval", 1, 1, 1, 1, "eval")
    pcm = pcm + b"\0\0" * int(1.0 * wl.RATE)
    for seq, i in enumerate(range(0, len(pcm) - wl.FRAME * 2 + 1, wl.FRAME * 2)):
        f = AudioFrame(seq, i // 2, i // 2 + wl.FRAME, time.time(), wl.RATE, pcm[i:i + wl.FRAME * 2], gen)
        if await det.feed(f, epoch):
            return True
    return False


async def main_async(thresholds):
    OUT.mkdir(parents=True, exist_ok=True)
    clips = []
    for label, items in (("positive", POSITIVE), ("negative", NEGATIVE)):
        for i, (lang, text) in enumerate(items):
            pcm = synth(lang, text, OUT / f"{label}-{i:02d}-{lang}.wav")
            for vname, v in variants(pcm, i).items():
                clips.append({"label": label, "lang": lang, "text": text, "variant": vname, "pcm": v})
    report = {"clips": len(clips), "positives": sum(c["label"] == "positive" for c in clips), "thresholds": {}}
    for th, score in thresholds:
        det = wl.make_detector(th, score)
        await det.prepare()
        hits = misses = fa = 0
        missed, alarms = [], []
        t0 = time.time()
        for n, c in enumerate(clips):
            got = await detect(det, c["pcm"], n + 1)
            if c["label"] == "positive":
                hits += got
                misses += not got
                if not got:
                    missed.append(f"{c['lang']} {c['variant']}: {c['text'][:30]}")
            elif got:
                fa += 1
                alarms.append(f"{c['lang']} {c['variant']}: {c['text'][:30]}")
        await det.close()
        audio_s = sum(len(c["pcm"]) / 2 / wl.RATE + 1.0 for c in clips)
        key = f"{th}/{score}"
        report["thresholds"][key] = {"detected": hits, "missed": misses, "false_alarms": fa,
                                          "negative_clips": len(clips) - report["positives"],
                                          "seconds": round(time.time() - t0, 1), "audio_seconds": round(audio_s),
                                          "missed_clips": missed, "false_alarm_clips": alarms}
        print(key, json.dumps({k: v for k, v in report["thresholds"][key].items() if not k.endswith("_clips")}), flush=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresholds", default="0.2,0.25,0.3,0.4")
    ap.add_argument("--scores", default="1.0,1.5,2.0", help="keyword boost scores (the detector's keyword_score)")
    a = ap.parse_args()
    combos = [(float(t), float(s)) for s in a.scores.split(",") for t in a.thresholds.split(",")]
    r = asyncio.run(main_async(combos))
    print("report:", OUT / "report.json", "clips:", r["clips"])


if __name__ == "__main__":
    main()
