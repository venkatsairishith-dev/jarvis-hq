"""Measure the local speech stack on THIS machine: versions, licences, cold and warm latency, memory and CPU.

Each cold-start figure comes from a fresh Python process. Warm figures are medians of repeated runs. Test audio is the stack's
own synthetic speech (TTS output), so accuracy is NOT measured here (that needs a real microphone: Settings, Voice check).
Run:  python -m jarvis_console.voicebench [--out D:\\jarvis\\cache\\voice-bench.json]
"""
import json
import os
import platform
import statistics
import subprocess
import sys
import time

MODELS = r"D:\jarvis\models\voice"
PIPER_PY = r"D:\jarvis\tools\piper-venv\Scripts\python.exe"
LICENSES = {   # from the model cards / repositories as recorded in docs/research/02 (verify before redistributing)
    "parakeet-tdt-0.6b-v3-int8": "CC-BY-4.0 (NVIDIA; attribution required)",
    "indicconformer-int8": "MIT (AI4Bharat weights); Apache-2.0 sherpa export",
    "kokoro-v1.0-fp32": "Apache-2.0 (model); espeak-ng GPL-3.0 inside the sherpa TTS path",
    "piper hi_IN-rohan-medium": "IIT Madras IndicTTS licence (terms unverified); runtime GPL-3.0 (separate process)",
    "piper te_IN-venkatesh-medium": "CC-BY-4.0 (IndicVoices-R); runtime GPL-3.0 (separate process)",
    "silero-vad": "MIT",
}
CHILD = r'''
import json, sys, time, os
t0 = time.time()
import numpy as np, sherpa_onnx, psutil
sys.path.insert(0, sys.argv[1])
from jarvis_console.voice import Voice, write_wav
t_import = time.time() - t0
p = psutil.Process()
v = Voice()
what = sys.argv[2]
res = {"what": what, "import_s": round(t_import, 2)}
def mem(): return round(p.memory_info().rss / 1e6)
if what.startswith("stt-"):
    lang = what[4:]
    wav_path = sys.argv[3]
    wav = open(wav_path, "rb").read()
    t = time.time(); r = v.transcribe(wav, lang); res["first_call_ms"] = round((time.time() - t) * 1000)
    res["text"] = r["text"][:80]; res["audio_s"] = r.get("audio_s")
    warm = []
    for _ in range(5):
        t = time.time(); v.transcribe(wav, lang); warm.append((time.time() - t) * 1000)
    res["warm_ms_median"] = round(sorted(warm)[2]); res["warm_ms_all"] = [round(x) for x in warm]
else:
    lang = what[4:]
    text = {"en": "Task finished. Two files were delivered to your outputs folder.",
            "hi": "काम पूरा हुआ। दो फ़ाइलें पहुँचा दी गई हैं।", "te": "పని పూర్తయింది. రెండు ఫైళ్లు అందజేయబడ్డాయి."}[lang]
    t = time.time(); wav, meta = v.speak(text, lang); res["first_call_ms"] = round((time.time() - t) * 1000)
    import wave, io
    with wave.open(io.BytesIO(wav)) as w: dur = w.getnframes() / w.getframerate()
    res["audio_s"] = round(dur, 2); res["rtf_first"] = round(res["first_call_ms"] / 1000 / dur, 2)
    warm = []
    for _ in range(4):
        t = time.time(); v.speak(text, lang); warm.append((time.time() - t) * 1000)
    res["warm_ms_median"] = round(sorted(warm)[len(warm) // 2]); res["rtf_warm"] = round(res["warm_ms_median"] / 1000 / dur, 2)
res["rss_mb"] = mem()
res["peak_rss_mb"] = round(getattr(p.memory_info(), "peak_wset", p.memory_info().rss) / 1e6)
res["cpu_pct_avg"] = p.cpu_percent(interval=None)
res["threads"] = p.num_threads()
res["ort_providers"] = sherpa_onnx.__dict__.get("__providers__", "cpu (sherpa-onnx CPU build)")
sys.stdout.write(json.dumps(res, ensure_ascii=False))
'''


def versions():
    out = {"python": platform.python_version(), "os": platform.platform()}
    try:
        import sherpa_onnx, numpy
        out["sherpa_onnx"], out["numpy"] = sherpa_onnx.__version__, numpy.__version__
    except ImportError:
        pass
    try:
        import importlib.metadata as md
        for pkg in ("onnxruntime", "sherpa-onnx", "sherpa-onnx-core", "soundfile", "psutil"):
            try:
                out[pkg] = md.version(pkg)
            except md.PackageNotFoundError:
                pass
    except ImportError:
        pass
    try:
        r = subprocess.run([PIPER_PY, "-c", "import importlib.metadata as m; print(m.version('piper-tts'))"], capture_output=True, text=True, timeout=30)
        out["piper-tts (separate venv)"] = r.stdout.strip()
    except Exception:
        pass
    return out


def model_inventory():
    inv = []
    for root, _, files in os.walk(MODELS):
        for f in files:
            if f.endswith((".onnx", ".bin")) or f.lower().startswith("license"):
                p = os.path.join(root, f)
                inv.append({"file": os.path.relpath(p, MODELS), "mb": round(os.path.getsize(p) / 1e6, 1)})
    return sorted(inv, key=lambda x: x["file"])


def machine():
    import psutil
    vm = psutil.virtual_memory()
    return {"cpu": platform.processor(), "logical_cpus": psutil.cpu_count(), "ram_gb": round(vm.total / 1e9, 1), "ram_available_gb_at_start": round(vm.available / 1e9, 1),
            "gpu_used_by_speech": "no: sherpa-onnx and Piper run on the CPU execution provider; the Radeon Pro 5500M stays free for the planner LLM"}


def child(what, wav_path=None):
    args = [sys.executable, "-c", CHILD, os.path.join(os.path.dirname(__file__), ".."), what] + ([wav_path] if wav_path else [])
    t = time.time()
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=300, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    if r.returncode != 0:
        return {"what": what, "error": (r.stderr or r.stdout)[-300:]}
    d = json.loads(r.stdout)
    d["process_wall_s"] = round(time.time() - t, 2)
    return d


def main(argv=None):
    argv = argv or sys.argv[1:]
    out_path = argv[argv.index("--out") + 1] if "--out" in argv else r"D:\jarvis\cache\voice-bench.json"
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from jarvis_console.voice import Voice
    import tempfile
    v = Voice()
    report = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "versions": versions(), "machine": machine(), "licenses": LICENSES, "models": model_inventory(), "results": []}
    with tempfile.TemporaryDirectory() as td:
        clips = {}
        for lang, text in (("en", "Open my downloads folder and show me the newest file."), ("hi", "मेरा डाउनलोड फ़ोल्डर खोलो और सबसे नई फ़ाइल दिखाओ"),
                           ("te", "నా డౌన్‌లోడ్ ఫోల్డర్ తెరవండి")):
            wav, _ = v.speak(text, lang)
            clips[lang] = os.path.join(td, f"{lang}.wav")
            with open(clips[lang], "wb") as fh:
                fh.write(wav)
        for lang in ("en", "hi", "te"):
            report["results"].append(child(f"stt-{lang}", clips[lang]))
        for lang in ("en", "hi", "te"):
            report["results"].append(child(f"tts-{lang}"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    for r in report["results"]:
        print(json.dumps(r, ensure_ascii=False))
    print("wrote", out_path)


if __name__ == "__main__":
    main()
