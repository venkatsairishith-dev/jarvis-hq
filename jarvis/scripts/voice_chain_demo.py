r"""Demo-data run of N.E.K.O's speech input, without the browser microphone. Windows, needs the stack running (neko-start.ps1).

Demo sentences in English, Hindi and Telugu are spoken by the local TTS (the "microphone" data), then each goes through
N.E.K.O's real recognizer worker (audio / commit / final protocol), which sends it to the local int8 endpoint. Prints what
was said and what was heard. Run with the N.E.K.O environment's Python:
    D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\scripts\voice_chain_demo.py
"""
import asyncio
import json
import os
import sys
import time
import urllib.request

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loopback_net  # noqa: E402  loopback only, no proxy, no redirect (completion H)
os.environ["NEKO_WHISPER_ENDPOINT"] = "http://127.0.0.1:48930/v1/audio/transcriptions"
os.environ.pop("NEKO_WHISPER_MODEL", None)
from main_logic.asr_client._infra import AsrSessionConfig, _AsrWorkerRequest
from main_logic.asr_client.workers import faster_whisper as fw

DEMO = [
    ("en", "en-US", "Hello. What is the capital of France?"),
    ("hi", "hi-IN", "\u0928\u092e\u0938\u094d\u0924\u0947, \u0906\u091c \u092e\u094c\u0938\u092e \u092c\u0939\u0941\u0924 \u0905\u091a\u094d\u091b\u093e \u0939\u0948 \u0914\u0930 \u092e\u0948\u0902 \u092c\u093e\u091c\u093c\u093e\u0930 \u091c\u093e \u0930\u0939\u093e \u0939\u0942\u0901\u0964"),
    ("te", "te-IN", "\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02, \u0c08\u0c30\u0c4b\u0c1c\u0c41 \u0c35\u0c3e\u0c24\u0c3e\u0c35\u0c30\u0c23\u0c02 \u0c1a\u0c3e\u0c32\u0c3e \u0c2c\u0c3e\u0c17\u0c41\u0c02\u0c26\u0c3f, \u0c28\u0c47\u0c28\u0c41 \u0c2e\u0c3e\u0c30\u0c4d\u0c15\u0c46\u0c1f\u0c4d\u0c15\u0c41 \u0c35\u0c46\u0c33\u0c4d\u0c24\u0c41\u0c28\u0c4d\u0c28\u0c3e\u0c28\u0c41."),
]


def speak16k(text, voice):
    req = urllib.request.Request("http://127.0.0.1:48930/v1/audio/speech", data=json.dumps(
        {"model": "m", "input": text, "voice": voice, "response_format": "pcm"}).encode(), headers={"Content-Type": "application/json"})
    pcm = np.frombuffer(loopback_net.urlopen(req, timeout=180).read(), dtype="<i2").astype("float32") / 32768.0
    n = int(len(pcm) * 16000 / 24000)
    a = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm)
    return (a * 32767).astype("<i2").tobytes()


async def recognise(lang_code, pcm16):
    requests, responses = asyncio.Queue(), asyncio.Queue()
    task = asyncio.create_task(fw.faster_whisper_asr_worker(requests, responses, "", AsrSessionConfig(language=lang_code)))
    ready = await asyncio.wait_for(responses.get(), 60)
    assert ready.kind == "ready", ready.kind
    key = {"generation": 0, "buffer_epoch": 0, "utterance_id": 1}
    t0 = time.time()
    await requests.put(_AsrWorkerRequest(kind="audio", audio=pcm16, **key))
    await requests.put(_AsrWorkerRequest(kind="commit", **key))
    while True:
        ev = await asyncio.wait_for(responses.get(), 60)
        if ev.kind in ("final", "error"):
            break
    took = time.time() - t0
    await requests.put(_AsrWorkerRequest(kind="shutdown", generation=0, buffer_epoch=0))
    try:
        await asyncio.wait_for(task, 10)
    except Exception:
        task.cancel()
    return ev.kind, getattr(ev, "text", "") or getattr(ev, "message", ""), took


async def main():
    heard = {}
    for voice, code, text in DEMO:
        pcm16 = speak16k(text, voice)
        kind, got, took = await recognise(code, pcm16)
        heard[voice] = got
        print(f"[{voice}] spoke {len(pcm16) / 2 / 16000:.1f}s | worker event={kind} in {took:.1f}s\n     said : {text}\n     heard: {got}", flush=True)
    return heard


asyncio.run(main())
