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
