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


def test_only_a_pure_stop_phrase_stops_a_running_task_not_any_sentence_that_starts_with_a_stop_word():
    async def go():
        for said in ("stop", "Stop.", "stop it", "stop that", "cancel the task", "please stop now", "abort", "halt everything"):
            w, calls, _ = make(text=said, running=True)
            await w.start()
            await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
            assert calls["stop"] == 1 and w.counts["stops"] == 1, said
        for said in ("cancel my meeting", "stop the music", "Hey Neko cancel my meeting tomorrow", "stop talking to Rahul about the car",
                     "abort the mission to buy milk"):
            w, calls, _ = make(text=said, running=True)
            await w.start()
            await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
            assert calls["stop"] == 0 and w.counts["stops"] == 0, f"{said!r} must not stop a running task"
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
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/status", headers={wl.control_auth.HEADER: srv.control_secret}), timeout=5) as r:
            st = json.loads(r.read())
        assert st["enabled"] and "counts" in st and st["keywords"] == ["hey_neko"]
        bad = urllib.request.Request(f"http://127.0.0.1:{port}/cancel", data=b"", method="POST", headers={"Origin": "https://evil.example"})
        try:
            urllib.request.urlopen(bad, timeout=5)
            raise AssertionError("a foreign page could cancel")
        except urllib.error.HTTPError as e:
            assert e.code == 403
        ok = urllib.request.Request(f"http://127.0.0.1:{port}/pause", data=b"", method="POST", headers={wl.control_auth.HEADER: srv.control_secret})
        with urllib.request.urlopen(ok, timeout=5) as r:
            assert json.loads(r.read())["state"] == "paused"
    finally:
        srv.shutdown()
        srv.server_close()


def test_resample_44100_to_16000():
    import numpy as np
    x = (np.sin(np.arange(44100) / 44100 * 2 * np.pi * 440) * 8000).astype("<i2").tobytes()
    assert abs(len(wl.resample_int16(x, 44100)) // 2 - 16000) <= 1


def test_cancel_from_the_screen_while_the_command_is_being_sent_stops_the_task_it_started():
    """VOI-D8: the request was already on its way and cannot be called back; the old loop said "cancelled" and the task ran."""
    import threading
    import time as _t

    async def go():
        sending, sent, stops = threading.Event(), [], []

        def slow_submit(t):
            sending.set()
            _t.sleep(0.3)
            sent.append(t)
            return {"ok": True, "run_id": "r1", "state": "planning"}

        def stop():
            stops.append(1)
            return {"ok": True, "stopped": True}
        w = wl.WakeLoop(lambda: FakeDetector(), vad_factory=FakeVad, transcribe=lambda a: "open notepad", submit=slow_submit,
                        stop=stop, speaking=lambda: False, running=lambda: False, chime=lambda k: None)
        await w.start()
        for f in [QUIET] * 5 + seconds(1.5, SPEECH) + seconds(1.2, QUIET):
            await w.on_frame(f)
            await asyncio.sleep(0)
        while not sending.is_set():
            await asyncio.sleep(0.01)
        assert w.state == "submitting"
        w.request_cancel()
        await w.on_frame(QUIET)
        assert w.state == "wake_listening" and w.counts["cancelled"] == 1
        while w.background is not None and not w.background.done():
            await asyncio.sleep(0.01)
        assert sent == ["open notepad"] and stops == [1]
        assert w.last["what"] == "cancelled from the screen" and w.last["stopped"] is True
        assert w.counts["tasks_started"] == 0 and w.counts["tasks_planning"] == 0
    asyncio.run(go())


def test_cancel_before_anything_was_sent_sends_nothing():
    async def go():
        w, calls, _ = make()

        def asr(audio):
            w.request_cancel()                          # the screen's cancel lands while the words are recognised
            return "open notepad"
        w.transcribe = asr
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1.5, SPEECH) + seconds(1.2, QUIET))
        assert calls["submit"] == [] and calls["stop"] == 0 and w.last["what"] == "cancelled from the screen"
    asyncio.run(go())


def test_a_task_still_being_planned_is_not_reported_as_started():
    """VOI-D4: the widget answers "planning" when the plan is not made yet; the outcome must not say "task started"."""
    async def go():
        w, calls, _ = make()
        w.submit = lambda t: {"ok": True, "run_id": "r1", "state": "planning", "note": "planning"}
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1.5, SPEECH) + seconds(1.2, QUIET))
        assert w.last["what"] == "task accepted, still planning" and w.counts["tasks_started"] == 0 and w.counts["tasks_planning"] == 1
        w, calls, _ = make()                            # a new loop: the fake detector wakes once
        w.submit = lambda t: {"ok": False, "state": "cancelled", "error": "Stopped as you asked"}
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1.5, SPEECH) + seconds(1.2, QUIET))
        assert w.last["what"] == "task stopped before it started" and w.counts["errors"] == 0
    asyncio.run(go())


def test_stop_reports_what_the_widget_really_answered():
    async def go():
        for answer, ok, note in (({"ok": True, "stopped": True, "message": "Stopped while it was still being planned"}, True, "planned"),
                                 ({"ok": False, "stopped": False, "error": "nothing to stop", "message": "Nothing is running."}, False,
                                  "nothing to stop")):
            w, calls, _ = make(text="Stop.", running=True)
            w.stop = lambda a=answer: a
            await w.start()
            await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
            assert w.last["what"] == "stop the running task" and w.last["ok"] is ok and note in w.last["note"]
    asyncio.run(go())


class Mic:
    """A microphone that delivers `frames` frames and then fails like an unplugged device (or ends, with fail=None)."""
    def __init__(self, frames, fail=OSError("PortAudio error: device unavailable")):
        self.n, self.fail, self.closed = frames, fail, False

    async def frames(self):
        for _ in range(self.n):
            yield QUIET
        if self.fail is not None:
            raise self.fail

    def close(self):
        self.closed = True


