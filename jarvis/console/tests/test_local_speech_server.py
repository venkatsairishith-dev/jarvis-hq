"""The local OpenAI-compatible speech endpoint: wire format, limits and safety, with a fake synthesizer (no models)."""
import http.client
import importlib.util
import io
import json
import os
import threading
import unittest
import wave
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "..", "scripts", "local_speech_server.py")
spec = importlib.util.spec_from_file_location("local_speech_server", SCRIPT)
lss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lss)


def tone_wav(rate=22050, seconds=0.5):
    import math
    n = int(rate * seconds)
    pcm = b"".join(int(8000 * math.sin(2 * math.pi * 440 * i / rate)).to_bytes(2, "little", signed=True) for i in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return buf.getvalue()


class Server:
    def __init__(self, synth, transcribe=None):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), lss.make_handler(synth, transcribe))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode() if not isinstance(body, bytes) else body
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = (r.status, r.getheader("Content-Type"), r.read())
        c.close()
        return out

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


class SpeechServer(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def synth(text, lang):
            self.calls.append((text, lang))
            if text == "boom":
                raise RuntimeError("engine exploded with secret-path D:\\x")
            if text == "toolong":
                raise ValueError("text is too long to speak (1500 characters)")
            return tone_wav(22050, 0.5)

        self.s = Server(synth)

    def tearDown(self):
        self.s.close()

    def test_pcm_is_24khz_mono_16bit_and_the_right_length(self):
        code, ctype, body = self.s.request("POST", "/v1/audio/speech", {"model": "m", "input": "hello", "voice": "en", "response_format": "pcm"})
        self.assertEqual(code, 200)
        self.assertEqual(ctype, "audio/pcm")
        self.assertEqual(len(body) % 2, 0)
        self.assertAlmostEqual(len(body) / 2 / lss.OUT_RATE, 0.5, delta=0.01)        # 0.5 s from a 22.05 kHz source
        self.assertTrue(any(body))                                                      # not silence

    def test_default_format_is_pcm_and_wav_is_available(self):
        code, ctype, body = self.s.request("POST", "/v1/audio/speech", {"input": "hello", "voice": "x"})
        self.assertEqual((code, ctype), (200, "audio/pcm"))
        code, ctype, body = self.s.request("POST", "/audio/speech", {"input": "hello", "response_format": "wav"})
        self.assertEqual((code, ctype), (200, "audio/wav"))
        self.assertEqual(body[:4], b"RIFF")

    def test_an_explicit_voice_wins_and_otherwise_the_script_of_the_text_decides(self):
        for voice, lang in (("hi", "hi"), ("Telugu", "te"), ("en", "en"), ("marin", "en"), ("", "en"), ("auto", "en")):
            self.s.request("POST", "/v1/audio/speech", {"input": "hello", "voice": voice})
            self.assertEqual(self.calls[-1][1], lang, voice)
        hindi, telugu = "\u0928\u092e\u0938\u094d\u0924\u0947, \u0906\u092a \u0915\u0948\u0938\u0947 \u0939\u0948\u0902?", "\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02, \u0c2e\u0c40\u0c30\u0c41 \u0c0e\u0c32\u0c3e \u0c09\u0c28\u0c4d\u0c28\u0c3e\u0c30\u0c41?"
        for text, voice, lang in ((hindi, "auto", "hi"), (telugu, "auto", "te"), (hindi, "marin", "hi"), (telugu, "", "te"),
                                  (hindi, "en", "en"), ("Meet me at \u0928\u092e\u0938\u094d\u0924\u0947 cafe near the station today", "auto", "en")):
            self.s.request("POST", "/v1/audio/speech", {"input": text, "voice": voice})
            self.assertEqual(self.calls[-1][1], lang, (text[:12], voice))

    def test_bad_requests_are_refused_before_synthesis(self):
        before = len(self.calls)
        self.assertEqual(self.s.request("POST", "/v1/audio/speech", {"input": "  "})[0], 400)
        self.assertEqual(self.s.request("POST", "/v1/audio/speech", b"not json")[0], 400)
        self.assertEqual(self.s.request("POST", "/v1/audio/speech", {"input": "x", "response_format": "mp3"})[0], 400)
        self.assertEqual(self.s.request("POST", "/v1/audio/speech", b"x" * (lss.MAX_BODY + 1))[0], 413)
        self.assertEqual(self.s.request("POST", "/v1/other", {"input": "x"})[0], 404)
        self.assertEqual(len(self.calls), before)

    def test_engine_errors_do_not_leak_details(self):
        code, _, body = self.s.request("POST", "/v1/audio/speech", {"input": "boom"})
        self.assertEqual(code, 500)
        self.assertNotIn(b"secret-path", body)
        code, _, body = self.s.request("POST", "/v1/audio/speech", {"input": "toolong"})
        self.assertEqual(code, 400)
        self.assertIn(b"too long", body)

    def test_only_loopback_host_names_are_served(self):
        code, _, _ = self.s.request("POST", "/v1/audio/speech", {"input": "hi"}, headers={"Host": "evil.example"})
        self.assertEqual(code, 403)
        code, _, _ = self.s.request("GET", "/health", headers={"Host": "evil.example:48930"})
        self.assertEqual(code, 403)
        self.assertEqual(self.s.request("GET", "/health")[0], 200)
        self.assertEqual(self.s.request("GET", "/v1/models")[0], 200)

    def test_resampling_keeps_the_rate_when_it_already_matches(self):
        wav = tone_wav(lss.OUT_RATE, 0.25)
        self.assertEqual(len(lss.wav_to_pcm16(wav)), int(lss.OUT_RATE * 0.25) * 2)


def multipart(fields, file_bytes=None, boundary="----testboundary7"):
    out = b""
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    if file_bytes is not None:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                f'Content-Type: audio/wav\r\n\r\n').encode() + file_bytes + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


