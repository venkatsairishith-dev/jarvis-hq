"""Wake-to-listening latency of the "Hey Neko" pipeline (2026-10-16). Windows, neko-venv; speech server (48930) for the clip.
    python jarvis\\wake\\wake_latency.py
A clip of "Hey Neko." from the local TTS is fed to the listener's own detector at real-time pace (40 ms frames, as the
microphone delivers them) followed by silence. Measured: from the moment the frame holding the end of the keyword is fed,
to the detection (after which the listener switches to "listening" in the same step). This is a TIMING measurement of the
pipeline; it says nothing about recall on the owner's voice (that is jarvis/tests/neko_voice_owner_record.py).
Results: jarvis/evals/neko95/results/wakelatency-<time>.json
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import eval_wake  # noqa: E402
import wake_listener as WL  # noqa: E402


def keyword_end(x: np.ndarray) -> int:
    """Last sample of speech: the end of the last 20 ms window above 10% of the clip's peak level."""
    win = int(0.02 * WL.RATE)
    env = np.array([np.abs(x[i:i + win]).mean() for i in range(0, len(x) - win, win)])
    loud = np.where(env > 0.1 * env.max())[0]
    return int((loud[-1] + 1) * win) if len(loud) else len(x)


async def one(det, pcm: np.ndarray, epoch: int) -> tuple[float | None, float | None]:
    from main_logic.voice_input.activation.contracts import ActivationGeneration, AudioFrame
    gen = ActivationGeneration("lat", 1, 1, 1, 1, "lat")
    end = keyword_end(pcm)
    x = np.concatenate([np.zeros(int(0.5 * WL.RATE)), pcm, np.zeros(int(2.0 * WL.RATE))]).astype("<i2")
    end += int(0.5 * WL.RATE)
    raw, step = x.tobytes(), WL.FRAME * 2
    t_end = None
    t0 = time.perf_counter()
    for seq, i in enumerate(range(0, len(raw) - step + 1, step)):
        due = t0 + seq * WL.FRAME / WL.RATE                  # real-time pace, like the microphone
        await asyncio.sleep(max(0.0, due - time.perf_counter()))
        if t_end is None and (i // 2 + WL.FRAME) >= end:
            t_end = time.perf_counter()
        f = AudioFrame(seq, i // 2, i // 2 + WL.FRAME, time.time(), WL.RATE, raw[i:i + step], gen)
        det_ = await det.feed(f, epoch)
        if det_:
            now = time.perf_counter()
            audio_lag = (getattr(det_, "sample_end", i // 2 + WL.FRAME) - end) / WL.RATE
            return (now - t_end if t_end else 0.0), audio_lag
    return None, None


async def main_async():
    clip = eval_wake.synth("en", "Hey Neko.", eval_wake.OUT / "latency-hey-neko.wav")
    variants = eval_wake.variants(clip, 7)
    det = WL.make_detector(0.25)
    await det.prepare()
    walls, lags, missed = [], [], 0
    try:
        for rep in range(2):
            for k, (name, v) in enumerate(variants.items()):
                if name == "quiet":
                    continue
                pcm = np.frombuffer(v, dtype="<i2").astype(np.float32)      # variants() returns 16-bit PCM bytes
                w, a = await one(det, pcm, 100 + rep * 10 + k)
                if w is None:
                    missed += 1
                else:
                    walls.append(round(w, 3))
                    lags.append(round(a, 3))
    finally:
        await det.close()
    walls.sort()
    p50 = walls[len(walls) // 2] if walls else None
    ok = p50 is not None and p50 <= 1.0
    detail = (f"keyword end -> detection, real-time feed: p50 {p50} s, max {walls[-1] if walls else None} s over {len(walls)} "
              f"detections ({missed} clips not detected); audio-time lag {sorted(lags)}; synthetic clip (timing only)")
    print(("PASS " if ok else "FAIL ") + "L-wake-to-listen", detail)
    rows = [{"cap": "C11_performance", "id": "L-wake-to-listen", "status": "pass" if ok else "fail", "slo": True, "detail": detail},
            {"cap": "C1_voice", "id": "V-wake-latency", "status": "pass" if ok else "fail", "slo": True, "detail": detail}]
    out = HERE.parents[0] / "evals" / "neko95" / "results" / f"wakelatency-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}, indent=1), encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    asyncio.run(main_async())
