# 06 Models, hardware-aware routing, remote fallback and fine-tuning (evidence report)

Date: 2026-10-16. OS: everything in this report is about **Windows 10** (this laptop). Nothing was installed or run except a directory listing of `D:\jarvis\models\llm`.
Author: research subagent. Scope: categories (11) local LLM inference and hardware-aware routing, the model matrix, (13) optional fine-tuning, and remote provider fallback.

## 0. How to read this report

Every row carries an evidence label:

| Label | Meaning |
|---|---|
| **[L]** | Measured on this laptop (i9-9980HK, 32 GB, Radeon Pro 5500M 4 GB, llama.cpp b11669 Vulkan `-fa off`), taken from `jarvis/docs/03_DECISIONS.md` (D1, D5, D19, D20) or `jarvis/local_models/llama-swap.yaml`. |
| **[V]** | Verified today (2026-10-16) on the primary page (model card, official docs, official terms). That a page says something is verified; whether the claim is true is a separate question. |
| **[C]** | Vendor claim, usually a self-reported benchmark from a model card. Harnesses, prompts and thinking settings differ between vendors, so these are not comparable across families. |
| **[S]** | Secondary source (blog, aggregator, forum, preprint summary). |
| **[E]** | My estimate. CPU speed estimates scale the local measurement Qwen3-1.7B Q4_K_M = 21.2 tok/s CPU-only (8 threads) [L] by the bytes of weights read per generated token (about 23 GB/s effective). They ignore compute limits and prompt processing. **Every [E] speed needs a local benchmark.** |
| **[U]** | Unverified: could not reach a primary page. |

No model in this report is described as "uncensored". Refusal behaviour is discussed only where a benchmark documents it (section 6).

---

## 1. Baseline already measured here (do not re-derive)

| Item | Result | Label |
|---|---|---|
| Qwen3-1.7B Q4_K_M | CPU 21.2 tok/s gen, 144.6 pp; Vulkan all layers 89.9 gen, 660 pp | [L] D5 |
| Qwen3-4B Q4_K_M | ~35 tok/s on Vulkan; 93% (82/88) on independent decision set vs 84% for 1.7B; uses ~3.6 GB of the 4 GB card | [L] D1 addendum |
| Ling-3.0-tiny Q4_K_M (7.9B/1.3B active, MIT), file 4.49 GB | 31 tok/s with `--n-cpu-moe 16`; 11/12 routing with a clear prompt | [L] D19, local file listing |
| Qwen2.5-Coder-1.5B Q4_K_M (1.04 GB) | 80 tok/s | [L] |
| Qwen2.5-Math-1.5B Q4_K_M (0.92 GB) | 96 tok/s | [L] |
| SmolLM2-360M | 203 tok/s but 5/12 routing: rejected | [L] |
| OpenJev Verdict 151M (ModernBERT + GLiClass) | 10/12 routing, ~150 ms, no GPU; adviser decision 11/12 fresh | [L] D20 |
| Embedding kNN (bge-small-en-v1.5) | beat the 1.7B LLM on independent wording (88% vs 84%) at ~40x lower latency | [L] D1 |
| Vulkan flash attention on RDNA1 | garbage after first token: always `-fa off` | [L] |
| 4 server slots | ~30% slower than 1 | [L] |
| Remote: Dahl MiniMax-M2.7 | ~11 tok/s; GLM-5.3-Flash rationed (429); DeepSeek-V4-Flash loops | [L] D11, D19 |

Design consequences already decided (D1): a natural-language classifier is advisory only, risk = max(structured policy, kNN, LLM), nothing executes on classification alone. This report does not change that.

---

## 2. Candidate fact sheet (verified where possible)

Sizes are the unsloth GGUF listings unless stated. "Active" = parameters used per token.

