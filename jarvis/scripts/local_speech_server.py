"""A local OpenAI-compatible speech endpoint backed by JARVIS's own voice models.

N.E.K.O's "custom" text-to-speech provider speaks the OpenAI speech API. JARVIS already holds, and has tested, local models
(Kokoro for English; Piper voices for Hindi and Telugu) behind `jarvis_console.voice.Voice`. This is only the wire format
between the two: no engine code, no model download, no cloud.

  POST /v1/audio/speech   {"model": "...", "input": "...", "voice": "en|hi|te", "response_format": "pcm|wav"}
                          -> raw PCM16 mono at 24 kHz (what N.E.K.O expects) or a WAV file
  GET  /v1/models, GET /health

Loopback only (Host header must be 127.0.0.1 or localhost, so a web page cannot reach it by DNS rebinding), no credentials,
at most 1,500 characters per request, one synthesis at a time.

Run with the JARVIS console's Python:  D:\\jarvis\\tools\\jarvis-venv\\Scripts\\python.exe local_speech_server.py [--port 48930]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "console"))

OUT_RATE = 24000                 # the PCM rate N.E.K.O's OpenAI-compatible TTS worker plays
MAX_BODY = 64 * 1024
LANG_ALIASES = {"en": "en", "english": "en", "hi": "hi", "hindi": "hi", "te": "te", "telugu": "te"}


def pick_language(voice: str) -> str:
    return LANG_ALIASES.get(str(voice or "").strip().lower(), "en")


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


def make_handler(synthesize):
    """`synthesize(text, lang) -> wav_bytes`; raises ValueError for unusable input (message is safe to return)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "jarvis-local-speech"

        def log_message(self, *a):
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower().rsplit(":", 1)[0].strip("[]")
            return host in ("127.0.0.1", "localhost", "::1")

        def _send(self, code, body=b"", ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode("utf-8"))

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": {"message": "loopback only"}})
            if self.path in ("/health", "/"):
                return self._json(200, {"status": "ok", "service": "jarvis-local-speech", "languages": ["en", "hi", "te"]})
            if self.path in ("/v1/models", "/models"):
                return self._json(200, {"object": "list", "data": [{"id": "jarvis-local", "object": "model", "owned_by": "local"}]})
            return self._json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"error": {"message": "loopback only"}})
            if self.path.rstrip("/") not in ("/v1/audio/speech", "/audio/speech"):
                return self._json(404, {"error": {"message": "not found"}})
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if n <= 0 or n > MAX_BODY:
                return self._json(413 if n > MAX_BODY else 400, {"error": {"message": "request body missing or too large"}})
            try:
                req = json.loads(self.rfile.read(n).decode("utf-8"))
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
            try:
                wav = synthesize(text, pick_language(req.get("voice")))
                if fmt == "wav":
                    return self._send(200, wav, "audio/wav")
                return self._send(200, wav_to_pcm16(wav), "audio/pcm")
            except ValueError as exc:
                return self._json(400, {"error": {"message": str(exc)[:200]}})
            except Exception as exc:                       # never a traceback to the caller
                return self._json(500, {"error": {"message": "synthesis failed: " + type(exc).__name__}})

    return Handler


def build_synthesizer():
    from jarvis_console.voice import Voice, VoiceError

    voice = Voice()

    def synthesize(text: str, lang: str) -> bytes:
        try:
            wav, _info = voice.speak(text, lang=lang)
            return wav
        except VoiceError as exc:                          # includes "too long" and "voice not installed"
            raise ValueError(str(exc))

    return synthesize


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=48930)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(build_synthesizer()))
    print(f"jarvis-local-speech listening on http://127.0.0.1:{args.port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
