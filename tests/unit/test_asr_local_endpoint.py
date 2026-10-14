"""faster_whisper worker: the opt-in local transcription endpoint (NEKO_WHISPER_ENDPOINT).

No Whisper model is loaded and nothing leaves the machine: a fake server on 127.0.0.1 plays the endpoint.
"""
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from main_logic.asr_client.workers import faster_whisper as fw


class _Endpoint:
    def __init__(self, reply=None, status=200):
        outer = self
        self.requests = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                outer.requests.append((self.headers.get("Content-Type", ""), self.rfile.read(n)))
                data = json.dumps(reply if reply is not None else {"text": "hello from the endpoint"}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/audio/transcriptions"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def endpoint():
    e = _Endpoint()
    yield e
    e.close()


def _tone(seconds=0.5):
    t = np.arange(int(16000 * seconds)) / 16000.0
    return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_audio_goes_out_as_openai_multipart_with_a_real_wav_and_the_language(endpoint):
    model = fw._EndpointModel(endpoint.url)
    segments, _info = model.transcribe(_tone(), language="hi", task="transcribe", beam_size=1)
    assert [s.text for s in segments] == ["hello from the endpoint"]
    ctype, body = endpoint.requests[0]
    boundary = re.search(r"boundary=([^;]+)", ctype).group(1)
    assert ctype.startswith("multipart/form-data")
    assert body.count(("--" + boundary).encode()) == 5                     # model, response_format, language, file, closing marker
    assert b'name="language"\r\n\r\nhi\r\n' in body
    assert b'name="file"; filename="utterance.wav"' in body
    wav = body[body.index(b"RIFF"):]
    assert wav[8:12] == b"WAVE" and len(wav) > 16000                         # 0.5 s of 16 kHz 16-bit audio


def test_pcm_path_returns_the_endpoint_text_through_the_workers_own_function(endpoint):
    model = fw._EndpointModel(endpoint.url)
    pcm = (_tone(1.0) * 32767).astype("<i2").tobytes()
    assert fw._transcribe_pcm16(model, pcm, "en") == "hello from the endpoint"


def test_empty_text_is_silence(endpoint):
    e = _Endpoint(reply={"text": ""})
    try:
        model = fw._EndpointModel(e.url)
        pcm = (_tone(0.5) * 32767).astype("<i2").tobytes()
        assert fw._transcribe_pcm16(model, pcm, None) == ""
    finally:
        e.close()


def test_only_local_http_urls_are_accepted():
    for bad in ("https://api.example.com/v1/audio/transcriptions", "http://192.168.1.5:48930/v1/audio/transcriptions",
                "http://evil.example/v1/audio/transcriptions", "file:///etc/passwd"):
        with pytest.raises(fw._LocalAsrFailure) as err:
            fw._EndpointModel(bad)
        assert err.value.code == "ASR_LOCAL_ENDPOINT_NOT_LOCAL"
    fw._EndpointModel("http://localhost:48930/v1/audio/transcriptions")


def test_an_endpoint_that_is_down_is_a_clean_failure_not_a_crash():
    model = fw._EndpointModel("http://127.0.0.1:9/v1/audio/transcriptions")
    with pytest.raises(fw._LocalAsrFailure) as err:
        model.transcribe(_tone(), language="en")
    assert err.value.code == "ASR_LOCAL_ENDPOINT_FAILED"


def test_loader_uses_the_endpoint_when_the_env_is_set_and_never_imports_whisper(monkeypatch, endpoint):
    monkeypatch.setenv(fw._ENDPOINT_ENV, endpoint.url)
    monkeypatch.setattr(fw, "_import_faster_whisper", lambda: pytest.fail("whisper must not be imported"))
    model = fw._load_whisper_model(fw._ModelSpec(model=None, device="auto", compute_type=None))
    assert isinstance(model, fw._EndpointModel)


def test_without_the_env_the_loader_still_goes_for_whisper(monkeypatch):
    monkeypatch.delenv(fw._ENDPOINT_ENV, raising=False)
    seen = []

    def fake_import():
        seen.append(True)
        raise fw._LocalAsrFailure("ASR_LOCAL_DEPENDENCY_MISSING", "stop here")

    monkeypatch.setattr(fw, "_import_faster_whisper", fake_import)
    with pytest.raises(fw._LocalAsrFailure):
        fw._load_whisper_model(fw._ModelSpec(model=None, device="cpu", compute_type=None))
    assert seen == [True]
