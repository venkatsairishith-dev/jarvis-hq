# Neko model matrix

Every recommendation names an exact model file, and the source for each claim is in `evidence/06-models-hardware-providers.md` (models) and `evidence/02-voice.md` (speech). Labels:

| Label | Meaning |
|---|---|
| [L] | Measured on this laptop |
| [V] | Verified on the model card or licence |
| [C] | The vendor's own claim |
| [E] | Estimate: needs a local benchmark before it is relied on |

Two rules apply throughout. A model is never called "uncensored" because of its name. Over-refusal is measured, not assumed (benchmark plan in section 5).

## 1. Always-on, model-free or tiny (every profile)

| Role | Component | Size | Cost | Licence |
|---|---|---|---|---|
| Wake word | sherpa-onnx KWS (N.E.K.O), keywords "Hey Neko" | ~33 MB model | CPU, small (needs measuring, B2) | Apache-2.0 |
| VAD | Silero VAD | ~2 MB | <1 ms per frame | MIT |
| Rules and policy | keyword rules + kernel policy | — | µs | ours |
| Typed decisions | OpenJev "Verdict" 151M (ModernBERT + GLiClass) | ~600 MB | ~150 ms on CPU [L] | Apache-2.0 [V] (English only) |
| PII finder | GLiNER-PII small, int8 ONNX | 83 MB | ~20 ms per sentence [L] | Apache-2.0 [V] |

## 2. Speech

| Role | Model | Measured here | Licence |
|---|---|---|---|
| STT English | Parakeet-TDT 0.6B v3, int8 (sherpa-onnx) | 0.04-0.1x real time [L] | CC-BY-4.0 [V] |
| STT Hindi and Telugu | AI4Bharat IndicConformer, int8 | Telugu voice note 86% word recall [L] | MIT [V] |
| TTS English | Kokoro 82M | 0.35x real time [L] | Apache-2.0 |
| TTS Hindi and Telugu | Piper voices | 0.9-1.2x real time on a cold request [L] | MIT (per-voice licences vary [V]) |

The challengers have to win a local benchmark before they replace anything. For STT that is SraVaani-1.0 (MIT) and Omnilingual 300M. For TTS it is Indic Parler-TTS and Supertonic 3 (benchmarks B5 and B6 in evidence 02).

## 3. Language models by hardware profile

The profile is chosen by `hwdetect.py`, and each profile has its own llama-swap config in `jarvis/local_models/profiles/`. Role codes:

| Code | Role |
|---|---|
| R | Command understanding and tool choice |
| P | Planning |
| D | Deep research synthesis |
| S | Outlining and summaries for decks and reports |
| C | Code |
| M | Telugu and Hindi |
| E | Embeddings |
| K | Reranker |

### cpu8: CPU only, 8 GB RAM

The model budget is about 2-3 GB, with one LLM loaded at a time.

| Role | Model (file) | Size | Speed | Notes |
|---|---|---|---|---|
| R | Qwen3.5-2B `Qwen3.5-2B-Q4_K_M.gguf` (unsloth) | 1.28 GB | ~15 tok/s [E] | Thinking off; constrained JSON; chooses among the top-5 tools |
| C | Qwen2.5-Coder-1.5B Q4_K_M | 1.04 GB | ~20 tok/s [E] | Already in use [L] |
| P, D, M | **remote** (Dahl, then the fallbacks) | — | — | Local 2B planning is not credible |
| S (offline) | Qwen3.5-2B | — | — | Short documents only |
| E | Qwen3-Embedding-0.6B Q8_0 | 639 MB | — | Or multilingual MiniLM (0.22 GB) |

### cpu16: CPU only, 16 GB RAM

| Role | Model | Size | Speed | Notes |
|---|---|---|---|---|
| R | Qwen3.5-4B Q4_K_M | 2.74 GB | ~8 tok/s [E] | Qwen3.5-2B as a fast tier |
| P, S | Ling-3.0-tiny Q4_K_M (7.9B, 1.3B active) | 4.49 GB | ~20 tok/s [E] | MoE: a big file, but fast. Challengers: granite-4.0-h-tiny (Apache-2.0), LFM2.5-8B-A1B (free only under $10M revenue) |
| D | remote; locally Qwen3.5-9B Q4_K_M | 5.68 GB | ~4 tok/s [E] | Background only |
| C | Qwen2.5-Coder-1.5B | 1.04 GB | — | — |
| E, K | Qwen3-Embedding-0.6B; bge-reranker-v2-m3 | 0.64 / ~2.3 GB | — | — |

### vram4: 4 GB VRAM, 32 GB RAM (this laptop, Vulkan, `-fa off`)

