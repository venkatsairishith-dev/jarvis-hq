"""Download the local model set into D:\\jarvis\\models\\llm and verify each file against Hugging Face's published SHA-256.

    python fetch_models.py            download what is missing (resumes a partial file), verify, print a table
    python fetch_models.py --check    only verify what is already there

Windows or any OS; standard library only. Public models, no token. Disk space is checked before each file.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request

DEST = r"D:\jarvis\models\llm"
UA = {"User-Agent": "jarvis-model-fetch"}
LING = ("generalist (MoE, 1.3B active)", "inclusionAI/Ling-3.0-tiny-GGUF", "Ling-3.0-tiny-Q4_K_M.gguf")
CODER = ("code", "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF", "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf")
MATH = ("maths", "bartowski/Qwen2.5-Math-1.5B-Instruct-GGUF", "Qwen2.5-Math-1.5B-Instruct-Q4_K_M.gguf")
PROFILE_MODELS = {   # (role, repo, file) per hardware profile (jarvis/docs/reports/MODEL_MATRIX.md)
    # The generalist also routes; SmolLM2-360M was tried as a router and dropped (too inaccurate, see local_team/core.py).
    "vram4": [LING, CODER, MATH],
    "cpu8": [("general + router", "unsloth/Qwen3.5-2B-GGUF", "Qwen3.5-2B-Q4_K_M.gguf"), CODER, MATH],
    "cpu16": [LING, ("router + tool choice", "unsloth/Qwen3.5-4B-GGUF", "Qwen3.5-4B-Q4_K_M.gguf"), CODER, MATH],
    "vram8plus": [("general + router", "unsloth/Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q4_K_M.gguf"), CODER, MATH],
    "remote_only": [],
}
MODELS = PROFILE_MODELS["vram4"]


def meta(repo, fname):
    info = json.load(urllib.request.urlopen(urllib.request.Request(
        f"https://huggingface.co/api/models/{repo}?blobs=true", headers=UA), timeout=60))
    for s in info.get("siblings", []):
        if s.get("rfilename") == fname:
            return s.get("size"), (s.get("lfs") or {}).get("sha256")
    raise FileNotFoundError(f"{fname} not in {repo}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(repo, fname, size, path):
    part = path + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    req = urllib.request.Request(f"https://huggingface.co/{repo}/resolve/main/{fname}", headers={**UA, "Range": f"bytes={have}-"})
    t0, last = time.time(), 0
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "ab") as out:
        while True:
            buf = r.read(4 * 1024 * 1024)
            if not buf:
                break
            out.write(buf)
            have += len(buf)
            if time.time() - last > 15:
                last = time.time()
                rate = (have / max(time.time() - t0, 1)) / 1e6
                print(f"    {fname}: {have / 1e9:.2f}/{size / 1e9:.2f} GB  ({rate:.1f} MB/s)", flush=True)
    os.replace(part, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--profile", choices=sorted(PROFILE_MODELS), default="vram4",
                    help="model set for a hardware profile (see jarvis/local_models/hwdetect.py)")
    a = ap.parse_args()
    os.makedirs(DEST, exist_ok=True)
    rows = []
    for role, repo, fname in PROFILE_MODELS[a.profile]:
        size, want = meta(repo, fname)
        path = os.path.join(DEST, fname)
        if not os.path.exists(path) and not a.check:
            free = shutil.disk_usage(DEST).free
            if free < (size or 0) + 5e9:
                print(f"STOP: only {free / 1e9:.1f} GB free on D:, {fname} needs {size / 1e9:.1f} GB plus a 5 GB margin")
                return 2
            print(f"  downloading {fname} ({size / 1e9:.2f} GB) from {repo}", flush=True)
            download(repo, fname, size, path)
        ok = os.path.exists(path) and (not want or sha256(path) == want)
        rows.append((role, fname, os.path.getsize(path) / 1e9 if os.path.exists(path) else 0, ok))
        print(f"  {'OK ' if ok else 'BAD'} {role:30s} {fname}", flush=True)
        if os.path.exists(path) and not ok:
            print(f"  checksum mismatch: {path} is not what {repo} published; delete it and run again")
    print(f"\nD: free now {shutil.disk_usage(DEST).free / 1e9:.1f} GB")
    return 0 if all(r[3] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
