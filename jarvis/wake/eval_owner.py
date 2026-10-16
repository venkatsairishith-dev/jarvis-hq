"""Offline tuning of "Hey Neko" on the OWNER'S real recordings (jarvis/tests/neko_voice_owner_record.py). Windows, neko-venv.
    python jarvis\\wake\\eval_owner.py D:\\jarvis\\outputs\\neko\\voice-owner\\<time>
For each recorded session it replays the audio through the same detector the listener uses, for a grid of detector
thresholds and fixed input gains (0, +6, +12, +18 dB, clipped), and counts: hits = prompts with a detection inside
[prompt, prompt + gap); false alarms = detections outside every prompt window. Real voice only; nothing synthetic.
Writes eval-owner.json next to the recordings. A setting is only adopted in the listener after the owner re-tests it live.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wake_listener as WL  # noqa: E402

THRESHOLDS = (0.15, 0.2, 0.25, 0.3)
GAINS_DB = (0, 6, 12, 18)


async def detect_times(pcm: np.ndarray, threshold: float) -> list[float]:
    """The listener's detector over the whole recording, frame by frame as it runs live (same AudioFrame contract as
    eval_wake.py); after each detection the epoch moves on, as the listener's does."""
    from main_logic.voice_input.activation.contracts import ActivationGeneration, AudioFrame
    det = WL.make_detector(threshold)
    await det.prepare()
    gen = ActivationGeneration("eval-owner", 1, 1, 1, 1, "eval-owner")
    raw = pcm.astype("<i2").tobytes()
    times, epoch, step = [], 1, WL.FRAME * 2
    try:
        for seq, i in enumerate(range(0, len(raw) - step + 1, step)):
            f = AudioFrame(seq, i // 2, i // 2 + WL.FRAME, time.time(), WL.RATE, raw[i:i + step], gen)
            if await det.feed(f, epoch):
                times.append(i / 2 / WL.RATE)
                epoch += 1
    finally:
        await det.close()
    return times


def score(times: list[float], prompts: list[float], gap: float) -> tuple[int, int]:
    hits = sum(1 for p in prompts if any(p <= t < p + gap for t in times))
    false = sum(1 for t in times if not any(p <= t < p + gap for p in prompts))
    return hits, false


def main(folder: str):
    d = Path(folder)
    sess = json.loads((d / "session.json").read_text(encoding="utf-8"))["sessions"]
    out = []
    for s in sess:
        raw = np.frombuffer(WL.read_wav_16k(s["wav"]), dtype=np.int16).astype(np.float32)
        for g in GAINS_DB:
            pcm = np.clip(raw * (10 ** (g / 20)), -32768, 32767).astype(np.int16)
            for th in THRESHOLDS:
                hits, false = score(asyncio.run(detect_times(pcm, th)), s["prompts_s"], s["gap_s"])
                out.append({"session": s["label"], "gain_db": g, "threshold": th, "hits": hits, "of": len(s["prompts_s"]),
                            "false_alarms": false, "minutes": round(len(raw) / WL.RATE / 60, 2)})
                print(f"{s['label']:6} gain +{g:2d} dB  threshold {th:.2f}: {hits}/{len(s['prompts_s'])}  false {false}", flush=True)
    (d / "eval-owner.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("saved", d / "eval-owner.json")


if __name__ == "__main__":
    main(sys.argv[1])
