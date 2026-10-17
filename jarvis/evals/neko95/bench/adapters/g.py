"""G: the offline parts of the voice pipeline: jarvis/wake/wake_listener.WakeLoop (the "Hey Neko" state machine) driven
with synthetic audio frames and fake detector/VAD/ASR/task services (the helpers of jarvis/wake/test_wake_listener.py).
Physical microphone, speaker, owner-voice and wake-accuracy checks are deferred cases, never simulated as passed."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "jarvis" / "wake"))
import test_wake_listener as T  # noqa: E402  (fakes only: FakeDetector, FakeVad, make, feed, seconds)
import wake_listener as wl  # noqa: E402

CMD = [T.QUIET] * 5


def _run(coro):
    return asyncio.run(coro)


def command_round_trip(a):
    async def go():
        w, calls, _ = T.make(text=a.get("text", "open notepad"))
        await w.start()
        await T.feed(w, CMD + T.seconds(1.5, T.SPEECH) + T.seconds(1.2, T.QUIET))
        ok = calls["submit"] == [a.get("text", "open notepad")] and w.state == "wake_listening" and "text" not in (w.last or {})
        return ok, f"submitted {calls['submit']}, state {w.state}, last {w.last}"
    return _run(go())


def cancel_words(a):
    async def go():
        bad = []
        for word in a["words"]:
            w, calls, _ = T.make(text=word)
            await w.start()
            await T.feed(w, CMD + T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
            if calls["submit"] or w.counts["cancelled"] != 1:
                bad.append(word)
        return not bad, f"not cancelled: {bad}"
    return _run(go())


def stop_running_task(a):
    async def go():
        w, calls, _ = T.make(text="stop", running=True)
        await w.start()
        await T.feed(w, CMD + T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
        return calls["stop"] == 1 and not calls["submit"], f"stop calls {calls['stop']}, submitted {calls['submit']}"
    return _run(go())


def ignored_while_speaking(a):
    async def go():
        w, calls, _ = T.make(speaking=True)
        await w.start()
        await T.feed(w, CMD + T.seconds(1.5, T.SPEECH) + T.seconds(1.2, T.QUIET))
        return not calls["submit"] and not calls["asr"] and w.counts["ignored_while_speaking"] >= 1, \
            f"ignored {w.counts['ignored_while_speaking']}, asr calls {len(calls['asr'])}"
    return _run(go())


def no_speech_no_asr(a):
    async def go():
        w, calls, _ = T.make()
        await w.start()
        await T.feed(w, CMD + T.seconds(5.3, T.QUIET))
        return not calls["asr"] and w.counts["no_speech"] == 1 and w.state == "wake_listening", f"asr {calls['asr']}, state {w.state}"
    return _run(go())


def asr_down(a):
    async def go():
        w, calls, _ = T.make(asr_error=True)
        await w.start()
        await T.feed(w, CMD + T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
        reported = bool(w.last) and any(k in str(w.last).lower() for k in ("error", "fail", "could not"))
        return (not calls["submit"]) and w.state == "wake_listening" and reported, \
            f"state {w.state}, last {w.last}"
    return _run(go())


def max_length(a):
    async def go():
        w, calls, _ = T.make()
        await w.start()
        await T.feed(w, CMD + T.seconds(15, T.SPEECH))
        cut = calls["asr"] and calls["asr"][0] <= int((wl.WakeLoop.MAX_COMMAND_S + wl.WakeLoop.PRE_ROLL_S) * wl.RATE) * 2
        return bool(cut) and len(calls["submit"]) == 1, f"asr bytes {calls['asr']}, submitted {len(calls['submit'])}"
    return _run(go())


def detector_restart(a):
    async def go():
        det = T.FakeDetector(hit_at=(), fail_at=(3,))
        w, calls, _ = T.make(detector=det)
        await w.start()
        await T.feed(w, [T.QUIET] * 6)
        for _ in range(60):
            if w.state == "wake_listening" and w.counts["errors"] == 1:
                break
            await asyncio.sleep(0.1)
        return w.counts["errors"] == 1 and w.state == "wake_listening", f"errors {w.counts['errors']}, state {w.state}"
    return _run(go())


def paused_ignores(a):
    async def go():
        w, calls, _ = T.make()
        await w.start()
        w.paused = True
        await T.feed(w, CMD + T.seconds(1.5, T.SPEECH) + T.seconds(1.2, T.QUIET))
        paused_ok = not calls["submit"] and w.status()["state"] == "paused"
        w.paused = False
        w.detector.hit_at = {w.detector.n + 3}
        await T.feed(w, [T.QUIET] * 4 + T.seconds(1.5, T.SPEECH) + T.seconds(1.2, T.QUIET))
        return paused_ok and calls["submit"] == ["open notepad"], f"paused ok {paused_ok}, after resume {calls['submit']}"
    return _run(go())


def cancel_twice(a):
    async def go():
        w, calls, _ = T.make()
        await w.start()
        await T.feed(w, CMD + T.seconds(0.6, T.SPEECH))
        w.request_cancel()
        w.request_cancel()
        await T.feed(w, T.seconds(1.5, T.SPEECH) + T.seconds(1.2, T.QUIET))
        return not calls["submit"] and w.counts["cancelled"] == 1 and w.state == "wake_listening", \
            f"cancelled {w.counts['cancelled']}, submitted {calls['submit']}, state {w.state}"
    return _run(go())


def second_wake_during_command(a):
    async def go():
        w, calls, _ = T.make(detector=T.FakeDetector(hit_at=(5, 20)))
        await w.start()
        await T.feed(w, CMD + T.seconds(1.5, T.SPEECH) + T.seconds(1.2, T.QUIET))
        return len(calls["submit"]) == 1, f"submitted {calls['submit']}"
    return _run(go())


def words_not_kept(a):
    async def go():
        w, calls, _ = T.make(text="email my bank statement to Kiran")
        await w.start()
        await T.feed(w, CMD + T.seconds(1.5, T.SPEECH) + T.seconds(1.2, T.QUIET))
        st = str(w.status())
        return "bank statement" not in st and "Kiran" not in st, f"status mentions the words: {'bank statement' in st}"
    return _run(go())


def resample(a):
    pcm = b"\x01\x00" * 44100
    out = wl.resample_int16(pcm, 44100)
    return abs(len(out) // 2 - 16000) <= 2, f"{len(out) // 2} samples from 1 s at 44.1 kHz"


def state_machine_speed(a):
    async def go():
        w, calls, _ = T.make()
        await w.start()
        frames = CMD + T.seconds(1.5, T.SPEECH) + T.seconds(1.2, T.QUIET)
        t0 = time.perf_counter()
        for f in frames:
            await w.on_frame(f)
        per = (time.perf_counter() - t0) / len(frames) * 1000
        while w.worker is not None and not w.worker.done():
            await asyncio.sleep(0.01)
        return per < a["max_ms"], f"{per:.3f} ms per 40 ms frame (limit {a['max_ms']} ms)"
    return _run(go())


def status_loopback(a):
    srv_check = T.test_status_server_loopback_and_origin
    try:
        srv_check()
        return True, "status server answers loopback, refuses foreign origins"
    except AssertionError as e:
        return False, f"assertion: {e}"


def long_stop_sentence_is_task(a):
    async def go():
        w, calls, _ = T.make(text="stop the music and open notepad")
        await w.start()
        await T.feed(w, CMD + T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
        return calls["submit"] == ["stop the music and open notepad"], f"submitted {calls['submit']}"
    return _run(go())


# ---------------------------------------------------------------- 2026-10-17: text rules, real service calls to FAKE local services,
# gain / level / timing on synthetic audio. The microphone itself is never simulated (those cases are owner_hardware).

import json  # noqa: E402
import threading  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def _loop(text="open notepad", detector=None, transcribe=None, submit=None, stop=None, running=None):
    """A WakeLoop with the fake detector and VAD of test_wake_listener.py; services default to recording fakes."""
    calls = {"submit": [], "stop": 0, "asr": []}

    def asr(audio):
        calls["asr"].append(audio)
        return text

    def sub(t):
        calls["submit"].append(t)
        return {"ok": True, "run_id": "r1"}

    def stp():
        calls["stop"] += 1
        return {"ok": True}
    w = wl.WakeLoop(lambda: detector or T.FakeDetector(), vad_factory=T.FakeVad, transcribe=transcribe or asr, submit=submit or sub,
                    stop=stop or stp, speaking=lambda: False, running=running or (lambda: False), chime=lambda k: None)
    return w, calls


class FakeSvc:
    """A local HTTP service (speech server or widget) on 127.0.0.1:<free port>: {(method, path): (status, json or bytes)}."""

    def __init__(self, routes):
        self.routes, self.seen = routes, []
        svc = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                svc.seen.append((method, self.path, self.headers.get("Content-Type"), body))
                code, out = svc.routes.get((method, self.path.split("?")[0]), (404, {"error": "not found"}))
                data = out if isinstance(out, bytes) else json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _dead_url():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def _say(w, frames_after_wake):
    return T.feed(w, CMD + frames_after_wake)


def short_commands_not_cancels(a):
    """Short commands that START with a cancel word but name something ("cancel my meeting", "stop the music") are tasks
    when nothing is running; bare cancel phrases still cancel."""
    async def go():
        wrong = []
        for text, want_task in [(t, True) for t in a["tasks"]] + [(t, False) for t in a["cancels"]]:
            w, calls = _loop(text)
            await w.start()
            await _say(w, T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
            if bool(calls["submit"]) != want_task:
                wrong.append(f"{text!r} -> {'task' if calls['submit'] else (w.last or {}).get('what')}")
        return not wrong, (f"wrong: {wrong}" if wrong else f"{len(a['tasks'])} tasks submitted, {len(a['cancels'])} phrases cancelled")
    return _run(go())


def empty_transcript(a):
    """Speech was heard but recognition returns no words: nothing is submitted, the outcome says so, listening resumes."""
    async def go():
        w, calls = _loop("")
        await w.start()
        await _say(w, T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
        ok = not calls["submit"] and (w.last or {}).get("what") == "no words recognized" and w.state == "wake_listening" and w.counts["no_speech"] == 1
        return ok, f"submitted {calls['submit']}, last {w.last}, state {w.state}"
    return _run(go())


def task_refused_or_widget_down(a):
    """The real submit_task() against a FAKE local widget that refuses (409, 'a task is already running'), then against an
    address where nothing listens: the outcome says the task was refused with the reason, never 'task started'; an error
    is counted; listening resumes."""
    async def one(url):
        old = wl.WIDGET
        wl.WIDGET = url
        try:
            w, calls = _loop("open notepad", submit=wl.submit_task)
            await w.start()
            await _say(w, T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
            return w
        finally:
            wl.WIDGET = old
    svc = FakeSvc({("POST", "/api/task"): (409, {"ok": False, "error": "a task is already running"})})
    try:
        w1 = _run(one(svc.url))
        sent = [json.loads(b) for m, p, ct, b in svc.seen if p == "/api/task"]
    finally:
        svc.close()
    w2 = _run(one(_dead_url()))
    bad = []
    for name, w, why in (("refused", w1, "already running"), ("down", w2, "URLError")):
        last = w.last or {}
        if last.get("what") != "the main agent refused the task" or why not in str(last.get("error")) or w.counts["tasks_started"] \
                or w.counts["errors"] != 1 or w.state != "wake_listening":
            bad.append(f"{name}: last {last}, counts {w.counts}, state {w.state}")
    if sent != [{"task": "open notepad", "mode": "work"}]:
        bad.append(f"widget received {sent}")
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"refused: {w1.last}; down: {w2.last}"


def transcribe_real_client(a):
    """The real transcribe() against a FAKE local speech server: it sends 16 kHz mono 16-bit WAV of exactly the command
    audio and returns the text trimmed; when the server answers 500 the loop reports 'speech recognition failed' and
    submits nothing."""
    import io
    import wave
    pcm = b"\x10\x00" * 8000
    svc = FakeSvc({("POST", "/v1/audio/transcriptions"): (200, {"text": "  open notepad \n"})})
    old = wl.SPEECH
    try:
        wl.SPEECH = svc.url
        text = wl.transcribe(pcm)
        m, path, ctype, body = svc.seen[-1]
        with wave.open(io.BytesIO(body)) as wv:
            fmt = (wv.getframerate(), wv.getnchannels(), wv.getsampwidth(), wv.getnframes())
        svc.routes[("POST", "/v1/audio/transcriptions")] = (500, {"error": "model not loaded"})

        async def go():
            w, calls = _loop(transcribe=wl.transcribe)
            await w.start()
            await _say(w, T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
            return w, calls
        w, calls = _run(go())
    finally:
        wl.SPEECH = old
        svc.close()
    ok = (text == "open notepad" and fmt == (16000, 1, 2, 8000) and ctype == "audio/wav" and "language=en" in path
          and (w.last or {}).get("what") == "speech recognition failed" and not calls["submit"] and w.state == "wake_listening")
    return ok, f"text {text!r}; WAV {fmt}; content type {ctype}; on 500: {w.last}, submitted {calls['submit']}"


def voice_stop_real_client(a):
    """'Hey Neko, stop' with the real task_running() and stop_task() against a FAKE widget: a task in 'planning' counts as
    running; the widget gets {"action": "stop"}; when it answers that nothing could be stopped, the outcome says ok=False
    with its note (not a claimed stop)."""
    svc = FakeSvc({("GET", "/api/status"): (200, {"task": {"state": "planning"}}),
                   ("POST", "/api/action"): (200, {"ok": False, "error": "nothing to stop"})})
    old = wl.WIDGET
    try:
        wl.WIDGET = svc.url

        async def go():
            w, calls = _loop("stop", running=wl.task_running, stop=wl.stop_task)
            await w.start()
            await _say(w, T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
            return w, calls
        w, calls = _run(go())
        acts = [json.loads(b) for m, p, ct, b in svc.seen if p == "/api/action"]
    finally:
        wl.WIDGET = old
        svc.close()
    last = w.last or {}
    ok = (acts == [{"action": "stop"}] and last.get("what") == "stop the running task" and last.get("ok") is False
          and "nothing to stop" in str(last.get("note")) and not calls["submit"] and w.counts["stops"] == 1)
    return ok, f"widget got {acts}; outcome {last}; submitted {calls['submit']}"


def _amp_frames(level, n):
    return [(int(level).to_bytes(2, "little", signed=True)) * wl.FRAME] * n


def command_gain(a):
    """The wake word's loudness sets the command's gain: a quiet speaker is raised (at most 16x, +24 dB), a loud one is
    never cut (1x), and raising never wraps around: a loud command after a quiet wake word is clipped at full scale."""
    import numpy as np

    async def one(wake_level, cmd_level):
        got = []
        w, calls = _loop(detector=T.FakeDetector(hit_at=(30,)), transcribe=lambda audio: got.append(audio) or "open notepad")
        await w.start()
        await T.feed(w, _amp_frames(wake_level, 30) + _amp_frames(cmd_level, 25) + T.seconds(1.2, T.QUIET))
        x = np.frombuffer(got[0], dtype="<i2") if got else np.zeros(1, dtype="<i2")
        return int(x.max()), int(x.min()), calls["submit"]
    quiet = _run(one(16, 16))
    loud = _run(one(20000, 20000))
    clip = _run(one(100, 10000))
    ok = (quiet[0] == 16 * 16 and loud[0] == 20000 and clip[0] == 32767 and clip[1] >= 0
          and quiet[2] == loud[2] == clip[2] == ["open notepad"])
    return ok, f"quiet wake: command peak {quiet[0]} (16x of 16); loud: {loud[0]} (1x); quiet wake + loud command: max {clip[0]}, min {clip[1]}"


def mic_level_status(a):
    """The status shows the microphone level: a second of digital silence reads about -120 dBFS (a muted microphone is
    visible), a full-scale second about 0 dBFS; max and min are kept."""
    async def go():
        w, calls = _loop(detector=T.FakeDetector(hit_at=()))
        await w.start()
        await T.feed(w, T.seconds(1, T.QUIET))
        silent = dict(w.status()["audio"] or {})
        await T.feed(w, _amp_frames(32767, 25))
        loud = dict(w.status()["audio"] or {})
        return silent, loud
    silent, loud = _run(go())
    ok = (silent.get("last_s_dbfs", 0) <= -100 and loud.get("last_s_dbfs", -999) >= -1 and loud.get("max_s_dbfs", -999) >= -1
          and loud.get("min_s_dbfs", 0) <= -100)
    return ok, f"silence {silent}; full scale {loud}"


def end_of_command_timing(a):
    """The command ends 1 s after the speaker stops (END_SILENCE_S), not earlier (cut words) and not much later: the audio
    sent to recognition is the speech plus that second of silence (frame accuracy)."""
    async def go():
        w, calls = _loop()
        await w.start()
        await _say(w, T.seconds(a["speech_s"], T.SPEECH) + T.seconds(3, T.QUIET))
        return calls
    calls = _run(go())
    dur = len(calls["asr"][0]) / 2 / wl.RATE if calls["asr"] else 0
    lo, hi = a["speech_s"] + 1.0, a["speech_s"] + 1.0 + a["slack_s"]
    ok = len(calls["asr"]) == 1 and lo <= dur <= hi
    return ok, f"audio sent {dur:.2f}s for {a['speech_s']}s of speech (want {lo:.2f}-{hi:.2f}s)"


def slow_starter(a):
    """The owner says 'Hey Neko', pauses 4.5 s, then speaks: still within the 5 s wait, so the command is heard in full."""
    async def go():
        w, calls = _loop()
        await w.start()
        await _say(w, T.seconds(a["pause_s"], T.QUIET) + T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
        return w, calls
    w, calls = _run(go())
    dur = len(calls["asr"][0]) / 2 / wl.RATE if calls["asr"] else 0
    ok = calls["submit"] == ["open notepad"] and dur >= a["pause_s"] + 1.0 and w.counts["no_speech"] == 0
    return ok, f"submitted {calls['submit']}; audio {dur:.2f}s; no_speech {w.counts['no_speech']}"


def ring_buffer_bounded(a):
    """Two minutes of listening without a wake word: the pre-roll buffer holds at most PRE_ROLL_S seconds (plus a frame);
    nothing else accumulates."""
    async def go():
        w, calls = _loop(detector=T.FakeDetector(hit_at=()))
        await w.start()
        for _ in range(int(a.get("seconds", 120) * wl.RATE / wl.FRAME)):
            await w.on_frame(T.QUIET)
        return w, calls
    w, calls = _run(go())
    held = sum(len(p) // 2 for _, p in w.ring) / wl.RATE
    ok = held <= wl.WakeLoop.PRE_ROLL_S + wl.FRAME / wl.RATE + 1e-9 and not w.cmd and not calls["asr"]
    return ok, f"after {a.get('seconds', 120)} s: ring holds {held:.2f}s (limit {wl.WakeLoop.PRE_ROLL_S}s + one frame); command buffer {len(w.cmd)}"


def status_host_and_origin(a):
    """Status server: a request whose Host is not loopback (DNS rebinding) is refused; the Neko widget's own origin may
    pause/resume; any other origin, even another localhost port, is refused and changes nothing."""
    w, _ = _loop()
    srv = wl.serve_status(w, 0)
    port = srv.server_address[1]

    def req(method, path, headers):
        r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"" if method == "POST" else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=5) as x:
                return x.status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        got = {"rebind_get": req("GET", "/status", {"Host": f"evil.example:{port}"}),
               "rebind_pause": req("POST", "/pause", {"Host": f"attacker.test:{port}"}),
               "widget_pause": req("POST", "/pause", {"Origin": "http://localhost:48940"})}
        paused_by_widget = w.paused
        got["other_port_resume"] = req("POST", "/resume", {"Origin": "http://localhost:9999"})
        still_paused = w.paused
        got["widget_resume"] = req("POST", "/resume", {"Origin": "http://127.0.0.1:48940"})
    finally:
        srv.shutdown()
        srv.server_close()
    want = a["want"]
    ok = got == want and paused_by_widget and still_paused and not w.paused
    return ok, f"{got}; paused by widget {paused_by_widget}; still paused after the foreign origin {still_paused}"


def wav_input_normalised(a):
    """Recorded test audio is normalised like the microphone: a 48 kHz stereo WAV becomes 16 kHz mono (channels averaged:
    opposite channels cancel), an 8-bit WAV is refused, and FileSource yields whole 40 ms frames plus 3 s of silence."""
    import io
    import wave
    import numpy as np
    t = np.arange(48000) / 48000
    left = (np.sin(2 * np.pi * 440 * t) * 8000).astype("<i2")
    st = np.stack([left, -left], axis=1).reshape(-1).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wv:
        wv.setnchannels(2)
        wv.setsampwidth(2)
        wv.setframerate(48000)
        wv.writeframes(st)
    mono = np.frombuffer(wl.read_wav_16k(buf.getvalue()), dtype="<i2")
    b8 = io.BytesIO()
    with wave.open(b8, "wb") as wv:
        wv.setnchannels(1)
        wv.setsampwidth(1)
        wv.setframerate(16000)
        wv.writeframes(b"\x80" * 16000)
    try:
        wl.read_wav_16k(b8.getvalue())
        eight = "accepted"
    except ValueError as e:
        eight = f"refused: {e}"

    async def frames():
        out = []
        async for f in wl.FileSource(b"\x01\x00" * 16000).frames():
            out.append(f)
        return out
    fr = _run(frames())
    tail = b"".join(fr)[16000 * 2:]
    ok = (abs(len(mono) - 16000) <= 2 and int(np.abs(mono).max()) <= 50 and eight.startswith("refused")
          and all(len(f) == wl.FRAME * 2 for f in fr) and len(fr) == (16000 + 3 * 16000) // wl.FRAME and not any(tail))
    return ok, f"48 kHz stereo -> {len(mono)} samples, peak {int(np.abs(mono).max())}; 8-bit: {eight}; FileSource {len(fr)} frames, silent tail {not any(tail)}"
