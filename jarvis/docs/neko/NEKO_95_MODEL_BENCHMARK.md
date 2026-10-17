# Neko 9.5: model benchmark (2026-10-16)

Everything was measured on Windows (the laptop): i9-9980HK, 32 GB RAM, Radeon Pro 5500M 4 GB, Vulkan, llama.cpp b11669 through llama-swap. Raw results are in `jarvis/evals/pc_control/results/decision-*.json` and `jarvis/evals/neko95/results/plans-*.json`.

## Decision model

### Accuracy

All rows run through `decide.py`: the real prompt, JSON-schema output, and the deterministic validation and completion rules.

| Model | Dev (70) | Holdout (30) | Plan cases, 5 repeats (19 × 5) | p50 / p95 latency |
|---|---|---|---|---|
| **Qwen3-4B-Instruct-2507 Q4_K_M** (`decider`) | **69/70** (98.6%)* | **30/30** | **95/95** (every case 5/5) | 3.9–4.9 s / 5.2–9.0 s** |
| Qwen3-1.7B Q4_K_M | 58/70 (82.9%) | 24/30 (80%) | — | **0.66–0.93 s** / 1.1–1.3 s |
| Earlier rounds (PC_CONTROL_BENCHMARK.md) | Qwen3-4B 64, Ling 53, Granite 50, LFM 36 | | | |

\* The one dev miss (A12) was a regression from a new typing rule ("put … on youtube" was read as typing into an app). It was found in this run and fixed, with a regression test. The dev set has not been re-run since the fix.

\** p50 4.93 s in the plan-case run, 3.91 s for dev and 4.11 s for holdout. The 3.5 s target is **not met**.

### Why the 4B model is slow here (measured)

- **Generation speed:** about 9–11 tokens/s whatever the output mode (JSON schema, JSON object or free text), so the grammar is not the cause. Prompt processing is cached and short (7–19 new tokens, about 0.3 s).
- **GPU memory:** the owner's own apps hold about 1.2 GB of the 4 GB (desktop compositor 535 MB, VS Code 357 MB, Chrome 325 MB, csrss 118 MB). The model then gets about 2.3 GB dedicated, and the rest spills into shared system memory over PCIe:

  | Context | Dedicated | Shared | Tokens/s |
  |---|---|---|---|
  | 4096 | 2,297 MB | 1,421 MB | 10.7 |
  | 2048 | 2,387 MB | 905 MB | 11.1 |

- **Settings tried:**

  | Setting | Tokens/s |
  |---|---|
  | `--no-kv-offload` | 6.4 |
  | `-ngl 28` | 9.7 |

- **Adopted:** context 2048. The prompt plus the answer is about 1,400 tokens.
- **Plan time therefore depends on output length:**
  - a 20-token plan takes about 2 s;
  - a 60-token plan takes about 6–7 s, typically when the model adds fields it does not need.

### Decision

- **Keep the 4B model.** The 1.7B model is 6× faster but misses 17–20% of requests, which a voice assistant cannot afford.
- **Latency is an open gap.** The honest next step is a rule fast path for unambiguous short commands ("open X", "pause", "minimize X"), evaluated on a **new holdout set written before the rules**. The current holdout has been seen, so it no longer counts as blind for rules.

## Vision model

- **Downloaded:** UI-Venus-1.5-2B Q4_K_M plus its F16 projector (2.0 GB, D: free 11.0 → 9.1 GB).
- **Configured:** llama-swap `vision-ui-venus-2b` (aliases `vision-ground` and `vision`), with min = max = 1024 image tokens for grounding.
- **Benchmark:** pending. See the next section of this file after `jarvis/tests/neko_vision_live.py` runs. It covers model-grounded click accuracy on Calculator, verified by Calculator's own display, and latency.
- **OCR first** (Windows.Media.Ocr, built in):
  - about 30 ms on a generated image and 3.3 s per call including PowerShell start-up;
  - reads the sample error dialog from pixels;
  - **small UI text needs a 2× capture**: at 1× the dialog's "OK" button was not found, at 2× it was (read as "0K"; matching treats 0 as O).

## Speech

No model was changed in this phase. The research (NEKO_95_RESEARCH.md section 1) points to:
- an audio front end (LiveKit APM: echo cancellation and noise suppression);
- Nemotron streaming ASR.

