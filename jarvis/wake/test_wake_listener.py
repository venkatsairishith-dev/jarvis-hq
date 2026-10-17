"""The Hey Neko loop with fake detector, VAD and services (no microphone, no network). neko-venv:
    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe -m pytest jarvis\\wake\\test_wake_listener.py -q
"""
import asyncio
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wake_listener as wl  # noqa: E402

SPEECH, QUIET = b"\x10\x00" * wl.FRAME, b"\x00\x00" * wl.FRAME


class FakeDetector:
    def __init__(self, hit_at=(5,), fail_at=()):
        self.hit_at, self.fail_at, self.n, self.closed, self.prepared = set(hit_at), set(fail_at), 0, False, 0

    async def prepare(self):
        self.prepared += 1

    async def feed(self, frame, epoch):
        self.n += 1
        if self.n in self.fail_at:
            raise RuntimeError("WAKE_WORD_TIMEOUT")
        if self.n in self.hit_at:
            return SimpleNamespace(sample_end=frame.sample_end - 320, keyword="hey_neko")
        return None

    async def close(self):
        self.closed = True


class FakeVad:
    def speech(self, pcm):
        return any(pcm)


def make(detector=None, text="open notepad", running=False, speaking=False, asr_error=False, dry=False):
    calls = {"submit": [], "stop": 0, "asr": []}

    def asr(audio):
        calls["asr"].append(len(audio))
        if asr_error:
            raise OSError("down")
        return text

    def submit(t):
        calls["submit"].append(t)
        return {"ok": True, "run_id": "r1"}

    def stop():
        calls["stop"] += 1
        return {"ok": True}
    det = detector or FakeDetector()
    w = wl.WakeLoop(lambda: det, vad_factory=FakeVad, transcribe=asr, submit=submit, stop=stop,
                    speaking=lambda: speaking, running=lambda: running, chime=lambda k: None, dry_run=dry)
    return w, calls, det


async def feed(w, frames):
    for f in frames:
        await w.on_frame(f)
        await asyncio.sleep(0)
    while w.worker is not None and not w.worker.done():
        await asyncio.sleep(0.01)


def seconds(s, pcm):
    return [pcm] * int(s * wl.RATE / wl.FRAME)


def test_wake_command_silence_submits_and_returns_to_listening():
    async def go():
        w, calls, _ = make()
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1.5, SPEECH) + seconds(1.2, QUIET))
        assert calls["submit"] == ["open notepad"] and w.state == "wake_listening"
        assert w.counts["detections"] == 1 and w.counts["tasks_started"] == 1 and w.last["run_id"] == "r1"
        assert "text" not in w.last                          # the words are never kept outside a dry run
    asyncio.run(go())


def test_speech_right_after_the_wake_word_is_kept():
    async def go():
        w, calls, _ = make()
        await w.start()
        await feed(w, [QUIET] * 4 + [SPEECH] + seconds(1.0, SPEECH) + seconds(1.2, QUIET))
        # the hit frame ends 320 samples after the detector's keyword end: those samples start the command
        assert calls["asr"] and calls["asr"][0] >= (320 + int(1.0 * wl.RATE / wl.FRAME) * wl.FRAME) * 2
    asyncio.run(go())


def test_no_speech_after_wake_goes_back_without_a_task():
    async def go():
        w, calls, _ = make()
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(5.2, QUIET))
        assert calls["submit"] == [] and calls["asr"] == [] and w.state == "wake_listening" and w.counts["no_speech"] == 1
    asyncio.run(go())


def test_cancel_word_cancels():
    async def go():
        w, calls, _ = make(text="Never mind.")
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
        assert calls["submit"] == [] and w.counts["cancelled"] == 1 and w.last["what"] == "cancelled by voice"
    asyncio.run(go())