def test_a_microphone_error_is_reported_and_retried_instead_of_ending_the_listener():
    """VOI-D5 / PRO-D6: a mic error raised out of run() and the process exited; /status never said why."""
    async def go():
        w, _, det = make(detector=FakeDetector(hit_at=()))
        first = Mic(3)
        opened, waits, seen = [], [], []

        def reopen():
            if len(opened) == 0:
                opened.append("missing")
                raise OSError("no input device")           # still unplugged on the first retry
            m = Mic(5, fail=None)                          # plugged back in: audio flows again
            opened.append(m)
            return m

        async def pause(s):
            waits.append(s)
            seen.append(dict(w.status()))                  # what the icon sees while it waits

        await wl.run(w, first, reopen=reopen, pause=pause)
        assert first.closed and waits == [2.0, 4.0]          # backs off between tries
        assert all(not st["healthy"] and st["state"] == "error" and "microphone error" in st["detail"] for st in seen)
        assert "OSError" in seen[0]["detail"] and "retrying in 2 s" in seen[0]["detail"]
        assert w.counts["mic_errors"] == 2 and w.counts["errors"] == 2 and w.mic_problem is None
        assert det.n == 3 + 5                                # the detector heard the mic before and after the error
        assert det.closed                                    # the run ended normally (the last source ended)
    asyncio.run(go())


def test_listening_resumes_when_the_microphone_comes_back():
    async def go():
        w, _, det = make(detector=FakeDetector(hit_at=()))
        await w.start()
        w.mic_failed("OSError: device unavailable", 2.0)
        assert w.status()["state"] == "error" and not w.status()["healthy"]
        w.mic_ok()
        assert w.state == "wake_listening" and w.status()["healthy"] and w.mic_problem is None
    asyncio.run(go())


def test_a_microphone_that_goes_silent_is_noticed(monkeypatch):
    """PortAudio may simply stop calling back when the device disappears: no frames at all for MIC_STALL_S is an error."""
    monkeypatch.setattr(wl, "MIC_STALL_S", 0.05)
    src = wl.MicSource.__new__(wl.MicSource)           # no sounddevice here: only the frame reader is exercised
    src.q, src.buf, src.rate = asyncio.Queue(), b"", wl.RATE

    async def go():
        agen = src.frames()
        try:
            await agen.__anext__()
        except wl.MicError as e:
            return str(e)
    assert "no audio" in asyncio.run(go())


def test_without_reopen_a_source_error_still_ends_the_run():
    async def go():
        w, _, _ = make(detector=FakeDetector(hit_at=()))
        try:
            await wl.run(w, Mic(2))
        except OSError:
            return True
        return False
    assert asyncio.run(go())


def test_a_microphone_gone_for_a_long_time_keeps_retrying_at_the_longest_pause():
    async def go():
        w, _, _ = make(detector=FakeDetector(hit_at=()))
        tries, waits = [0], []

        def reopen():
            tries[0] += 1
            if tries[0] < 1500:                             # far more failures than 2.0 ** n can count
                raise OSError("no input device")
            return Mic(1, fail=None)

        async def pause(s):
            waits.append(s)
        await wl.run(w, None, reopen=reopen, pause=pause)
        assert len(waits) == 1499 and max(waits) == wl.MIC_RETRY_MAX_S and waits[:3] == [2.0, 4.0, 8.0]
    asyncio.run(go())


# ---------------------------------------------------------------- VOI-D9: command language
def make_lang(lang="en", text="open notepad", **kw):
    seen = []

    def asr(audio, lang="en"):
        seen.append(lang)
        return text
    det = FakeDetector()
    w = wl.WakeLoop(lambda: det, vad_factory=FakeVad, transcribe=asr, submit=lambda t: {"ok": True, "run_id": "r1"},
                    stop=lambda: {"ok": True}, speaking=lambda: False, running=lambda: False, chime=lambda k: None,
                    lang=lang, **kw)
    return w, seen


def test_the_selected_language_reaches_the_speech_server():
    """VOI-D9: _handle always called transcribe(audio) and so always asked for English."""
    async def go():
        for lang in ("en", "hi", "te"):
            w, seen = make_lang(lang)
            await w.start()
            await feed(w, [QUIET] * 5 + seconds(1.5, SPEECH) + seconds(1.2, QUIET))
            assert seen == [lang], lang
            assert w.status()["lang"] == lang
    asyncio.run(go())


def test_the_real_transcribe_sends_the_language_in_the_url(monkeypatch):
    urls = []

    def fake_http(url, body=None, timeout=5.0, raw=None, ctype=None):
        urls.append(url)
        return {"text": "namaste"}
    monkeypatch.setattr(wl, "http_json", fake_http)

    async def go():
        det = FakeDetector()
        w = wl.WakeLoop(lambda: det, vad_factory=FakeVad, submit=lambda t: {"ok": True}, stop=lambda: {"ok": True},
                        speaking=lambda: False, running=lambda: False, chime=lambda k: None, lang="te")
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1.5, SPEECH) + seconds(1.2, QUIET))
    asyncio.run(go())
    assert urls and urls[0].endswith("/v1/audio/transcriptions?language=te")


def test_a_transcribe_function_without_a_lang_parameter_still_works():
    async def go():
        w, calls, _ = make()                                  # the older fakes take only the audio
        w.lang = "hi"
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(1.5, SPEECH) + seconds(1.2, QUIET))
        assert calls["submit"] == ["open notepad"]
    asyncio.run(go())


def test_set_lang_validates_and_keeps_the_choice(tmp_path):
    w, _ = make_lang()
    w.lang_path = tmp_path / "neko" / "wake_lang.txt"
    assert w.set_lang(" HI ") == "hi" and w.lang == "hi" and w.lang_path.read_text().strip() == "hi"
    assert w.set_lang("fr") is None and w.set_lang(None) is None and w.set_lang("") is None and w.lang == "hi"
    assert wl.default_lang(w.lang_path) == "hi"


