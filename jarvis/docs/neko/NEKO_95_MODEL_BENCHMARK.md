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