class Transcription(unittest.TestCase):
    def setUp(self):
        self.heard = []

        def transcribe(wav, lang):
            self.heard.append((len(wav), lang))
            if len(wav) < 100:
                raise ValueError("not a readable WAV file")
            if wav.startswith(b"BOOM"):
                raise RuntimeError("engine exploded in D:\\secret")
            return f"heard {lang}"

        self.s = Server(lambda text, lang: tone_wav(), transcribe)
        self.wav = tone_wav(16000, 0.5)

    def tearDown(self):
        self.s.close()

    def test_openai_multipart_request_returns_json_text_in_the_chosen_language(self):
        for sent, want in (("hi", "hi"), ("Telugu", "te"), ("en", "en"), ("", "en"), ("zh", "en")):
            body, headers = multipart({"model": "whisper-1", "language": sent}, self.wav)
            code, ctype, out = self.s.request("POST", "/v1/audio/transcriptions", body, headers)
            self.assertEqual((code, ctype), (200, "application/json"), sent)
            self.assertEqual(json.loads(out), {"text": f"heard {want}"}, sent)
        self.assertEqual(self.heard[0][0], len(self.wav))                    # the file part arrives byte for byte

    def test_raw_wav_body_with_query_language_and_text_format(self):
        code, ctype, out = self.s.request("POST", "/v1/audio/transcriptions?language=hi&response_format=text", self.wav,
                                          {"Content-Type": "audio/wav"})
        self.assertEqual((code, out), (200, b"heard hi"))
        self.assertTrue(ctype.startswith("text/plain"))

    def test_bad_requests_and_engine_errors_are_clean(self):
        body, headers = multipart({"language": "en"})                         # no file part
        self.assertEqual(self.s.request("POST", "/v1/audio/transcriptions", body, headers)[0], 400)
        body, headers = multipart({"language": "en"}, b"tiny")                # engine says unreadable
        code, _, out = self.s.request("POST", "/v1/audio/transcriptions", body, headers)
        self.assertEqual(code, 400)
        self.assertIn(b"readable", out)
        body, headers = multipart({"language": "en"}, b"BOOM" + b"x" * 200)
        code, _, out = self.s.request("POST", "/v1/audio/transcriptions", body, headers)
        self.assertEqual(code, 500)
        self.assertNotIn(b"secret", out)
        body, headers = multipart({"response_format": "srt"}, self.wav)
        self.assertEqual(self.s.request("POST", "/v1/audio/transcriptions", body, headers)[0], 400)
        self.assertEqual(self.s.request("POST", "/v1/audio/transcriptions", b"x" * (lss.MAX_AUDIO_BODY + 1),
                                        {"Content-Type": "audio/wav"})[0], 413)

    def test_loopback_only_and_not_available_without_an_engine(self):
        body, headers = multipart({}, self.wav)
        headers["Host"] = "evil.example"
        self.assertEqual(self.s.request("POST", "/v1/audio/transcriptions", body, headers)[0], 403)
        bare = Server(lambda text, lang: tone_wav())
        try:
            body, headers = multipart({}, self.wav)
            self.assertEqual(bare.request("POST", "/v1/audio/transcriptions", body, headers)[0], 501)
        finally:
            bare.close()


if __name__ == "__main__":
    unittest.main()
