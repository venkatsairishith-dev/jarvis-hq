"""A local OpenAI-compatible speech endpoint backed by JARVIS's own voice models.

N.E.K.O's "custom" text-to-speech provider speaks the OpenAI speech API. JARVIS already holds, and has tested, local models
(Kokoro for English; Piper voices for Hindi and Telugu) behind `jarvis_console.voice.Voice`. This is only the wire format
between the two: no engine code, no model download, no cloud.

  POST /v1/audio/speech   {"model": "...", "input": "...", "voice": "en|hi|te", "response_format": "pcm|wav"}
                          -> raw PCM16 mono at 24 kHz (what N.E.K.O expects) or a WAV file
  POST /v1/audio/transcriptions   multipart (file, language) or a raw audio/wav body -> {"text": "..."}
                          int8 speech recognition: Parakeet for English, IndicConformer for Hindi and Telugu
  POST /interrupt         cancel the synthesis requests in flight (their audio is dropped and they answer 409) and clear the
                          "speaking" estimate; answers what it did: {"ok", "cancelled", "playback_stopped": false, ...}
  GET  /v1/models, GET /health, GET /activity

Loopback only (Host header must be 127.0.0.1 or localhost, so a web page cannot reach it by DNS rebinding), no credentials for
the compute routes (speech, transcription: they change no state beyond the "speaking" estimate), at most 1,500 characters per
synthesis request and 12 MB of audio per recognition request, one job at a time.

Route classes (re-review M3, 2026-10-19): public-harmless: GET /health, /v1/models, /activity (timestamps and a speaking estimate, no
text, no audio). Compute routes, left open on purpose: POST /v1/audio/speech and /v1/audio/transcriptions take text or audio from the
caller and return the result; they keep no history and change nothing but the speaking estimate, and N.E.K.O's own TTS/ASR clients
(not ours to change) call them without a credential. The cost of that is free local compute for any loopback client, not data.
POST /interrupt is the one route that changes what is happening (it drops speech in progress), so (audit M15) it needs this
launch's control secret in `X-Jarvis-Control`, an exact loopback Host on this port and no foreign Origin. main() publishes the
secret to <run dir>/speech.token (jarvis/scripts/control_auth.py) for the wake listener. A process of the same OS user can still
read that file; the worst a forged /interrupt does is cut off speech, never start an action.

Run with the JARVIS console's Python:  D:\\jarvis\\tools\\jarvis-venv\\Scripts\\python.exe local_speech_server.py [--port 48930]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "console"))
sys.path.insert(0, HERE)
import control_auth   # noqa: E402  per-launch secret for the one state-changing route (audit M15)

OUT_RATE = 24000                 # the PCM rate N.E.K.O's OpenAI-compatible TTS worker plays
MAX_BODY = 64 * 1024
MAX_AUDIO_BODY = 12 * 1024 * 1024
LANG_ALIASES = {"en": "en", "english": "en", "hi": "hi", "hindi": "hi", "te": "te", "telugu": "te"}


def detect_language(text: str) -> str:
    """Hindi for Devanagari, Telugu for Telugu script, otherwise English: the majority script of the letters wins."""
    hi = te = other = 0
    for ch in str(text or ""):
        o = ord(ch)
        if 0x0900 <= o <= 0x097F:
            hi += 1
        elif 0x0C00 <= o <= 0x0C7F:
            te += 1
        elif ch.isalpha():
            other += 1
    if max(hi, te) == 0 or max(hi, te) < other / 4:          # a few native letters inside English text stay English
        return "en"
    return "hi" if hi >= te else "te"


def pick_language(voice: str, text: str = "") -> str:
    """An explicit voice (en, hi, te or the language name) wins; anything else, including "auto" or an OpenAI voice name,
    follows the script of the text."""
    return LANG_ALIASES.get(str(voice or "").strip().lower()) or detect_language(text)


def wav_to_pcm16(wav_bytes: bytes, out_rate: int = OUT_RATE) -> bytes:
    """16-bit mono WAV -> raw little-endian PCM16 at `out_rate` (linear resampling when the rates differ)."""
    import numpy as np

    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, width, channels, frames = w.getframerate(), w.getsampwidth(), w.getnchannels(), w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("expected 16-bit audio")
    samples = np.frombuffer(frames, dtype="<i2").astype("float32")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != out_rate and len(samples):
        n_out = max(1, int(round(len(samples) * out_rate / rate)))
        samples = np.interp(np.linspace(0, len(samples) - 1, n_out), np.arange(len(samples)), samples)
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


def parse_multipart(body: bytes, content_type: str) -> dict:
    """The named parts of a multipart/form-data body, as bytes (enough for the OpenAI transcription request)."""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        raise ValueError("multipart body without a boundary")
    boundary = b"--" + m.group(1).encode()
    parts = {}
    for chunk in body.split(boundary)[1:]:
        if chunk.startswith(b"--"):
            break
        head, _, data = chunk.lstrip(b"\r\n").partition(b"\r\n\r\n")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        name = re.search(rb'name="([^"]*)"', head)
        if name:
            parts[name.group(1).decode("utf-8", "replace")] = data
    return parts


_ACTIVITY = {"tts_at": 0.0, "tts_audio_s": 0.0, "asr_at": 0.0}   # for the Neko icon: what speech work happened when


def _wav_seconds(wav: bytes) -> float:
    try:
        import io
        import wave
        with wave.open(io.BytesIO(wav)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:                                      # noqa: BLE001 - only an estimate for the icon
        return 0.0


# Synthesis requests in flight, and a counter that every /interrupt bumps. A request that started before the bump has its
# audio dropped when the engine returns: the engine call itself cannot be aborted, but nothing from it reaches the page.
_SYNTH = {"epoch": 0, "inflight": 0}
_SYNTH_LOCK = threading.Lock()


def interrupt() -> dict:
    """Cancel in-flight synthesis and clear the speaking estimate. Truthful by construction: `cancelled` counts requests
    whose audio was dropped; `playback_stopped` is always false because audio the page already received is played by the page
    (N.E.K.O has no HTTP route to stop it), which this server cannot reach. UNVERIFIED on real hardware."""
    with _SYNTH_LOCK:
        _SYNTH["epoch"] += 1
        cancelled = _SYNTH["inflight"]
        was_speaking = activity()["speaking_estimate"]
        _ACTIVITY.update(tts_at=0.0, tts_audio_s=0.0)
    return {"ok": True, "cancelled": cancelled, "speaking_estimate_cleared": was_speaking, "playback_stopped": False,
            "note": "synthesis in flight was cancelled; audio the page already received keeps playing"}


def activity(now: float | None = None) -> dict:
    """Seconds since the last synthesis and transcription, and how long the last synthesized audio lasts. The page plays
    audio right after synthesis, so 'speaking' is estimated as: synthesized less than its own length ago (+1 s)."""
    import time as _t
    now = now or _t.time()
    a = dict(_ACTIVITY)
    tts_age = now - a["tts_at"] if a["tts_at"] else None
    asr_age = now - a["asr_at"] if a["asr_at"] else None
    return {"tts_age_s": tts_age, "tts_audio_s": a["tts_audio_s"], "asr_age_s": asr_age,
            "speaking_estimate": bool(tts_age is not None and tts_age <= a["tts_audio_s"] + 1.0)}


def make_handler(synthesize, transcribe=None, secret=None, readiness=None):
    """`synthesize(text, lang) -> wav_bytes` and `transcribe(wav_bytes, lang) -> text`; both raise ValueError for unusable
    input (the message is safe to return). Without `transcribe` the recognition endpoint answers 501. `secret`: the control
    secret for POST /interrupt (a fresh random one when not given; read it back from `Handler.control_secret`)."""
    control_secret = secret or control_auth.new_secret()

    class Handler(BaseHTTPRequestHandler):
        server_version = "jarvis-local-speech"
        _unread = 0                      # bytes of the request body the handler has not consumed yet

        def log_message(self, *a):
            pass

        def _drain(self):
            """Read and drop an unread request body (bounded), so a refusal is not cut off by a connection reset."""
            left = min(self._unread, 2 * MAX_AUDIO_BODY)
            self._unread = 0
            try:
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
            except OSError:
                pass

        def _host_ok(self) -> bool:
            host = (control_auth.host_value(self.headers) or "").lower().rsplit(":", 1)[0].strip("[]")      # none/duplicate Host: refused
            return host in ("127.0.0.1", "localhost", "::1")

        def _send(self, code, body=b"", ctype="application/json"):
            if self._unread:
                self._drain()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _origin_ok(self) -> bool:
            """A browser page on another site names itself in Origin; scripts and the wake listener send none."""
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            host = (urlsplit(origin).hostname or "").lower()
            return host in ("127.0.0.1", "localhost", "::1")

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode("utf-8"))

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": {"message": "loopback only"}})
            if self.path in ("/health", "/"):
                # liveness AND honesty (completion H): "ok" only when recognition and synthesis can really run; otherwise "degraded" with
                # the names of what is missing (model/engine names only: no paths, no private data)
                try:
                    info = (readiness or getattr(transcribe, "readiness", None) or (lambda: {"problems": [] if transcribe else ["transcription_unavailable"]}))()
                    problems = [str(x)[:60] for x in (info.get("problems") or [])][:12]
                except Exception:
                    problems = ["readiness_unknown"]
                return self._json(200, {"status": "degraded" if problems else "ok", "ready": not problems, "problems": problems,
                                        "service": "jarvis-local-speech", "languages": ["en", "hi", "te"]})
            if self.path == "/activity":
                return self._json(200, activity())
            if self.path in ("/v1/models", "/models"):
                return self._json(200, {"object": "list", "data": [{"id": "jarvis-local", "object": "model", "owned_by": "local"}]})
            return self._json(404, {"error": {"message": "not found"}})

        def _transcribe(self):
            if transcribe is None:
                return self._json(501, {"error": {"message": "speech recognition is not available"}})
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if n <= 0 or n > MAX_AUDIO_BODY:
                return self._json(413 if n > MAX_AUDIO_BODY else 400, {"error": {"message": "audio missing or too large"}})
            body = self.rfile.read(n)
            self._unread = 0
            ctype = self.headers.get("Content-Type", "")
            try:
                if ctype.lower().startswith("multipart/"):
                    parts = parse_multipart(body, ctype)
                    wav = parts.get("file") or b""
                    language = parts.get("language", b"").decode("utf-8", "replace")
                    fmt = parts.get("response_format", b"json").decode("utf-8", "replace").lower() or "json"
                else:                                                   # a raw WAV body, language in the query string
                    query = parse_qs(urlsplit(self.path).query)
                    wav, language, fmt = body, (query.get("language") or [""])[0], (query.get("response_format") or ["json"])[0]
                if not wav:
                    return self._json(400, {"error": {"message": "no audio file in the request"}})
                if fmt not in ("json", "text"):
                    return self._json(400, {"error": {"message": "response_format must be json or text"}})
                text = transcribe(wav, LANG_ALIASES.get(language.strip().lower(), "en"))
                import time as _t
                _ACTIVITY["asr_at"] = _t.time()
                if fmt == "text":
                    return self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
                return self._json(200, {"text": text})
            except ValueError as exc:
                return self._json(400, {"error": {"message": str(exc)[:200]}})
            except Exception as exc:                                    # never a traceback to the caller
                return self._json(500, {"error": {"message": "recognition failed: " + type(exc).__name__}})

        def do_POST(self):
            try:
                self._unread = max(int(self.headers.get("Content-Length", "0")), 0)
            except ValueError:
                self._unread = 0
            if not self._host_ok():
                return self._json(403, {"error": {"message": "loopback only"}})
            route = urlsplit(self.path).path.rstrip("/")
            if route == "/interrupt":
                if not (self._origin_ok() and control_auth.host_exact(control_auth.host_value(self.headers), self.server.server_address[1])
                        and control_auth.secret_ok(self.headers.get(control_auth.HEADER), control_secret)):
                    return self._json(403, {"error": {"message": "refused: control secret, loopback Host and own origin required"}})
                return self._json(200, interrupt())
            if route in ("/v1/audio/transcriptions", "/audio/transcriptions"):
                return self._transcribe()
            if route not in ("/v1/audio/speech", "/audio/speech"):
                return self._json(404, {"error": {"message": "not found"}})
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if n <= 0 or n > MAX_BODY:
                return self._json(413 if n > MAX_BODY else 400, {"error": {"message": "request body missing or too large"}})
            try:
                raw = self.rfile.read(n)
                self._unread = 0
                req = json.loads(raw.decode("utf-8"))
                if not isinstance(req, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"error": {"message": "body must be a JSON object"}})
            text = str(req.get("input") or "").strip()
            if not text:
                return self._json(400, {"error": {"message": "input is empty"}})
            fmt = str(req.get("response_format") or "pcm").lower()
            if fmt not in ("pcm", "wav"):
                return self._json(400, {"error": {"message": "response_format must be pcm or wav"}})
            with _SYNTH_LOCK:
                epoch = _SYNTH["epoch"]
                _SYNTH["inflight"] += 1
            try:
                try:
                    wav = synthesize(text, pick_language(req.get("voice"), text))
                except BaseException:
                    with _SYNTH_LOCK:
                        _SYNTH["inflight"] -= 1
                    raise
                import time as _t
                with _SYNTH_LOCK:
                    _SYNTH["inflight"] -= 1
                    interrupted = _SYNTH["epoch"] != epoch     # /interrupt arrived while the engine ran: drop the audio
                    if not interrupted:
                        _ACTIVITY.update(tts_at=_t.time(), tts_audio_s=_wav_seconds(wav))
                if interrupted:
                    return self._json(409, {"error": {"message": "interrupted"}, "interrupted": True})
                if fmt == "wav":
                    return self._send(200, wav, "audio/wav")
                return self._send(200, wav_to_pcm16(wav), "audio/pcm")
            except ValueError as exc:
                return self._json(400, {"error": {"message": str(exc)[:200]}})
            except Exception as exc:                       # never a traceback to the caller
                return self._json(500, {"error": {"message": "synthesis failed: " + type(exc).__name__}})

    Handler.control_secret = control_secret          # for main() and tests; never served by a route
    return Handler


def build_engines():
    from jarvis_console.voice import Voice, VoiceError

    voice = Voice()

    def synthesize(text: str, lang: str) -> bytes:
        try:
            wav, _info = voice.speak(text, lang=lang)
            return wav
        except VoiceError as exc:                          # includes "too long" and "voice not installed"
            raise ValueError(str(exc))

    def transcribe(wav: bytes, lang: str) -> str:
        try:
            return str(voice.transcribe(wav, lang=lang).get("text") or "")
        except VoiceError as exc:                          # includes "not a readable WAV file" and "too long"
            raise ValueError(str(exc))

    def readiness() -> dict:
        """What /health reports: the engine, each language's recognizer and voice, and the voice-activity model must all be present."""
        st = voice.status()
        problems = [] if st.get("engine") else ["engine_missing"]
        problems += [f"stt_{lang}_missing" for lang, v in sorted((st.get("stt") or {}).items()) if not v.get("available")]
        problems += [f"tts_{lang}_missing" for lang, v in sorted((st.get("tts") or {}).items()) if not v.get("available")]
        problems += [] if st.get("vad") else ["vad_missing"]
        return {"problems": problems}

    transcribe.readiness = readiness
    return synthesize, transcribe


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=48930)
    args = ap.parse_args()
    synthesize, transcribe = build_engines()
    handler = make_handler(synthesize, transcribe)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    try:
        control_auth.publish("speech", handler.control_secret)       # for the wake listener's POST /interrupt
    except OSError as e:
        print(f"warning: could not publish the control token ({type(e).__name__}); POST /interrupt will be refused", flush=True)
    print(f"jarvis-local-speech listening on http://127.0.0.1:{args.port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        control_auth.unpublish("speech")
    return 0


if __name__ == "__main__":
    sys.exit(main())
