"""Local speech: STT and TTS on this machine's CPU. Audio never leaves the laptop.

Stack (docs/research/02-voice-and-local-models.md, docs/research/07-integration-decisions.md):
  VAD       Silero v6 through sherpa-onnx (also what rejects silence, so no hallucinated words)
  STT en    Parakeet TDT 0.6B v3 int8 (sherpa-onnx)         STT hi/te  AI4Bharat IndicConformer int8 CTC (sherpa-onnx)
  TTS en    Kokoro v1.0 fp32 (sherpa-onnx)                  TTS hi/te  Piper voices in a separate process (GPL runtime)
One speech model is resident at a time per direction and is unloaded after an idle period, so the GPU stays with the
planner LLM and RAM stays small. Missing models or engines degrade to a clear status, never a crash.
"""
import io
import os
import subprocess
import tempfile
import threading
import time
import wave

try:                                    # the engines are optional: status() says what is missing
    import numpy as np
except ImportError:                     # pragma: no cover
    np = None
try:
    import sherpa_onnx
except ImportError:                     # pragma: no cover
    sherpa_onnx = None

SR = 16000
MAX_AUDIO_S = 90
LANGS = ("en", "hi", "te")


class VoiceError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def read_wav(data: bytes):
    """16-bit PCM WAV bytes -> (float32 mono samples at 16 kHz, original rate). Other rates are resampled."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if width != 2:
                raise VoiceError("send 16-bit PCM WAV audio")
            if n / max(rate, 1) > MAX_AUDIO_S:
                raise VoiceError(f"audio is longer than {MAX_AUDIO_S} s", 413)
            raw = w.readframes(n)
    except (wave.Error, EOFError):
        raise VoiceError("not a readable WAV file")
    x = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != SR and len(x):
        t = np.arange(int(len(x) * SR / rate)) * (rate / SR)
        x = np.interp(t, np.arange(len(x)), x).astype("float32")
    return x, rate


def write_wav(samples, rate: int) -> bytes:
    pcm = (np.clip(np.asarray(samples, dtype="float32"), -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return buf.getvalue()


class Voice:
    def __init__(self, model_dir=r"D:\jarvis\models\voice", piper_python=r"D:\jarvis\tools\piper-venv\Scripts\python.exe",
                 threads=4, idle_unload_s=300, clock=time.time):
        self.dir, self.piper_python, self.threads, self.idle, self.clock = model_dir, piper_python, threads, idle_unload_s, clock
        self._lock = threading.Lock()                 # one recognition or synthesis at a time: the CPU is shared with the LLM
        self._stt = (None, None)                      # (key, recognizer)
        self._tts = None
        self._vad = None
        self._last = 0.0
        self.last_error = None
        self.stats = {"stt_calls": 0, "tts_calls": 0, "last_stt_ms": None, "last_tts_ms": None}

    # ---- paths
    def p(self, *a):
        return os.path.join(self.dir, *a)

    def stt_paths(self, lang):
        if lang == "en":
            d = self.p("stt", "parakeet-tdt-0.6b-v3-int8")
            return {"key": "en", "name": "parakeet-tdt-0.6b-v3-int8", "files": [os.path.join(d, f) for f in
                    ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")]}
        d = self.p("stt", "indicconformer-int8")
        return {"key": lang, "name": f"indicconformer-int8-{lang}", "files": [os.path.join(d, lang, "model.int8.onnx"), os.path.join(d, "tokens.txt")]}

    def tts_paths(self, lang):
        if lang == "en":
            d = self.p("tts", "kokoro-multi-lang-v1_0")
            return {"name": "kokoro-v1.0-fp32", "files": [os.path.join(d, f) for f in ("model.onnx", "voices.bin", "tokens.txt")]}
        voice = {"hi": "hi_IN-rohan-medium", "te": "te_IN-venkatesh-medium"}[lang]
        d = self.p("tts", "piper-voices")
        return {"name": "piper-" + voice, "files": [os.path.join(d, voice + ".onnx"), os.path.join(d, voice + ".onnx.json")]}

    def status(self):
        def have(paths):
            return all(os.path.exists(f) for f in paths["files"])
        stt = {l: {"model": self.stt_paths(l)["name"], "available": bool(sherpa_onnx and np and have(self.stt_paths(l)))} for l in LANGS}
        tts = {l: {"model": self.tts_paths(l)["name"],
                   "available": bool(have(self.tts_paths(l)) and ((sherpa_onnx and np) if l == "en" else os.path.exists(self.piper_python)))}
               for l in LANGS}
        return {"engine": "sherpa-onnx" if sherpa_onnx else None, "stt": stt, "tts": tts,
                "vad": os.path.exists(self.p("vad", "silero_vad.onnx")), "resident_stt": self._stt[0], "resident_tts": bool(self._tts),
                "idle_unload_s": self.idle, "threads": self.threads, "stats": self.stats, "last_error": self.last_error,
                "audio_leaves_machine": False}

    # ---- lifecycle
    def unload_if_idle(self):
        with self._lock:
            if self.idle and self.clock() - self._last > self.idle and (self._stt[1] or self._tts):
                self._stt, self._tts, self._vad = (None, None), None, None
                return True
        return False

    def _vad_detector(self):
        if self._vad is None:
            path = self.p("vad", "silero_vad.onnx")
            if not os.path.exists(path):
                return None
            cfg = sherpa_onnx.VadModelConfig()
            cfg.silero_vad.model = path
            cfg.silero_vad.threshold = 0.5
            cfg.silero_vad.min_silence_duration = 0.6
            cfg.silero_vad.min_speech_duration = 0.2
            cfg.silero_vad.window_size = 512
            cfg.sample_rate = SR
            cfg.num_threads = 1
            self._vad = cfg
        return sherpa_onnx.VoiceActivityDetector(self._vad, buffer_size_in_seconds=MAX_AUDIO_S + 5)

    def _recognizer(self, lang):
        info = self.stt_paths(lang)
        missing = [f for f in info["files"] if not os.path.exists(f)]
        if missing:
            raise VoiceError(f"speech model for '{lang}' is not installed ({os.path.basename(missing[0])})", 503)
        if self._stt[0] != info["key"]:
            self._stt = (None, None)                  # unload the previous one first: sequential, never two at once
            if lang == "en":
                e, d, j, t = info["files"]
                r = sherpa_onnx.OfflineRecognizer.from_transducer(encoder=e, decoder=d, joiner=j, tokens=t, num_threads=self.threads,
                                                                  sample_rate=SR, feature_dim=128, decoding_method="greedy_search",
                                                                  model_type="nemo_transducer")
            else:
                m, t = info["files"]
                r = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(model=m, tokens=t, num_threads=self.threads)
            self._stt = (info["key"], r)
        return self._stt[1]

    # ---- speech to text
    def transcribe(self, wav: bytes, lang="en"):
        if not (sherpa_onnx and np):
            raise VoiceError("the speech engine (sherpa-onnx) is not installed", 503)
        if lang not in LANGS:
            raise VoiceError("language must be en, hi or te")
        x, rate = read_wav(wav)
        with self._lock:
            t0 = time.time()
            try:
                vad = self._vad_detector()
                speech = x
                if vad is not None and len(x):
                    seg = []
                    for i in range(0, len(x), 512):
                        vad.accept_waveform(x[i:i + 512])
                    vad.flush()
                    while not vad.empty():
                        seg.append(vad.front.samples); vad.pop()
                    if not seg:
                        self._last = self.clock()
                        return {"text": "", "speech": False, "language": lang, "model": self.stt_paths(lang)["name"],
                                "audio_s": round(len(x) / SR, 2), "ms": round((time.time() - t0) * 1000)}
                    speech = np.concatenate([np.zeros(int(0.2 * SR), "float32"), *seg, np.zeros(int(0.2 * SR), "float32")])
                rec = self._recognizer(lang)
                s = rec.create_stream()
                s.accept_waveform(SR, speech.astype("float32"))
                rec.decode_stream(s)
                text = " ".join(s.result.text.split())
            except VoiceError:
                raise
            except Exception as e:
                self.last_error = f"stt {type(e).__name__}: {str(e)[:120]}"
                raise VoiceError("speech recognition failed: " + type(e).__name__, 500)
            self._last = self.clock()
            ms = round((time.time() - t0) * 1000)
            self.stats.update(stt_calls=self.stats["stt_calls"] + 1, last_stt_ms=ms)
            return {"text": text, "speech": True, "language": lang, "model": self.stt_paths(lang)["name"],
                    "audio_s": round(len(x) / SR, 2), "speech_s": round(len(speech) / SR, 2), "ms": ms}

    # ---- text to speech
    def speak(self, text: str, lang="en", speed=1.0):
        text = " ".join(str(text or "").split())
        if not text:
            raise VoiceError("nothing to say")
        if lang not in LANGS:
            raise VoiceError("language must be en, hi or te")
        if len(text) > 1500:
            raise VoiceError("text is too long to speak (1500 characters)", 413)
        info = self.tts_paths(lang)
        missing = [f for f in info["files"] if not os.path.exists(f)]
        if missing:
            raise VoiceError(f"voice for '{lang}' is not installed ({os.path.basename(missing[0])})", 503)
        with self._lock:
            t0 = time.time()
            try:
                if lang == "en":
                    if not (sherpa_onnx and np):
                        raise VoiceError("the speech engine (sherpa-onnx) is not installed", 503)
                    if self._tts is None:
                        d = self.p("tts", "kokoro-multi-lang-v1_0")
                        k = sherpa_onnx.OfflineTtsKokoroModelConfig(
                            model=os.path.join(d, "model.onnx"), voices=os.path.join(d, "voices.bin"), tokens=os.path.join(d, "tokens.txt"),
                            data_dir=os.path.join(d, "espeak-ng-data"), dict_dir=os.path.join(d, "dict"),
                            lexicon=",".join(os.path.join(d, f) for f in ("lexicon-us-en.txt", "lexicon-zh.txt")))
                        cfg = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
                            kokoro=k, num_threads=self.threads, provider="cpu"), max_num_sentences=1)
                        self._tts = sherpa_onnx.OfflineTts(cfg)
                    a = self._tts.generate(text, sid=0, speed=float(speed))
                    wav, rate = write_wav(np.asarray(a.samples, dtype="float32"), a.sample_rate), a.sample_rate
                else:
                    wav = self._piper(text, info["files"][0])
                    with wave.open(io.BytesIO(wav)) as w:
                        rate = w.getframerate()
            except VoiceError:
                raise
            except Exception as e:
                self.last_error = f"tts {type(e).__name__}: {str(e)[:120]}"
                raise VoiceError("speech synthesis failed: " + type(e).__name__, 500)
            self._last = self.clock()
            ms = round((time.time() - t0) * 1000)
            self.stats.update(tts_calls=self.stats["tts_calls"] + 1, last_tts_ms=ms)
            return wav, {"model": info["name"], "rate": rate, "ms": ms, "language": lang}

    def _piper(self, text, model):
        """Piper in its own process (its runtime is GPL-3.0): text on stdin, WAV file out."""
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "out.wav")
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")    # Windows reads stdin as cp1252 otherwise
            r = subprocess.run([self.piper_python, "-m", "piper", "-m", model, "-f", out], input=text.encode("utf-8"),
                               capture_output=True, timeout=60, env=env)
            if r.returncode != 0 or not os.path.exists(out):
                raise VoiceError("the Piper voice failed: " + r.stderr.decode("utf-8", "replace")[-160:], 500)
            with open(out, "rb") as fh:
                return fh.read()