def test_default_language_order(tmp_path, monkeypatch):
    missing = tmp_path / "none.txt"
    monkeypatch.delenv("NEKO_WAKE_LANG", raising=False)
    assert wl.default_lang(missing) == "en"
    monkeypatch.setenv("NEKO_WAKE_LANG", "te")
    assert wl.default_lang(missing) == "te"
    saved = tmp_path / "saved.txt"
    saved.write_text("hi\n", encoding="utf-8")
    assert wl.default_lang(saved) == "hi"                     # the console's last choice beats the environment
    saved.write_text("klingon", encoding="utf-8")
    assert wl.default_lang(saved) == "te"                     # an unusable file is ignored
    monkeypatch.setenv("NEKO_WAKE_LANG", "xx")
    assert wl.default_lang(missing) == "en"


def _post(port, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_post_lang_switches_the_language_with_the_same_guard_as_pause():
    """(Audit M15: the guard is the launch's control secret + exact Host + allow-listed Origin.)"""
    w, seen = make_lang()
    srv = wl.serve_status(w, 0)
    port = srv.server_address[1]
    key = {wl.control_auth.HEADER: srv.control_secret}
    try:
        assert _post(port, "/lang", {"lang": "hi"}, key) == (200, {"ok": True, "lang": "hi"}) and w.lang == "hi"
        assert _post(port, "/lang?lang=te", None, key) == (200, {"ok": True, "lang": "te"}) and w.lang == "te"
        code, out = _post(port, "/lang", {"lang": "fr"}, key)
        assert code == 400 and w.lang == "te" and "en, hi, te" in out["error"]
        assert _post(port, "/lang", {"nothing": 1}, key)[0] == 400 and _post(port, "/lang", None, key)[0] == 400
        # a page on another origin cannot change it, nor can a request for another host name (DNS rebinding)
        assert _post(port, "/lang", {"lang": "en"}, {**key, "Origin": "https://evil.example"})[0] == 403 and w.lang == "te"
        assert _post(port, "/lang", {"lang": "en"}, {**key, "Host": "evil.example"})[0] == 403 and w.lang == "te"
        assert _post(port, "/lang", {"lang": "en"}, {**key, "Origin": "http://127.0.0.1:48940"})[0] == 200 and w.lang == "en"
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/status", headers=key), timeout=5) as r:
            assert json.loads(r.read())["lang"] == "en"
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------- R3: barge-in by voice (opt-in; fakes only)
def make_barge(text="stop", running=True, speaking=True, barge=True, strict_hits=(5,), lang="en", dry=False, speech_ok=True):
    calls = {"submit": [], "stop": 0, "speech_stop": 0, "asr": [], "langs": []}

    def asr(audio, lang="en"):
        calls["asr"].append(len(audio))
        calls["langs"].append(lang)
        return text

    def submit(t):
        calls["submit"].append(t)
        return {"ok": True, "run_id": "r1"}

    def stop():
        calls["stop"] += 1
        return {"ok": True, "stopped": True}

    def stop_speech():
        calls["speech_stop"] += 1
        return {"ok": speech_ok}
    det, strict = FakeDetector(), FakeDetector(hit_at=strict_hits)
    w = wl.WakeLoop(lambda: det, vad_factory=FakeVad, transcribe=asr, submit=submit, stop=stop, speaking=lambda: speaking,
                    running=lambda: running, chime=lambda k: None, lang=lang, dry_run=dry, stop_speech=stop_speech,
                    barge_detector_factory=(lambda: strict) if barge else None)
    return w, calls, det, strict


HEY_STOP = [QUIET] * 5 + seconds(0.8, SPEECH) + seconds(1.2, QUIET)


def test_barge_in_is_off_by_default_and_speech_while_speaking_is_dropped():
    async def go():
        w, calls, det, strict = make_barge(barge=False)       # Neko speaks, a task runs, the wake word + "stop" is heard
        await w.start()
        await feed(w, HEY_STOP)
        assert calls["asr"] == [] and calls["stop"] == 0 and calls["speech_stop"] == 0 and calls["submit"] == []
        assert w.counts["ignored_while_speaking"] == 1 and w.state == "wake_listening"
        assert w.status()["barge_in"]["enabled"] is False and strict.n == 0
    asyncio.run(go())


def test_barge_in_on_but_only_the_normal_detector_heard_it_is_still_dropped():
    """The stricter detector did not agree: the score was not above the higher threshold, so nothing is acted on."""
    async def go():
        w, calls, det, strict = make_barge(strict_hits=())
        await w.start()
        await feed(w, HEY_STOP)
        assert strict.n > 0                                    # it listened while Neko spoke ...
        assert calls["asr"] == [] and calls["stop"] == 0 and calls["speech_stop"] == 0 and calls["submit"] == []
        assert w.counts["ignored_while_speaking"] == 1 and w.counts["barge_in_heard"] == 0
    asyncio.run(go())


def test_barge_in_stop_word_stops_speech_and_task_and_starts_nothing():
    async def go():
        w, calls, det, strict = make_barge(text="Stop.")
        await w.start()
        await feed(w, HEY_STOP)
        assert calls["speech_stop"] == 1 and calls["stop"] == 1 and calls["submit"] == []
        assert w.last["what"] == "barge-in: stop" and w.last["speech_stopped"] is True and w.last["task_ok"] is True
        assert w.counts["barge_in_stops"] == 1 and w.counts["detections"] == 0 and w.state == "wake_listening"
        assert calls["langs"] == ["en"] and not w.barge
    asyncio.run(go())


def test_barge_in_stops_the_speech_even_when_no_task_runs():
    async def go():
        w, calls, *_ = make_barge(text="be quiet", running=False, speech_ok=False)
        await w.start()
        await feed(w, HEY_STOP)
        assert calls["speech_stop"] == 1 and calls["stop"] == 0 and calls["submit"] == []
        assert w.last["what"] == "barge-in: stop" and w.last["speech_stopped"] is False      # reported as it was
    asyncio.run(go())


def test_barge_in_anything_but_a_stop_word_is_dropped_and_never_becomes_a_task():
    async def go():
        for said in ("open notepad", "stop the music and open notepad", "cancel my meeting", "what time is it", "",
                     "Hey Neko, stop" + " and play something else", "I said stop to the dog"):
            w, calls, *_ = make_barge(text=said)
            await w.start()
            await feed(w, HEY_STOP)
            assert calls["submit"] == [] and calls["stop"] == 0 and calls["speech_stop"] == 0, said
            assert w.counts["barge_in_stops"] == 0 and w.state == "wake_listening", said
            assert w.last["what"] in ("barge-in ignored: not a stop word", "no command heard"), said
    asyncio.run(go())


def test_barge_in_dry_run_stops_nothing():
    async def go():
        w, calls, *_ = make_barge(text="stop", dry=True)
        await w.start()
        await feed(w, HEY_STOP)
        assert calls["speech_stop"] == 0 and calls["stop"] == 0 and calls["submit"] == []
        assert w.last["what"] == "barge-in: stop"
    asyncio.run(go())


def test_barge_in_hindi_and_telugu_stop_words_follow_the_selected_language():
    async def go():
        for lang, said in (("hi", "रुको।"), ("te", "ఆపండి")):
            w, calls, *_ = make_barge(text=said, lang=lang)
            await w.start()
            await feed(w, HEY_STOP)
            assert calls["speech_stop"] == 1 and calls["langs"] == [lang] and calls["submit"] == [], lang
        w, calls, *_ = make_barge(text="रुको", lang="en")          # not a stop word for the English setting
        await w.start()
        await feed(w, HEY_STOP)
        assert calls["speech_stop"] == 0 and calls["submit"] == []
    asyncio.run(go())


def test_barge_in_does_not_change_normal_wake_when_neko_is_quiet():
    async def go():
        w, calls, det, strict = make_barge(text="open notepad", speaking=False)
        await w.start()
        await feed(w, HEY_STOP)
        assert strict.n == 0                                   # the stricter detector is idle unless Neko speaks
        assert calls["submit"] == ["open notepad"] and calls["speech_stop"] == 0 and w.counts["detections"] == 1
    asyncio.run(go())


def test_barge_in_recording_is_short_and_a_failing_strict_detector_only_turns_it_off():
    async def go():
        w, calls, det, strict = make_barge(text="stop")
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(6, SPEECH))        # a long monologue after the wake word
        assert calls["asr"] and calls["asr"][0] <= int((wl.WakeLoop.BARGE_MAX_COMMAND_S + 0.5) * wl.RATE) * 2

        bad = FakeDetector(hit_at=(), fail_at=(2,))
        w2, calls2, *_ = make_barge(text="stop")
        w2.barge_detector_factory = lambda: bad
        await w2.start()
        await feed(w2, HEY_STOP)
        assert w2.barge_detector is None and w2.state == "wake_listening" and w2.status()["barge_in"]["active"] is False
        assert calls2["speech_stop"] == 0 and calls2["submit"] == []
    asyncio.run(go())


