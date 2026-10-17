"""Hey Neko: the local wake word for the Neko desktop agent. Windows. Runs with N.E.K.O's Python (neko-venv), which holds the
patched sherpa-onnx runtime 1.13.8+neko.kws2 that N.E.K.O's own keyword spotter requires.

    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe jarvis\\wake\\wake_listener.py [--dry-run] [--minutes N]

Flow (nothing leaves the laptop):
  microphone, 16 kHz mono  ->  N.E.K.O's SherpaWakeWordDetector (its own process, killed on close)
  "Hey Neko"               ->  short chime; record the command until 1 s of silence (Silero VAD), at most 12 s
  command audio            ->  local speech recognition (jarvis-local-speech, 127.0.0.1:48930)
  command text             ->  a main-agent task through the widget server (127.0.0.1:48940/api/task)
  then                     ->  back to wake listening
Cancel: say "cancel" / "never mind" as the command; say "Hey Neko, stop" while a task runs (stops that task); or POST /cancel.
While Neko itself is speaking, detections are ignored (its own voice must not wake it).
No audio is saved. The log holds states, timings and lengths, never what was said.
Status for the Neko icon: GET http://127.0.0.1:48945/status. POST /cancel, /pause (microphone off), /resume.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

RATE = 16000
FRAME = 640                       # 40 ms: the largest frame N.E.K.O's detector takes in one batch
PORT = 48945
MODEL_ROOT = Path(r"D:\jarvis\models\voice")
DATA = Path(os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data"))
SPEECH = "http://127.0.0.1:48930"
WIDGET = "http://127.0.0.1:48940"
# Phone tokens of the bundled zh-en model (ARPAbet for English), not spelling: "neh-ko" (two stresses), "nay-ko", "nee-ko".
# "nee-ko" is how the English voice says it, and without it most English clips were missed (offline check 2026-10-16);
# it is also how "Nico" sounds, so "Hey Nico" wakes Neko too. Drop that line in a keywords file if that matters more.
DEFAULT_KEYWORDS = ("HH EY1 N EH1 K OW0 @hey_neko", "HH EY1 N EH1 K OW1 @hey_neko", "HH EY1 N EY1 K OW0 @hey_neko",
                    "HH EY1 N IY1 K OW0 @hey_neko")
CANCEL_WORDS = re.compile(r"^\W*(cancel|never ?mind|nothing|forget it|stop|no thanks?|quiet)\b", re.I)
# The whole utterance is a cancel ("stop", "cancel that", "never mind", "Forget it!"); "cancel my meeting" is a task.
PURE_CANCEL = re.compile(r"^\W*(?:cancel|never ?mind|nothing|forget it|stop|no thanks?|no thank you|quiet)"
                         r"(?:\s+(?:it|that|this))?(?:\s+please)?\W*$", re.I)
STOP_WORDS = re.compile(r"^\W*(stop|cancel|abort|halt)\b", re.I)
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


# ---------------------------------------------------------------- configuration
def model_dir() -> str | None:
    """NEKO_WAKE_WORD_MODEL_DIR, or the newest provisioned bundle under D:\\jarvis\\models\\voice\\kws\\versions."""
    explicit = os.environ.get("NEKO_WAKE_WORD_MODEL_DIR", "").strip()
    if explicit:
        return explicit
    versions = MODEL_ROOT / "kws" / "versions"
    found = [d for d in versions.iterdir() if (d / "tokens.txt").is_file()] if versions.is_dir() else []
    return str(max(found, key=lambda d: d.stat().st_mtime)) if found else None


def keywords() -> tuple[str, ...]:
    """NEKO_WAKE_WORD_KEYWORDS_FILE: one phone line per row ("HH EY1 N EH1 K OW0 @hey_neko"); # starts a comment."""
    path = os.environ.get("NEKO_WAKE_WORD_KEYWORDS_FILE", "").strip()
    if not path:
        return DEFAULT_KEYWORDS
    lines = [ln.split("#", 1)[0].strip() for ln in Path(path).read_text(encoding="utf-8").splitlines()]
    return tuple(ln for ln in lines if ln)


