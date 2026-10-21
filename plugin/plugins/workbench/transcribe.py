"""Audio to timestamped text, on this PC only.

Provider: JARVIS's own local speech server (``jarvis/scripts/local_speech_server.py``, loopback ``POST
/v1/audio/transcriptions``): Parakeet for English, IndicConformer for Hindi and Telugu (sherpa-onnx). No audio leaves the
PC on this path and no external speech service is ever called from here.

- Non-WAV audio is converted to 16 kHz mono WAV with ffmpeg when ffmpeg is installed (``WORKBENCH_FFMPEG`` or PATH);
  without it only WAV can be read and the result says so.
- The audio is cut into chunks of at most ``CHUNK_S`` seconds; each chunk's start time is the evidence timestamp of its
  text (the recognizer returns no word timings, so a requirement points at a 30-second window, not an exact second).
- Language: the owner's hint (en, hi, te). Without one the English model is used and the language is recorded as
  ``unconfirmed``, which becomes an open question: the models cannot tell the languages apart by themselves here.
- Speakers are not separated (no diarisation in this stack): audio segments carry no speaker name, so their requests are
  attributed to "the recording", and the requirements say so.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Callable

from . import safety

CHUNK_S = 30
MAX_AUDIO_S = 3 * 60 * 60
LANGS = ("en", "hi", "te")
Transcriber = Callable[[bytes, str], str]


def _stt_url() -> str:
    return os.environ.get("WORKBENCH_STT_URL", "http://127.0.0.1:48930").rstrip("/")


def _ffmpeg() -> str | None:
    env = os.environ.get("WORKBENCH_FFMPEG", "").strip()
    if env and Path(env).is_file():
        return env
    return shutil.which("ffmpeg")


def to_wav(path: Path) -> tuple[bytes | None, str]:
    """16 kHz mono 16-bit WAV bytes, or (None, why not)."""
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                if w.getsampwidth() == 2 and w.getnchannels() == 1 and w.getframerate() == 16000:
                    return path.read_bytes(), ""
        except (wave.Error, EOFError):
            return None, "the WAV file could not be read"
    exe = _ffmpeg()
    if not exe:
        return None, ("only 16 kHz mono WAV can be read without ffmpeg; install ffmpeg or give the recording as WAV "
                      "(or add a transcript file)")
    with tempfile.TemporaryDirectory(prefix="wb-audio-") as tmp:
        out = Path(tmp) / "a.wav"
        try:
            subprocess.run([exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path), "-t", str(MAX_AUDIO_S),
                            "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-f", "wav", str(out)],
                           check=True, capture_output=True, timeout=600)
            return out.read_bytes(), ""
        except (subprocess.SubprocessError, OSError) as e:
            return None, f"ffmpeg could not convert the recording ({type(e).__name__})"


def chunks(wav_bytes: bytes, seconds: int = CHUNK_S) -> list[tuple[float, bytes]]:
    """(start time in seconds, WAV bytes) for each chunk."""
    out: list[tuple[float, bytes]] = []
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        per = rate * seconds
        start = 0
        while True:
            frames = w.readframes(per)
            if not frames:
                break
            buf = io.BytesIO()
            with wave.open(buf, "wb") as o:
                o.setnchannels(channels)
                o.setsampwidth(width)
                o.setframerate(rate)
                o.writeframes(frames)
            out.append((start / rate, buf.getvalue()))
            start += per
    return out


def local_server_transcriber(wav: bytes, lang: str) -> str:
    """The local speech server. Loopback only (``_shared.loopback_http``: no proxy, no redirects)."""
    import urllib.request

    from plugin.plugins._shared import loopback_http
    url = loopback_http.check_loopback(f"{_stt_url()}/v1/audio/transcriptions?language={lang}&response_format=json")
    req = urllib.request.Request(url, data=wav, method="POST", headers={"Content-Type": "audio/wav"})
    with loopback_http.urlopen(req, timeout=120) as r:
        return str(json.loads(r.read().decode("utf-8", "replace")).get("text") or "")


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def transcribe(path: Path, transcriber: Transcriber | None = None, language: str = "") -> dict[str, Any]:
    """{"status": done|blocked|failed, "provider", "language", "segments": [{"time", "text"}], "note"}. Never raises."""
    lang = language if language in LANGS else ""
    wav, why = to_wav(path)
    if wav is None:
        return {"status": "blocked", "note": why, "segments": []}
    use = transcriber or local_server_transcriber
    provider = "injected" if transcriber else "jarvis-local-speech-server"
    segs: list[dict[str, Any]] = []
    try:
        pieces = chunks(wav)
    except (wave.Error, EOFError):
        return {"status": "failed", "note": "the audio could not be read", "segments": []}
    for start, piece in pieces:
        try:
            text = use(piece, lang or "en")
        except Exception as e:  # noqa: BLE001 - server down, model missing: say what to do, never a traceback
            done = len(segs)
            return {"status": "blocked" if not done else "partial", "provider": provider, "language": lang or "unconfirmed",
                    "segments": segs,
                    "note": (f"speech recognition stopped at {_clock(start)} ({type(e).__name__}); start the local speech "
                             "server (neko-start.ps1 on the laptop) or add a transcript file")}
        if str(text).strip():
            segs.append({"time": _clock(start), "text": str(text)})
    note = "" if lang else ("the recording's language was not given, so the English model was used; say whether it is "
                            "English, Telugu or Hindi to transcribe it with the right model")
    return {"status": "done", "provider": provider, "language": lang or "unconfirmed", "segments": segs, "note": note}


def to_segments(source: str, got: dict[str, Any]) -> list[dict[str, Any]]:
    from .ingest import detect_language
    out = []
    for s in got.get("segments") or []:
        raw = " ".join(str(s.get("text") or "").split())
        if not raw:
            continue
        cleaned = safety.clean(raw, 2000)
        out.append({"source": source, "ref": {"time": s.get("time", "")}, "speaker": "", "text": cleaned,
                    "lang": detect_language(cleaned), "to_ai": safety.addressed_to_ai(raw), "audio": True,
                    "language_confirmed": got.get("language") != "unconfirmed"})
    return out