def test_stop_while_a_task_runs_stops_it():
    async def go():
        w, calls, _ = make(text="Stop.", running=True)
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
        assert calls["stop"] == 1 and calls["submit"] == [] and w.counts["stops"] == 1
        w2, calls2, _ = make(text="Stop.", running=False)     # nothing running: "stop" just cancels
        await w2.start()
        await feed(w2, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
        assert calls2["stop"] == 0 and w2.counts["cancelled"] == 1
    asyncio.run(go())


def test_long_sentence_starting_with_stop_word_is_a_task_when_nothing_runs():
    async def go():
        w, calls, _ = make(text="stop the music and open notepad")
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
        assert calls["submit"] == ["stop the music and open notepad"]
    asyncio.run(go())


def test_short_commands_starting_with_a_cancel_word_are_tasks_when_nothing_runs():
    async def go():
        for said in ("cancel my meeting", "stop the music", "cancel my dentist appointment tomorrow"):
            w, calls, _ = make(text=said)
            await w.start()
            await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
            assert calls["submit"] == [said] and w.counts["cancelled"] == 0, said
        for said in ("cancel", "Never mind.", "nevermind", "Forget it!", "no thanks", "quiet", "stop it", "cancel that"):
            w, calls, _ = make(text=said)
            await w.start()
            await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
            assert calls["submit"] == [] and w.counts["cancelled"] == 1, said
    asyncio.run(go())


def test_ignored_while_neko_speaks():
    async def go():
        w, calls, _ = make(speaking=True)
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
        assert calls["asr"] == [] and w.counts["ignored_while_speaking"] == 1 and w.state == "wake_listening"
    asyncio.run(go())


def test_cancel_from_the_screen_during_recording():
    async def go():
        w, calls, _ = make()
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(0.5, SPEECH))
        assert w.state == "recording"
        w.request_cancel()
        await feed(w, [SPEECH] + seconds(1.2, QUIET))
        assert calls["submit"] == [] and w.state == "wake_listening" and w.last["what"] == "cancelled from the screen"
    asyncio.run(go())


def test_speech_recognition_failure_is_reported_and_listening_resumes():
    async def go():
        w, calls, _ = make(asr_error=True)
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
        assert calls["submit"] == [] and w.last["what"] == "speech recognition failed" and w.state == "wake_listening"
    asyncio.run(go())


def test_long_command_is_cut_at_the_limit():
    async def go():
        w, calls, _ = make()
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(13, SPEECH))
        assert len(calls["submit"]) == 1 and calls["asr"][0] <= int((wl.WakeLoop.MAX_COMMAND_S + 0.5) * wl.RATE) * 2
    asyncio.run(go())


def test_dry_run_never_submits_but_shows_the_words():
    async def go():
        w, calls, _ = make(dry=True)
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
        assert calls["submit"] == [] and w.last["text"] == "open notepad"
    asyncio.run(go())


def test_detector_failure_restarts_it():
    async def go():
        det = FakeDetector(hit_at=(), fail_at=(3,))
        w, _, _ = make(detector=det)
        orig_sleep = asyncio.sleep
        wl.asyncio.sleep = lambda s: orig_sleep(0)            # the 2 s restart pause, without waiting
        try:
            await w.start()
            await feed(w, [QUIET] * 4)
        finally:
            wl.asyncio.sleep = orig_sleep
        assert det.prepared == 2 and w.state == "wake_listening" and w.counts["errors"] == 1
    asyncio.run(go())


def test_paused_ignores_the_microphone():
    async def go():
        w, calls, det = make()
        await w.start()
        w.paused = True
        await feed(w, [QUIET] * 6 + seconds(1, SPEECH) + seconds(1.2, QUIET))
        assert det.n == 0 and calls["asr"] == [] and w.status()["state"] == "paused"
    asyncio.run(go())


def test_keywords_file(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "k.txt")
        Path(p).write_text("# mine\nHH EY1 N EH1 K OW0 @hey_neko  # neh-ko\n\n", encoding="utf-8")
        monkeypatch.setenv("NEKO_WAKE_WORD_KEYWORDS_FILE", p)
        assert wl.keywords() == ("HH EY1 N EH1 K OW0 @hey_neko",)
    monkeypatch.delenv("NEKO_WAKE_WORD_KEYWORDS_FILE")
    assert wl.keywords() == wl.DEFAULT_KEYWORDS and not any("@" not in k for k in wl.DEFAULT_KEYWORDS)


def test_status_server_loopback_and_origin():
    w, _, _ = make()
    srv = wl.serve_status(w, 0)
    port = srv.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=5) as r:
            st = json.loads(r.read())
        assert st["enabled"] and "counts" in st and st["keywords"] == ["hey_neko"]
        bad = urllib.request.Request(f"http://127.0.0.1:{port}/cancel", data=b"", method="POST", headers={"Origin": "https://evil.example"})
        try:
            urllib.request.urlopen(bad, timeout=5)
            raise AssertionError("a foreign page could cancel")
        except urllib.error.HTTPError as e:
            assert e.code == 403
        ok = urllib.request.Request(f"http://127.0.0.1:{port}/pause", data=b"", method="POST")
        with urllib.request.urlopen(ok, timeout=5) as r:
            assert json.loads(r.read())["state"] == "paused"
    finally:
        srv.shutdown()
        srv.server_close()


def test_resample_44100_to_16000():
    import numpy as np
    x = (np.sin(np.arange(44100) / 44100 * 2 * np.pi * 440) * 8000).astype("<i2").tobytes()
    assert abs(len(wl.resample_int16(x, 44100)) // 2 - 16000) <= 1