# ---------------------------------------------------------------- audio helpers
def to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def resample_int16(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Any rate and channel count -> 16 kHz mono PCM16 (polyphase filter, so no aliasing)."""
    import numpy as np
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    if rate != RATE:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(RATE, rate)
        x = resample_poly(x, RATE // g, rate // g)
    return np.clip(x, -32768, 32767).astype("<i2").tobytes()


def read_wav_16k(path_or_bytes) -> bytes:
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb")
    with src, wave.open(src) as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit WAV files are supported")
        return resample_int16(w.readframes(w.getnframes()), w.getframerate(), w.getnchannels())


class Vad:
    """Silero VAD (the model JARVIS's speech server already uses): is someone speaking right now?"""

    def __init__(self, path: str | None = None):
        import sherpa_onnx
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = path or str(MODEL_ROOT / "vad" / "silero_vad.onnx")
        cfg.silero_vad.threshold = 0.5
        cfg.silero_vad.min_silence_duration = 0.25
        cfg.silero_vad.min_speech_duration = 0.15
        cfg.silero_vad.window_size = 512
        cfg.sample_rate = RATE
        cfg.num_threads = 1
        self._np = __import__("numpy")
        self.v = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)
        self.rest = self._np.zeros(0, dtype=self._np.float32)

    def speech(self, pcm: bytes) -> bool:
        np = self._np
        self.rest = np.concatenate([self.rest, np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0])
        while len(self.rest) >= 512:
            self.v.accept_waveform(self.rest[:512])
            self.rest = self.rest[512:]
        while not self.v.empty():                    # finished segments are not needed: only "speaking now?"
            self.v.pop()
        return bool(self.v.is_speech_detected())


# ---------------------------------------------------------------- local services
def http_json(url, body=None, timeout=5.0, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": ctype} if data is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "null")


def transcribe(pcm: bytes, lang: str = "en") -> str:
    out = http_json(f"{SPEECH}/v1/audio/transcriptions?language={lang}", raw=to_wav(pcm), timeout=60, ctype="audio/wav")
    return str((out or {}).get("text") or "").strip()


def neko_speaking() -> bool:
    try:
        return bool((http_json(f"{SPEECH}/activity", timeout=1.0) or {}).get("speaking_estimate"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def task_running() -> bool:
    try:
        task = (http_json(f"{WIDGET}/api/status", timeout=8.0) or {}).get("task") or {}
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return task.get("state") in ("running", "planning")


def submit_task(text: str) -> dict:
    try:
        return http_json(f"{WIDGET}/api/task", {"task": text, "mode": "work"}, timeout=30) or {"ok": False, "error": "no answer"}
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode()) | {"ok": False}
        except ValueError:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "error": type(e).__name__}


def stop_task() -> dict:
    try:
        return http_json(f"{WIDGET}/api/action", {"action": "stop"}, timeout=20) or {"ok": False}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "error": type(e).__name__}


def chime(kind: str = "wake"):
    try:
        import winsound
        winsound.PlaySound("SystemAsterisk" if kind == "wake" else "SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:  # noqa: BLE001 - a missing sound never stops listening
        pass


# ---------------------------------------------------------------- the loop
class WakeLoop:
    """One state at a time, decided by what the microphone, the detector and the services actually returned.

    wake_listening -> (wake word) heard -> recording -> transcribing -> submitting -> wake_listening
                                       \\-> no speech for 5 s / cancel word / POST /cancel -> cancelled -> wake_listening
    paused: microphone ignored (POST /pause). error: the detector failed; it is restarted after a pause.
    """
    WAIT_SPEECH_S = 5.0
    END_SILENCE_S = 1.0
    MAX_COMMAND_S = 12.0
    PRE_ROLL_S = 2.0

    def __init__(self, detector_factory, vad_factory=Vad, transcribe=transcribe, submit=submit_task, stop=stop_task,
                 speaking=neko_speaking, running=task_running, chime=chime, dry_run=False, log=None, clock=time.time):
        self.detector_factory, self.vad_factory = detector_factory, vad_factory
        self.transcribe, self.submit, self.stop, self.speaking, self.running = transcribe, submit, stop, speaking, running
        self.chime, self.dry_run, self.clock = chime, dry_run, clock
        self.save_dir = None          # dry runs only (--save-commands): where the command audio is written for checking
        self.gain = 1.0
        self.log = log or (lambda *a: None)
        from main_logic.voice_input.activation.contracts import ActivationGeneration
        self.generation = ActivationGeneration("neko-wake", 1, 1, 1, 1, "wake_listener")
        self.detector = None
        self.state, self.since, self.detail = "starting", clock(), ""
        self.counts = {"detections": 0, "ignored_while_speaking": 0, "commands": 0, "cancelled": 0, "no_speech": 0,
                       "tasks_started": 0, "stops": 0, "errors": 0}
        self.last = None              # the last outcome: {"what", "at", ...} (never the words)
        self.seq = self.pos = self.epoch = 0
        self.ring: list[tuple[int, bytes]] = []
        self.cmd: list[bytes] = []
        self.vad = None
        self.heard_at = self.speech_started = self.silence_since = None
        self.cancel_flag = threading.Event()
        self.paused = False
        self._speaking_checked, self._speaking = 0.0, False
        self.worker: asyncio.Task | None = None

    # ---- status
    def set(self, state, detail=""):
        if state != self.state:
            self.log(f"state {self.state} -> {state}" + (f" ({detail})" if detail else ""))
        self.state, self.since, self.detail = state, self.clock(), detail

    def status(self) -> dict:
        healthy = self.detector is not None and self.state not in ("error", "starting")
        return {"enabled": True, "healthy": healthy, "state": "paused" if self.paused else self.state, "detail": self.detail,
                "seconds_in_state": round(self.clock() - self.since, 1), "dry_run": self.dry_run, "counts": dict(self.counts),
                "last": self.last, "keywords": sorted({k.split()[-1][1:] for k in keywords()}),
                "audio": getattr(self, "level", None)}

    def outcome(self, what, **extra):
        self.last = {"what": what, "at": time.strftime("%H:%M:%S"), **extra}
        self.log("outcome " + json.dumps(self.last))

    # ---- detector life cycle
    async def start(self):
        self.set("starting")
        self.detector = self.detector_factory()
        await self.detector.prepare()
        self.epoch += 1
        self.set("wake_listening")

    async def close(self):
        if self.detector is not None:
            await self.detector.close()
            self.detector = None

    async def restart_detector(self, reason: str):
        self.counts["errors"] += 1
        self.set("error", reason)
        await self.close()
        await asyncio.sleep(2.0)
        try:
            await self.start()
        except Exception as e:  # noqa: BLE001
            self.set("error", f"wake word could not restart ({type(e).__name__})")

    def _level(self, pcm: bytes):
        """Loudness of the last second, the loudest and the quietest second so far (dBFS): shows a muted or dead microphone,
        and how far speech stands above the room's noise."""
        import numpy as np
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        self._sq = getattr(self, "_sq", 0.0) + float(np.dot(x, x))
        self._sn = getattr(self, "_sn", 0) + len(x)
        if self._sn >= RATE:
            import math
            db = round(10 * math.log10(self._sq / self._sn / 32768.0 ** 2 + 1e-12), 1)
            old = getattr(self, "level", None) or {}
            self.level = {"last_s_dbfs": db, "max_s_dbfs": max(db, old.get("max_s_dbfs", -120.0)),
                          "min_s_dbfs": min(db, old.get("min_s_dbfs", 0.0)),      # the room's noise floor, roughly
                          "heard_s": round(self.pos / RATE)}
            self._sq, self._sn = 0.0, 0

    # ---- control from the status server (another thread)
    def request_cancel(self):
        self.cancel_flag.set()

    # ---- one microphone frame
    async def on_frame(self, pcm: bytes):
        n = len(pcm) // 2
        start, self.pos = self.pos, self.pos + n
        self.seq += 1
        self._level(pcm)
        self.ring.append((start, pcm))
        while self.ring and self.ring[0][0] < self.pos - int(self.PRE_ROLL_S * RATE):
            self.ring.pop(0)
        if self.cancel_flag.is_set():
            self.cancel_flag.clear()
            await self._cancel_now()
            return
        if self.paused or self.state in ("starting", "error"):
            return
        if self.state == "wake_listening":
            await self._listen(start, pcm)
        elif self.state == "recording":
            self._record(pcm)

    async def _listen(self, start: int, pcm: bytes):
        from main_logic.voice_input.activation.contracts import AudioFrame
        frame = AudioFrame(self.seq, start, start + len(pcm) // 2, self.clock(), RATE, pcm, self.generation)
        try:
            det = await self.detector.feed(frame, self.epoch)
        except Exception as e:  # noqa: BLE001 - the backend raises safe reason codes only
            await self.restart_detector(str(e)[:60] or type(e).__name__)
            return
        if det is None:
            return
        self.epoch += 1                                   # the next frame starts a fresh decoder stream
        if self._is_speaking():
            self.counts["ignored_while_speaking"] += 1
            self.outcome("ignored: Neko was speaking")
            return
        self.counts["detections"] += 1
        self.chime("wake")
        self.gain = self._gain_from(getattr(det, "sample_start", det.sample_end - RATE), det.sample_end)
        # what was said after the wake word, before the detector reported it, belongs to the command
        self.cmd = [self._amp(p[max(0, det.sample_end - s) * 2:]) for s, p in self.ring if s + len(p) // 2 > det.sample_end]
        self.vad = self.vad_factory()
        self.heard_at, self.speech_started, self.silence_since = self.audio_time(), None, None
        for p in self.cmd:
            self._vad_step(p)
        self.set("recording", "Listening")

    def _gain_from(self, start: int, end: int) -> float:
        """The wake word was just spoken: its loudness sets the gain for the command, so a quiet microphone or a speaker
        across the room still reaches the VAD and speech recognition at a normal level (at most +24 dB, never a cut)."""
        import numpy as np
        seg = b"".join(p[max(0, start - s) * 2:max(0, end - s) * 2] for s, p in self.ring if s < end and s + len(p) // 2 > start)
        x = np.abs(np.frombuffer(seg, dtype="<i2").astype(np.float32))
        if len(x) < RATE // 10:
            return 1.0
        peak = float(np.percentile(x, 99.5)) or 1.0
        return float(min(16.0, max(1.0, 0.3 * 32768 / peak)))

    def _amp(self, pcm: bytes) -> bytes:
        g = getattr(self, "gain", 1.0)
        if g == 1.0 or not pcm:
            return pcm
        import numpy as np
        return np.clip(np.frombuffer(pcm, dtype="<i2").astype(np.float32) * g, -32768, 32767).astype("<i2").tobytes()

    def _is_speaking(self) -> bool:
        now = self.clock()
        if now - self._speaking_checked > 1.0:
            self._speaking, self._speaking_checked = bool(self.speaking()), now
        return self._speaking

    def audio_time(self) -> float:
        """Seconds of audio heard so far: command timing follows the audio, so a file and the microphone behave alike."""
        return self.pos / RATE

    def _vad_step(self, pcm: bytes):
        now = self.audio_time()
        if self.vad.speech(pcm):
            self.speech_started = self.speech_started or now
            self.silence_since = None
        elif self.speech_started and self.silence_since is None:
            self.silence_since = now

    def _record(self, pcm: bytes):
        pcm = self._amp(pcm)
        self.cmd.append(pcm)
        self._vad_step(pcm)
        now = self.audio_time()
        if self.speech_started is None and now - self.heard_at >= self.WAIT_SPEECH_S:
            self.counts["no_speech"] += 1
            self.outcome("no command heard")
            self._back()
        elif (self.silence_since is not None and now - self.silence_since >= self.END_SILENCE_S) or now - self.heard_at >= self.MAX_COMMAND_S:
            audio, self.cmd = b"".join(self.cmd), []
            self.set("transcribing", "Understanding")
            self.worker = asyncio.ensure_future(self._handle(audio))

    async def _handle(self, audio: bytes):
        if self.dry_run and self.save_dir:                 # test aid, dry runs only: the command audio, to check the cut
            Path(self.save_dir).mkdir(parents=True, exist_ok=True)
            (Path(self.save_dir) / time.strftime("command-%H%M%S.wav")).write_bytes(to_wav(audio))
        try:
            text = await asyncio.to_thread(self.transcribe, audio)
        except Exception as e:  # noqa: BLE001
            self.counts["errors"] += 1
            self.chime("error")
            self.outcome("speech recognition failed", error=type(e).__name__)
            return self._back()
        if self.cancel_flag.is_set():
            return                                         # the next frame handles the cancel
        if not text:
            self.counts["no_speech"] += 1
            self.outcome("no words recognized", seconds=round(len(audio) / 2 / RATE, 1))
            return self._back()
        self.counts["commands"] += 1
        if STOP_WORDS.match(text) and await asyncio.to_thread(self.running):
            self.counts["stops"] += 1
            res = {"ok": True, "dry_run": True} if self.dry_run else await asyncio.to_thread(self.stop)
            self.outcome("stop the running task", ok=bool(res.get("ok")), note=str(res.get("note") or res.get("error") or "")[:120])
            return self._back()
        if PURE_CANCEL.match(text) or (CANCEL_WORDS.match(text) and len(text.split()) <= 3
                                         and await asyncio.to_thread(self.running)):
            self.counts["cancelled"] += 1
            self.outcome("cancelled by voice")
            return self._back()
        self.set("submitting", "Starting the task")
        if self.dry_run:
            self.outcome("command heard (dry run, not sent)", chars=len(text), words=len(text.split()))
            self.last["text"] = text                       # dry runs are tests: the status (not the log) shows the words
            return self._back()
        res = await asyncio.to_thread(self.submit, text)
        if res.get("ok"):
            self.counts["tasks_started"] += 1
            self.outcome("task started", run_id=res.get("run_id"), chars=len(text))
        else:
            self.counts["errors"] += 1
            self.chime("error")
            self.outcome("the main agent refused the task", error=str(res.get("error") or "")[:160])
        self._back()

    async def _cancel_now(self):
        if self.state in ("recording", "transcribing", "submitting"):
            if self.worker and not self.worker.done():
                self.worker.cancel()
            self.counts["cancelled"] += 1
            self.outcome("cancelled from the screen")
            self._back()
        elif await asyncio.to_thread(self.running):
            self.counts["stops"] += 1
            res = {"ok": True, "dry_run": True} if self.dry_run else await asyncio.to_thread(self.stop)
            self.outcome("stop the running task", ok=bool(res.get("ok")))

    def _back(self):
        self.cmd, self.vad, self.worker, self.gain = [], None, None, 1.0
        self.heard_at = self.speech_started = self.silence_since = None
        self.epoch += 1
        self.set("wake_listening")


# ---------------------------------------------------------------- audio sources
class MicSource:
    """The default Windows microphone. Opened at 16 kHz when the driver allows it, else at its own rate and resampled."""

    def __init__(self, loop: asyncio.AbstractEventLoop, device=None):
        import sounddevice as sd
        self.q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.loop, self.buf = loop, b""
        info = sd.query_devices(device, kind="input")
        self.name = str(info["name"])
        self.overflows = 0

        def cb(data, frames, t, status):
            if status:
                self.overflows += 1
            raw = bytes(data)
            loop.call_soon_threadsafe(self._put, raw)
        try:
            self.rate = RATE
            self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, device=device, callback=cb)
        except Exception:  # noqa: BLE001 - the driver refused 16 kHz
            self.rate = int(info["default_samplerate"])
            self.stream = sd.RawInputStream(samplerate=self.rate, channels=1, dtype="int16", blocksize=int(self.rate * 0.04),
                                            device=device, callback=cb)
        self.stream.start()

    def _put(self, raw):
        if self.q.full():
            self.q.get_nowait()                            # drop the oldest: never fall behind real time
        self.q.put_nowait(raw)

    async def frames(self):
        while True:
            raw = await self.q.get()
            self.buf += raw if self.rate == RATE else resample_int16(raw, self.rate)
            while len(self.buf) >= FRAME * 2:
                yield self.buf[:FRAME * 2]
                self.buf = self.buf[FRAME * 2:]

    def close(self):
        self.stream.stop()
        self.stream.close()


class FileSource:
    """A WAV file as if spoken into the microphone (tests): followed by silence so the command can end."""

    def __init__(self, path_or_pcm, tail_s: float = 3.0, realtime: bool = False):
        pcm = path_or_pcm if isinstance(path_or_pcm, bytes) else read_wav_16k(path_or_pcm)
        self.pcm = pcm + b"\0\0" * int(tail_s * RATE)
        self.realtime = realtime

    async def frames(self):
        for i in range(0, len(self.pcm) - FRAME * 2 + 1, FRAME * 2):
            yield self.pcm[i:i + FRAME * 2]
            if self.realtime:
                await asyncio.sleep(FRAME / RATE)

    def close(self):
        pass


async def run(loop_obj: WakeLoop, source, until=None):
    """Feed every frame; between frames let the transcribe/submit worker run. `until()` true ends the run."""
    await loop_obj.start()
    try:
        async for pcm in source.frames():
            await loop_obj.on_frame(pcm)
            await asyncio.sleep(0)                         # let a transcribe/submit worker take its turn
            if until and until():
                break
        while loop_obj.worker is not None and not loop_obj.worker.done():
            await asyncio.sleep(0.05)
    finally:
        source.close()
        await loop_obj.close()


def make_detector(threshold: float = 0.25, score: float = 1.0):
    from main_logic.voice_input.wake_word.sherpa_backend import SherpaWakeWordConfig, SherpaWakeWordDetector
    d = model_dir()
    if not d:
        raise SystemExit("no wake-word model: run scripts\\provision_wake_word_model.py --model-dir D:\\jarvis\\models\\voice\\kws")
    return SherpaWakeWordDetector(SherpaWakeWordConfig(model_dir=d, keywords=keywords(), keyword_threshold=threshold,
                                                       keyword_score=score))


# ---------------------------------------------------------------- status server
def serve_status(w: WakeLoop, port: int = PORT):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _ok(self):
            host = (self.headers.get("Host") or "").lower().rsplit(":", 1)[0].strip("[]")
            origin = (self.headers.get("Origin") or "").rstrip("/").lower()
            return host in LOCAL_HOSTS and (not origin or origin in ("http://127.0.0.1:48940", "http://localhost:48940"))

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if not self._ok():
                return self._json(403, {"error": "loopback only"})
            if self.path in ("/status", "/health"):
                return self._json(200, w.status())
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._ok():
                return self._json(403, {"error": "loopback only"})
            if self.path == "/cancel":
                w.request_cancel()
            elif self.path == "/pause":
                w.paused = True
                w.log("paused: microphone ignored")
            elif self.path == "/resume":
                w.paused = False
                w.log("resumed")
            else:
                return self._json(404, {"error": "not found"})
            self._json(200, {"ok": True, "state": w.status()["state"]})

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> int:
    ap = argparse.ArgumentParser(description="Hey Neko wake word (local)")
    ap.add_argument("--dry-run", action="store_true", help="detect and transcribe, but never start or stop a task")
    ap.add_argument("--minutes", type=float, default=0, help="stop after this many minutes (0 = run until stopped)")
    ap.add_argument("--threshold", type=float, default=float(os.environ.get("NEKO_WAKE_WORD_THRESHOLD", "0.25")))
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--wav", help="use this WAV file instead of the microphone (test)")
    ap.add_argument("--no-chime", action="store_true")
    ap.add_argument("--save-commands", help="dry runs only: write each command's audio here as a WAV file (test aid)")
    a = ap.parse_args()
    if a.save_commands and not a.dry_run:
        raise SystemExit("--save-commands is a test aid and works only with --dry-run")
    logs = DATA / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logf = open(logs / "wake_listener.log", "a", encoding="utf-8")

    def log(msg):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    w = WakeLoop(lambda: make_detector(a.threshold), dry_run=a.dry_run, log=log, chime=(lambda k: None) if a.no_chime else chime)
    w.save_dir = a.save_commands
    srv = serve_status(w, a.port)
    deadline = time.time() + a.minutes * 60 if a.minutes else None

    async def go():
        source = FileSource(a.wav) if a.wav else MicSource(asyncio.get_running_loop())
        log(f"listening on {'file ' + Path(a.wav).name if a.wav else 'microphone ' + source.name} (dry run: {a.dry_run}, "
            f"threshold {a.threshold}, model {Path(model_dir() or '?').name[:12]})")
        await run(w, source, until=(lambda: time.time() > deadline) if deadline else None)
    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass
    finally:
        log("stopped " + json.dumps(w.counts) + " audio " + json.dumps(getattr(w, "level", None)))
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