Both are measured only on the owner's real recordings, from `jarvis/tests/neko_voice_owner_record.py` and `jarvis/wake/eval_owner.py`.

## Decision model: 12 small models on the CPU test server (2026-10-17)

Owner away, so this ran on the Linux server, not the laptop: Xeon Gold 6244, 6 threads, llama.cpp b11669 CPU build,
one loopback `llama-server --no-webui` per model. `bench/model_compare.py` sends all 157 cases (plan_cases 17, dev 70,
holdout 30, holdout2 40) through the real `decide.py`: the same prompt, JSON schema and rules as the laptop, with
made-up names and synthetic profiles. Every model comes from `bench/model_candidates.json`, pinned by repo, revision,
file and SHA-256, and the hash is checked before use. The candidates came from the research shortlist
(`evidence/07-small-models-2026-10-17.md`).

**These latencies are CPU numbers from this server.** They are not a forecast for the laptop GPU.

Raw results: `results/models-20261017-060424.json` and `results/models-20261017-074642.json`.

| Model (Q4_K_M) | Licence | Correct | JSON valid | p50 / p95 s | tok/s | Peak RSS | Decision |
|---|---|---|---|---|---|---|---|
| **Qwen3-4B-Instruct-2507** | Apache-2.0 | **152/157** | 107/107 | 2.97 / 5.52 | 13.2 | 4.6 GB | **Keep** as `decider` |
| Qwen3.5-4B | Apache-2.0 | 146/157 | 107/107 | 2.32 / 6.29 | 12.0 | 4.5 GB | Reject (a)|
| Granite-4.2-3B | Apache-2.0 | 135/157 | 107/107 | 6.86 / 15.44 | 9.0 | 3.9 GB | Reject (b) |
| Qwen3.5-2B | Apache-2.0 | 131/157 | 107/107 | 3.97 / 7.63 | 11.3 | 2.1 GB | Reject |
| Qwen3-1.7B | Apache-2.0 | 130/157 | 107/107 | 1.02 / 1.85 | 25.2 | 2.1 GB | Keep as fast fallback (c) |
| MiniCPM5-2B | Apache-2.0 | 130/157 | 107/107 | 1.77 / 2.95 | 23.1 | 2.7 GB | Reject (d) |
| LFM2.5-2.6B | LFM Open 1.0 | 114/157 | 77/107 | 19.7 / 44.5 | 14.8 | 2.9 GB | Reject (e) |
| Qwen3.5-0.8B | Apache-2.0 | 112/157 | 106/107 | 1.53 / 5.54 | 37.4 | 1.0 GB | Reject (f) |
| MiniCPM5-1B | Apache-2.0 | 98/157 | 107/107 | 0.90 / 3.79 | 35.8 | 1.2 GB | Reject (f) |
| LFM2.5-1.2B | LFM Open 1.0 | 98/157 | 107/107 | 2.42 / 3.80 | 50.6 | 1.3 GB | Reject (f) |
| LFM2.5-8B-A1B | LFM Open 1.0 | 96/157 | 63/107 | 14.4 / 20.3 | 30.2 | 8.9 GB | Reject (e) |
| LFM2.5-350M | LFM Open 1.0 | 94/157 | 107/107 | 0.87 / 1.57 | 128.4 | 0.5 GB | Reject (f) |

Notes on the decisions:

- **(a) Qwen3.5-4B.** 6 fewer correct answers and a 32 s cold start. It is also hit by the RDNA1 Vulkan prompt-processing slowdown on the laptop's GPU (llama.cpp issue #30039).
- **(b) Granite-4.2-3B.** Less accurate and 2.3× slower than the current model.
- **(c) Qwen3-1.7B.** Same accuracy as MiniCPM5-2B, but faster and smaller. This matches the laptop round of 2026-10-16.
- **(d) MiniCPM5-2B.** No gain over Qwen3-1.7B.
- **(e) LFM2.5-2.6B and LFM2.5-8B-A1B.** Their answers were often not JSON under the schema grammar, and the run times are long. This was not investigated further: even if every one of those cases had passed, the 2.6B would reach at most about 135, still below the current model.
- **(f) The four small models.** No sub-2B model is good enough to route or decide alone. The earlier "no separate router model" decision stands.

Revisit the decider only when a model beats 152/157 on these fixed sets.
