"""Hey Neko: the local wake word for the Neko desktop agent. Windows. Runs with N.E.K.O's Python (neko-venv), which holds the
patched sherpa-onnx runtime 1.13.8+neko.kws2 that N.E.K.O's own keyword spotter requires.

    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe jarvis\\wake\\wake_listener.py [--dry-run] [--minutes N]

Flow (nothing leaves the laptop):
  microphone, 16 kHz mono  ->  N.E.K.O's SherpaWakeWordDetector (its own process, killed on close)
  "Hey Neko"               ->  short chime; record the command until 1 s of silence (Silero VAD), at most 12 s
  command audio            ->  local speech recognition (jarvis-local-speech, 127.0.0.1:48930)
  command text             ->  a main-agent task through the widget server (127.0.0.1:48940/api/task)
  then                     ->  back to wake listening
Cancel: say "cancel" / "never mind" as the command; say "Hey Neko, stop" while a task is planned or runs (stops that task);
or POST /cancel. A cancel that arrives while the command is already being sent stops the task as soon as it is accepted.
The outcome says what really happened: "task started" only when the plan is made and running, "task accepted, still
planning" otherwise (the result is then said in N.E.K.O's chat and shown on the icon).
While Neko itself is speaking, detections are ignored (its own voice must not wake it).
Language of the spoken command: --lang en|hi|te (default: the last choice saved by POST /lang, else NEKO_WAKE_LANG, else en),
shown on /status and sent to the speech server. Automatic language detection is NOT provided: one language is active at a
time, and a Hindi or Telugu command spoken while "en" is selected is transcribed by the English model. The cancel/stop words
below are English (plus a few Hindi/Telugu stop words for barge-in only).
Barge-in by voice (--barge-in, OFF by default; unverified on real hardware): while Neko speaks, only "Hey Neko" followed at once
by a stop word ("Hey Neko, stop") can stop her speech and the running task, and only when a second, stricter wake detector
(--barge-in-threshold, default 0.6 against the normal 0.25) also hears the wake word. Anything else heard while she speaks is
still dropped, and no task is ever started from audio heard while she speaks.
No audio is saved. The log holds states, timings and lengths, never what was said.
Status for the Neko icon: GET http://127.0.0.1:48945/status. POST /cancel, /pause (microphone off), /resume, /lang need the
launch's control secret (header X-Jarvis-Control; file <run dir>\\wake.token, see jarvis/scripts/control_auth.py).
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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "jarvis" / "scripts"))
import control_auth  # noqa: E402  per-launch secrets for the local control surfaces (audit M15)

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
# While a task runs, only a PURE stop phrase stops it ("stop", "stop that", "cancel the task", "please stop now"). It used to be any
# utterance that merely started with one of these words, so "cancel my meeting" or "stop the music" stopped a running task.
STOP_WORDS = re.compile(r"^\W*(?:please\s+)?(?:stop|cancel|abort|halt)"
                       r"(?:\s+(?:it|that|this|the\s+(?:task|job|run|work)|everything|all|now|right\s+now|please))*\W*$", re.I)
# The same rule in Hindi, Telugu and Hinglish: the WHOLE utterance is a stop phrase (optionally with a polite or address word
# around it: "Neko, ruko", "रुको अब", "ఆపండి నేకో"); a sentence that merely contains one ("रुको मत, चलते रहो", "don't stop",
# "ruko mat") is a task. Text is normalised first (NFKC, invisible format characters such as zero-width joiners removed,
# punctuation incl. the danda dropped). Script follows --lang: Devanagari phrases for hi, Telugu script for te; Roman
# (Hinglish / Roman Telugu) and English words in every language, because the speech server often romanises. UNVERIFIED on real
# speech: no microphone was available, the lists are tested on text only.
_STOP_FILLERS = frozenset({"neko", "नेको", "నేకో", "please", "plz", "pls", "ab", "अब", "abhi", "अभी", "kripya", "कृपया", "ji", "जी",
                           "hey", "he", "हे", "ఇప్పుడు", "ippudu", "dayachesi", "దయచేసి"})
_STOP_ROMAN = frozenset({"ruko", "ruk jao", "rukjao", "rukiye", "rukiyae", "ruk jaiye", "rukein", "ruko ruko", "band karo", "bandh karo",
                         "band kijiye", "band kar do", "bandh kar do", "rok do", "roko", "rokiye", "thehro", "thahro", "bas", "bas karo",
                         "bas bas", "aapu", "aapandi", "apandi", "aapeyyi", "aapeyandi", "aagu", "aagandi", "agandi", "aapu cheyyi",
                         "stop karo", "stop cheyyi", "stop cheyandi"})
_STOP_DEVANAGARI = frozenset({"रुको", "रुकिए", "रुकिये", "रुकें", "रुक जाओ", "रुक जाइए", "रुक जाइये", "रुक जा", "बंद करो", "बन्द करो",
                              "बंद कीजिए", "बंद कीजिये", "बंद कर दो", "बन्द कर दो", "ठहरो", "ठहरिए", "ठहरिये", "रोको", "रोक दो",
                              "रोकिए", "बस", "बस करो", "स्टॉप", "स्टाप", "स्टॉप करो"})
_STOP_TELUGU = frozenset({"ఆపు", "ఆపండి", "ఆపేయి", "ఆపేయండి", "ఆపివేయి", "ఆపివేయండి", "ఆగు", "ఆగండి", "చాలు", "ఆపు చేయి", "ఆపు చేయండి",
                          "స్టాప్"})
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
LANGS = ("en", "hi", "te")          # the languages the local speech server transcribes (jarvis-local-speech /health)
BARGE_THRESHOLD = 0.6               # the stricter detector's keyword threshold for barge-in (normal wake: 0.25)
# While Neko speaks, the only thing heard after the wake word that is acted on: a short pure stop phrase (the whole utterance).
BARGE_STOP_EN = re.compile(r"^\W*(?:please\s+)?(?:stop|cancel|halt|quiet|enough|be\s+quiet|that'?s\s+enough)"
                           r"(?:\s+(?:it|that|this|talking|now|please))*\W*$", re.I)
# Hindi / Telugu stop words (Devanagari / Telugu script as the speech server writes them). UNVERIFIED on real speech.
BARGE_STOP_WORDS = {"hi": ("रुको", "रुक जाओ", "बस", "चुप", "बंद करो"), "te": ("ఆపు", "ఆపండి", "చాలు", "ఆగు")}
MIC_STALL_S = 5.0                 # no audio at all for this long (40 ms frames normally arrive 25 times a second): the mic is gone
MIC_RETRY_MAX_S = 60.0            # microphone retries back off 2, 4, 8 ... seconds, at most this long apart


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


def norm_lang(value) -> str | None:
    """'en' / 'hi' / 'te' (any case, surrounding spaces), else None."""
    v = str(value or "").strip().lower()
    return v if v in LANGS else None


def lang_file() -> Path:
    """Where the console's language choice (POST /lang) is kept so it survives a restart: NEKO_WAKE_LANG_FILE, else
    <data>/neko/wake_lang.txt."""
    explicit = os.environ.get("NEKO_WAKE_LANG_FILE", "").strip()
    return Path(explicit) if explicit else DATA / "neko" / "wake_lang.txt"


def default_lang(path: Path | None = None) -> str:
    """The command language when --lang is not given: the saved choice, else NEKO_WAKE_LANG, else English."""
    try:
        saved = norm_lang((path or lang_file()).read_text(encoding="utf-8"))
    except OSError:
        saved = None
    return saved or norm_lang(os.environ.get("NEKO_WAKE_LANG")) or "en"


def _stop_text(text: str) -> str:
    """NFKC, invisible format characters (zero-width space/joiners, BOM, soft hyphen, bidi marks) removed, case folded, punctuation
    and symbols (incl. the danda) turned into spaces. Letters AND combining marks are kept: Devanagari vowel signs and Telugu
    signs are marks, not letters, and must not split a word."""
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")
    t = t.casefold()
    return " ".join("".join(" " if unicodedata.category(ch)[0] in "PSZC" else ch for ch in t).split())


def stop_phrase(text: str, lang: str = "en") -> bool:
    """Is the WHOLE utterance a stop phrase (English, Hindi, Telugu, Hinglish)? Used while a task runs: only then is the spoken
    stop sent on. Never true for a sentence that merely contains a stop word."""
    t = _stop_text(text)
    if not t:
        return False
    if STOP_WORDS.match(t):
        return True
    words = t.split()
    while words and words[0] in _STOP_FILLERS:
        words.pop(0)
    while words and words[-1] in _STOP_FILLERS:
        words.pop()
    if not words:
        return False
    native = _STOP_DEVANAGARI if lang == "hi" else _STOP_TELUGU if lang == "te" else ()
    for n in range(1, len(words) + 1):                          # the phrase, or the same phrase said up to three times ("रुको रुको")
        if len(words) % n == 0 and len(words) // n <= 3 and words == words[:n] * (len(words) // n):
            base = " ".join(words[:n])
            if STOP_WORDS.match(base) or base in _STOP_ROMAN or base in native:
                return True
    return False


def stop_note(res: dict) -> tuple[bool, str]:
    """(ok, note) of the answer to a spoken stop. ok only when what was running has really ended; a stop that was sent but not
    yet confirmed ("stop requested, 1 still running") is NOT ok."""
    res = res if isinstance(res, dict) else {}
    if res.get("requested") is True and not res.get("ok"):
        n = res.get("still_running")
        left = f"{n} still running" if isinstance(n, int) and not isinstance(n, bool) and n else "not yet confirmed ended"
        return False, f"stop requested, {left}"
    return bool(res.get("ok")), str(res.get("note") or res.get("error") or res.get("message") or "")[:120]


def barge_stop(text: str, lang: str = "en") -> bool:
    """Is the whole utterance a short stop phrase? ("stop", "stop it", "be quiet", "रुको" ...). Anything longer or different
    (a question, a request, Neko's own words coming back through the microphone) is not."""
    if BARGE_STOP_EN.match(text or ""):
        return True
    plain = " ".join((text or "").replace("।", " ").translate(str.maketrans("", "", ".,!?;:\"'")).split())
    return plain in BARGE_STOP_WORDS.get(lang, ())


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

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                                   # a 3xx is an error, never followed


# Calls to local services (the speech server, the widget, the privacy gateway ...): an explicit empty proxy map, so http_proxy /
# https_proxy / ALL_PROXY in the environment can never send the owner's voice or text to a proxy instead of 127.0.0.1 (re-review M13).
# The same rule as plugin/plugins/_shared/loopback_http.py, repeated because this program does not import the plugin package.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
_LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _open_local(req, timeout=None):
    host = (urllib.parse.urlsplit(req.full_url if isinstance(req, urllib.request.Request) else str(req)).hostname or "").lower()
    if host not in _LOOPBACK:
        raise ValueError("this call may only go to this machine, so nothing was sent")
    return _LOCAL_OPENER.open(req, timeout=timeout)


def http_json(url, body=None, timeout=5.0, raw=None, ctype="application/json", control=None):
    """`control`: name of the local server (control_auth token file) whose secret to send with a state-changing POST."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": ctype} if data is not None else {}
    if control:
        headers.update(control_auth.headers_for(control))       # re-read every call: the server may have restarted
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET", headers=headers)
    with _open_local(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "null")


def transcribe(pcm: bytes, lang: str = "en") -> str:
    out = http_json(f"{SPEECH}/v1/audio/transcriptions?language={lang}", raw=to_wav(pcm), timeout=60, ctype="audio/wav")
    return str((out or {}).get("text") or "").strip()


def stop_speech() -> dict:
    """Ask the JARVIS speech server to cancel the speech it is synthesizing now (POST /interrupt). UNVERIFIED on real hardware.
    `ok` is true only when that server says it cancelled at least one synthesis request in flight. What this cannot do:
    audio the page has already received is played by N.E.K.O's page, and N.E.K.O has no HTTP route that stops it (its
    interrupt runs over the page's websocket and inside the session), so that part is reported as not stopped."""
    try:
        r = http_json(f"{SPEECH}/interrupt", {}, timeout=3.0, control="speech")
    except urllib.error.HTTPError as e:
        return {"ok": False, "acknowledged": False, "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "acknowledged": False, "error": type(e).__name__}
    if not isinstance(r, dict) or r.get("ok") is not True:
        return {"ok": False, "acknowledged": False, "error": "no acknowledgement"}
    n = r.get("cancelled")
    cancelled = n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0
    if cancelled:
        return {"ok": True, "acknowledged": True, "cancelled": cancelled,
                "note": "speech still being synthesized was cancelled; audio already played or queued in the page is not stopped"}
    return {"ok": False, "acknowledged": True, "cancelled": 0,
            "note": "nothing was being synthesized; audio already in the page is not stopped (no N.E.K.O interrupt route)"}


def neko_speaking() -> bool:
    try:
        return bool((http_json(f"{SPEECH}/activity", timeout=1.0) or {}).get("speaking_estimate"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def task_running() -> bool:
    """A main-agent task is running or being planned, or the desktop plugin is busy on the PC (a single action started from chat
    is no main-agent task; the widget's status reports it as `pc.busy`, read from the plugin's activity file). Then a spoken
    "stop" has something to stop: it goes to the main agent's stop_task, which also asks the desktop plugin to stop."""
    try:
        status = http_json(f"{WIDGET}/api/status", timeout=8.0, control="widget") or {}
    except (urllib.error.URLError, OSError, ValueError):
        return False
    task = status.get("task") or {}
    pc = status.get("pc") if isinstance(status.get("pc"), dict) else {}
    return task.get("state") in ("running", "planning") or pc.get("busy") is True


def submit_task(text: str) -> dict:
    try:
        return http_json(f"{WIDGET}/api/task", {"task": text, "mode": "work"}, timeout=30, control="widget") or {"ok": False, "error": "no answer"}
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode()) | {"ok": False}
        except ValueError:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "error": type(e).__name__}


def stop_task() -> dict:
    try:
        return http_json(f"{WIDGET}/api/action", {"action": "stop"}, timeout=20, control="widget") or {"ok": False}
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
    paused: microphone ignored (POST /pause). error: the detector failed; it is restarted after a pause. A microphone
    that fails (unplugged, driver error, no audio any more) is error too, with the reason on /status: run() reopens it with
    a growing pause between tries, and the listener goes back to wake listening once audio flows again.
    """
    WAIT_SPEECH_S = 5.0
    END_SILENCE_S = 1.0
    MIN_COMMAND_SPEECH_S = 0.4        # less voiced audio than this is a click, a breath or the tail of the wake word, not a command
    MAX_COMMAND_S = 12.0
    BARGE_WAIT_SPEECH_S = 2.0         # a barge-in is one short word: give up sooner ...
    BARGE_MAX_COMMAND_S = 4.0         # ... and never record a long sentence while Neko speaks
    PRE_ROLL_S = 2.0

    def __init__(self, detector_factory, vad_factory=Vad, transcribe=transcribe, submit=submit_task, stop=stop_task,
                 speaking=neko_speaking, running=task_running, chime=chime, dry_run=False, log=None, clock=time.time,
                 lang="en", barge_detector_factory=None, stop_speech=stop_speech):
        self.detector_factory, self.vad_factory = detector_factory, vad_factory
        self.transcribe, self.submit, self.stop, self.speaking, self.running = transcribe, submit, stop, speaking, running
        self.lang = norm_lang(lang) or "en"            # language of the spoken command (see set_lang)
        self.lang_path: Path | None = None              # where set_lang keeps the choice (main() sets it; tests leave None)
        # Barge-in by voice: OFF unless a stricter second detector is given. Unverified on real hardware.
        self.barge_detector_factory, self.stop_speech = barge_detector_factory, stop_speech
        self.barge_detector = None
        self.barge_epoch = 1
        self.barge = False                              # the current recording is a barge-in: stop words only
        self.barge_threshold: float | None = None       # for /status only
        self.chime, self.dry_run, self.clock = chime, dry_run, clock
        self.save_dir = None          # dry runs only (--save-commands): where the command audio is written for checking
        self.gain = 1.0
        self.log = log or (lambda *a: None)
        from main_logic.voice_input.activation.contracts import ActivationGeneration
        self.generation = ActivationGeneration("neko-wake", 1, 1, 1, 1, "wake_listener")
        self.detector = None
        self.state, self.since, self.detail = "starting", clock(), ""
        self.counts = {"detections": 0, "ignored_while_speaking": 0, "commands": 0, "cancelled": 0, "no_speech": 0,
                       "tasks_started": 0, "tasks_planning": 0, "stops": 0, "errors": 0, "mic_errors": 0,
                       "barge_in_heard": 0, "barge_in_stops": 0, "barge_in_ignored": 0, "passed_while_running": 0,
                       "too_short": 0}
        self.last = None              # the last outcome: {"what", "at", ...} (never the words)
        self.seq = self.pos = self.epoch = 0
        self.ring: list[tuple[int, bytes]] = []
        self.cmd: list[bytes] = []
        self.vad = None
        self.heard_at = self.speech_started = self.silence_since = None
        self.voiced_s = None          # seconds of speech in the command being handled (set when the recording ends)
        self.pre_voiced_s = 0.0       # the part of it that was already in the buffer when the wake word was reported
        self.cancel_flag = threading.Event()
        self.paused = False
        self._speaking_checked, self._speaking = 0.0, False
        self.worker: asyncio.Task | None = None
        self.withdraw: asyncio.Task | None = None   # the submit cancelled from the screen while sending: stop its task
        self.background: asyncio.Task | None = None
        self.mic_problem: str | None = None          # why the microphone is not delivering audio (None: it is)

    # ---- status
    def set(self, state, detail=""):
        if state != self.state:
            self.log(f"state {self.state} -> {state}" + (f" ({detail})" if detail else ""))
        self.state, self.since, self.detail = state, self.clock(), detail

    def status(self) -> dict:
        healthy = self.detector is not None and self.state not in ("error", "starting") and not self.mic_problem
        state = "paused" if self.paused else "error" if self.mic_problem else self.state
        return {"enabled": True, "healthy": healthy, "state": state, "detail": self.mic_problem or self.detail,
                "seconds_in_state": round(self.clock() - self.since, 1), "dry_run": self.dry_run, "counts": dict(self.counts),
                "last": self.last, "keywords": sorted({k.split()[-1][1:] for k in keywords()}),
                "audio": getattr(self, "level", None), "lang": self.lang,
                "barge_in": {"enabled": self.barge_detector_factory is not None, "active": self.barge_detector is not None,
                             "threshold": self.barge_threshold}}

    def set_lang(self, value) -> str | None:
        """Switch the language of the spoken command (POST /lang). Returns the language now active, or None when `value` is
        not en/hi/te (nothing changes). The choice is kept in `lang_path` when set. No automatic language detection exists."""
        lang = norm_lang(value)
        if lang is None:
            return None
        if lang != self.lang:
            self.log(f"command language {self.lang} -> {lang}")
        self.lang = lang
        if self.lang_path is not None:
            try:
                self.lang_path.parent.mkdir(parents=True, exist_ok=True)
                self.lang_path.write_text(lang + "\n", encoding="utf-8")
            except OSError as e:
                self.log(f"command language not saved ({type(e).__name__})")
        return lang

    def _asr(self, audio: bytes) -> str:
        """Transcribe in the selected language. A transcribe function that takes no `lang` (older fakes) is called without."""
        import inspect
        try:
            params = inspect.signature(self.transcribe).parameters
            takes_lang = "lang" in params or any(p.kind is p.VAR_KEYWORD for p in params.values())
        except (TypeError, ValueError):
            takes_lang = False
        return self.transcribe(audio, lang=self.lang) if takes_lang else self.transcribe(audio)

    def outcome(self, what, **extra):
        self.last = {"what": what, "at": time.strftime("%H:%M:%S"), **extra}
        self.log("outcome " + json.dumps(self.last))

    # ---- detector life cycle
    async def start(self):
        self.set("starting")
        self.detector = self.detector_factory()
        await self.detector.prepare()
        self.epoch += 1
        if self.barge_detector_factory is not None and self.barge_detector is None:
            try:                                          # a failing strict detector only switches barge-in off
                self.barge_detector = self.barge_detector_factory()
                await self.barge_detector.prepare()
            except Exception as e:  # noqa: BLE001
                self.barge_detector = None
                self.log(f"barge-in unavailable ({type(e).__name__}): normal wake listening continues")
        self.set("wake_listening")

    async def close(self):
        if self.detector is not None:
            await self.detector.close()
            self.detector = None
        if self.barge_detector is not None:
            try:
                await self.barge_detector.close()
            finally:
                self.barge_detector = None

    async def restart_detector(self, reason: str):
        self.counts["errors"] += 1
        self.set("error", reason)
        await self.close()
        await asyncio.sleep(2.0)
        try:
            await self.start()
        except Exception as e:  # noqa: BLE001
            self.set("error", f"wake word could not restart ({type(e).__name__})")

    # ---- microphone life cycle (run() calls these)
    def mic_failed(self, reason: str, retry_in: float):
        """The microphone stopped (unplugged, driver error) or could not be opened. Reported like a detector error: state
        error with the reason on /status, counted, and retried by run(). Whatever was being recorded is dropped."""
        self.counts["errors"] += 1
        self.counts["mic_errors"] += 1
        self.mic_problem = f"microphone error ({reason}); retrying in {retry_in:.0f} s"
        if self.state in ("wake_listening", "recording", "error"):   # a transcribe/submit worker finishes on its own
            if self.state == "recording":
                self.counts["cancelled"] += 1
            self.set("error", self.mic_problem)
        else:
            self.log("microphone: " + self.mic_problem)

    def mic_ok(self):
        """Audio flows again: back to listening for the wake word (the detector itself was never closed)."""
        if self.mic_problem is None:
            return
        self.log("microphone back: " + self.mic_problem)
        self.mic_problem = None
        if self.state == "error" and self.detector is not None:
            self._back()

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
        # Without barge-in "is Neko speaking?" is asked only when the wake word was heard (as before). With it, it is needed on
        # every frame, so it is asked off the audio loop (at most once a second) and never blocks the microphone.
        speaking = await self._speaking_now() if self.barge_detector is not None else None
        strict = None
        if speaking and self.barge_detector is not None:
            # Barge-in (opt-in): the stricter detector only listens while Neko speaks. It alone decides whether this was
            # really "Hey Neko"; the normal detector's hit during her speech is still dropped below.
            try:
                strict = await self.barge_detector.feed(frame, self.barge_epoch)
            except Exception as e:  # noqa: BLE001 - barge-in is optional: it switches itself off, wake listening goes on
                self.log(f"barge-in detector failed ({type(e).__name__}): barge-in is off until the next restart")
                self.barge_detector = None
            if strict is not None:
                self.barge_epoch += 1
        try:
            det = await self.detector.feed(frame, self.epoch)
        except Exception as e:  # noqa: BLE001 - the backend raises safe reason codes only
            await self.restart_detector(str(e)[:60] or type(e).__name__)
            return
        if det is not None:
            self.epoch += 1                               # the next frame starts a fresh decoder stream
        if strict is not None:
            return self._begin_recording(strict, barge=True)
        if det is None:
            return
        if speaking is None:
            speaking = self._is_speaking()
        if speaking:
            self.counts["ignored_while_speaking"] += 1
            self.outcome("ignored: Neko was speaking")
            return
        self.counts["detections"] += 1
        self.chime("wake")
        self._begin_recording(det)

    def _begin_recording(self, det, barge: bool = False):
        """The wake word was heard: record what follows. In barge mode (Neko is speaking) that recording can only ever end in
        a stop; it is never sent as a task."""
        self.barge = barge
        if barge:
            self.counts["barge_in_heard"] += 1
        self.gain = self._gain_from(getattr(det, "sample_start", det.sample_end - RATE), det.sample_end)
        # what was said after the wake word, before the detector reported it, belongs to the command
        self.cmd = [self._amp(p[max(0, det.sample_end - s) * 2:]) for s, p in self.ring if s + len(p) // 2 > det.sample_end]
        self.vad = self.vad_factory()
        self.heard_at, self.speech_started, self.silence_since = self.audio_time(), None, None
        self.pre_voiced_s = sum(len(p) / 2 / RATE for p in self.cmd if self._vad_step(p))   # speech already said before the detector fired
        self.set("recording", "Listening for a stop word" if barge else "Listening")

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

    async def _speaking_now(self) -> bool:
        now = self.clock()
        if now - self._speaking_checked > 1.0:
            self._speaking, self._speaking_checked = bool(await asyncio.to_thread(self.speaking)), now
        return self._speaking

    def audio_time(self) -> float:
        """Seconds of audio heard so far: command timing follows the audio, so a file and the microphone behave alike."""
        return self.pos / RATE

    def _vad_step(self, pcm: bytes) -> bool:
        now = self.audio_time()
        if self.vad.speech(pcm):
            self.speech_started = self.speech_started or now
            self.silence_since = None
            return True
        if self.speech_started and self.silence_since is None:
            self.silence_since = now
        return False

    def _record(self, pcm: bytes):
        pcm = self._amp(pcm)
        self.cmd.append(pcm)
        self._vad_step(pcm)
        now = self.audio_time()
        wait_s = self.BARGE_WAIT_SPEECH_S if self.barge else self.WAIT_SPEECH_S
        max_s = self.BARGE_MAX_COMMAND_S if self.barge else self.MAX_COMMAND_S
        if self.speech_started is None and now - self.heard_at >= wait_s:
            self.counts["no_speech"] += 1
            self.outcome("no command heard")
            self._back()
        elif (self.silence_since is not None and now - self.silence_since >= self.END_SILENCE_S) or now - self.heard_at >= max_s:
            audio, self.cmd = b"".join(self.cmd), []
            if self.speech_started is not None:
                self.voiced_s = (self.silence_since if self.silence_since is not None else now) - self.speech_started + self.pre_voiced_s
            self.set("transcribing", "Understanding")
            self.worker = asyncio.ensure_future(self._handle(audio))

    async def _handle(self, audio: bytes):
        if self.dry_run and self.save_dir:                 # test aid, dry runs only: the command audio, to check the cut
            Path(self.save_dir).mkdir(parents=True, exist_ok=True)
            (Path(self.save_dir) / time.strftime("command-%H%M%S.wav")).write_bytes(to_wav(audio))
        if self.barge:
            return await self._handle_barge(audio)
        try:
            text = await asyncio.to_thread(self._asr, audio)
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
        if stop_phrase(text, self.lang) and await asyncio.to_thread(self.running):
            self.counts["stops"] += 1
            res = {"ok": True, "dry_run": True} if self.dry_run else await asyncio.to_thread(self.stop)
            ok, note = stop_note(res)
            self.outcome("stop the running task", ok=ok, note=note, requested=bool(res.get("requested")))
            return self._back()
        if PURE_CANCEL.match(text):                        # the whole utterance is "never mind" / "cancel that" / "forget it"
            self.counts["cancelled"] += 1
            self.outcome("cancelled by voice")
            return self._back()
        if CANCEL_WORDS.match(text) and await asyncio.to_thread(self.running):
            # "stop the music", "cancel my meeting" while a task runs: NOT a stop phrase (the running task is not stopped), and no
            # longer dropped silently as "cancelled by voice" either (it used to vanish): it goes on like any command, so the main
            # agent answers it (a second request while one runs gets its honest "busy"), and this says so in the log and status.
            self.counts["passed_while_running"] += 1
            self.outcome("not a stop phrase: sent as a new command; the running task is NOT stopped", chars=len(text))
        if not self.barge and self.voiced_s is not None and self.voiced_s < self.MIN_COMMAND_SPEECH_S:
            # A blip too short to be a sentence (the transcriber then invents words, e.g. "Tico, pénico" after a bare "Hey Neko"):
            # never a task. A stop or cancel word is handled above, so a short "stop" still works.
            self.counts["too_short"] += 1
            self.outcome("too little speech to be a command: ignored", seconds=round(self.voiced_s, 2), chars=len(text))
            return self._back()
        self.set("submitting", "Starting the task")
        if self.dry_run:
            self.outcome("command heard (dry run, not sent)", chars=len(text), words=len(text.split()))
            self.last["text"] = text                       # dry runs are tests: the status (not the log) shows the words
            return self._back()
        if self.cancel_flag.is_set():
            return                                         # cancelled before anything was sent: the next frame handles it
        res = await asyncio.to_thread(self.submit, text)
        if self.withdraw is not None and self.withdraw is asyncio.current_task():
            # Cancelled from the screen while the request was on its way. It cannot be called back, so the task it started
            # is stopped at once (main_agent stops a task that is still being planned before any step starts). The listener
            # already went back to listening; a new command may be under way, so the state is left alone.
            self.withdraw = None
            if res.get("ok"):
                stopped = await asyncio.to_thread(self.stop)
                self.outcome("cancelled from the screen", note="the task had already been sent, so it was stopped",
                             stopped=bool(stopped.get("ok")))
            return
        if res.get("ok") and res.get("state") == "planning":
            self.counts["tasks_planning"] += 1
            self.outcome("task accepted, still planning", run_id=res.get("run_id"), chars=len(text))
        elif res.get("ok"):
            self.counts["tasks_started"] += 1
            self.outcome("task started", run_id=res.get("run_id"), chars=len(text))
        elif res.get("state") == "cancelled":
            self.outcome("task stopped before it started", note=str(res.get("error") or "")[:160])
        else:
            self.counts["errors"] += 1
            self.chime("error")
            self.outcome("the main agent refused the task", error=str(res.get("error") or "")[:160])
        self._back()

    async def _handle_barge(self, audio: bytes):
        """"Hey Neko, <stop>" heard while Neko speaks (opt-in; unverified on real hardware). Only a short pure stop phrase does
        anything: it stops her speech and, if one runs, the task. Everything else is dropped. This path never calls submit()."""
        try:
            text = await asyncio.to_thread(self._asr, audio)
        except Exception as e:  # noqa: BLE001
            self.counts["errors"] += 1
            self.outcome("barge-in: speech recognition failed", error=type(e).__name__)
            return self._back()
        if self.cancel_flag.is_set():
            return                                         # the next frame handles the cancel
        if not text or not barge_stop(text, self.lang):
            self.counts["barge_in_ignored"] += 1
            self.outcome("barge-in ignored: not a stop word", chars=len(text or ""))
            return self._back()
        self.counts["barge_in_stops"] += 1
        if self.dry_run:
            speech, task = {"ok": True, "dry_run": True}, {"ok": True, "dry_run": True}
        else:
            speech = await asyncio.to_thread(self.stop_speech)
            running = await asyncio.to_thread(self.running)
            task = await asyncio.to_thread(self.stop) if running else {"ok": True, "note": "no task was running"}
            if running:
                self.counts["stops"] += 1
        self.outcome("barge-in: stop", speech_stopped=bool(speech.get("ok")), task_ok=bool(task.get("ok")),
                     speech_note=str(speech.get("error") or speech.get("note") or "")[:160],
                     note=str(task.get("note") or task.get("error") or speech.get("error") or "")[:120])
        self._back()

    async def _cancel_now(self):
        if self.state == "submitting" and self.worker and not self.worker.done():
            # The command is already being sent and that cannot be undone: let the worker finish sending, then stop the
            # task it started (see _handle). Cancelling the worker would only hide that the task went through.
            self.withdraw = self.background = self.worker
            self.counts["cancelled"] += 1
            self.outcome("cancelled from the screen", note="the command was already being sent; it will be stopped")
            self._back()
        elif self.state in ("recording", "transcribing", "submitting"):
            if self.worker and not self.worker.done():
                self.worker.cancel()
            self.counts["cancelled"] += 1
            self.outcome("cancelled from the screen")
            self._back()
        elif await asyncio.to_thread(self.running):
            self.counts["stops"] += 1
            res = {"ok": True, "dry_run": True} if self.dry_run else await asyncio.to_thread(self.stop)
            ok, note = stop_note(res)
            self.outcome("stop the running task", ok=ok, note=note, requested=bool(res.get("requested")))

    def _back(self):
        self.cmd, self.vad, self.worker, self.gain, self.barge = [], None, None, 1.0, False
        self.heard_at = self.speech_started = self.silence_since = None
        self.epoch += 1
        self.set("wake_listening")


# ---------------------------------------------------------------- audio sources
class MicError(OSError):
    """The microphone stopped delivering audio."""


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
            try:
                raw = await asyncio.wait_for(self.q.get(), MIC_STALL_S)
            except asyncio.TimeoutError:
                # PortAudio does not always raise when a device disappears: the callback just stops. Silence is still
                # audio (zeros), so no frames at all for this long means the microphone is gone.
                raise MicError(f"no audio for {MIC_STALL_S:.0f} s") from None
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


def _close_quietly(source):
    try:
        source.close()
    except Exception:  # noqa: BLE001 - closing a microphone that already vanished may fail too
        pass


async def run(loop_obj: WakeLoop, source, until=None, reopen=None, pause=None):
    """Feed every frame; between frames let the transcribe/submit worker run. `until()` true ends the run.

    With `reopen` (a function that opens the audio source again; the microphone in main()), an audio source that fails,
    or cannot be opened (`source` may be None), does not end the listener: the error is shown on /status and the source
    is reopened after 2, 4, 8 ... seconds (at most MIC_RETRY_MAX_S). Without `reopen`, a source error is raised as before."""
    pause = pause or asyncio.sleep
    await loop_obj.start()
    tries = 0
    try:
        while True:
            try:
                if source is None:
                    source = reopen()
                fed = False
                async for pcm in source.frames():
                    if not fed:
                        fed, tries = True, 0
                        loop_obj.mic_ok()
                    await loop_obj.on_frame(pcm)
                    await asyncio.sleep(0)                 # let a transcribe/submit worker take its turn
                    if until and until():
                        break
                break                                      # the source ended (a file) or `until` said stop
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any microphone/driver failure: report it and try again
                if reopen is None:
                    raise
                tries += 1
                wait = min(MIC_RETRY_MAX_S, 2.0 ** min(tries, 10))   # capped: a mic gone for days must not overflow
                loop_obj.mic_failed(f"{type(e).__name__}: {str(e)[:80]}", wait)
                if source is not None:
                    _close_quietly(source)
                source = None
                await pause(wait)
                if until and until():
                    break
        while any(t is not None and not t.done() for t in (loop_obj.worker, loop_obj.background)):
            await asyncio.sleep(0.05)
    finally:
        if source is not None:
            _close_quietly(source)
        await loop_obj.close()


def make_detector(threshold: float = 0.25, score: float = 1.0):
    from main_logic.voice_input.wake_word.sherpa_backend import SherpaWakeWordConfig, SherpaWakeWordDetector
    d = model_dir()
    if not d:
        raise SystemExit("no wake-word model: run scripts\\provision_wake_word_model.py --model-dir D:\\jarvis\\models\\voice\\kws")
    return SherpaWakeWordDetector(SherpaWakeWordConfig(model_dir=d, keywords=keywords(), keyword_threshold=threshold,
                                                       keyword_score=score))


# ---------------------------------------------------------------- status server
def off_switch(w, on: bool) -> bool:
    """The owner's persistent "wake off": POST /pause writes <data>\\wake.off and POST /resume removes it (`w.off_path`, set by main).
    The watchdog never starts the listener while that file exists, and a listener started while it exists comes up paused, so a
    pause survives a crash, a restart and a reboot instead of silently turning the microphone back on. False when it could not be
    saved (the in-memory pause still holds)."""
    path = getattr(w, "off_path", None)
    if not path:
        return False
    try:
        if on:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(time.strftime("%Y-%m-%d %H:%M:%S") + " paused by POST /pause; POST /resume removes this file\n", encoding="utf-8")
        else:
            Path(path).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def serve_status(w: WakeLoop, port: int = PORT, secret: str | None = None):
    """GET /health is liveness only and open. GET /status (counts, last outcome, language, microphone problems) and POST /cancel,
    /pause, /resume and /lang need (audit M15; re-review M3 2026-10-19 for /status) this launch's control secret in `X-Jarvis-Control` (control_auth; `main` publishes it to the `wake` token
    file for the owner's own scripts), an exact loopback Host on this port, and no Origin or the widget's. Without `secret` a fresh
    random one is made (read it from `srv.control_secret`). A same-OS-user process can still read the token file: see
    scripts/control_auth.py. Nothing here starts a task."""
    control_secret = secret or control_auth.new_secret()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _ok(self):
            """Read-only access: a loopback Host, and no foreign Origin (the widget's own is fine)."""
            host = (control_auth.host_value(self.headers) or "").lower().rsplit(":", 1)[0].strip("[]")      # none/duplicate Host: refused
            origin = (self.headers.get("Origin") or "").rstrip("/").lower()
            return host in LOCAL_HOSTS and (not origin or origin in ("http://127.0.0.1:48940", "http://localhost:48940"))

        def _control_ok(self):
            """A state-changing request: the secret, the exact Host (name and this port) and an allow-listed Origin."""
            real = self.server.server_address[1]
            return (control_auth.host_exact(control_auth.host_value(self.headers), real)
                    and control_auth.origin_ok(self.headers.get("Origin"), ("http://127.0.0.1:48940", "http://localhost:48940"))
                    and control_auth.secret_ok(self.headers.get(control_auth.HEADER), control_secret))

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if not self._ok():
                return self._json(403, {"error": "loopback only"})
            if self.path == "/health":                  # public-harmless liveness (the watchdog, neko-start.ps1): no listener state
                try:
                    ready = bool(w.status().get("healthy"))       # one boolean, no listener state: false when the detector, the microphone or the loop is not working
                except Exception:
                    ready = False
                return self._json(200, {"status": "ok" if ready else "degraded", "ready": ready, "service": "jarvis-wake"})
            if self.path == "/status":                  # sensitive-read (counts, last outcome, language, mic problems): the token
                if not self._control_ok():
                    return self._json(401, {"error": "not signed in: send the wake token (wake.token in the run folder)", "login": True})
                return self._json(200, w.status())
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._control_ok():
                return self._json(403, {"error": "refused: the control secret, a loopback Host and no foreign Origin are required"})
            if self.path == "/cancel":
                w.request_cancel()
            elif self.path == "/pause":
                w.paused = True
                off_switch(w, True)
                w.log("paused: microphone ignored" + (" (saved: the watchdog will not restart the listener until /resume)" if getattr(w, "off_path", None) else ""))
            elif self.path == "/resume":
                w.paused = False
                off_switch(w, False)
                w.log("resumed")
            elif self.path.split("?", 1)[0] == "/lang":
                # {"lang": "en"|"hi"|"te"} as JSON, or ?lang=hi. Same loopback + same-origin guard as /pause (_ok above).
                from urllib.parse import parse_qs
                value = (parse_qs(self.path.partition("?")[2]).get("lang") or [None])[0]
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if value is None and 0 < n <= 1024:
                        body = json.loads(self.rfile.read(n).decode("utf-8", "replace"))
                        value = body.get("lang") if isinstance(body, dict) else None
                except (ValueError, OSError):
                    value = None
                lang = w.set_lang(value)
                if lang is None:
                    return self._json(400, {"error": "lang must be one of " + ", ".join(LANGS), "lang": w.lang})
                return self._json(200, {"ok": True, "lang": lang})
            else:
                return self._json(404, {"error": "not found"})
            self._json(200, {"ok": True, "state": w.status()["state"]})

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.control_secret = control_secret              # for main() and tests; never served by a route
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
    ap.add_argument("--lang", choices=LANGS, default=None,
                    help="language of the spoken command (default: saved choice, else NEKO_WAKE_LANG, else en). "
                         "No automatic language detection: one language is active at a time; POST /lang switches it")
    ap.add_argument("--barge-in", action="store_true",
                    help="OFF by default. While Neko speaks, 'Hey Neko, stop' (wake word + stop word, heard by a stricter "
                         "detector) stops her speech and the running task. Never starts a task. Unverified on real hardware")
    ap.add_argument("--barge-in-threshold", type=float, default=BARGE_THRESHOLD,
                    help=f"keyword threshold of the stricter barge-in detector (default {BARGE_THRESHOLD}; normal wake 0.25)")
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

    if a.barge_in and not a.threshold < a.barge_in_threshold <= 1:
        raise SystemExit(f"--barge-in-threshold must be above the normal threshold ({a.threshold}) and at most 1")
    w = WakeLoop(lambda: make_detector(a.threshold), dry_run=a.dry_run, log=log, chime=(lambda k: None) if a.no_chime else chime,
                 lang=a.lang or default_lang(),
                 barge_detector_factory=(lambda: make_detector(a.barge_in_threshold)) if a.barge_in else None)
    w.lang_path = lang_file()
    w.off_path = DATA / "wake.off"                   # the same file the watchdog honours (jarvis/scripts/watchdog.py WAKE_OFF)
    if w.off_path.exists():
        w.paused = True                              # the owner paused it before: it stays off until POST /resume
        log("wake.off exists: starting paused (POST /resume to listen)")
    w.barge_threshold = a.barge_in_threshold if a.barge_in else None
    w.save_dir = a.save_commands
    srv = serve_status(w, a.port)
    try:
        control_auth.publish("wake", srv.control_secret)      # for the owner's own scripts (POST /cancel, /pause, /resume, /lang)
    except OSError as e:
        log(f"warning: could not publish the control token ({type(e).__name__}); POST /pause, /resume, /cancel, /lang need it")
    deadline = time.time() + a.minutes * 60 if a.minutes else None

    async def go():
        until = (lambda: time.time() > deadline) if deadline else None
        log(f"listening on {'file ' + Path(a.wav).name if a.wav else 'the microphone'} (dry run: {a.dry_run}, "
            f"threshold {a.threshold}, language {w.lang}, barge-in {'on, threshold ' + str(a.barge_in_threshold) if a.barge_in else 'off'}, "
            f"model {Path(model_dir() or '?').name[:12]})")
        if a.wav:
            return await run(w, FileSource(a.wav), until=until)

        def open_mic():
            mic = MicSource(asyncio.get_running_loop())
            log("microphone: " + mic.name)
            return mic
        # A microphone that is missing, unplugged or failing never ends the listener: /status says why and it retries.
        await run(w, None, until=until, reopen=open_mic)
    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass
    finally:
        log("stopped " + json.dumps(w.counts) + " audio " + json.dumps(getattr(w, "level", None)))
        srv.shutdown()
        control_auth.unpublish("wake")
    return 0


if __name__ == "__main__":
    sys.exit(main())
