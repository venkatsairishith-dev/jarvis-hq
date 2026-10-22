"""Wake-word benchmark scorer: runs openWakeWord's "hey jarvis" model over the recorded clips in D:\\jarvis\\cache\\wakeword\\audio and
reports detections, load time and memory. Windows; measurement only (results in bench/wakeword/).
"""
import glob, json, os, time, wave
import numpy as np, psutil
from scipy.signal import resample_poly
import openwakeword
from openwakeword.model import Model

AUD = r"D:\jarvis\cache\wakeword\audio"
MP = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models", "hey_jarvis_v0.1.onnx")
proc = psutil.Process()
rss0 = proc.memory_info().rss
t0 = time.perf_counter()
m = Model(wakeword_models=[MP], inference_framework="onnx")
load_s = time.perf_counter() - t0
rss_loaded = proc.memory_info().rss

def load16(p):
    with wave.open(p, "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32)
    if sr != 16000:
        from math import gcd
        g = gcd(sr, 16000); a = resample_poly(a, 16000 // g, sr // g)
    return np.clip(a, -32768, 32767).astype(np.int16)

FR = 1280
frame_ms = []
def run(x):
    m.reset()
    x = np.concatenate([np.zeros(FR * 4, np.int16), x, np.zeros(FR * 12, np.int16)])        # lead-in/out so the buffers are primed/flushed
    best = 0.0
    for i in range(0, len(x) - FR + 1, FR):
        t = time.perf_counter()
        sc = m.predict(x[i:i + FR])
        frame_ms.append((time.perf_counter() - t) * 1000)
        best = max(best, float(max(sc.values())))
    return best

res = {"pos": {}, "neg": {}}
for kind in ("pos", "neg"):
    for p in sorted(glob.glob(os.path.join(AUD, kind, "*.wav"))):
        res[kind][os.path.basename(p)] = run(load16(p))
# non-speech: white noise, pink-ish noise, 50 Hz hum + clicks, 60 s each, plus silence
rng = np.random.default_rng(7)
def noise(sec, kind):
    n = 16000 * sec
    if kind == "white": a = rng.normal(0, 2000, n)
    elif kind == "pink":
        f = np.fft.rfft(rng.normal(0, 1, n)); k = np.arange(len(f)); k[0] = 1; a = np.fft.irfft(f / np.sqrt(k), n); a = a / np.abs(a).max() * 6000
    elif kind == "hum":
        t = np.arange(n) / 16000; a = 3000 * np.sin(2 * np.pi * 50 * t) + 1500 * np.sin(2 * np.pi * 150 * t); a[rng.integers(0, n, 40)] = 20000
    else: a = np.zeros(n)
    return a.astype(np.int16)
for k in ("white", "pink", "hum", "silence"):
    res["neg"]["noise_" + k] = run(noise(60, k))
rss_end = proc.memory_info().rss

def stats(d, th): return sum(1 for v in d.values() if v >= th)
out = {"model": "hey_jarvis_v0.1.onnx (openWakeWord 0.6.0, onnxruntime)", "load_s": round(load_s, 3),
       "rss_mb_before": round(rss0 / 1e6), "rss_mb_loaded": round(rss_loaded / 1e6), "rss_mb_end": round(rss_end / 1e6),
       "frame_ms_mean": round(float(np.mean(frame_ms)), 2), "frame_ms_p95": round(float(np.percentile(frame_ms, 95)), 2), "frame_budget_ms": 80,
       "n_pos": len(res["pos"]), "n_neg": len(res["neg"]), "neg_audio_note": "synthetic TTS speech (en, hi, te) + 4 min of noise/silence; no real microphone audio",
       "by_threshold": {str(th): {"pos_detected": stats(res["pos"], th), "neg_detected": stats(res["neg"], th)} for th in (0.2, 0.3, 0.5, 0.7)},
       "pos_scores_summary": {k: round(float(np.percentile(list(res["pos"].values()), q)), 3) for k, q in (("min", 0), ("p25", 25), ("median", 50), ("max", 100))},
       "neg_top": sorted(res["neg"].items(), key=lambda kv: -kv[1])[:8]}
# which positive phrasing/voices fail at 0.5?
miss = {k: round(v, 3) for k, v in res["pos"].items() if v < 0.5}
out["pos_missed_at_0.5"] = len(miss)
out["pos_missed_by_phrase_index"] = {str(i): sum(1 for k in miss if k.endswith(f"_{i}.wav")) for i in range(4)}
json.dump({"summary": out, "pos": res["pos"], "neg": res["neg"]}, open(r"D:\jarvis\cache\wakeword\result.json", "w"), indent=1)
print(json.dumps(out, indent=1))
