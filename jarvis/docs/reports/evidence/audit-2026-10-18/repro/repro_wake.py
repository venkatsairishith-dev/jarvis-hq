"""Audit repro (read-only): wake_listener behaviours. Uses the repo's own test fakes."""
import asyncio, sys, threading, time
sys.path.insert(0, "/src"); sys.path.insert(0, "/src/jarvis/wake")
import test_wake_listener as T
import wake_listener as wl

# R1: "cancel from the screen" while the command is being submitted still starts the task
async def r1():
    submitted, started = [], threading.Event()
    def slow_submit(t):
        started.set(); time.sleep(0.5); submitted.append(t); return {"ok": True, "run_id": "r1"}
    w = wl.WakeLoop(lambda: T.FakeDetector(), vad_factory=T.FakeVad, transcribe=lambda a: "open notepad",
                    submit=slow_submit, stop=lambda: {"ok": True}, speaking=lambda: False, running=lambda: False,
                    chime=lambda k: None)
    await w.start()
    for f in [T.QUIET]*5 + T.seconds(1.5, T.SPEECH) + T.seconds(1.2, T.QUIET):
        await w.on_frame(f); await asyncio.sleep(0)
    while not started.is_set():
        await asyncio.sleep(0.01)
    print("R1 state while submitting:", w.state)
    w.request_cancel(); await w.on_frame(T.QUIET)
    print("R1 outcome after cancel:", w.last, "| counts.cancelled:", w.counts["cancelled"])
    await asyncio.sleep(0.8)
    print("R1 submit() calls that completed anyway:", submitted)

# R2: the wake path always asks the speech server for English
async def r2():
    langs = []
    def asr(audio, lang="en"):
        langs.append(lang); return "नोटपैड खोलो"
    w = wl.WakeLoop(lambda: T.FakeDetector(), vad_factory=T.FakeVad, transcribe=asr, submit=lambda t: {"ok": True},
                    stop=lambda: {"ok": True}, speaking=lambda: False, running=lambda: False, chime=lambda k: None)
    await w.start()
    await T.feed(w, [T.QUIET]*5 + T.seconds(1.5, T.SPEECH) + T.seconds(1.2, T.QUIET))
    print("R2 language passed to transcribe():", langs, "(no caller ever passes hi/te)")

# R3: while Neko is speaking, a wake word is ignored (so 'Hey Neko, stop' cannot barge in)
async def r3():
    w, calls, _ = T.make(text="stop", running=True, speaking=True)
    await w.start()
    await T.feed(w, [T.QUIET]*5 + T.seconds(1.0, T.SPEECH) + T.seconds(1.2, T.QUIET))
    print("R3 speaking=True, task running, said 'Hey Neko, stop': stop calls", calls["stop"], "| last", w.last)

asyncio.run(r1()); asyncio.run(r2()); asyncio.run(r3())