| Role | Model | Size | Placement | Speed | Status |
|---|---|---|---|---|---|
| R (first line) | rules → Verdict → Ling one-word router | — | CPU and GPU | ~150 ms / ~1 s [L] | **in use** (D20) |
| R (tool choice) | Qwen3.5-4B Q4_K_M | 2.74 GB | all on GPU | needs a benchmark (Qwen3-4B was ~35 tok/s [L]) | **candidate**: must pass the GPU-vs-CPU output check (Gated DeltaNet on Vulkan) |
| P, S, general | Ling-3.0-tiny Q4_K_M | 4.49 GB | `-ngl 99 --n-cpu-moe 16 -np 1` | 31 tok/s [L] | **in use** |
| C | Qwen2.5-Coder-1.5B Q4_K_M | 1.04 GB | GPU | 80 tok/s [L] | **in use** |
| Maths | Qwen2.5-Math-1.5B Q4_K_M | 0.92 GB | GPU | 96 tok/s [L] | **in use** |
| D (optional local deep lane) | Qwen3.5-35B-A3B UD-Q3_K_XL or Gemma-4-26B-A4B UD-Q4_K_M | 16.6 / 16.9 GB | experts in RAM | ~8-12 tok/s [E] | not downloaded (D: has 19 GB free; would need a cleanup first) |
| M | Qwen3.5-4B / Gemma-4-E4B; Sarvam-30B (22 Indian languages [C]) | 2.7 / 5.0 / ~19 GB | GPU / RAM | [E] | not downloaded; MASSIVE te-IN/hi-IN test needed |
| E | Qwen3-Embedding-0.6B Q8_0 on CPU | 0.64 GB | CPU | — | planned |
| Remote P, D | Dahl MiniMax-M2.7 through the privacy gateway | — | remote | ~11 tok/s [L] | **in use** |

### vram8plus: 8-24 GB VRAM

| VRAM | R | P / S | D | C |
|---|---|---|---|---|
| 8 GB | Qwen3.5-4B, or 9B IQ3 | Qwen3.5-9B | 35B-A3B with experts in RAM | Qwen3.5-9B |
| 12-16 GB | Qwen3.5-9B | gpt-oss-20b (harmony format) or Gemma-4-26B-A4B IQ3 | Qwen3.5/3.6-35B-A3B IQ3 | Qwen3.6-35B-A3B |
| 24 GB | Qwen3.5-9B | Gemma-4-26B-A4B Q4 | Qwen3.6-35B-A3B Q4 | same |

### remote_only

- The gateway's provider order is Dahl → Groq → OpenRouter (free models) → Cloudflare Workers AI. Each provider is used only when its key is configured and enabled.
- Routing (R) stays local: rules plus Verdict.
- Gemini's free tier is excluded for personal data, because its terms allow training on it.

## 4. Engines

| Engine | Use | Reason |
|---|---|---|
| llama.cpp b11669 (Vulkan + CPU builds) | all local LLMs | Measured; works with `-fa off` [L] |
| llama-swap v261 | on-demand loading, TTL, exclusive groups | MIT; in use [L] |
| onnxruntime | Verdict, GLiNER, KWS, speech | already present |
| not used | Ollama, vLLM, LM Studio | Ollama duplicates llama-swap and is unproven on this RDNA1 card; vLLM has no Windows or Vulkan; LM Studio is closed source |

## 5. Local benchmark plan

These run on Windows on this laptop and need no reboot. Results go to `jarvis/bench/neko/`.

1. **Qwen3.5-4B correctness on Vulkan:** the first 50 tokens on the GPU must match the CPU build. Then speed and RAM.
2. **Router and tool-choice set:** about 100 labelled requests mapped to tools, including Telugu and Hindi items. Candidates: rules+Verdict+Ling (current) vs +Qwen3.5-4B constrained.
3. **Over-refusal:** an XSTest-style set of safe prompts plus 30 owner-task prompts. The metric is the refusal rate per model, with one consistent judge.
4. **Generalist challengers:** granite-4.0-h-tiny and LFM2.5-8B-A1B vs Ling, measured on speed and on the deliverable-outline task.
5. **Embeddings:** Qwen3-Embedding-0.6B vs multilingual MiniLM, on vault retrieval and the kNN router.
6. **Speech:** benchmarks B1-B6 in evidence 02.

## 6. Fine-tuning

Fine-tuning is not part of the plan for now. The first step, if routing accuracy falls short, is a small SetFit or encoder intent model trained on MASSIVE (CC-BY-4.0, includes te-IN and hi-IN) plus owner-approved logs, on the laptop CPU. LoRA on an LLM comes only if argument-filling errors dominate after that. A Kaggle notebook is made only with evidence and the owner's go-ahead (decision D10).
