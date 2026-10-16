"""Download the decision-model candidates named by the 2026-10-16 research, checked against Hugging Face's own SHA-256
(the LFS object id). Writes to D:\\jarvis\\models\\gguf. Skips a file that is already there with the right hash.
    python jarvis\\evals\\pc_control\\fetch_candidates.py
"""
import hashlib
import json
import os
import shutil
import sys
import urllib.request

DEST = r"D:\jarvis\models\gguf"
FILES = [("LiquidAI/LFM2.5-1.2B-Instruct-GGUF", "LFM2.5-1.2B-Instruct-Q4_K_M.gguf"),
         ("ibm-granite/granite-4.0-1b-GGUF", "granite-4.0-1b-Q4_K_M.gguf"),
         ("unsloth/Qwen3-4B-Instruct-2507-GGUF", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf")]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    free = shutil.disk_usage("D:\\").free / 2**30
    print(f"D: free before: {free:.1f} GB", flush=True)
    for repo, name in FILES:
        meta = json.load(urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}/tree/main", timeout=60))
        entry = next((e for e in meta if e.get("path") == name), None)
        if not entry:
            print("NOT FOUND", repo, name, flush=True)
            continue
        want, size = (entry.get("lfs") or {}).get("oid"), entry.get("size")
        path = os.path.join(DEST, name)
        if os.path.isfile(path) and sha256(path) == want:
            print("ok (already)", name, flush=True)
            continue
        if free - size / 2**30 < 6:
            print("SKIP (would leave under 6 GB free on D:)", name, flush=True)
            continue
        tmp = path + ".part"
        with urllib.request.urlopen(f"https://huggingface.co/{repo}/resolve/main/{name}", timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        got = sha256(tmp)
        if got != want:
            os.remove(tmp)
            print("HASH MISMATCH, removed", name, flush=True)
            continue
        os.replace(tmp, path)
        free = shutil.disk_usage("D:\\").free / 2**30
        print(f"downloaded {name} ({size / 2**20:.0f} MB, sha256 ok); D: free {free:.1f} GB", flush=True)


if __name__ == "__main__":
    sys.exit(main())