def test_barge_stop_phrases():
    for yes in ("stop", "Stop!", "stop it", "stop talking", "please stop", "cancel", "be quiet", "enough", "that's enough", "halt"):
        assert wl.barge_stop(yes), yes
    for no in ("", "stop the music", "don't stop", "open notepad", "cancel my meeting", "stop and open notepad", "what"):
        assert not wl.barge_stop(no), no


def test_asking_whether_neko_speaks_never_floods_or_blocks_the_audio_loop():
    """Barge-in needs the answer on every frame: it is asked at most once a second, and not at all without barge-in until the
    wake word is heard (the question is an HTTP call to the speech server)."""
    async def go():
        for barge, expect_calls in ((False, 0), (True, "few")):
            w, calls, det, strict = make_barge(barge=barge, strict_hits=(), speaking=False)
            asked = []
            w.speaking = lambda: asked.append(1) or False
            det.hit_at = set()                                   # the wake word is never heard in this stretch
            now = [1000.0]
            w.clock = lambda: now[0]
            await w.start()
            for _ in range(250):                                 # ten seconds of 40 ms frames
                now[0] += wl.FRAME / wl.RATE
                await w.on_frame(QUIET)
            if expect_calls == 0:
                assert asked == []
            else:
                assert 1 <= len(asked) <= 11, len(asked)
    asyncio.run(go())


# ---------------------------------------------------------------- stop_speech: the real interrupt call (fakes; unverified on real hardware)
def _with_http(monkeypatch, reply=None, exc=None):
    seen = []

    def fake(url, body=None, timeout=5.0, raw=None, ctype="application/json", control=None):
        assert control == "speech"                          # POST /interrupt sends the speech server's control secret (audit M15)
        seen.append((url, body))
        if exc is not None:
            raise exc
        return reply

    monkeypatch.setattr(wl, "http_json", fake)
    return seen


def test_stop_speech_posts_to_the_speech_servers_interrupt_and_is_true_only_when_it_cancelled_something(monkeypatch):
    seen = _with_http(monkeypatch, {"ok": True, "cancelled": 2, "playback_stopped": False})
    r = wl.stop_speech()
    assert seen == [(f"{wl.SPEECH}/interrupt", {})] and wl.SPEECH.startswith("http://127.0.0.1:")
    assert r["ok"] is True and r["cancelled"] == 2 and "not stopped" in r["note"]


def test_stop_speech_acknowledged_but_nothing_in_flight_is_not_reported_as_stopped(monkeypatch):
    _with_http(monkeypatch, {"ok": True, "cancelled": 0, "speaking_estimate_cleared": True})
    r = wl.stop_speech()
    assert r["ok"] is False and r["acknowledged"] is True and "not stopped" in r["note"]


