"""What does the listener hear after "Hey Neko"? Real detector, VAD and local speech recognition on synthesized clips,
at their normal level and 20 dB quieter (dry run; nothing is sent). Windows, neko-venv; the speech server must run.

    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe jarvis\\wake\\command_cut_check.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wake_listener as wl  # noqa: E402

CLIPS = Path(r"D:\jarvis\outputs\neko\wake-eval")
CASES = [("positive-01-en.wav", "open notepad"), ("positive-02-en.wav", "what is the time"),
         ("positive-03-en.wav", "play a kalyani song on youtube"), ("positive-04-en.wav", "are you there")]


def quieter(pcm: bytes) -> bytes:
    import numpy as np
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) * 0.1
    x += np.random.default_rng(1).normal(0, 32.8, len(x)).astype(np.float32)
    return np.clip(x, -32768, 32767).astype("<i2").tobytes()


async def one(pcm: bytes) -> dict:
    lead = b"\0\0" * (wl.RATE // 2)
    w = wl.WakeLoop(wl.make_detector, dry_run=True, speaking=lambda: False, running=lambda: False, chime=lambda k: None)
    await wl.run(w, wl.FileSource(lead + pcm, tail_s=2.5))
    return {"woke": w.counts["detections"] > 0, "heard": (w.last or {}).get("text"), "what": (w.last or {}).get("what")}


async def main():
    rows = []
    for name, want in CASES:
        pcm = wl.read_wav_16k(str(CLIPS / name))
        for level, audio in (("normal", pcm), ("quiet", quieter(pcm))):
            r = await one(audio)
            words = set((r["heard"] or "").lower().replace(",", "").replace(".", "").replace("?", "").split())
            r.update(clip=name, level=level, want=want, words_found=f"{len(set(want.split()) & words)}/{len(want.split())}")
            rows.append(r)
            print(json.dumps(r, ensure_ascii=False), flush=True)
    full = sum(r["words_found"].split("/")[0] == r["words_found"].split("/")[1] for r in rows)
    print("SUMMARY", json.dumps({"runs": len(rows), "woke": sum(r["woke"] for r in rows), "every_command_word_heard": full}))


if __name__ == "__main__":
    asyncio.run(main())
