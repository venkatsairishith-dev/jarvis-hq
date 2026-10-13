"""Synthesize wake-word test audio with the local TTS only (nothing leaves the machine).
Run with the console venv (sherpa-onnx, Kokoro). Writes cache/wakeword/audio/{pos,neg}/*.wav.
Pair with score.py (run with a venv that has openwakeword==0.6.0 + psutil). Synthetic speech only: it says nothing about a real
microphone, a real voice or a real room."""
import json, os, sys, wave, io
import numpy as np
sys.path.insert(0, r"D:\jarvis\hq\console")
import sherpa_onnx
from jarvis_console.voice import Voice

OUT = r"D:\jarvis\cache\wakeword\audio"
for d in ("pos", "neg"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)

v = Voice()
# the app's own English TTS (Kokoro) with several speakers, to get voice variety
def tts_cfg():
    base = r"D:\jarvis\models\voice\tts\kokoro-multi-lang-v1_0"
    kw = dict(model=os.path.join(base, "model.onnx"), voices=os.path.join(base, "voices.bin"), tokens=os.path.join(base, "tokens.txt"),
              data_dir=os.path.join(base, "espeak-ng-data"), lexicon=",".join(os.path.join(base, f) for f in ("lexicon-us-en.txt", "lexicon-zh.txt")))
    if os.path.exists(os.path.join(base, "dict")): kw["dict_dir"] = os.path.join(base, "dict")
    cfg = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
        kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(**kw), num_threads=4, provider="cpu"))
    return cfg
tts = sherpa_onnx.OfflineTts(tts_cfg())

def save(path, samples, sr):
    a = np.clip(np.asarray(samples, dtype=np.float32), -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes((a * 32767).astype("<i2").tobytes())

POS = ["Hey Jarvis.", "Hey Jarvis, open my downloads folder.", "Hey, Jarvis!", "hey jarvis what are you working on right now"]
NEG_REPLY = ["JARVIS has finished the task and saved the report.", "Jarvis is checking your downloads folder now.",
             "I am JARVIS, your assistant. How can I help?", "The task failed. JARVIS will try again with a smaller input.",
             "Tell Jarvis to stop all running tasks.", "Jarvis said the build passed.", "Okay Jarvis, I will wait.", "Hey there, what is happening?"]
PROSE = ["The committee will meet on Thursday to discuss the budget for the coming year.", "Rain is expected across the region overnight, with clearing skies by morning.",
         "Please remember to save your work before closing the application.", "The new library opens next month and will host free workshops for students.",
         "Researchers reported a small improvement in battery life after the software update.", "She walked along the river and watched the boats go by.",
         "Open the document, scroll to the second page and read the summary aloud.", "He asked whether the train to the city had already left the station.",
         "The recipe calls for two cups of flour, a pinch of salt and some warm water.", "Traffic on the main road was slow because of repairs near the bridge.",
         "You can change the volume from the settings menu or with the keys on the keyboard.", "The museum is closed on Mondays but open late on Fridays.",
         "Hey, can you pass me that book on the table?", "Hi Harvey, how was your trip to the coast?", "Let me see if Jarvis, the dog next door, is outside.",
         "What time does the meeting start tomorrow, and who is presenting?", "I think the service is down, so try again in a few minutes.",
         "A new phone with a faster processor and a better camera was announced today.", "Make sure the door is locked and the lights are off before you leave.",
         "Hey Siri is not what I want to say, I said hey there, Travis."]
n = 0
for sid in (0, 3, 7, 11, 13, 16, 20, 24):
    for sp in (0.9, 1.0, 1.15):
        for i, t in enumerate(POS):
            a = tts.generate(t, sid=sid, speed=sp)
            save(os.path.join(OUT, "pos", f"pos_s{sid}_x{int(sp*100)}_{i}.wav"), a.samples, a.sample_rate); n += 1
print("positives", n)
n = 0
for sid in (0, 11, 20):
    for i, t in enumerate(NEG_REPLY):
        a = tts.generate(t, sid=sid, speed=1.0); save(os.path.join(OUT, "neg", f"reply_s{sid}_{i}.wav"), a.samples, a.sample_rate); n += 1
for sid in (0, 3, 11, 24):
    for i, t in enumerate(PROSE):
        a = tts.generate(t, sid=sid, speed=1.0); save(os.path.join(OUT, "neg", f"prose_s{sid}_{i}.wav"), a.samples, a.sample_rate); n += 1
phr = json.load(open(r"D:\jarvis\hq\console\jarvis_console\voice_phrases.json", encoding="utf-8"))
for lang in ("en", "hi", "te"):
    for i, t in enumerate(phr[lang]):
        t = t["text"] if isinstance(t, dict) else t
        if lang == "en":
            a = tts.generate(t, sid=0, speed=1.0); save(os.path.join(OUT, "neg", f"phrase_en_{i}.wav"), a.samples, a.sample_rate)
        else:
            wavb, _ = v.speak(t, lang)
            open(os.path.join(OUT, "neg", f"phrase_{lang}_{i}.wav"), "wb").write(wavb)
        n += 1
v._stop_worker()
print("negatives", n)