def test_stop_speech_unreachable_refused_or_nonsense_answers_are_not_stopped(monkeypatch):
    for exc in (urllib.error.URLError("refused"), ConnectionRefusedError(), TimeoutError(), ValueError("bad json")):
        _with_http(monkeypatch, exc=exc)
        r = wl.stop_speech()
        assert r["ok"] is False and r["acknowledged"] is False and r["error"], exc
    _with_http(monkeypatch, exc=urllib.error.HTTPError("u", 404, "nf", {}, None))
    assert wl.stop_speech() == {"ok": False, "acknowledged": False, "error": "HTTP 404"}
    for reply in (None, [], "ok", {"ok": "yes"}, {"ok": False, "cancelled": 3}, {}):
        _with_http(monkeypatch, reply)
        assert wl.stop_speech()["ok"] is False, reply
    for bad in ("3", True, 1.5, float("nan"), [1], {"a": 1}, -1, None):         # a count that is not a real positive integer
        _with_http(monkeypatch, {"ok": True, "cancelled": bad})
        assert wl.stop_speech()["ok"] is False, bad


def test_barge_in_outcome_says_what_was_and_was_not_stopped():
    async def go():
        w, calls, *_ = make_barge(text="stop", speech_ok=False)
        w.stop_speech = lambda: {"ok": False, "acknowledged": True, "note": "nothing was being synthesized; audio already in the page is not stopped"}
        await w.start()
        await feed(w, HEY_STOP)
        assert w.last["speech_stopped"] is False and "not stopped" in w.last["speech_note"]
        w2, *_ = make_barge(text="stop")
        w2.stop_speech = lambda: {"ok": False, "acknowledged": False, "error": "URLError"}
        await w2.start()
        await feed(w2, HEY_STOP)
        assert w2.last["speech_stopped"] is False and w2.last["speech_note"] == "URLError"
    asyncio.run(go())


def test_stop_speech_against_the_real_speech_server_module_over_loopback(monkeypatch, tmp_path):
    """The wake listener's call and the speech server's /interrupt agree on the wire (fake synthesizer, real HTTP)."""
    import importlib.util
    import threading
    from http.server import ThreadingHTTPServer
    path = Path(__file__).resolve().parents[1] / "scripts" / "local_speech_server.py"
    spec = importlib.util.spec_from_file_location("lss_for_wake", path)
    lss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lss)
    started, release = threading.Event(), threading.Event()

    def synth(text, lang):
        started.set()
        release.wait(5)
        return lss_wav()

    def lss_wav():
        import io
        import wave
        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\x01\x00" * 2400)
        return b.getvalue()

    handler = lss.make_handler(synth)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(wl, "SPEECH", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))                          # the speech server's secret, as main() publishes it
    wl.control_auth.publish("speech", handler.control_secret)
    result = {}

    def speak():
        req = urllib.request.Request(wl.SPEECH + "/v1/audio/speech", data=json.dumps({"input": "hello"}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as e:
            result["code"] = e.code
    try:
        assert wl.stop_speech()["ok"] is False                                   # idle: acknowledged, nothing to stop
        t = threading.Thread(target=speak, daemon=True)
        t.start()
        assert started.wait(5)
        r = wl.stop_speech()
        release.set()
        t.join(5)
        assert r["ok"] is True and r["cancelled"] == 1 and result.get("code") == 409
    finally:
        release.set()
        srv.shutdown(); srv.server_close()


# ---------------------------------------------------------------- M15: the control routes need the launch's secret
def _status_server():
    w, _, _ = make()
    srv = wl.serve_status(w, 0)
    return w, srv, srv.server_address[1]


def test_status_control_routes_refuse_everything_but_the_secret_and_change_nothing():
    w, srv, port = _status_server()
    cancels = []
    w.request_cancel = lambda: cancels.append(1)
    key = {wl.control_auth.HEADER: srv.control_secret}
    wrong = {wl.control_auth.HEADER: srv.control_secret + "x"}
    try:
        for path, body in (("/cancel", {}), ("/pause", {}), ("/resume", {}), ("/lang", {"lang": "hi"})):
            w.paused, lang0 = True, w.lang                       # a resume that worked would flip this
            for headers in ({}, wrong, {wl.control_auth.HEADER: ""}, {wl.control_auth.HEADER: "1"},
                            {**key, "Origin": "https://evil.example"}, {**key, "Origin": "null"},
                            {**key, "Origin": "http://127.0.0.1:9999"}, {**key, "Host": "evil.example"},
                            {**key, "Host": "127.0.0.1"}, {**key, "Host": "127.0.0.1:1"}):
                assert _post(port, path, body, headers)[0] == 403, (path, headers)
            assert w.paused is True and w.lang == lang0 and cancels == [], path
        w.paused = False
        assert _post(port, "/pause", {}, key)[0] == 200 and w.paused is True
        assert _post(port, "/resume", {}, {**key, "Host": f"localhost:{port}"})[0] == 200 and w.paused is False
        assert _post(port, "/cancel", {}, key)[0] == 200 and cancels == [1]
    finally:
        srv.shutdown()
        srv.server_close()


def test_status_needs_the_token_and_only_health_is_open(monkeypatch=None):
    """Re-review M3 (2026-10-19): the old test pinned "GET /status is open". /status (counts, last outcome, language, mic problems)
    is a sensitive read now; /health is liveness only and shows no listener state."""
    w, srv, port = _status_server()

    def get(path, headers=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {}), timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
    try:
        code, body = get("/health")
        h = json.loads(body)             # liveness plus ONE readiness boolean (completion H): a listener whose detector is missing says "degraded"
        assert code == 200 and set(h) == {"status", "ready", "service"} and h["service"] == "jarvis-wake"
        assert h["status"] == ("ok" if h["ready"] else "degraded") and h["ready"] is bool(w.status()["healthy"])
        for headers in ({}, {wl.control_auth.HEADER: "1"}, {wl.control_auth.HEADER: ""}, {wl.control_auth.HEADER: srv.control_secret + "x"}):
            code, body = get("/status", headers)
            assert code == 401 and set(json.loads(body)) == {"error", "login"}
        code, body = get("/status", {wl.control_auth.HEADER: srv.control_secret})
        assert code == 200 and "counts" in json.loads(body) and srv.control_secret.encode() not in body
        for path in ("/status", "/health"):                             # a rebound host or a foreign page never reads anything
            assert get(path, {"Host": "evil.example", wl.control_auth.HEADER: srv.control_secret})[0] == 403
            assert get(path, {"Origin": "https://evil.example", wl.control_auth.HEADER: srv.control_secret})[0] == 403
        assert get("/status", {"Host": f"127.0.0.1:{port + 1}", wl.control_auth.HEADER: srv.control_secret})[0] == 401   # exact port
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_json_sends_the_published_secret_of_the_named_server(monkeypatch, tmp_path):
    """The wake listener's own calls (task start/stop at the widget) carry the widget's published token, re-read each time."""
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))
    got = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'
    monkeypatch.setattr(wl, "_open_local", lambda req, timeout=None: got.append(dict(req.header_items())) or Resp())   # the loopback seam (re-review M13)
    wl.http_json("http://127.0.0.1:1/x", {"a": 1}, control="widget")                 # no token file yet: no header, no crash
    wl.control_auth.publish("widget", "tok-1")
    wl.http_json("http://127.0.0.1:1/x", {"a": 1}, control="widget")
    wl.control_auth.publish("widget", "tok-2")                                        # the widget restarted
    wl.http_json("http://127.0.0.1:1/x", {"a": 1}, control="widget")
    wl.http_json("http://127.0.0.1:1/x", {"a": 1})                                    # no control name: never sends one
    assert [h.get("X-jarvis-control") for h in got] == [None, "tok-1", "tok-2", None]


