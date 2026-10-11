"""A resident Piper process (runs in the separate piper venv: its runtime is GPL-3.0, so it is never imported by the console).

Protocol on stdin/stdout, one request at a time: a JSON line {"model": "...onnx", "text": "..."} in; a JSON line
{"ok": true, "bytes": N} followed by exactly N bytes of WAV out (or {"ok": false, "error": "..."}). Voices stay loaded between
requests, which removes the 5 to 8 s that a fresh process per sentence cost.
"""
import io
import json
import sys
import wave


def main():
    from piper import PiperVoice
    voices = {}
    out = sys.stdout.buffer
    out.write(b'{"ready": true}\n')
    out.flush()
    for line in sys.stdin.buffer:
        try:
            req = json.loads(line)
            model = req["model"]
            if model not in voices:
                if len(voices) >= 2:                      # at most two voices resident
                    voices.pop(next(iter(voices)))
                voices[model] = PiperVoice.load(model)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                voices[model].synthesize_wav(str(req["text"]), w)
            data = buf.getvalue()
            out.write(json.dumps({"ok": True, "bytes": len(data)}).encode() + b"\n" + data)
        except Exception as e:                            # a bad request must not kill the worker
            out.write(json.dumps({"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}).encode() + b"\n")
        out.flush()


if __name__ == "__main__":
    main()
