"""Acoustic check of the real microphone path: play synthesized clips through the laptop's speakers while the listener
hears them through its microphone (dry run: it never starts a task). Windows, neko-venv. Makes a few seconds of sound.

    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe jarvis\\wake\\loopback_test.py

Each clip: did the listener wake, and what did local speech recognition hear afterwards? A negative clip must not wake it.
Synthetic voice through a laptop speaker is not the owner's voice: the owner's spoken test is still the real test.
"""
import json
import subprocess
import sys
import time
import urllib.request
import winsound
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIPS = Path(r"D:\jarvis\outputs\neko\wake-eval")
PORT = 48947
CASES = [("positive-01-en.wav", True), ("negative-04-en.wav", False), ("positive-03-en.wav", True),
         ("negative-08-en.wav", False), ("positive-06-hi.wav", True)]


def status():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status", timeout=3) as r:
        return json.loads(r.read())


def main():
    proc = subprocess.Popen([sys.executable, str(HERE / "wake_listener.py"), "--dry-run", "--no-chime", "--port", str(PORT), "--minutes", "4",
                             "--save-commands", str(CLIPS / "loopback-commands")],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    try:
        for _ in range(60):
            try:
                if status()["state"] == "wake_listening":
                    break
            except OSError:
                pass
            time.sleep(0.5)
        time.sleep(2.0)
        results = []
        for name, should_wake in CASES:
            before = status()["counts"]
            winsound.PlaySound(str(CLIPS / name), winsound.SND_FILENAME)      # blocks while it plays
            deadline = time.time() + 20
            while time.time() < deadline:                  # wait until it is back to listening (or never left)
                time.sleep(0.5)
                st = status()
                if st["state"] == "wake_listening" and time.time() > deadline - 17:
                    break
            woke = st["counts"]["detections"] > before["detections"]
            heard = (st.get("last") or {}).get("text") if woke else None
            results.append({"clip": name, "should_wake": should_wake, "woke": woke, "heard": heard,
                            "pass": woke == should_wake, "audio": st.get("audio")})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
            time.sleep(1.5)
        print("SUMMARY", json.dumps({"passed": sum(r["pass"] for r in results), "of": len(results)}))
        return 0 if all(r["pass"] for r in results) else 1
    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            out = proc.communicate()[0]
        print("--- listener log ---\n" + "\n".join(ln for ln in out.splitlines() if "outcome" in ln or "stopped" in ln))


if __name__ == "__main__":
    sys.exit(main())
