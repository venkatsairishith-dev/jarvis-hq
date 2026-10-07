# 03 Decisions and decision-layer evaluation

Each record: problem, options, evidence, decision, fallback. Evidence quality is stated. Nothing here was benchmarked on this machine yet; those are marked UNTESTED.

## D1 Decision layer (the System One / Laya question)
- Problem: pick the cheapest mechanism that routes and classifies reliably.
- Options: rules (ZEN or plain code), state machines, embedding router, tiny classifier on embeddings, fine-tuned encoder (Laya-style), small LLM, larger LLM, hybrid cascade.
- Evidence: Laya is a real open ModernBERT-large encoder (~421M) but new, with no independent replication and no CPU latency figure. Jev's gains are vendor-only. ZEN does about 91k evaluations/s per core. FastEmbed bge-small is 4 to 38 ms. RouteLLM-style routing cut cost 35 to 85% at 95% quality. No published false-allow rates for risk classification exist.
- Decision: rules set the risk floor and make the final permission call. Intent routing uses an embedding matcher with a logistic-regression head. A small local LLM with constrained JSON handles low-confidence cases. A larger model or you handle the rest. Laya is not used until the benchmark shows a gain.
- Fallback: if embeddings are weak, move tier 2 to a fine-tuned small encoder. If a small LLM matches the encoder within 2 points at p95 under 1.5 s, drop the encoder.

## Benchmark (design only, not run)
Six task classes: permission and risk, intent routing, tool selection, agent routing, completion detection, injection resistance. Test set: 200 labelled items (real logs, generated and reviewed items, public sets, injection strings), at least 40 high-risk. Compare: rules only, embedding router, small LLM, larger LLM, hybrid.
Metrics: accuracy, latency p50 and p95, RAM, false-allow rate, calibration (ECE), recovery.
Pass (targets, UNTESTED): false-allow at most 1% overall and 0 of 40 high-risk at the final stage, reported with a confidence interval and with a larger high-risk set; ECE at most 0.08; tier-2 p95 at most 50 ms; RAM at most 3 GB. Small-LLM latency and RAM figures in the research are estimates, not measurements.

## D2 Rules engine: ZEN 2.1.2 vs plain code
Decision: start with plain Python rules (a table plus tests) for GREEN/YELLOW/RED. Add ZEN when rules need editing without code. Fallback: plain code. ZEN has an aarch64 wheel, so install is not a blocker; runtime on the VM is UNTESTED.

## D3 Embedding matcher
Decision: FastEmbed plus numpy cosine, about 60 lines. semantic-router is optional because 0.1.16 requires a litellm version tied to a past supply-chain concern [verify].

## D4 Model gateway
Decision: trial OmniRoute on the VM bound to Tailscale only, with API keys. Alternatives: a LiteLLM proxy or Hermes own fallback. Hold it to a short trial with a keep or drop rule, since it stores provider credentials and has an unread advisory.

## D5 Local models on the laptop
Evidence: ROCm does not cover this GPU on Windows. Vulkan may work; unconfirmed. CPU speed estimates are 10 to 15 tokens/s for 3B and 5 to 8 for 7 to 8B.
Decision: small local lane. Router and extractor at 1.7B to 4B, an 8B only as a slow background worker, embeddings local. Total under about 25 GB on D:. First test llama.cpp Vulkan from D: with `--list-devices` and `llama-bench` at 0 and 99 GPU layers, then install Ollama only if needed. Set `OLLAMA_MODELS`, `HF_HOME` and the pip, npm and TEMP caches to D: before any download. WSL2 is not worth it here; use native Windows binaries.
Fallback: if Vulkan fails, CPU only.

## D6 State and memory
SQLite for task state, node registry, model registry and the event log (append-only, hash-chained). Markdown vault in git for long-term memory. No vector database until retrieval over the vault proves too slow or inaccurate with FastEmbed plus a flat index.

## D7 Workflow engine
Decision: a small state machine in the kernel plus the Hermes cron scheduler. n8n and other engines are rejected for now.

## D8 Desktop presence (Windows 10)
Task Scheduler (at logon, restart on failure) plus a tray client, because UI work needs the interactive session. WinSW for a headless watcher if needed. NSSM rejected. Tauri and Task Scheduler claims came from memory [verify].

## D9 Secrets
Windows: D:\jarvis-secrets (locked to the user). macOS: ~/.jarvis/secrets.env mode 600. Tools read them directly; no model sees values. Draft snippets that echo tokens or write them into ~/.claude.json are rewritten to read from files.