def test_pause_is_saved_for_the_watchdog_and_resume_removes_it(tmp_path):
    """Audit Low (2026-10-18): wake.off used to be a hand-made file nothing created, so a pause was lost whenever the listener
    restarted. POST /pause now writes it (the file the watchdog honours) and POST /resume removes it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("watchdog_t", Path(__file__).resolve().parents[1] / "scripts" / "watchdog.py")
    wd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wd)
    w, srv, port = _status_server()
    off = tmp_path / "data" / "wake.off"
    w.off_path = off
    key = {wl.control_auth.HEADER: srv.control_secret}
    seen = {"boot_at": 1000.0}
    try:
        assert wd.wake_wanted(seen, 1000.0, True, off.exists())[0] is True
        assert _post(port, "/pause", {})[0] == 403 and not off.exists()               # an unauthorised pause saves nothing
        assert _post(port, "/pause", {}, key)[0] == 200 and w.paused is True and off.exists()
        wanted, why = wd.wake_wanted(seen, 1000.0, True, off.exists())
        assert wanted is False and "wake.off" in why                                   # the watchdog now leaves the microphone off
        assert _post(port, "/resume", {}, key)[0] == 200 and w.paused is False and not off.exists()
        assert wd.wake_wanted(seen, 1000.0, True, off.exists())[0] is True
    finally:
        srv.shutdown()
        srv.server_close()


def test_task_running_counts_a_lone_pc_action_not_only_main_agent_tasks(monkeypatch):
    """A single PC action started from chat is no main-agent run: the widget reports it as status.pc.busy and "stop" must see it."""
    def with_status(status):
        monkeypatch.setattr(wl, "http_json", lambda url, body=None, timeout=5.0, **kw: status)
        return wl.task_running()
    assert with_status({"task": {"state": "running"}}) is True
    assert with_status({"task": {"state": "planning"}, "pc": {"busy": False}}) is True
    assert with_status({"task": {"state": "succeeded"}, "pc": {"busy": True, "state": "working"}}) is True
    assert with_status({"task": None, "pc": {"busy": True}}) is True
    assert with_status({"task": {"state": "succeeded"}, "pc": {"busy": False}}) is False
    assert with_status({"task": {"state": "succeeded"}, "pc": {"busy": "yes"}}) is False          # only a real true counts
    assert with_status({"task": {"state": "succeeded"}, "pc": "busy"}) is False
    assert with_status({}) is False

    def down(*a, **k):
        raise OSError("widget down")
    monkeypatch.setattr(wl, "http_json", down)
    assert wl.task_running() is False


# ------------------------------------------------------------------ spoken stop in Hindi, Telugu and Hinglish; an honest answer (re-review M14/L1, findings a-c)
# Real speech recognition of these words is NOT RUN (no microphone here): the transcripts are typed text, the lists are checked as text.
STOP_PHRASES = {
    "en": ["stop", "Stop.", "STOP!", "please stop", "stop it", "stop that", "cancel the task", "abort", "halt everything", "stop now",
           "ＳＴＯＰ"],                                                                    # full-width letters: NFKC
    "hi": ["रुको", "रुको।", "रुक जाओ", "रुकिए", "बंद करो", "बन्द करो", "ठहरो", "रोको", "बस करो", "नेको रुको", "रुको नेको", "अब रुको", "रुको​",
           "रु‍को", "रुको,  रुको", "स्टॉप", "stop"],
    "te": ["ఆపు", "ఆపండి", "ఆగండి", "ఆగు", "ఆపేయండి", "చాలు", "నేకో ఆపండి", "ఆపండి నేకో", "ఆపు.", "స్టాప్", "stop"],
}
ROMAN_STOP = ["ruko", "Ruko!", "ruko neko", "neko ruko", "ruk jao", "band karo", "Band karo.", "bandh karo", "rok do", "roko", "thehro",
              "bas karo", "aapu", "aapandi", "aagandi", "agandi", "aapu neko", "ab ruko", "stop karo"]
NOT_STOP = ["रुको मत, चलते रहो", "रुको मत", "रुको और गाना बजाओ", "बंद करो टीवी", "चलते रहो", "बस एक मिनट", "मत रुको", "आपु मत करो",
            "ఆపకండి", "ఆపండి ఆ పాట", "నేను ఆగను", "ఆగండి అని చెప్పండి", "आपु",
            "don't stop", "do not stop", "stop the music", "stop talking to Rahul", "cancel my meeting", "Hey Neko, cancel my meeting",
            "never stop", "can you stop the car", "stopwatch", "ruko mat", "ruko aur gaana chalao", "band karo tv", "aapu ledu",
            "bas ek minute", "", "   ", "ruko ruko ruko mat"]


def test_whole_utterance_stop_phrases_in_every_language_stop_a_running_task():
    for lang, phrases in STOP_PHRASES.items():
        for said in phrases:
            assert wl.stop_phrase(said, lang), (lang, said)
    for lang in ("en", "hi", "te"):                                    # Roman (Hinglish / Roman Telugu) and English: any language
        for said in ROMAN_STOP + ["stop", "Stop it."]:
            assert wl.stop_phrase(said, lang), (lang, said)


def test_a_sentence_that_merely_contains_a_stop_word_never_stops_a_task():
    for lang in ("en", "hi", "te"):
        for said in NOT_STOP:
            assert not wl.stop_phrase(said, lang), (lang, said)


def test_the_script_follows_the_selected_language():
    assert wl.stop_phrase("रुको", "hi") and not wl.stop_phrase("रुको", "en") and not wl.stop_phrase("रुको", "te")
    assert wl.stop_phrase("ఆపండి", "te") and not wl.stop_phrase("ఆపండి", "hi") and not wl.stop_phrase("ఆపండి", "en")
    assert wl.stop_phrase("ruko", "te") and wl.stop_phrase("aapandi", "hi")          # Roman words: whatever the language


def test_text_is_normalised_before_matching():
    assert wl._stop_text("  Ｓｔｏｐ​﻿.  ") == "stop"                       # NFKC, zero-width and BOM removed
    assert wl._stop_text("रुको‌।") == "रुको"                                       # danda and joiners; the vowel sign stays
    assert wl._stop_text("रुको") == "रुको" and len(wl._stop_text("रुको")) == 4          # combining marks are not word breaks


def test_a_hindi_telugu_or_hinglish_stop_stops_the_running_task_and_a_sentence_does_not():
    async def go():
        for lang, said, stops in (("hi", "रुको", 1), ("hi", "रुको मत, चलते रहो", 0), ("te", "ఆపండి", 1), ("te", "ఆపండి ఆ పాట", 0),
                                  ("en", "ruko", 1), ("en", "band karo", 1), ("en", "ruko mat", 0), ("en", "stop the music", 0)):
            w, calls, _ = make(text=said, running=True)
            w.lang = lang
            await w.start()
            await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
            assert calls["stop"] == stops and w.counts["stops"] == stops, (lang, said)
    asyncio.run(go())


def test_a_stop_that_is_requested_but_not_yet_confirmed_is_logged_as_such_not_as_ok():
    async def go():
        for answer, ok, note in (
                ({"ok": False, "stopped": False, "requested": True, "still_running": 2, "error": "stop requested, 2 still running"},
                 False, "stop requested, 2 still running"),
                ({"ok": False, "stopped": False, "requested": True, "still_running": 0}, False, "stop requested, not yet confirmed ended"),
                ({"ok": True, "stopped": True, "requested": True, "still_running": 0, "message": "Stopped"}, True, "Stopped")):
            w, calls, _ = make(text="Stop.", running=True)
            w.stop = lambda a=answer: a
            await w.start()
            await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
            assert w.last["what"] == "stop the running task" and w.last["ok"] is ok and note in w.last["note"], (answer, w.last)
    asyncio.run(go())


def test_the_screen_stop_button_reports_a_requested_stop_the_same_way():
    async def go():
        w, calls, _ = make(running=True)
        w.stop = lambda: {"ok": False, "stopped": False, "requested": True, "still_running": 1}
        await w._cancel_now()
        assert w.last["what"] == "stop the running task" and w.last["ok"] is False and "stop requested, 1 still running" in w.last["note"]
    asyncio.run(go())


def test_a_long_running_pc_operation_keeps_the_spoken_stop_reaching_the_stop_path(monkeypatch):
    """task_running() reads the widget's status; a PC operation the registry says is registered counts however long it ran."""
    for pc in ({"busy": True, "state": "running", "source": "registry", "operations": [{"id": "task-1", "idle_s": 900.0, "age_s": 960.0}]},):
        monkeypatch.setattr(wl, "http_json", lambda url, *a, pc=pc, **k: {"task": None, "pc": pc})
        assert wl.task_running() is True
    monkeypatch.setattr(wl, "http_json", lambda url, *a, **k: {"task": None, "pc": {"busy": False, "state": "", "detail": ""}})
    assert wl.task_running() is False