| Model (HF repo) | Licence | Total / active | Context | GGUF file (size) | Tool-use numbers (all [C] unless noted) | Notes |
|---|---|---|---|---|---|---|
| Qwen/Qwen3.5-0.8B | Apache-2.0 [V] | 0.8B dense | 262K [V] | unsloth/Qwen3.5-0.8B-GGUF `Qwen3.5-0.8B-Q4_K_M.gguf` (533 MB) [V] | BFCL-V4 25.3, TAU2 11.6 | Non-thinking by default; card warns of thinking loops; card recommends it mainly for fine-tuning/prototyping [V] |
| Qwen/Qwen3.5-2B | Apache-2.0 [V via 0.8B card + [S]] | 2B dense | 262K | `Qwen3.5-2B-Q4_K_M.gguf` (1.28 GB) [V] | BFCL-V4 43.6, TAU2 48.8 | Hybrid Gated DeltaNet + attention, vision encoder (text-only use needs no mmproj) |
| Qwen/Qwen3.5-4B | Apache-2.0 [V] | 4B dense | 262K (1M YaRN) [V] | `Qwen3.5-4B-Q4_K_M.gguf` (2.74 GB) [V]; mmproj-F16 672 MB | BFCL-V4 50.3, TAU2 79.9 | Thinking ON by default; disable with `enable_thinking: false`; `/nothink` not supported [V]. 3 of 4 layers are linear-attention (Gated DeltaNet), so KV cache is small [V architecture] |
| Qwen/Qwen3.5-9B | Apache-2.0 [S] | 9B dense | 262K | `Qwen3.5-9B-Q4_K_M.gguf` (5.68 GB), `UD-IQ3_XXS` 4.02 GB [V] | BFCL-V4 66.1, TAU2 79.1 (from 4B card) | Does not fit 4 GB VRAM |
| Qwen/Qwen3.5-35B-A3B | Apache-2.0 [V] | 35B / ~3B | 262K [V] | `Q4_K_M` 22 GB, `UD-Q3_K_XL` 16.6 GB, `UD-IQ3_XXS` 13.1 GB [V] | BFCL-V4 67.3, TAU2 81.2 | 256 experts, 8+1 active; 201 languages claimed [V/C] |
| Qwen/Qwen3.6-35B-A3B | Apache-2.0 [V] | 35B / 3B | 262K [V] | not checked | TAU3-Bench 67.2; no BFCL on card [V] | April 2026; same hybrid architecture as 3.5 [V]. No sub-10B Qwen3.6 found [S] |
| Qwen3.8-27B | not checked | 27B dense | ? | ? | ? | Exists as a hosted model on Groq, Cerebras, Cloudflare (section 9) [V]; open weights status [U] |
| google/gemma-4-E2B-it | Apache-2.0 [S, consistent with E4B card] | 2.3B effective | 128K | `gemma-4-E2B-it-Q4_K_M.gguf` (3.11 GB) [V] | not checked | Per-layer embeddings make the file large for its "effective" size |
| google/gemma-4-E4B-it | Apache-2.0 [V] | 4.5B effective (8B with embeddings) [V] | 128K [V] | `gemma-4-E4B-it-Q4_K_M.gguf` (4.98 GB) [V] | Tau2 42.2 [V/C] | Text, image, audio (30 s), video; 35+ languages out of the box, 140+ pretraining [V/C]; native function calling [V/C] |
| google/gemma-4-26B-A4B-it | Apache-2.0 [V] | 25.2B / 3.8B [V] | 256K [V] | `UD-Q4_K_M` 16.9 GB, `UD-Q3_K_XL` 12.9 GB, `UD-IQ3_XXS` 11.4 GB [V] | Tau2 68.2, MMLU-Pro 82.6 [V/C] | No audio on this size [V] |
| ibm-granite/granite-4.0-h-tiny | Apache-2.0 [V] | 7B / 1B [V] | 128K [V] | ~4 GB Q4 [E] | BFCL v3 57.65 [V/C] | 36 Mamba2 + 4 attention layers [V]; IBM notes Mamba2 support in llama.cpp may be less optimised [S] |
| ibm-granite/granite-4.0-h-micro | Apache-2.0 [V] | 3B dense hybrid [V] | 128K [V] | ~2 GB Q4 [E] | BFCL v3 57.56, IFEval avg 84.3 [V/C] | 12 languages, no Hindi/Telugu [V] |
| ibm-granite/granite-4.2-3b | Apache-2.0 [V] | 3B dense (safetensors says 4B) [V] | 128K, 512K ext [V] | ~2 GB Q4 [E] | BFCL v4 52.41 [V/C] | Released 2026-08-25 [V]; plain transformer (not Mamba) [V]; thinking on by default, `enable_thinking=False` [V] |
| LiquidAI/LFM2.5-8B-A1B | LFM Open License v1.0 [V] | 8.3B / 1.5B [V] | 128K [V] | GGUF repo exists [V]; ~5 GB Q4 [S] | BFCLv3 64.4-64.8, BFCLv4 48.5-49.7 (two tables disagree), Tau2 Telecom 88.1, Retail 39.8 [V/C] | Licence: free commercial use only under US$10M annual revenue; not OSI-open [V liquid.ai/docs]. 10 languages, no Indic [V] |
| LiquidAI/LFM2-8B-A1B | LFM Open v1.0 [V] | 8.3B / 1.5B | 32K [V] | unsloth/LFM2-8B-A1B-GGUF | not listed | Card says not for knowledge-intensive or coding tasks [V] |
| inclusionAI/Ling-3.0-tiny | MIT [V] | 7.9B / 1.3B [V] | 256K [V] | local Q4_K_M 4.49 GB [L] | benchmark table only as image | Thinking ON by default [V]; tool parser documented only for vLLM/SGLang (`ling3`) [V]; llama.cpp tool-call parsing [U] |
| HuggingFaceTB/SmolLM3-3B | Apache-2.0 [V] | 3B | 64K (128K YaRN) [V] | ~1.9 GB Q4 [E] | "BFCL 92.3" no-think [V/C] (subset not stated; not comparable to BFCL-V4 numbers) | 6 European languages; no Indic [V] |
| microsoft/Phi-4-mini-instruct | MIT [V] | 3.8B | 128K [V] | ~2.5 GB Q4 [E] | none | No Hindi/Telugu in language list [V]; card warns it may hallucinate function names [V]. Feb 2025, now dated |
| baidu/ERNIE-4.5-21B-A3B-PT | Apache-2.0 [V] | 21B / 3B [V] | 131K [V] | GGUF quants exist [V] | none | English and Chinese only [V] |
| openai/gpt-oss-20b | Apache-2.0 [V] | 21B / 3.6B [V] | (not checked) | unsloth `gpt-oss-20b-Q4_K_M.gguf` 11.6 GB, Q8_0 12.1 GB [V] | not checked | Must use harmony format [V]; reasoning effort low/medium/high [V]. Tool-call parse failures seen here under Ollama (D11) [L] |
| sarvamai/sarvam-30b | Apache-2.0 [V] | 32B / 2.4B non-embedding active [V] | 65K used in benchmarks [V] | sarvamai/sarvam-30b-gguf (Q4_K_M ~19 GB [S]) | Tau2 avg 45.7 [V/C] | "22 Indian languages" claimed [V/C]; llama.cpp `sarvam_moe` support merged 2026-05-09 (PR #20275) [V], so b11669 should include it [E] |
| sarvamai/sarvam-1 | Sarvam **non-commercial** licence [V] | 2B (3B in metadata) | 8K | - | - | Base model only, not instruct [V]. Not suitable |
| krutrim-ai-labs/Krutrim-2-instruct | Krutrim Community Licence v1.0 (custom; commercial limit at >1M MAU) [S from licence file] | 12B dense | 128K | - | - | Mistral-NeMo based; 22 Indic languages [S] |

**Architecture risk for this GPU (important).** Qwen3.5/3.6 use Gated DeltaNet. In March 2026 the Vulkan backend had no GATED_DELTA_NET shader and fell back to CPU (issue #20354, build b8234) [V]; SSM ops also lacked Vulkan shaders with corrupted output on Intel Arc (issue #19957) [S]. A Vulkan GATED_DELTA_NET op was later added (PR #20334, follow-up #20662) [S, commit pages]. The local build b11669 is far newer, so the op is probably present [E], but correctness on RDNA1 with `-fa off` must be checked the same way flash attention was (compare first 50 tokens with the CPU build).

---

## 3. Model matrix by hardware profile

Roles: **R** command understanding / intent / tool selection (structured JSON); **P** planning; **D** deep research synthesis (long context); **S** outlining/summarising for slides and PDFs; **C** code; **M** multilingual (Telugu/Hindi) understanding; **E** embeddings; **K** reranker.

Common to every profile (no LLM needed): keyword rules + structured policy (exists), Verdict 151M (~150 ms CPU) [L], GLiNER-PII small int8 (83 MB) [L]. Verdict's backbone ModernBERT was trained on English text and code and its card warns of lower performance on other languages [V], so Verdict must not be trusted on Telugu/Hindi input (section 7).

RAM budget assumption [E]: Windows 10 + N.E.K.O + browser take about 4-6 GB; the rest is the model budget.

### 3.1 CPU-only, 8 GB RAM (model budget about 2-3 GB, one LLM loaded at a time)

| Role | Model and file | Licence | Params (active) | File | RAM need [E] | CPU tok/s | Notes |
|---|---|---|---|---|---|---|---|
| R | Qwen3.5-2B `Qwen3.5-2B-Q4_K_M.gguf` (unsloth) | Apache-2.0 | 2B | 1.28 GB | ~1.8 GB at 8K ctx | ~15-17 [E] | Non-thinking, GBNF/json_schema-constrained, choose among top-5 retrieved tools only |
| R (ultra-low) | Qwen3.5-0.8B Q4_K_M | Apache-2.0 | 0.8B | 533 MB | ~0.9 GB | ~30-40 [E] | Expect weak tool use (BFCL-V4 25.3 [C]); use only as constrained classifier fallback; SmolLM2-360M precedent was 5/12 [L] |
| P, D, S | **Remote** (section 9) | - | - | - | - | - | Local 2B planning is not credible; summaries of short docs can use the 2B |
| S (short docs, offline) | Qwen3.5-2B (same file) | Apache-2.0 | 2B | - | - | ~15 [E] | Chunk to <=8K tokens |
| C | Qwen2.5-Coder-1.5B Q4_K_M | Apache-2.0 | 1.5B | 1.04 GB | ~1.5 GB | ~20-25 [E] | Already in use [L] |
| M | Remote first; locally Qwen3.5-2B (201 languages claimed [C]) | - | - | - | - | - | Untested on Telugu; measure with MASSIVE te-IN/hi-IN (section 7) |
| E | Qwen3-Embedding-0.6B `Q8_0` GGUF (official Qwen repo) | Apache-2.0 | 0.6B | 639 MB | ~0.8 GB | n/a | `llama-server --embedding --pooling last` [V]. Lighter alternative: paraphrase-multilingual-MiniLM-L12-v2 via FastEmbed (0.22 GB, Apache-2.0) [V] |
| K | none, or bge-reranker-base (FastEmbed, MIT) for English | MIT | ~0.3B | - | - | - | Rerank only in deep research, which is remote on this profile |

### 3.2 CPU-only, 16 GB RAM (model budget about 8-10 GB)

| Role | Model and file | Licence | Params (active) | File | RAM need [E] | CPU tok/s | Notes |
|---|---|---|---|---|---|---|---|
| R | Qwen3.5-4B `Qwen3.5-4B-Q4_K_M.gguf` | Apache-2.0 | 4B | 2.74 GB | ~3.3 GB | ~8 [E] | Highest small-model TAU2 claim (79.9 [C]); qwen3-family 4B already best here on decisions (93%) [L] |
| R (fast) | Qwen3.5-2B Q4_K_M | Apache-2.0 | 2B | 1.28 GB | ~1.8 GB | ~15-17 [E] | Keep loaded; 4B on demand |
| P, S | Ling-3.0-tiny Q4_K_M (current) **or** LFM2.5-8B-A1B Q4 **or** granite-4.0-h-tiny Q4 | MIT / LFM Open (<$10M revenue) / Apache-2.0 | 7.9-8.3B (1-1.5B) | 4.5 / ~5 / ~4 GB | ~5-6 GB | ~20-25 [E]; LFM2.5-8B-A1B measured 13.8 on a 4-thread i5-8350U DDR4-2400 [S] | MoE: big file, fast decode. Prefer Ling (MIT, already in use) unless the benchmark says otherwise |
| D | Qwen3.5-9B `UD-Q4_K_XL` 5.97 GB or `Q4_K_M` 5.68 GB | Apache-2.0 | 9B | 5.68 GB | ~6.5 GB | ~4 [E] | Slow; batch/background only. Otherwise remote |
| C | Qwen2.5-Coder-1.5B (current) | Apache-2.0 | 1.5B | 1.04 GB | 1.5 GB | ~20 [E] | |
| M | Qwen3.5-4B, test vs Gemma-4-E4B (`Q4_K_M` 4.98 GB, Apache-2.0) | Apache-2.0 | 4B / 4.5B eff. | 2.74 / 4.98 GB | 3.3 / 5.5 GB | ~8 / ~6 [E] | Both claim broad multilingual coverage [C]; neither has published Telugu numbers found |
| E | Qwen3-Embedding-0.6B Q8_0, or bge-m3 (MIT, 8K tokens, dense+sparse+multi-vector) [V] | Apache-2.0 / MIT | 0.6B / ~0.57B | 639 MB / ~2.3 GB fp32 | ~1-2.5 GB | n/a | Qwen3-Embedding MTEB-multilingual mean 64.33 [V/C]; EmbeddingGemma-300m 61.15 [V/C] but Gemma terms + gated download [V] |
| K | BAAI/bge-reranker-v2-m3 | Apache-2.0 [V] | 0.6B | ~2.3 GB fp32 | ~2.5 GB | n/a | Multilingual [V]. Qwen3-Reranker-0.6B GGUF has known llama.cpp scoring problems [S] |

### 3.3 4 GB VRAM + 32 GB RAM (this laptop, Vulkan, `-fa off`)

| Role | Model and file | Licence | Params (active) | File | Placement | Speed | Notes |
|---|---|---|---|---|---|---|---|
| R | **Qwen3.5-4B Q4_K_M** (candidate to replace Qwen3-4B) | Apache-2.0 | 4B | 2.74 GB | all layers on GPU, 8K ctx | Qwen3-4B was ~35 tok/s [L]; 3.5 needs benchmark | Must pass the GDN-on-Vulkan correctness check first |
| R (fast, no GPU) | Verdict + multilingual kNN (exists / to add) | Apache-2.0 | 151M | - | CPU | ~150 ms [L] | First line, as in D20 |
| P, S, general | Ling-3.0-tiny (current) | MIT | 7.9B (1.3B) | 4.49 GB | `-ngl 99 --n-cpu-moe 16` | 31 tok/s [L] | Keep. Challengers: LFM2.5-8B-A1B, granite-4.0-h-tiny (same placement trick) |
| D (local deep lane, optional) | Qwen3.5-35B-A3B `UD-Q3_K_XL` (16.6 GB) or Gemma-4-26B-A4B `UD-Q4_K_M` (16.9 GB) | Apache-2.0 | 35B (3B) / 25B (3.8B) | 16.6-16.9 GB | attention on GPU, experts in RAM (`--n-cpu-moe` high) | ~8-12 [E]; needs benchmark | Leaves ~10 GB RAM for Windows + everything else: tight. `Q4_K_M` 22 GB for the Qwen is too tight. Use only when Dahl is out and the owner accepts slowness |
| C | Qwen2.5-Coder-1.5B (current, 80 tok/s [L]); challenger Qwen3.5-4B | Apache-2.0 | | | GPU | | |
| M | Qwen3.5-4B / Gemma-4-E4B on GPU; Sarvam-30B Q4_K_M (~19 GB [S]) with experts in RAM as the Indic specialist | Apache-2.0 | 32B (2.4B) for Sarvam | ~19 GB | experts on CPU | ~10-14 [E] | Sarvam is the only open Apache-2.0 model found that is built for 22 Indian languages [V/C]. RAM-heavy: cannot co-reside with the 35B lane |
| E | Qwen3-Embedding-0.6B Q8_0 | Apache-2.0 | 0.6B | 639 MB | CPU (keep the GPU for the LLM) | - | |
| K | bge-reranker-v2-m3 | Apache-2.0 | 0.6B | | CPU | | |

### 3.4 Bigger GPU, 8-24 GB VRAM

| VRAM | R | P / S | D (long context) | C | M | Notes |
|---|---|---|---|---|---|---|
| 8 GB | Qwen3.5-4B Q4_K_M on GPU, or Qwen3.5-9B `UD-IQ3_XXS` (4.02 GB) / `Q4_K_M` (5.68 GB) | Qwen3.5-9B | Qwen3.5-35B-A3B with experts in RAM | Qwen3.5-9B | Gemma-4-E4B or Qwen3.5-9B; Sarvam-30B experts in RAM | Speeds [E] unknown; GDN kernels per backend matter |
| 12-16 GB | Qwen3.5-9B Q4 full GPU | gpt-oss-20b (Q4_K_M 11.6 GB, Apache-2.0) or Gemma-4-26B-A4B `UD-IQ3_XXS` 11.4 GB | Qwen3.5/3.6-35B-A3B `UD-IQ3_XXS` 13.1 GB | Qwen3.6-35B-A3B (agentic coding claims [S]) | Sarvam-30B partial offload | gpt-oss needs harmony-aware parsing; test tool calls in llama.cpp before trusting (D11 failures were in Ollama) |
| 24 GB | Qwen3.5-9B | Gemma-4-26B-A4B `UD-Q4_K_M` 16.9 GB | Qwen3.6-35B-A3B `UD-Q4_K_XL`-class (~22 GB) | same | Sarvam-30B Q4_K_M (~19 GB) full GPU | vLLM becomes relevant only on Linux + NVIDIA (section 8) |

### 3.5 Remote

See section 9. Role mapping: P and D to Dahl MiniMax-M2.7 (public lane only, through the privacy gateway) then the next free provider; R never remote (latency and privacy); M remote fallback for Telugu/Hindi generation where local quality fails.

---

## 4. Router design (hardware-aware, reuse-first)

Everything here reuses components already present (llama-swap, llama.cpp, Verdict, GLiNER, privacy gateway, FastEmbed or llama-server embeddings). No new repository is needed.

1. **Detect hardware at start** (section 8.3) and pick one of the five profiles; render the llama-swap YAML from a per-profile template (the current file is the "4 GB VRAM" template). llama-swap supports `groups` with `swap`/`exclusive`/`persistent`, `ttl`, `hooks.preload`, `matrix` for allowed concurrent sets, `concurrencyLimit`, and `peers` (remote OpenAI-compatible endpoints with an `apiKey` injected from an env var) [V config.example.yaml].
2. **Tier 0, deterministic**: keyword rules + the structured policy (exists). Script detection: Telugu block U+0C00-0C7F, Devanagari U+0900-097F; mark the request non-English.
3. **Tier 1, typed choice** (English only): Verdict picks code/math/general and "needs deep" with the D20 thresholds (0.5, 0.77) [L].
4. **Tier 2, multilingual embedding kNN** over labelled exemplars (and over tool descriptions, to retrieve the top-5 candidate tools). Same embedding model as memory retrieval, so it costs nothing extra. This tier handles Telugu/Hindi and anything Verdict scores below threshold. Evidence that this works: kNN beat the 1.7B LLM here [L]; a 22M SetFit model reached ~91% vs ~69% for zero-shot gpt-oss-20b on MASSIVE intents in a 2026 preprint [S].
5. **Tier 3, small local LLM with constrained output**: only to choose among the top-5 retrieved tools and fill arguments, using llama-server `json_schema`/GBNF, thinking off. Validate the arguments against the tool's JSON Schema; one repair retry; otherwise escalate. Small models are known to fail on output format and on large or polymorphic schemas, and hard constraints can change answers of sub-3B models [S, section 5], so keep schemas small and measure.
6. **Tier 4, remote** (planning, deep research, long synthesis) through the privacy gateway at 127.0.0.1:48950 (D19). If remote endpoints are added as llama-swap `peers`, the gateway must sit **in front of** llama-swap (client -> gateway -> llama-swap), otherwise the peer route bypasses redaction. Simpler: keep remote providers out of llama-swap and in the gateway's upstream list.
7. **Authority unchanged**: every action still goes through the kernel policy and approvals (D1). The router only chooses who answers.

---

## 5. Tool-calling reliability of small models (evidence and caveats)

| Evidence | What it says | Label |
|---|---|---|
| Vendor BFCL-V4: Qwen3.5 0.8B 25.3, 2B 43.6, 4B 50.3, 9B 66.1, 35B-A3B 67.3; Granite-4.2-3B 52.41; LFM2.5-8B-A1B ~49 | Steep drop below 4B | [V/C] model cards |
| Vendor TAU2: Qwen3.5-4B 79.9 vs Gemma-4-E4B 42.2 vs Gemma-4-26B-A4B 68.2 | Different harnesses (Qwen applied airline fixes from another vendor's system card) | [V/C] |
| BFCL official leaderboard last updated 2025-12-16; I could not read its table | No independent small-model numbers verified | [V] page, table [U] |
| Kavathekar et al., "Small Models, Big Tasks" (arXiv 2504.19277, EASE 2025) | Small models improve zero-shot -> few-shot -> fine-tuned, but "struggle significantly with adhering to the given output format" | [S] |
| "The Constraint Tax" (arXiv 2605.26128, 2026) | For sub-3B models, hard output constraints can change the answer itself, not just its format | [S] summary only |
| "When Correct Isn't Usable" (arXiv 2605.02363, 2026, 7-9B models) | Task accuracy up to 85% with 0% usable output under naive prompting | [S] |
| SLM agent survey (arXiv 2510.03847) | Tool-use accuracy depends more on argument correctness and schema adherence than on parameter count | [S] |
| Local: gpt-oss tool calls failed in Ollama's parser; MiniMax and GLM correct on Dahl | Parser/template is a failure point in itself | [L] D11 |

Caveats: BFCL and TAU2 numbers are self-reported, often in thinking mode, with vendor prompts; they do not predict performance on our tool catalogue. Conclusion: measure on our own tool set (section 11) with thinking off and constrained decoding on.

## 6. Refusal and over-refusal evidence

| Source | Finding | Label |
|---|---|---|
| OR-Bench (ICML 2025, arXiv 2405.20947) | 80K over-refusal prompts, ~1K hard subset, 600 toxic | [S] |
| Refusal-compliance audit (arXiv 2605.05427) | XSTest safe-prompt refusal: Llama-3-8B 4.22%, Qwen-2.5-7B 0.44%; several models over-refuse more on XSTest than OR-Bench (surface wording triggers) | [S] |
| FalseReject (arXiv 2505.08054) | LLM-judged refusal: Llama-3.1-8B 8.8 XSTest / 3.5 OR-Bench; Qwen-2.5-7B 8.8 / 2.7; Mistral-7B-v0.3 7.2 / 0.7 | [S] |
| ORFuzz (arXiv 2508.11222) | Phi-3.5: 0.00% XSTest benign vs 14.18% OR-Bench benign | [S] |
| Judge choice | LlamaGuard-3 judging understated over-refusal vs string matching on Gemma fine-tunes | [S] |

No over-refusal numbers were found for Qwen3.5, Gemma 4, Granite 4.x, LFM2.5 or Ling-3.0. Local observation: MiniMax spent its whole budget deliberating whether a password task was allowed (D20) [L]. Action: run an XSTest-style safe-prompt set (and our own owner-task set) through each candidate as part of the local benchmark; use one judge consistently.

## 7. Multilingual (Telugu / Hindi)

| Item | Finding | Label |
|---|---|---|
| Verdict / ModernBERT | English + code training; lower performance for other languages | [V] |
| Qwen3.5 | 201 languages and dialects claimed | [V/C] |
| Gemma 4 | 35+ languages out of the box, 140+ in pretraining | [V/C] |
| Phi-4-mini, SmolLM3, LFM2.5, Granite 4.x, ERNIE 4.5 | No Hindi or Telugu in their language lists | [V] |
| Sarvam-30B | Apache-2.0, 22 Indian languages claimed, llama.cpp support merged 2026-05-09 | [V/C] |
| TeluguEval (LoResLM 2026) | English-centric models drop 30-40+ points in Telugu, especially maths/science; regional models hold up better | [S] |
| MASSIVE (AmazonScience/massive) | CC-BY-4.0, 60 intents, 55 slots, includes te-IN and hi-IN, ~11.5K train / 3K test utterances per language | [V] |
| Embeddings | Qwen3-Embedding-0.6B (100+ languages), bge-m3 (100+), multilingual-e5-large (MIT, FastEmbed-supported), paraphrase-multilingual (FastEmbed) | [V] |

Recommendation: route Telugu/Hindi by script detection to the multilingual kNN tier; evaluate on MASSIVE te-IN/hi-IN test splits; use Sarvam-30B or remote only where generation quality is needed.

## 8. Inference and serving on Windows

### 8.1 Engines

| Engine | Status for this box | Label |
|---|---|---|
| llama.cpp (Vulkan/CPU) | Works (b11669, `-fa off`); `--list-devices` prints each device with total and free MiB, e.g. `Vulkan0: ... (6144 MiB, 5136 MiB free)` [S issue #16659]. `--fit` (default on in late-2025 builds) auto-sizes unset `-ngl`/MoE placement; community reports mixed, and it ignores mmproj size [S]. Keep explicit `--n-cpu-moe` where measured | [L]/[S] |
| llama-swap (MIT) | Already in use; adds `peers`, `matrix`, `hooks.preload`, `persistent` groups, `concurrencyLimit` | [V] |
| Ollama | Official GPU doc now says "Vulkan is enabled by default when the backend is installed"; disable with `OLLAMA_VULKAN=0`. Windows ROCm list covers RDNA3 only (no gfx1012/5500M) | [V] docs.ollama.com/gpu |
| LM Studio | Free for home and work since 2025-07-08; closed-source app (Element Labs terms) | [V] blog, terms [S] |
| KoboldCpp | AGPL-3.0 (bundled ggml/llama.cpp MIT); `koboldcpp-nocuda.exe` = CPU + Vulkan; OpenAI-compatible API on :5001; also Whisper, TTS, image gen | [V] |
| vLLM | Not for this box: Linux-only officially, no native Windows; WSL2/Docker paths need NVIDIA (Docker Model Runner: compute capability >= 8.0); no Vulkan backend; RDNA1 not covered by ROCm on Windows | [S] |

Recommendation: stay on llama.cpp + llama-swap. Do not add Ollama: its default flash-attention behaviour on this RDNA1 card is untested, and it would duplicate llama-swap. KoboldCpp's AGPL is fine for local use but adds nothing we need.

### 8.2 Memory budgeting and unloading

- One specialist on the 4 GB GPU at a time (`exclusive` group) [L]; TTL 300 s [L].
- MoE models: keep attention + shared weights on GPU and push expert tensors to RAM (`--n-cpu-moe N`) [L]. RAM need is roughly the file size plus KV cache; Qwen3.5's linear-attention layers keep KV small [V architecture].
- Big local-deep models (16-19 GB) must be in their own exclusive group with the generalist so that two never coexist in RAM; add a pre-load RAM check (psutil `available` > file size + 3 GB) [E].
- Embedding + reranker on CPU in a `persistent` group so they are not evicted by LLM swaps [V feature].
- Use `-np 1` (4 slots cost ~30%) [L].

### 8.3 Portable hardware detection (Python)

| Need | Method | Caveat |
|---|---|---|
| RAM total/available | `psutil.virtual_memory()` | none significant |
| CPU model, cores, AVX2/AVX-512 flags | `py-cpuinfo` (`cpuinfo.get_cpu_info()`), `psutil.cpu_count(logical=False)` | py-cpuinfo is slow (~1 s) and updates rarely [U]; cache the result |
| GPU VRAM on Windows | DXGI `IDXGIAdapter3::QueryVideoMemoryInfo` returns current Budget and CurrentUsage per local/non-local segment [V]; `DXGI_ADAPTER_DESC.DedicatedVideoMemory` for total | Needs a COM binding (ctypes or comtypes) |
| GPU VRAM via WMI | `Win32_VideoController.AdapterRAM` is `uint32`, so it truncates above ~4 GB [V]; read registry `HardwareInformation.qwMemorySize` (64-bit) under the Display class key instead [S] | WMIC is deprecated; use CIM [S] |
| What llama.cpp can actually use | run the bundled `llama-server --list-devices` and parse `VulkanN: name (total MiB, free MiB)` | Most reliable for our purpose because it is the runtime's own view [S] |

Profile rule [E]: no Vulkan device or <3 GB free -> CPU profile by RAM (<12 GB -> 8 GB profile; else 16 GB profile); 3-6 GB -> "4 GB VRAM" profile; 6-12 GB -> 8 GB column; >=12 GB -> 12-24 GB columns.

## 9. Remote providers (optional fallback) - verified 2026-10-16 unless marked

Free tiers change often; re-check before relying on any number.

| Provider | Free offer | Limits | Data use / privacy | Multiple accounts/keys | Label |
|---|---|---|---|---|---|
| **Dahl** (inference.dahl.global) | "First 100 million tokens free"; models MiniMax M2.7, DeepSeek Flash, GLM 5.3 Flash, Qwen 3.8 Flash | "reasonable rate, concurrency, context, spending limits" (no numbers); bypassing them prohibited | Terms v2.0 (2026-09-08): processing only to deliver the service, "not ... a general-purpose training licence"; requests may be routed to independent operators in a decentralised network | Agents and API automation allowed; separate keys per app encouraged; multiple identities for repeated introductory benefits prohibited; another key creates no new entitlement | [V] homepage + terms |
| OpenRouter `:free` models | yes | 20 RPM; 50 RPD if <10 credits ever purchased, 1,000 RPD if >=10 | Some providers may store/train on inputs; separate privacy settings for free and paid; can exclude training providers | "Making additional accounts or API keys will not affect your rate limits"; Terms (2026-08-31) prohibit multiple accounts to bypass limits | [V] limits page, terms, logging page |
| Groq | Free plan exists; limits are per organisation | Page table (base Developer-plan values, free values shown in console): gpt-oss-20b / gpt-oss-120b / qwen3.8-27b 30 RPM, 1K RPD, 8K TPM, 200K TPD | No retention by default; temporary logs up to 30 days for abuse/reliability; ZDR toggle available; training not stated | Limits at organisation level, not per key | [V] rate-limits + your-data pages; free-plan exact numbers [U] |
| Google Gemini API | Free tier on many models (Gemini 3.8 Flash, 3.5/3.1 Flash-Lite, 2.5 Pro/Flash/Flash-Lite, Gemma 4, Embedding 2, Live/TTS) | Per project, not per key; numbers only in AI Studio; "not guaranteed" | Free tier: content used "to provide, improve, and develop" products; human reviewers may read it; "Do not submit sensitive, confidential, or personal information to the Unpaid Services"; EEA/UK/CH users must be served by paid only | No clause found in Gemini API terms; limits are per project | [V] pricing + rate-limits pages (updated 2026-10-15), terms (updated 2026-04-28) |
| Cerebras | Free **trial**: $5 credits, expire after 30 days | gpt-oss-120b and qwen-3.8-27b at 5 RPM, 1M TPD | Support article reportedly says no prompt retention [S]; training not addressed | - | [V] rate-limits page; privacy [S] |
| Mistral La Plateforme (Experiment plan) | Free tier exists | Reported 1 req/s, 500K TPM, 1B tokens/month [S] | Experiment plan inputs used for training unless opted out in Admin Console privacy panel [S, help-center summary] | - | [U] official docs returned 404 |
| GitHub Models | **Retired 2026-07-30**; playground, catalogue, API and BYOK no longer available | - | - | - | [V] GitHub docs |
| Cloudflare Workers AI | 10,000 Neurons/day on Free and Paid plans, reset 00:00 UTC; Free plan has no overage, requests fail | gpt-oss-20b: 18,182 in / 27,273 out Neurons per M tokens (about 0.37M output tokens/day free [E]); qwen3.8-27b: 290,909 out (about 34K output tokens/day [E]) | not checked | - | [V] pricing page (2026-10-09) |
| Hugging Face Inference Providers | **Free users: no monthly credits** (must buy); PRO $2.00/month | pay-as-you-go | provider-specific | - | [V] pricing doc |
| NVIDIA build.nvidia.com (NIM API trial) | Trial is rate-limited, not credit-based | ~40 RPM, varies by model; moderators say no way to raise it on the trial tier; production is steered to self-hosted NIM | not checked | - | [S] forum threads; official page [U] |

**Key rotation.** Using several keys or accounts to multiply free quota is explicitly prohibited or ineffective at OpenRouter (terms + limits page), Dahl (terms sections 5, 7, 8) and Groq/Gemini (limits are per organisation/project) [V]. Recommendation: one key per provider (several keys only to separate apps, as Dahl allows), and on 429 move to the **next provider**, never the next key.

**Recommended fallback order** (public lane only, all through the privacy gateway):
1. Dahl MiniMax-M2.7 -> Dahl GLM-5.3-Flash (current D11 policy).
2. Groq gpt-oss-120b / qwen3.8-27b (fast; no retention by default; enable ZDR).
3. OpenRouter `:free` with "exclude providers that train" set for free models (buy 10 credits only if the owner chooses; 50 RPD otherwise).
4. Cloudflare Workers AI gpt-oss-20b (small daily budget, hard stop on Free plan).
5. Local models (always available).
Not recommended: Gemini free tier for anything personal (training + human review by terms); Cerebras (30-day trial only); GitHub Models (retired); HF Inference Providers (no free credits); NVIDIA trial (prototyping only, terms not verified).

## 10. Fine-tuning (optional, category 13)

| Question | Evidence | Label |
|---|---|---|
| Is LoRA on a small LLM router worth it? | Not first. The router's measured failures are wording/generalisation, which kNN handled better than an LLM [L]; small fine-tuned encoders beat zero-shot LLMs on intent (SetFit 22M ~91% vs gpt-oss-20b zero-shot ~69% on MASSIVE; few-shot LLM numbers in that preprint are extrapolated, not measured) [S]; Parikh et al. (ACL 2023 Industry) found parameter-efficient fine-tuning best even with 1 example per intent [S]; Kavathekar: fine-tuning gives the best function-calling results for small models [S] | mixed |
| Cheapest worthwhile step | Fine-tune the encoder side (SetFit-style or GLiClass head) on MASSIVE (CC-BY-4.0, te-IN/hi-IN included) + owner logs; trains on the laptop CPU, no notebook needed [E] | [E] |
| When LLM LoRA is justified | Only if, after constrained decoding and top-5 tool retrieval, argument-filling errors dominate on our own tool set | [E] |
| Unsloth Qwen3.5 LoRA VRAM | bf16 LoRA: 0.8B ~3 GB, 2B ~5 GB, 4B ~10 GB, 9B ~22 GB; **QLoRA not recommended** for Qwen3.5; Mamba Triton kernels compile slowly, especially on T4; export with `save_pretrained_gguf` (q4_k_m/q8_0); keep the same chat template | [V] Unsloth docs |
| Kaggle free GPU | Commonly reported ~30 GPU-hours/week, T4x2 or P100 (16 GB), 9-12 h sessions; Kaggle's doc pages did not render for verification and one 2026 review says the quota "floats" | [S]/[U] |
| T4/P100 and bf16 | Neither supports bf16 natively, so Unsloth's bf16 recommendation becomes fp16 on Kaggle [E]; a 4B LoRA (~10 GB) fits a 16 GB T4 [E] | [E] |
| Policy | Training in a notebook is not serving; D11 parks Kaggle/Colab **serving** only. Still an owner decision (D10 "Training parked") | [L] |
| Datasets | MASSIVE CC-BY-4.0 [V]; Salesforce xlam-function-calling-60k is tagged CC-BY-4.0 but gated and says "for research purposes only" [V] (avoid for product use); hermes-function-calling-v1, glaive-function-calling-v2 licences [U] | |
| Eval method | Same discipline as D1: independent-author held-out sets, MASSIVE te-IN/hi-IN test splits, exact tool name + JSON-Schema-valid args + correct abstention, Wilson 95% intervals, p50/p95 latency, compare against the current Verdict + Ling baseline | [L] method |

## 11. What to benchmark locally next (Windows, this laptop; no reboot needed)

1. **GDN correctness on Vulkan**: Qwen3.5-4B Q4_K_M, `-fa off`, compare 50 greedy tokens with the CPU build; then `llama-bench` tg/pp at `-ngl 0` and `99`.
2. **Router candidates on the existing D1 sets** (Test 2, Tests 4-5) and the 12+12 routing items: Qwen3.5-4B vs Qwen3-4B vs Qwen3.5-2B vs Ling-tiny, thinking off, json_schema on.
3. **Tool-selection set** (new, ~100 items from our real tool catalogue, independent author): top-5 retrieval + constrained call; measure name accuracy, schema-valid args, abstention.
4. **Generalist MoE challengers** with `--n-cpu-moe` sweep: LFM2.5-8B-A1B and granite-4.0-h-tiny vs Ling-tiny (speed + 12-item routing + summary quality spot-check).
5. **Local deep lane**: Qwen3.5-35B-A3B `UD-Q3_K_XL` and Gemma-4-26B-A4B `UD-Q4_K_M`: tok/s, peak RAM, time to first token at 32K context.
6. **Multilingual**: MASSIVE te-IN/hi-IN 300-item sample: Verdict (expect weak) vs Qwen3-Embedding-0.6B kNN vs bge-m3 kNN vs Qwen3.5-4B vs Sarvam-30B.
7. **Embeddings + reranker** on vault retrieval: bge-small-en (current) vs Qwen3-Embedding-0.6B vs bge-m3; with/without bge-reranker-v2-m3.
8. **Over-refusal**: XSTest-style safe prompts + owner-task prompts on each candidate, one judge.
9. **CPU-only profiles**: re-run 2-4 with `-ngl 0` and 4 threads to stand in for weaker laptops.

## 12. Not verified / open

- Qwen3.5 Vulkan GDN status on RDNA1 at b11669 (likely present, unproven).
- Exact Groq free-plan numbers (the page table is labelled as Developer-plan base limits).
- Mistral official free-tier limits and training clause (docs 404 today).
- Kaggle weekly quota from an official page.
- NVIDIA trial terms from an official page.
- llama.cpp tool-call parsing for Ling-3.0 (`ling3` parser documented only for vLLM/SGLang).
- Qwen3.8-27B open-weight status and licence.
- Whether Gemma-4/Qwen3.5 Telugu quality is usable (no published Telugu numbers found).

## Sources

Local: `D:\jarvis\hq-neko\jarvis\docs\03_DECISIONS.md` (D1, D5, D11, D19, D20); `D:\jarvis\hq-neko\jarvis\local_models\llama-swap.yaml`; listing of `D:\jarvis\models\llm`.

Models
- https://huggingface.co/Qwen/Qwen3.5-0.8B
- https://huggingface.co/Qwen/Qwen3.5-4B
- https://huggingface.co/Qwen/Qwen3.5-35B-A3B
- https://huggingface.co/Qwen/Qwen3.6-35B-A3B
- https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF/tree/main
- https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/tree/main
- https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/tree/main
- https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/tree/main
- https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/tree/main
- https://artificialanalysis.ai/articles/qwen3-5-small-models
- https://docs.apiyi.com/en/news/qwen-3-6-opensource-launch
- https://www.orcarouter.ai/es/blog/qwen-3-8-27b-open-weights-leak
- https://huggingface.co/google/gemma-4-E4B-it
- https://huggingface.co/google/gemma-4-26B-A4B-it
- https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/tree/main
- https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/tree/main
- https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/tree/main
- https://the-decoder.com/googles-gemma-4-is-now-available-with-apache-2-0-licensing-for-the-first-time/
- https://huggingface.co/ibm-granite/granite-4.0-h-tiny
- https://huggingface.co/ibm-granite/granite-4.0-h-micro
- https://huggingface.co/ibm-granite/granite-4.2-3b
- https://datanorth.ai/news/ibm-releases-granite-4-2
- https://www.ibm.com/granite/docs/models/granite
- https://huggingface.co/LiquidAI/LFM2-8B-A1B
- https://huggingface.co/LiquidAI/LFM2.5-8B-A1B
- https://docs.liquid.ai/lfm/help/model-license
- https://www.liquid.ai/lfm-license
- https://huggingface.co/inclusionAI/Ling-3.0-tiny
- https://huggingface.co/HuggingFaceTB/SmolLM3-3B
- https://huggingface.co/microsoft/Phi-4-mini-instruct
- https://huggingface.co/baidu/ERNIE-4.5-21B-A3B-PT
- https://huggingface.co/openai/gpt-oss-20b
- https://huggingface.co/unsloth/gpt-oss-20b-GGUF/tree/main
- https://huggingface.co/sarvamai/sarvam-30b
- https://huggingface.co/sarvamai/sarvam-30b-gguf
- https://huggingface.co/sarvamai/sarvam-1
- https://github.com/ggml-org/llama.cpp/pull/20275
- https://lilting.ch/en/articles/sarvam-105b-30b-open-source-llm
- https://artificialanalysis.ai/articles/sarvam-105b-Sarvam-30b-everything-you-need-to-know
- https://huggingface.co/krutrim-ai-labs/Krutrim-2-instruct/blob/main/LICENSE.md
- https://huggingface.co/answerdotai/ModernBERT-base

Embeddings and rerankers
- https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF
- https://huggingface.co/google/embeddinggemma-300m
- https://huggingface.co/BAAI/bge-m3
- https://huggingface.co/BAAI/bge-reranker-v2-m3
- https://qdrant.github.io/fastembed/examples/Supported_Models/
- https://huggingface.co/cicaba/Qwen3-Reranker-0.6B-GGUF-llamacpp-fixed
- https://huggingface.co/JonathanMiddleton/Qwen3-Reranker-0.6B/blob/main/README.md

llama.cpp and serving
- https://github.com/ggml-org/llama.cpp/issues/20354
- https://github.com/ggml-org/llama.cpp/issues/19957
- https://github.com/ggml-org/llama.cpp/issues/20603
- https://app.semanticdiff.com/gh/ngxson/llama.cpp/commit/c1258830b28dddc65ac3aa3452214553fdece30c
- https://github.com/ggml-org/llama.cpp/issues/16659
- https://dyn.manpages.debian.org/unstable/llama.cpp-tools/llama-cli.1.en.html
- https://reddit.sentinel-team.org/posts/1pvke55/snapshots/2025-12-26T03%3A37%3A59.692698Z
- https://github.com/mostlygeek/llama-swap
- https://github.com/mostlygeek/llama-swap/blob/main/docs/config.example.yaml
- https://docs.ollama.com/gpu
- https://www.phoronix.com/news/ollama-0.12.11-Vulkan
- https://www.lmstudio.ai/blog/free-for-work
- https://lmstudio.ai/terms
- https://github.com/LostRuins/koboldcpp
- https://fazm.ai/t/vllm-windows-support-2026
- https://www.docker.com/blog/docker-model-runner-vllm-windows
- https://huggingface.co/ljupco/cpu-only-inference-models
- https://localaimaster.com/ram/best-llm-32gb-ram
- https://openclawdc.com/blog/run-llm-without-gpu/

Hardware detection
- https://learn.microsoft.com/en-ie/Windows/Win32/cimwin32prov/win32-videocontroller
- https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_4/nf-dxgi1_4-idxgiadapter3-queryvideomemoryinfo
- https://en.ittrip.xyz/windows/vbnet-gpu-vram-registry
- https://forum.xojo.com/t/amount-of-vram-on-pc-windows/78299

Tool calling, refusal, multilingual
- https://gorilla.cs.berkeley.edu/leaderboard
- https://arxiv.org/html/2504.19277v1
- https://awesomepapers.io/similarity-search/papers/2605.26128
- https://papers.cool/arxiv/2605.02363
- https://arxiv.org/pdf/2510.03847
- https://arxiv.org/html/2405.20947v5
- https://arxiv.org/pdf/2605.05427
- https://arxiv.org/pdf/2505.08054
- https://arxiv.org/pdf/2504.14985
- https://arxiv.org/pdf/2508.11222
- https://aclanthology.org/2026.loreslm-1.20
- https://www.arxiv.org/pdf/2501.15747
- https://huggingface.co/datasets/AmazonScience/massive

Providers
- https://inference.dahl.global
- https://inference.dahl.global/terms
- https://openrouter.ai/docs/api-reference/limits
- https://openrouter.ai/terms
- https://openrouter.ai/docs/guides/privacy/provider-logging
- https://console.groq.com/docs/rate-limits
- https://console.groq.com/docs/your-data
- https://groq.com/terms-of-use
- https://ai.google.dev/gemini-api/docs/rate-limits
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/terms
- https://inference-docs.cerebras.ai/support/rate-limits
- https://vorplabs.com/models/cerebras-inference-provider-operations
- https://help.mistral.ai/en/articles/347614-can-i-opt-out-of-my-input-or-output-data-being-used-for-training
- https://mintlify.com/cheahjs/free-llm-api-resources/providers/free/mistral-plateforme
- https://docs.github.com/en/github-models/use-github-models/prototyping-with-ai-models
- https://developers.cloudflare.com/workers-ai/platform/pricing/
- https://huggingface.co/docs/inference-providers/pricing
- https://forums.developer.nvidia.com/t/request-for-nvidia-nim-api-rate-limit-increase-40-200-rpm/375340
- https://yangmao.ai/en/providers/nvidia-build/no-credit-card/

Fine-tuning
- https://unsloth.ai/docs/models/qwen3.5/fine-tune
- https://gpuperhour.com/blog/free-cloud-gpus-and-credits
- https://huggingface.co/datasets/Salesforce/xlam-function-calling-60k
- https://arxiv.org/pdf/2602.18922
- https://arxiv.org/pdf/2305.07157
- https://huggingface.co/blog/setfit