## D10 Training
Parked. The TrainingProvider interface is specified in 02_ARCHITECTURE.md; nothing is built, and Colab is not hard-coded.


## D5 result: measured on this laptop (2026-10-07, llama.cpp b11461, Qwen3-1.7B Q4_K_M, 8 threads)
The Radeon Pro 5500M works through the Vulkan build under Boot Camp (4,080 MiB, about 3.4 GB free). llama-bench: CPU 144.6 tokens/s prompt, 21.2 generation; Vulkan with all layers on the GPU 660.2 prompt, 89.9 generation; Vulkan build with zero GPU layers 224.3 and 16.9. So the GPU is about 4.2 times faster for generation. This replaces the earlier estimates. Not yet measured: 4B and 8B models, thermals under sustained load, Ollama's own Vulkan path (llama.cpp is used directly until Ollama is shown to match). Models over about 3 GB will not fit fully in 4 GB of VRAM.

## D1 result: measured decision-layer benchmark (2026-10-07)
Setup: llama.cpp b11461; LLM = Qwen3-1.7B Q4_K_M on the Radeon via Vulkan (constrained JSON, 2 examples per label); embeddings = bge-small-en-v1.5 q8 on CPU, nearest neighbour; rules = hand-written keyword rules. Six task classes, 200 items written by the author (bench/data/decision_set.jsonl), labels UNREVIEWED. Test 1 = the 97-item test half. Test 2 = 88 items written by a separate subagent that had seen only the label definitions (bench/data/ood_items.jsonl).

| Mechanism | Test 1 (own wording) | Test 2 (independent) | p50 latency | p95 latency |
|---|---|---|---|---|
| Keyword rules | 98% | 67% (7 of 22 high-risk requests allowed) | 0.1 ms | 0.1 ms |
| Embedding nearest neighbour | 59% | 88% | 22 ms | 49 ms |
| Qwen3-1.7B, constrained JSON | 77% | 84% | 837 ms | 1.1 s (worst single call 13.7 s) |
| Hybrid (kNN if similar, else LLM; rules floor on risk) | 82% | 89% | 712 ms | 1.2 s |

What this shows: (1) the keyword rules looked perfect on my own data and fell to 67% on independent wording, so keyword rules are not a classifier for natural language. They stay as the deterministic policy for STRUCTURED actions (tool, operation, path), which has its own tests. (2) The cheap embedding matcher beat the 1.7B LLM on independent data, at about 40 times lower latency, and needs only a few examples per label. (3) The LLM adds value mainly on injection detection (96%) and where the embedding is unsure. (4) Risk: taking the highest level proposed by kNN and the LLM gave 0 of 22 false-allows and 92% exact on Test 2 (3 of 40 over-cautious, which is the safe direction). That rule was chosen after seeing Test 2 errors, so it must be confirmed on a fresh set. Rules plus kNN plus LLM gave 0 of 22 but 85% exact (6 over-cautious).
Decision: risk = max(structured policy, kNN, LLM), a model can only raise. Intent, injection and tool routing = kNN first, LLM when the match is below threshold. Completion and tier have too few items (4 and 8 test items) to judge; they stay rule-based for now.
Limits: false-allow of 0 of 22 means the true rate could still be up to about 14% (rule of three, 95%); showing 1% needs roughly 300 high-risk items. Calibration (ECE) was not measured. The set has 200 items, not a production-sized one. Laya was not tested (no CPU figure, unreplicated); a 4B model comparison is pending the download.
Threshold changes that follow: grow the example sets from real use; re-run on every new set before trusting a number.

### D1 and D5 addendum: Qwen3-4B Q4_K_M measured (2026-10-07)
Same benchmark, Test 2 (88 independent items), same constrained-JSON prompts, GPU via Vulkan with -fa off: overall 82 of 88 (93%) against 74 of 88 (84%) for Qwen3-1.7B. Intent 24 of 24, injection 23 of 24, risk 35 of 40 (88%) with 0 of 22 high-risk false-allows (95% upper bound about 14%). p50 981 ms, p95 1.25 s, worst call 8.5 s (first call after a prefix change). Real server speed: about 35 tokens/s generation against 70 to 83 for the 1.7B; the model uses roughly 3.6 GB of the 4 GB card, so only one such model fits at a time.
Decision: the 4B is the default local classifier tier on this laptop (it is much better on independent wording for about 1.2 times the latency); the 1.7B stays as the fast fallback when the 4B is not loaded. kNN stays first for cheap cases. Risk stays max(structured policy, kNN, LLM). The 8B is not tried: it does not fit in 4 GB of VRAM and CPU-only generation would be about 5 tokens/s.