def _say(w, frames=None):
    return feed(w, frames or ([QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET)))


def test_stop_the_music_while_a_task_runs_is_neither_a_stop_nor_silently_dropped():
    """Completion I: 'stop the music' / 'cancel my meeting' while a task runs used to be dropped as "cancelled by voice" (they vanished,
    and the owner heard nothing). They are not a stop phrase, so the running task is NOT stopped; they go on like any command, so
    the main agent answers (busy, or does it), and the log says exactly that."""
    async def go():
        for said in ("stop the music", "cancel my meeting", "stop the music please", "Cancel my dentist appointment"):
            w, calls, _ = make(text=said, running=True)
            logs = []
            w.log = logs.append
            await w.start()
            await _say(w)
            assert calls["stop"] == 0 and w.counts["stops"] == 0, f"{said!r} stopped the running task"
            assert calls["submit"] == [said], f"{said!r} vanished instead of being passed on"
            assert w.counts["cancelled"] == 0 and w.counts["passed_while_running"] == 1
            assert any("not a stop phrase" in line and "NOT stopped" in line for line in logs), logs
            assert not any("cancelled by voice" in line for line in logs)
    asyncio.run(go())


def test_the_main_agents_busy_answer_to_a_command_passed_on_while_a_task_runs_is_reported_not_swallowed():
    async def go():
        w, calls, _ = make(text="stop the music", running=True)
        w.submit = lambda t: {"ok": False, "error": "busy: a task is already running"}
        errors = []
        w.chime = errors.append
        await w.start()
        await _say(w)
        assert w.last["what"] == "the main agent refused the task" and "busy" in w.last["error"] and "error" in errors
        assert w.counts["errors"] == 1
    asyncio.run(go())


