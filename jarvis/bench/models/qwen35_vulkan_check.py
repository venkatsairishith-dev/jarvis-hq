"""Does Qwen3.5-4B (Gated DeltaNet layers) give the same output on this GPU (Vulkan) as on the CPU? And how fast?

    python jarvis\\bench\\models\\qwen35_vulkan_check.py

Windows, this laptop. Greedy decoding (temperature 0), thinking off, same prompts on llama.cpp b11669's CPU build and its
Vulkan build (-fa off). Writes result.json next to this file. Port 18092 (8091 belongs to another service on this PC).
"""
import json
import os
import subprocess
import time
import urllib.request

MODEL = r"D:\jarvis\models\llm\Qwen3.5-4B-Q4_K_M.gguf"
BUILDS = {"cpu": (r"D:\jarvis\tools\llama.cpp\b11669\cpu\llama-server.exe", ["-ngl", "0"]),
          "vulkan": (r"D:\jarvis\tools\llama.cpp\b11669\vulkan\llama-server.exe", ["-ngl", "99", "-fa", "off"])}
PROMPTS = ["Explain in two sentences why the sky is blue.",
           "List three free, legal places to practise web security.",
           "Return JSON with keys city and country for: Hyderabad."]
PORT = 18092


def ask(prompt):
    body = {"model": "x", "max_tokens": 80, "temperature": 0, "top_k": 1, "seed": 1,
            "chat_template_kwargs": {"enable_thinking": False}, "messages": [{"role": "user", "content": prompt}]}
    r = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions",
                                                                data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=300))
    return r["choices"][0]["message"]["content"], r.get("timings", {})


def run(build):
    exe, extra = BUILDS[build]
    log = open(os.path.join(os.path.dirname(__file__), f"server_{build}.log"), "w")
    p = subprocess.Popen([exe, "-m", MODEL, "--host", "127.0.0.1", "--port", str(PORT), "--jinja", "--no-webui", "-c", "4096",
                          "-np", "1", *extra], stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        for _ in range(180):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
                break
            except OSError:
                time.sleep(1)
        ask("hi")                                                    # warm-up
        out = []
        for pr in PROMPTS:
            text, t = ask(pr)
            out.append({"prompt": pr, "text": text, "gen_tok_s": round(t.get("predicted_per_second") or 0, 1),
                        "prompt_tok_s": round(t.get("prompt_per_second") or 0, 1)})
        return out
    finally:
        p.terminate()
        p.wait(30)
        log.close()
        time.sleep(3)


def main():
    res = {b: run(b) for b in BUILDS}
    same = [a["text"] == b["text"] for a, b in zip(res["cpu"], res["vulkan"])]
    summary = {"identical_outputs": f"{sum(same)}/{len(same)}",
               "cpu_gen_tok_s": [r["gen_tok_s"] for r in res["cpu"]], "vulkan_gen_tok_s": [r["gen_tok_s"] for r in res["vulkan"]]}
    json.dump({"summary": summary, "runs": res}, open(os.path.join(os.path.dirname(__file__), "result.json"), "w"), indent=2)
    print(json.dumps(summary))
    for a, b, s in zip(res["cpu"], res["vulkan"], same):
        print("SAME" if s else "DIFFERENT", "|", a["text"][:90].replace("\n", " "), "||", b["text"][:90].replace("\n", " "))


if __name__ == "__main__":
    main()
