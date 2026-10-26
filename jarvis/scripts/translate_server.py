"""The local translation service (D58): ONE process on this laptop that holds the IndicTrans2 ONNX model, so the gateway, the console, the speech server and the
plugin host do not each load a copy (about 0.5 GB each, against a 6.5 GB budget for all of JARVIS).

    python translate_server.py [--port 48960]        (Windows, the console's venv or any venv that has onnxruntime, tokenizers, huggingface-hub, numpy, IndicTransToolkit)

  POST /v1/translate  {"text": "...", "src": "te|hi|en", "tgt": "en|te|hi"}   ->  {"text": "...", "model": "indictrans2-onnx", "seconds": 0.4}
  GET  /health                                                                  ->  {"ready": bool, "loaded": [...], "load_seconds": {...}, "requests": n}

Loopback only: the Host header must be 127.0.0.1 or localhost (no DNS rebinding), a body over 16 KB is refused, one translation at a time (the model is shared and
the CPU budget is 3 threads). The model is released after `idle_unload_s` (config language.idle_unload_s, default 120) without a request, so an idle laptop holds none.
NOT RUN HERE: written against the model card; the laptop session installs the model folders (see D58) and measures load seconds, RAM and seconds per sentence.
Nothing in this file opens a connection to anything: it only serves.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

MAX_BODY = 16 * 1024
PORT = 48960


def make_handler(backend):
    lock = threading.Lock()
    stats = {"requests": 0, "errors": 0}

    class Handler(BaseHTTPRequestHandler):
        server_version = "jarvis-translate"

        def log_message(self, *a):
            pass

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "").split(":")[0].strip("[]").lower() in ("127.0.0.1", "localhost", "::1")

        def _json(self, code, obj):
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "loopback only"})
            if self.path.rstrip("/") in ("/health", "/v1/health"):
                models = getattr(backend, "_models", {})
                return self._json(200, {"ready": bool(backend.ready()), "loaded": sorted(models), "load_seconds": getattr(backend, "load_seconds", {}), **stats})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"error": "loopback only"})
            if self.path.rstrip("/") not in ("/v1/translate", "/translate"):
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > MAX_BODY:
                    return self._json(413, {"error": "body missing or too large"})
                req = json.loads(self.rfile.read(n))
                text, src, tgt = str(req["text"]), str(req["src"]), str(req["tgt"])
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "send {\"text\", \"src\", \"tgt\"}"})
            if not backend.supports(src, tgt, False):
                return self._json(422, {"error": f"{src} to {tgt} is not installed here"})
            t0 = time.monotonic()
            try:
                with lock:
                    out = backend.translate(text, src, tgt)
            except Exception as e:                                   # noqa: BLE001 - never a traceback, never the text
                stats["errors"] += 1
                return self._json(500, {"error": "translation failed: " + type(e).__name__})
            stats["requests"] += 1
            return self._json(200, {"text": out, "model": getattr(backend, "name", "backend"), "seconds": round(time.monotonic() - t0, 3)})

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("JARVIS_TRANSLATE_PORT", PORT)))
    a = ap.parse_args()
    from jarvis.plugins._shared import translate as T
    from jarvis.plugins._shared import translate_indictrans2 as I2
    backend = I2.build(T.load_config())
    if backend is None:
        print("The IndicTrans2 model folders are not installed (see D58): nothing to serve.", file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(backend))
    srv.daemon_threads = True
    print(f"jarvis-translate listening on http://127.0.0.1:{a.port}/v1", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