def test_a_pure_cancel_still_cancels_with_or_without_a_running_task_and_never_claims_a_stop():
    async def go():
        for running in (False, True):
            for said in ("never mind", "forget it", "no thanks", "cancel that"):
                w, calls, _ = make(text=said, running=running)
                await w.start()
                await _say(w)
                if running and said == "cancel that":
                    assert calls["stop"] == 1                      # a stop phrase while a task runs stops it (existing contract)
                else:
                    assert calls["submit"] == [] and w.counts["cancelled"] == 1, (said, running)
    asyncio.run(go())


def test_changing_the_language_while_a_task_runs_changes_which_stop_words_stop_it():
    async def go():
        w, calls, _ = make(text="रुको", running=True)               # Hindi stop word, English selected: a plain word, not a stop
        await w.start()
        await _say(w)
        assert calls["stop"] == 0 and calls["submit"] == ["रुको"]
        w2, calls2, _ = make(text="रुको", running=True)
        assert w2.set_lang("hi") == "hi"                            # the owner switched to Hindi (POST /lang) before the next wake
        await w2.start()
        await _say(w2)
        assert calls2["stop"] == 1 and calls2["submit"] == [], "after switching to Hindi the Hindi stop word stops the task"
        assert w2.set_lang("xx") is None and w2.lang == "hi", "an unknown language keeps the choice"
        w3, calls3, _ = make(text="ruko", running=True)             # Roman Hindi stops in every language
        await w3.start()
        await _say(w3)
        assert calls3["stop"] == 1
    asyncio.run(go())


def test_noise_and_one_word_fragments_never_stop_a_task_or_count_as_a_cancel():
    for said in ("uh", "hmm", "a", ".", "...", "ah ah", "stopwatch", "stoppage", "quietly", "cancellation", "nothing much to do today",
                 "रुकावट", "ఆపరేషన్", "ruko mat", "ఆపకండి", "bas itna hi", "😀", "​", ""):
        assert wl.stop_phrase(said, "en") is False, said
        assert wl.stop_phrase(said, "hi") is False, said
        assert wl.stop_phrase(said, "te") is False, said


def test_silence_noise_and_empty_recognition_start_nothing_and_stop_nothing():
    async def go():
        for text in ("", "   ", "​"):
            w, calls, _ = make(text=text, running=True)
            await w.start()
            await _say(w)
            assert calls["stop"] == 0, repr(text)
            assert w.state == "wake_listening"
    asyncio.run(go())


def test_a_spoken_stop_never_says_ok_while_the_work_is_still_running():
    assert wl.stop_note({"requested": True, "ok": False, "still_running": [{"id": "task-1"}, {"id": "m-2"}]}) == (False, "stop requested, 2 still running") or \
        wl.stop_note({"requested": True, "ok": False, "still_running": 2})[0] is False
    ok, note = wl.stop_note({"requested": True, "ok": False, "still_running": 1})
    assert ok is False and "still running" in note
    ok, note = wl.stop_note({"requested": True, "ok": False})
    assert ok is False and "not yet confirmed" in note
    assert wl.stop_note({"ok": True, "note": "stopped"})[0] is True
    assert wl.stop_note("garbage") == (False, "") and wl.stop_note(None) == (False, "")


def test_the_listener_state_stays_consistent_when_the_detector_dies_and_when_paused_and_resumed():
    async def go():
        det = FakeDetector(hit_at=(5,), fail_at=(3,))
        w, calls, _ = make(detector=det, text="open notepad")
        await w.start()
        await feed(w, [QUIET] * 4)                                 # the detector fails on frame 3 and is restarted
        assert w.state in ("wake_listening", "starting") and w.paused is False
        w.paused = True
        await feed(w, [QUIET] * 5 + seconds(1, SPEECH) + seconds(1.2, QUIET))
        assert calls["submit"] == [], "a paused listener must not hear the microphone"
        w.paused = False
        assert w.status()["state"] != "paused"
    asyncio.run(go())


def test_a_blip_too_short_to_be_a_command_is_ignored_even_if_the_transcriber_invents_words():
    async def go():
        w, calls, _ = make(text="Tico, penico")
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(0.15, SPEECH) + seconds(1.2, QUIET))
        assert calls["submit"] == [] and w.counts["too_short"] == 1 and w.counts["tasks_started"] == 0
        assert w.last["what"].startswith("too little speech") and "text" not in w.last and w.state == "wake_listening"
    asyncio.run(go())


def test_a_real_short_command_and_a_short_stop_word_still_work():
    async def go():
        w, calls, _ = make(text="weather")
        await w.start()
        await feed(w, [QUIET] * 5 + seconds(0.6, SPEECH) + seconds(1.2, QUIET))
        assert calls["submit"] == ["weather"] and w.counts["too_short"] == 0
        w2, calls2, _ = make(text="stop", running=True)
        await w2.start()
        await feed(w2, [QUIET] * 5 + seconds(0.15, SPEECH) + seconds(1.2, QUIET))
        assert calls2["stop"] == 1 and calls2["submit"] == [] and w2.counts["too_short"] == 0          # a stop word is never "too short"
    asyncio.run(go())
