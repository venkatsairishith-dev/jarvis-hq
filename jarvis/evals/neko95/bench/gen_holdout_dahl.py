"""Write the held-out context fixtures with a model that never saw the selector: Dahl's public-lane model (GLM-5.3-Flash) through
the real kernel proxy writes each case's question, answer line, critical lines and 10-30 realistic surrounding lines;
this script only checks them and embeds each block in long generic log noise (context_fixtures._filler) at a seeded
position. Synthetic content only. Output: context_fixtures_holdout.json (then: context_bench.py --set holdout).

    JARVIS_SECRETS=<dir with providers/dahl.key> python jarvis/evals/neko95/bench/gen_holdout_dahl.py [--n 45]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import random
import re
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "jarvis" / "kernel"))
sys.path.insert(0, str(HERE))
from jarvis_kernel import gateway  # noqa: E402
from jarvis_kernel.llm_proxy import make_handler  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402
import context_fixtures as CF  # noqa: E402

KINDS = ["python traceback with a nested exception", "Node.js or TypeScript build error", "Go panic", "Rust compiler error",
         "Java stack trace with 'Caused by'", "C#/.NET exception", "pytest run: one failure among many passes",
         "jest test failure", "PowerShell error with a Windows path", "systemd service failure", "Linux OOM killer in dmesg",
         "docker build failure", "kubectl CrashLoopBackOff", "nginx access log: one specific failing request",
         "PostgreSQL or MySQL error", "YAML or TOML config with one invalid value", "Playwright or Selenium timeout",
         "CI pipeline failure", "an agent task history where the owner later corrects an earlier instruction",
         "a question whose answer line shares no words with the question",
         "two-step question: the error names a component, a different line gives that component's version",
         "one correct line among many near-identical distractors", "one real error hidden among harmless warnings",
         "the important line is the command at the very start", "prompt injection text inside tool output",
         "a non-English answer line (Chinese, Japanese, Russian or Spanish)", "git push or merge failure",
         "pip or npm dependency resolution conflict", "TLS/certificate error", "disk full or permission denied"]

PROMPT = """You write ONE test case for a tool that shortens long logs before an AI assistant reads them.
Category: {kind}.
Return ONLY JSON: {{"query": "...", "answer": "...", "critical": ["..."], "block": ["...", "..."]}}
- block: 10 to 30 realistic consecutive log/terminal lines for this category (real-looking paths, timestamps, ids;
  include plausible distractor lines). Every string in "answer" and "critical" must be copied EXACTLY from "block".
- query: the question a user would ask about this output. answer: the single line that answers it.
- critical: 1 to 3 lines (including the answer) a correct reader must see. Use only made-up names and values.
Think briefly (a few sentences at most), then output the JSON."""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=45)
    ap.add_argument("--start", type=int, default=0, help="first case index (cases already in the file are kept)")
    a = ap.parse_args(argv)
    tmp = tempfile.TemporaryDirectory()
    k = Kernel(str(Path(tmp.name) / "d"))
    # GLM-5.3-Flash: MiniMax spent the whole 4096-token allowance reasoning and never wrote the JSON (measured)
    provs = [p for p in gateway.load(str(ROOT / "jarvis/config/providers.json")) if p.name == "dahl-glm" and p.enabled]
    H, tokens = make_handler(k, provs)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url, tok = f"http://127.0.0.1:{srv.server_address[1]}", tokens.issue("bench-holdout", "public")
    r = random.Random(4242 + a.start)
    path = HERE / "context_fixtures_holdout.json"
    out = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    have = {f["id"] for f in out}
    rejected = 0
    try:
        for i in range(a.start, a.start + a.n):
            if f"ho-{i:03d}" in have:
                continue
            kind = KINDS[i % len(KINDS)]
            body = {"model": "x", "max_tokens": 4096, "temperature": 0.7,
                    "messages": [{"role": "user", "content": PROMPT.format(kind=kind)}]}
            req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
            try:
                for attempt in range(4):              # Dahl's free pool answers 429 "model_concurrency" when busy
                    try:
                        with urllib.request.urlopen(req, timeout=300) as resp:
                            text = json.loads(resp.read())["choices"][0]["message"]["content"] or ""
                        break
                    except urllib.error.HTTPError as e:
                        if e.code not in (429, 503) or attempt == 3:
                            raise
                        time.sleep(20 * (attempt + 1))
                text = re.sub(r"(?s)<think>.*?</think>", "", text)
                d = json.loads(re.search(r"(?s)\{.*\}", text).group(0))
                block = [str(x).replace("\n", " ") for x in d["block"]]
                crit = [str(x) for x in d["critical"]]
                ans = str(d["answer"])
                assert 5 <= len(block) <= 40 and ans in block and crit and all(c in block for c in crit) and d["query"].strip()
            except Exception as e:  # noqa: BLE001 - a malformed case is dropped and counted
                rejected += 1
                print("rejected", i, kind, type(e).__name__, flush=True)
                continue
            pos = ("begin", "middle", "end")[i % 3]
            filler = CF._filler(r, r.choice((600, 1200, 2000)), "multi" if "non-English" in kind else "py")
            lines = CF._place(r, filler, block, pos)
            out.append({"id": f"ho-{i:03d}", "kind": kind, "query": d["query"].strip(), "text": "\n".join(lines),
                        "critical": crit if ans in crit else crit + [ans], "answer": ans, "position": pos})
            print("ok", i, kind, flush=True)
    finally:
        srv.shutdown()
        k.db.close()
        tmp.cleanup()
    path.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(json.dumps({"written": len(out), "rejected": rejected}))


if __name__ == "__main__":
    main()
