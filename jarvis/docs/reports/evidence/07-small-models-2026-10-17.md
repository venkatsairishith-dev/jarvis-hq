# Small-model research for Neko (as of 2026-10-17)

Web research only: Hugging Face API, model cards, licence files, GitHub issues, and leaderboards.
Nothing was downloaded or run against the repo.

**Labels**
- **[V]**: vendor claim, from the model's own card.
- **[3P]**: a different vendor measured it, usually in its own marketing table, so it is biased.
- **[IND]**: independent leaderboard.
- **[I]**: my inference or estimate.

**Hugging Face data**
- Dates come from the `createdAt` field in the HF API.
- Parameter counts come from the HF `safetensors.total` field.
- "sha256=yes" means the HF tree API returns an LFS sha256 oid for that file, so a download can be pinned and verified.
- Every GGUF and ONNX file listed below has an LFS sha256. The first 16 hex characters are shown.

**Current baseline**

The decider is Qwen3-4B-Instruct-2507 Q4_K_M:
- 2497 MB, sha256 `3605803b982cb64a...` in `unsloth/Qwen3-4B-Instruct-2507-GGUF`.
- 92.9% on our 70-case dev set (measured by us).
- BFCL v4 overall 35.68% (FC mode) [IND, Berkeley leaderboard CSV, last modified 2026-04-13].

---

## 0. What is new since our last model pass (key 2026 releases)

| Family | Small sizes | Released | Licence | Note |
|---|---|---|---|---|
| Qwen3.5 | 0.8B, 2B, 4B, 9B | 2026-02-27/28 | Apache-2.0 | Natively multimodal (image+text). Hybrid Gated-DeltaNet architecture. 4B and 9B think by default; 2B is non-thinking by default [V]. Qwen3.6 and 3.8 have no models under 27B. |
| Gemma 4 | E2B (5.1B incl. embeddings), E4B (8B), 12B | 2026-03-02 (E2B/E4B) | **Apache-2.0** | ai.google.dev: "Gemma 4 is governed by a separate license (Apache 2.0)". Gemma 3, 3n, 270M, FunctionGemma and EmbeddingGemma-1 stay under the custom Gemma Terms. |
| LiquidAI LFM2.5 | 350M, 1.2B, 2.6B, 8B-A1B MoE, VL-3B | 2026-01 to 08 | LFM Open License v1.0 | Apache-like. Commercial use is licensed only below $10M annual revenue. Fine for personal use. |
| IBM Granite 4.2 | 3B (actually 3.66B), 8B | 2026-08-07 | Apache-2.0 | Reasoning model. Thinking is on by default and can be switched off with `enable_thinking=False`. |
| OpenBMB MiniCPM5 | 1B, 2B | 2026-05-21 / 2026-09-06 | Apache-2.0 | Llama architecture. Emits XML tool calls. llama.cpp tool-call parsing was missing (#23781, closed). |
| NVIDIA Nemotron-Labs Dory 1.0 4B | 4B looped LM | 2026-10-16 | OpenMDW-1.1 | Needs an unmerged llama.cpp fork (PR #30306) and has a 4K context. **Skip for now.** |
| Microsoft harrier-oss-v1 | 270M, 0.6B embeddings | 2026-03-30 | MIT | Card claims multilingual MTEB v2 of 66.5 (270M) and 69.0 (0.6B) [V]. |
| Google EmbeddingGemma 2 | 740M total (270M text tower) | 2026-09-14 | Apache-2.0 | Multimodal embeddings. |
| BitNet | bitnet-embedding 0.6B/270M | 2026-07-15 | MIT | No new BitNet chat LLM since the 2B4T model (2025-04). |

---

## 1. Sub-1B models for intent and routing

| Model (link) | Released | Licence | Params | GGUF / ONNX (sha256) | Q4 size | Relevant numbers |
|---|---|---|---|---|---|---|
| [LiquidAI/LFM2.5-350M](https://huggingface.co/LiquidAI/LFM2.5-350M) | 2026-03-31 | LFM Open v1.0 | 354M | [LiquidAI/LFM2.5-350M-GGUF](https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF): `LFM2.5-350M-Q4_K_M.gguf` 229 MB `7e6f72643caafc9a`, `Q8_0` 379 MB `be036a757295e550` (yes) | 0.23 GB | IFEval 76.96, IFBench 40.69, BFCLv3 44.11, BFCLv4 21.86 [V]. The same Liquid table puts Qwen3.5-0.8B (Instruct) at IFEval 59.94 and BFCLv3 35.08 [3P]. The card notes that Qwen3.5-0.8B in thinking mode "doom looped". |
| [Qwen/Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B) | 2026-02-28 | Apache-2.0 | 873M (includes vision) | [unsloth/Qwen3.5-0.8B-GGUF](https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF): `Qwen3.5-0.8B-Q4_K_M.gguf` 533 MB `bd258782e35f7f45`. Also [ggml-org/Qwen3.5-0.8B-GGUF](https://huggingface.co/ggml-org/Qwen3.5-0.8B-GGUF) `Q8_0` 834 MB `37ae482d336108d2` (yes) | 0.53 GB | Non-thinking IFEval 52.1 and MMLU-Pro 29.7. Thinking mode: BFCL-V4 25.3, TAU2 11.6 [V]. |
| [openbmb/MiniCPM5-1B](https://huggingface.co/openbmb/MiniCPM5-1B) | 2026-05-21 | Apache-2.0 | 1.08B | [openbmb/MiniCPM5-1B-GGUF](https://huggingface.co/openbmb/MiniCPM5-1B-GGUF): `MiniCPM5-1B-Q4_K_M.gguf` 688 MB `81b64d05a23b17b3` (yes) | 0.69 GB | Card claims "1B-class SOTA" over LFM2.5-1.2B-Thinking, Qwen3-0.6B and Qwen3.5-0.8B. The numbers are only in a radar image [V]. |
| [LiquidAI/LFM2.5-1.2B-Instruct](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct) | 2026-01-06 | LFM Open v1.0 | 1.17B | [LiquidAI/LFM2.5-1.2B-Instruct-GGUF](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF): `Q4_K_M` 731 MB `b1b3de114215d950`, `Q8_0` 1246 MB `f6b981dcb86917fa` (yes) | 0.73 GB | IFEval 86.23, IFBench 47.33, BFCLv3 49.12, against Qwen3-1.7B at 73.68 / 21.33 / 46.30 [V/3P]. |
| [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) | 2025-04-27 | Apache-2.0 | 752M | [unsloth/Qwen3-0.6B-GGUF](https://huggingface.co/unsloth/Qwen3-0.6B-GGUF) `Q4_K_M` 397 MB `ac2d97712095a558` (yes) | 0.40 GB | BFCL v4 overall 23.93% FC [IND]. |
| [google/gemma-3-270m-it](https://huggingface.co/google/gemma-3-270m-it) and [functiongemma-270m-it](https://huggingface.co/google/functiongemma-270m-it) | 2025-07-30 / 2025-10-08 | **Gemma Terms** (custom) | 268M | many GGUFs | ~0.25 GB | Gemma-3-1b-it scores only 7.17% on BFCL [IND]. The 270M is meant for fine-tuning. Licence restrictions: incorporated Prohibited Use Policy, flow-down to derivatives, Google may "restrict usage remotely or otherwise", delete-on-termination. |
| HuggingFaceTB/SmolLM2-360M-Instruct | 2024-10-31 | Apache-2.0 | 362M | — | — | Already rejected (5-6/12, measured by us). No newer SmolLM below 3B exists, and SmolLM3 comes only as 3B. |
| [vllm-sr/Decision-2.0-Eos-0.8B](https://huggingface.co/vllm-sr/Decision-2.0-Eos-0.8B) (also Kai-0.6B on Qwen3-0.6B, and GGUF repos) | 2026-09-28 | Apache-2.0 | 0.75B | `vllm-sr/Decision-2.0-Eos-0.8B-GGUF` | — | A purpose-built "choice / yes-no / score" decision model from vLLM Semantic Router. JevArena 53.9 [V, vendor's own benchmark]. Needs `trust_remote_code` and is very new. Curiosity only. |

**Known issues**
- Qwen3.5 thinking-mode loops at 0.8B [3P: Liquid card].
- `enable_thinking` could not turn off Qwen3.5 thinking in llama.cpp. Issue [#20182](https://github.com/ggml-org/llama.cpp/issues/20182) was closed 2026-03-07.

**Recommendation [I]:** for routing, use the grammar `enum` output. Also try a no-generation kNN router over labelled example utterances, using the embedding model from section 5. It is cheap and deterministic.

---

## 2. 1-3B models for structured tool selection and JSON planning

| Model | Released | Licence | Params | GGUF (sha256) | Q4_K_M | Tool / structured numbers |
|---|---|---|---|---|---|---|
| [LiquidAI/LFM2.5-2.6B](https://huggingface.co/LiquidAI/LFM2.5-2.6B) | 2026-07-28 | LFM Open v1.0 (<$10M revenue) | 2.70B | [LiquidAI/LFM2.5-2.6B-GGUF](https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF) `LFM2.5-2.6B-Q4_K_M.gguf` 1674 MB `02a8b7e17487d326`, `Q8_0` 2875 MB `1e22128dfa128bdf` (yes) | 1.67 GB | BFCLv4 56.88, IFStruct 85.49, IFBench 59.17, ToolSandbox 77.83, against Qwen3.5-4B at 50.56 / 36.25 / 48.40 / 75.55 [V/3P]. MiniCPM's table puts it at BFCLv4 61.1 [3P]. Its native tool format is Pythonic, but JSON can be forced via the system prompt [V]. With our json_schema grammar the tool format does not matter [I]. |
| [openbmb/MiniCPM5-2B](https://huggingface.co/openbmb/MiniCPM5-2B) | 2026-09-06 | Apache-2.0 | 2.52B | [openbmb/MiniCPM5-2B-GGUF](https://huggingface.co/openbmb/MiniCPM5-2B-GGUF) `MiniCPM5-2B-Q4_K_M.gguf` 1561 MB `ec2d5801640099e9` (yes) | 1.56 GB | BFCL v4 66.6, IFEval 86.7, IFBench 66.3. The same table gives LFM2.5-2.6B 61.1, Qwen3.5-2B 43.6, Gemma-4-E2B 36.6, Qwen3.5-4B 56.8 and granite-4.2-3B 52.2 [V, "reproduced internally"]. Probably measured in thinking mode, since training is "deep-thinking SFT" [I]. |
| [Qwen/Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B) | 2026-02-28 | Apache-2.0 | 2.27B (includes vision) | [unsloth/Qwen3.5-2B-GGUF](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF) `Qwen3.5-2B-Q4_K_M.gguf` 1281 MB `aaf42c8b7c3cab2b`, `mmproj-F16.gguf` 668 MB (yes) | 1.28 GB | Non-thinking: IFEval 61.2, below Qwen3-1.7B's 68.2 and Qwen3-4B-2507's 83.4. Thinking: BFCL-V4 43.6, TAU2 48.8 [V]. Weak in non-thinking JSON mode [I]. |
| [ibm-granite/granite-4.2-3b](https://huggingface.co/ibm-granite/granite-4.2-3b) | 2026-08-07 (card says release Aug 25) | Apache-2.0 | 3.66B | [ibm-granite/granite-4.2-3b-GGUF](https://huggingface.co/ibm-granite/granite-4.2-3b-GGUF) `granite-4.2-3b-Q4_K_M.gguf` 2244 MB `e0406663965846ae` (yes) | 2.24 GB | BFCL v4 52.41, IFBench (prompt) 74.33 [V, probably thinking mode]. Card says to use temperature 1.0 everywhere. |
| [HuggingFaceTB/SmolLM3-3B](https://huggingface.co/HuggingFaceTB/SmolLM3-3B) | 2025-07-08 | Apache-2.0 | 3.08B | [ggml-org/SmolLM3-3B-GGUF](https://huggingface.co/ggml-org/SmolLM3-3B-GGUF) `SmolLM3-Q4_K_M.gguf` 1915 MB `8334b850b7bd4623` (yes) | 1.92 GB | IFEval 76.7 [V]. No BFCL figure. Superseded by the 2026 models [I]. |
| [google/gemma-4-E2B-it](https://huggingface.co/google/gemma-4-E2B-it) | 2026-03-02 | Apache-2.0 | 5.1B (2.3B effective) | [google/gemma-4-E2B-it-qat-q4_0-gguf](https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf) `gemma-4-E2B_q4_0-it.gguf` 3350 MB `fa401b55b07ee70a`, or [unsloth](https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF) `Q4_K_M` 3107 MB (yes) | 3.1-3.35 GB | Tau2 24.5 [V]. BFCLv4 36.98 / 31.91 [3P, Liquid]. The file is big for the quality because of per-layer embeddings. Also does audio and vision. |
| [microsoft/Phi-4-mini-instruct](https://huggingface.co/microsoft/Phi-4-mini-instruct) | 2025-02-19 | MIT | 3.84B | many | ~2.5 GB | No current tool numbers checked. Older generation [I]. |
| [mistralai/Ministral-3-3B-Instruct-2512](https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512) | 2025-10-31 | Apache-2.0 | 3.85B (includes vision) | [official GGUF repo](https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512-GGUF) | ~2.2 GB [I] | No BFCL on the card. |
| [meta-llama/Llama-3.2-1B / 3B-Instruct](https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct) | 2024-09-18 | **Llama 3.2 Community Licence** (gated) | 1.24B / 3.21B | many | 0.8 / 2.0 GB | BFCL v4: 3B 21.95%, 1B 10.82% [IND]. Licence: Acceptable Use Policy, attribution and licence copy on redistribution, a 700M-MAU clause, and no rights for EU-domiciled users to the *multimodal* 3.2 models. **Not recommended.** |
| Qwen3-1.7B (reference) | 2025-04 | Apache-2.0 | 2.03B | — | — | BFCL v4 28.41% [IND]. 17-20% below Qwen3-4B on our dev set (measured by us). |

**Known issues (llama.cpp GitHub)**
- [#30039](https://github.com/ggml-org/llama.cpp/issues/30039), open since 2026-10-06. Vulkan prompt processing is about **45% slower on RDNA1/RDNA2** for **Qwen3.5 (Gated-DeltaNet hybrid)** models since builds b10950 to b11010. That covers our laptop's b11669 on the Radeon Pro 5500M (RDNA1). Text generation is unchanged. Bisected to 2026-09-14..17.
- [#24066](https://github.com/ggml-org/llama.cpp/issues/24066), open. A Vulkan slowdown with Qwen3.5-9B was traced to `server_n_outputs_max()`.
- [#20837](https://github.com/ggml-org/llama.cpp/issues/20837), open. With thinking enabled, Qwen3.5-9B puts XML tool calls inside the think block. This does not apply in our grammar and non-thinking mode [I].
- [#21831](https://github.com/ggml-org/llama.cpp/issues/21831), closed 2026-09-29. Hybrid and SWA models (Qwen3.5, Gemma 4) forced full prompt re-processing. This matters for our long fixed system prompt, so we should check that the cache is reused [I].
- [#20809](https://github.com/ggml-org/llama.cpp/issues/20809), closed. At b8429, Qwen3-Instruct-2507 was wrongly flagged as a thinking model and tool calls ended up in reasoning_content.
- [#23781](https://github.com/ggml-org/llama.cpp/issues/23781). MiniCPM5 XML tool calls were not parsed; closed after #23860 added support.
- [#21375](https://github.com/ggml-org/llama.cpp/issues/21375) and [#21516](https://github.com/ggml-org/llama.cpp/issues/21516), both closed. Gemma 4 repetition loops and `<unused>` token output.
- A grammar applies from the first token, so a thinking model must have thinking off when using `json_schema`.

---

## 3. 3-8B reasoning models and compact VLMs

| Model | Released | Licence | Params | GGUF (sha256) | Q4 size | Numbers |
|---|---|---|---|---|---|---|
| [Qwen/Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) | 2026-02-27 | Apache-2.0 | 4.66B (includes vision) | [unsloth/Qwen3.5-4B-GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF) `Qwen3.5-4B-Q4_K_M.gguf` 2741 MB `00fe7986ff5f6b46` + `mmproj-F16.gguf` 672 MB. [bartowski](https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF) `Q4_K_M` 3013 MB (yes) | 2.7 GB | Thinking: BFCL-V4 50.3, TAU2 79.9, IFEval 89.8 [V]. ScreenSpot-v2 78.5 [3P, Liquid]. Thinks by default. Hit by the RDNA1 Vulkan prompt-processing regression (#30039). |
| [LiquidAI/LFM2.5-8B-A1B](https://huggingface.co/LiquidAI/LFM2.5-8B-A1B) | 2026-05-28 | LFM Open v1.0 | 8.47B total, about 1.5B active | [LiquidAI/LFM2.5-8B-A1B-GGUF](https://huggingface.co/LiquidAI/LFM2.5-8B-A1B-GGUF) `LFM2.5-8B-A1B-Q4_K_M.gguf` 5156 MB `4923ec14f06b968b` (yes) | 5.2 GB | IFEval 91.84, BFCLv3 64.79, BFCLv4 49.73, Tau2 Telecom 88.07 [V]. The same table shows Qwen3.5-4B ahead on BFCLv4 (54.01) and Tau2-Retail (71.93) [3P]. Good for CPU (small active parameter count) [I]. |
| [inclusionAI/Ling-3.0-tiny](https://huggingface.co/inclusionAI/Ling-3.0-tiny) (current) | 2026-08-10 | MIT | 7.9B total / 1.3B active | [inclusionAI/Ling-3.0-tiny-GGUF](https://huggingface.co/inclusionAI/Ling-3.0-tiny-GGUF) `Q4_K_M` 4824 MB `246d67d45f5b0c74` | 4.8 GB | Uses Kimi-Delta-Attention, so it may share the #30039 regression [I, not verified]. |
| [ibm-granite/granite-4.2-8b](https://huggingface.co/ibm-granite/granite-4.2-8b) | 2026-08-07 | Apache-2.0 | 8.79B | [granite-4.2-8b-GGUF](https://huggingface.co/ibm-granite/granite-4.2-8b-GGUF) `Q4_K_M` 5348 MB `16a9369d0805f80b` | 5.3 GB | BFCL v4 52.39 [V]. Dense 8B, so slow on our hardware [I]. |
| [google/gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it) | 2026-03-02 | Apache-2.0 | 8B (4.5B effective) | [google/gemma-4-E4B-it-qat-q4_0-gguf](https://huggingface.co/google/gemma-4-E4B-it-qat-q4_0-gguf) `gemma-4-E4B_q4_0-it.gguf` 5155 MB `676c35070db6dbe5` | 5.0-5.2 GB | Tau2 42.2, MMLU-Pro 69.4 [V]. BFCLv4 46.39 [3P]. |
| [Qwen/Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) | 2026-02-27 | Apache-2.0 | 9.65B | unsloth GGUF | ~5.7 GB [I] | BFCL-V4 66.1, TAU2 79.1 [V]. Too big for 4 GB VRAM. CPU only [I]. |

**Compact VLMs and GUI grounding.** UI-Venus-1.5-2B is the current model.

| Model | Released | Licence | Params | GGUF | Numbers |
|---|---|---|---|---|---|
| [inclusionAI/UI-Venus-1.5-2B](https://huggingface.co/inclusionAI/UI-Venus-1.5-2B) (current) | 2026-02-09 | Apache-2.0 | 2.44B | (in use) | ScreenSpot-Pro 57.7 [V] |
| [mPLUG/GUI-Owl-1.5-2B-Instruct](https://huggingface.co/mPLUG/GUI-Owl-1.5-2B-Instruct) | 2026-02-14 | MIT | 2.13B | [mradermacher/GUI-Owl-1.5-2B-Instruct-GGUF](https://huggingface.co/mradermacher/GUI-Owl-1.5-2B-Instruct-GGUF) `Q4_K_M` 1107 MB `5fd2c77781a14a91` + `mmproj-Q8_0` 445 MB (community quant) | OSWorld-Verified 43.5, WindowsAA 25.8, OSWorld-MCP 33.0 [V]. Grounding results are only in the paper. 4B variant: 48.2 / 29.4. |
| [LiquidAI/LFM2.5-VL-3B](https://huggingface.co/LiquidAI/LFM2.5-VL-3B) | 2026-08-11 | LFM Open v1.0 | 3.12B | [LiquidAI/LFM2.5-VL-3B-GGUF](https://huggingface.co/LiquidAI/LFM2.5-VL-3B-GGUF) `Q4_K_M` 1674 MB `2436cf4bbac9a16e` + `mmproj-Q8_0` 583 MB | ScreenSpot-v2 80.7, against Qwen3.5-2B 66.5, Qwen3.5-4B 78.5 and Gemma4-E2B 31.1 [V/3P] |
| [microsoft/Fara1.5-4B](https://huggingface.co/microsoft/Fara1.5-4B) | 2026-07-17 | MIT | 4.54B (on Qwen3.5-4B) | [bartowski/Fara1.5-4B-GGUF](https://huggingface.co/bartowski/Fara1.5-4B-GGUF) | Web agent: WebVoyager 80.8, Online-Mind2Web 57.3 [V]. Pixel-coordinate actions. Trained at 1440x900. |
| [openbmb/MiniCPM-V-4.7-1B](https://huggingface.co/openbmb/MiniCPM-V-4.7-1B) | 2026-10-06 | Apache-2.0 | 1.55B | [official gguf](https://huggingface.co/openbmb/MiniCPM-V-4.7-1B-gguf) `Q4_K_M` 672 MB + `mmproj-f16` 1110 MB | Has a grounding cookbook. Numbers not checked (released 11 days ago). |
| [inclusionAI/UI-Venus-2-9B](https://huggingface.co/inclusionAI/UI-Venus-2-9B) | 2026-08-26 | **Weight licence "pending"**: the card says Apache-2.0 was deliberately not carried over | 9B | bartowski GGUF | ScreenSpot-Pro 73.0 [V]. Too big, and the licence is unclear. |
| Florence-2-base, SmolVLM2-2.2B, Qwen3-VL-2B/4B, UI-TARS-1.5-7B | 2024-2025 | MIT / Apache | — | — | Superseded by the models above [I]. |

---

## 4. Microsoft BitNet

- **Model:** [microsoft/bitnet-b1.58-2B-4T](https://huggingface.co/microsoft/bitnet-b1.58-2B-4T), released 2025-04-15, MIT licence.
- **GGUF:** [microsoft/bitnet-b1.58-2B-4T-gguf](https://huggingface.co/microsoft/bitnet-b1.58-2B-4T-gguf) `ggml-model-i2_s.gguf` 1188 MB, sha256 `4221b252fdd5fd25` (yes).
- **Quality [V]:**
  - Average 54.19 against Qwen2.5-1.5B's 55.23.
  - IFEval 53.48 and MMLU 53.17.
  - Non-embedding memory 0.4 GB and 29 ms CPU decode latency.
  - No tool-calling numbers. It is **about Qwen2.5-1.5B class**, well below the 2026 models above.
- **Runtime:** needs [bitnet.cpp](https://github.com/microsoft/BitNet) (MIT, 41k stars, last push 2026-07-27, no tagged releases). The card itself says transformers gives no speed gain.
- **2026 additions:**
  - [bitnet-embedding-0.6b and 270m](https://huggingface.co/microsoft/bitnet-embedding-0.6b), released 2026-07-15, MIT. MTEB v2 mean 67.49 and 66.26, close to their FP16 Harrier teachers (69.0 and 66.5). CPU prefill is 1.4-2.3x faster than F16 [V].
  - VibeVoice-ASR-BitNet with [VibeASR.cpp](https://github.com/microsoft/VibeASR.cpp).
- **Verdict [I]:** no BitNet chat LLM is worth benchmarking for the decider. The BitNet embeddings would need a second runtime (a bitnet.cpp fork), which is not worth it while Harrier and Qwen3-Embedding run on stock llama.cpp and ONNX.

---

## 5. Embeddings and rerankers for local memory

| Model | Released | Licence | Params | GGUF / ONNX (sha256) | Numbers |
|---|---|---|---|---|---|
| [microsoft/harrier-oss-v1-0.6b](https://huggingface.co/microsoft/harrier-oss-v1-0.6b) | 2026-03-30 | MIT | 596M, 1024-d, 32K context | ONNX: [onnx-community/harrier-oss-v1-0.6b-ONNX](https://huggingface.co/onnx-community/harrier-oss-v1-0.6b-ONNX) `onnx/model_quantized.onnx` + `.onnx_data` 706 MB `95e9e447398ba3ef`. GGUF: [mradermacher/harrier-oss-v1-0.6b-GGUF](https://huggingface.co/mradermacher/harrier-oss-v1-0.6b-GGUF) `Q8_0` 639 MB `ba2c9408f82cfdb7` (community) | Multilingual MTEB v2 69.0 [V]. Last-token pooling, and queries need a one-line instruction. |
| [microsoft/harrier-oss-v1-270m](https://huggingface.co/microsoft/harrier-oss-v1-270m) | 2026-03-30 | MIT (see note) | 268M, 640-d | [onnx-community/harrier-oss-v1-270m-ONNX](https://huggingface.co/onnx-community/harrier-oss-v1-270m-ONNX) `onnx/model_quantized.onnx` 344 MB `a81e3db98eaedd77` | 66.5 [V]. The parameter count is identical to gemma-3-270m, and the BitNet card lists a "Gemma3" backbone, so it may be Gemma-derived. **Licence ambiguity [I].** |
| [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | 2025-06-03 | Apache-2.0 | 596M, 1024-d | Official [Qwen/Qwen3-Embedding-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF) `Qwen3-Embedding-0.6B-Q8_0.gguf` 639 MB `06507c7b42688469`. ONNX: [onnx-community](https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX) `onnx/model_int8.onnx` 614 MB `6d0ea863f78b4a84` | MMTEB 64.33, MTEB-R 61.82 [V] |
| [google/embeddinggemma-2](https://huggingface.co/google/embeddinggemma-2) | 2026-09-14 | Apache-2.0 | 740M total; text tower 270M; 768-d with MRL down to 128 | [ggml-org/embeddinggemma-2-GGUF](https://huggingface.co/ggml-org/embeddinggemma-2-GGUF) `embeddinggemma-2-Q8_0.gguf` 310 MB `2188ac1deca4b77d`. [onnx-community/embeddinggemma-2-ONNX](https://huggingface.co/onnx-community/embeddinggemma-2-ONNX) `onnx/model_quantized.onnx_data` 314 MB | MTEB multilingual v2 61.36, eng v2 68.46, code 78.68 [V]. 8K context. |
| [ibm-granite/granite-embedding-97m-multilingual-r2](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2) | 2026-04-20 | Apache-2.0 | 97M | Official `onnx/model_quint8_avx2.onnx` 98 MB `a6022dd8220ea6f6` | Tiny fallback. Scores not checked. |
| [LiquidAI/LFM2.5-Embedding-350M](https://huggingface.co/LiquidAI/LFM2.5-Embedding-350M) | 2026-05-05 | LFM Open v1.0 | 354M | Official GGUF `Q4_K_M` 229 MB `4d7aa9dc6406a10f` | 512-token maximum. Aimed at short-context retrieval [V]. |
| jinaai/jina-embeddings-v5-text-nano / small | 2026-01-22 | **CC BY-NC 4.0** | 212M / 596M | Official GGUF and ONNX | MTEB eng v2 71.0 for nano [V]. Non-commercial only. |
| BAAI/bge-m3, bge-small-en-v1.5, Alibaba gte-multilingual-base | 2023-24 | MIT / Apache | 33-568M | many | bge-m3 MMTEB 59.56 [3P, Qwen table]. Older. |
| **Rerankers** | | | | | |
| [Qwen/Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) | 2025-05-29 | Apache-2.0 | 596M | [ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF](https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF) `qwen3-reranker-0.6b-q8_0.gguf` 639 MB `22c9979ce4fbcdc5` | MTEB-R 65.80 against BGE-reranker-v2-m3 at 57.03 [V] |
| [ibm-granite/granite-embedding-reranker-english-r2](https://huggingface.co/ibm-granite/granite-embedding-reranker-english-r2) | 2025-08-04 | Apache-2.0 | 150M | — | English-only. Small cross-encoder. |
| jinaai/jina-reranker-v3.5 | 2026-07-14 | **CC BY-NC 4.0** | 597M | official GGUF | Non-commercial only. |
| tencent/R3-embedding-0.6b and R3-rerank-0.6b | 2026-07-08 | Apache-2.0 | 596M | — | Fine-tunes of the Qwen3 models. Low adoption. |

---

## 6. Claim verification (current model: cross-encoder/nli-deberta-v3-xsmall)

| Model | Released | Licence | Params | Format (sha256) | Numbers |
|---|---|---|---|---|---|
| [yaxili96/FactCG-DeBERTa-v3-Large](https://huggingface.co/yaxili96/FactCG-DeBERTa-v3-Large) | 2025-02-10 | MIT | 435M | `model.safetensors` 1740 MB `379a343d1ca25c9d` (safetensors, plain `AutoModelForSequenceClassification`). Needs our own ONNX int8 export, about 0.45-0.65 GB [I]. | **LLM-AggreFact average 75.6** [IND, llm-aggrefact.github.io]. Paper: NAACL 2025. |
| [lytang/MiniCheck-Flan-T5-Large](https://huggingface.co/lytang/MiniCheck-Flan-T5-Large) | 2024-04-14 | MIT | ~0.8B | `pytorch_model.bin` 3133 MB (**pickle** format). A community ONNX conversion (FamiliarTools) exists but is unvetted. | LLM-AggreFact 75.0 [IND]. Needs the minicheck package. Encoder-decoder, so slower [I]. |
| [vectara/hallucination_evaluation_model](https://huggingface.co/vectara/hallucination_evaluation_model) (HHEM-2.1-Open) | 2023-10 (updated 2026-10-06) | Apache-2.0 | 110M (flan-t5-base) | `model.safetensors` 439 MB `634de18a38cf1e99`. Needs **trust_remote_code**. Community ONNX `FamiliarTools/HHEM-2.1-Open-onnx` 439 MB (0 downloads, unvetted). | AggreFact-SOTA balanced accuracy 76.55, RAGTruth-Summ 64.42 [V]. Unlimited context. |
| [cross-encoder/nli-deberta-v3-base](https://huggingface.co/cross-encoder/nli-deberta-v3-base) / [-large](https://huggingface.co/cross-encoder/nli-deberta-v3-large) | 2022 | Apache-2.0 | 184M / 435M | Official `onnx/model_qint8_avx512_vnni.onnx` 244 MB `cc4ef779fe3cb190` (base) and 643 MB `9411944eb4cfb593` (large) | Drop-in upgrade with the same API and labels as our current model [I]. General NLI, not tuned for grounding. |
| MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli | 2022 | MIT | 435M | safetensors | Stronger general NLI. Not on AggreFact. |
| [bespokelabs/Bespoke-MiniCheck-7B](https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B) | 2024-08-09 | **CC BY-NC 4.0** | 7.7B | — | Top of LLM-AggreFact at 77.4 [IND]. Non-commercial and too slow for CPU [I]. |
| [ibm-granite/granite-guardian-4.1-8b](https://huggingface.co/ibm-granite/granite-guardian-4.1-8b) | 2026-04-16 | Apache-2.0 | 8.4B | Official GGUF | Granite Guardian 3.3 scores 76.5 on LLM-AggreFact [IND]. 8B is too heavy [I]. |
| AlignScore-large ([liuyanyi/AlignScore-large-hf](https://huggingface.co/liuyanyi/AlignScore-large-hf)) | 2025-06 (port) | MIT | 355M | safetensors | Not in the current AggreFact top table. |

Note: vllm-sr/Vela-1.0-Encoder-307M-FactCheck only decides *whether a request needs fact-checking*. It does not verify claims.

---

## 7. ASR and wake word (for later hardware tests)

| Item | Released | Licence | Size | Notes |
|---|---|---|---|---|
| [moonshine-ai/moonshine-streaming-tiny / small / medium](https://huggingface.co/moonshine-ai/moonshine-streaming-small) | 2026-01-06 | MIT (legacy non-English non-streaming models use the non-commercial Moonshine Community Licence) | 44M / 140M / 266M | Open ASR average WER 12.00 / 7.84 / 6.65; LibriSpeech-clean 4.83 / 2.61 / 2.17 with the shipped 8-bit models [V]. Library `moonshine-voice` v0.1.5 (2026-08-24). Old `UsefulSensors` org renamed. |
| [nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) | 2025-08-04 (updated 2026-08) | CC-BY-4.0 | 627M | Official `parakeet-tdt-0.6b-v3.q8_0.gguf` 714 MB `e3880d0aaaaf2c30` for NeMo-Speech.cpp. sherpa-onnx int8 `encoder.int8.onnx` 652 MB. 25 EU languages; English Fleurs WER 4.85 [V]. |
| [nvidia/canary-180m-flash](https://huggingface.co/nvidia/canary-180m-flash) | 2025-03-11 | CC-BY-4.0 | 182M | LS-clean 1.87 and AMI 14.86 [V/Open ASR]. NeMo runtime. |
| [ibm-granite/granite-speech-5.0-470m-turboctc](https://huggingface.co/ibm-granite/granite-speech-5.0-470m-turboctc) | 2026-08-04 | Apache-2.0 (the `-nc` variant is CC-BY-NC-SA) | 473M | GGUF through `handy-computer/...-gguf` and transcribe.cpp. Scores only in images. |
| [Qwen/Qwen3-ASR-0.6B](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) | 2026-01-28 | Apache-2.0 | 0.94B | 52 languages and dialects. vLLM-oriented. |
| [microsoft/VibeVoice-ASR-BitNet](https://huggingface.co/microsoft/VibeVoice-ASR-BitNet) | 2026-07-24 | MIT | ~1.6 GB of GGUF | RTF below 1 on 3+ x86 threads [V]. Needs VibeASR.cpp. |
| whisper.cpp ([ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp)) | v1.9.5 on 2026-10-06 | MIT | `ggml-small.en-q5_1.bin` 190 MB `bfdff4894dcb76bb`, `ggml-base.en-q5_1.bin` 60 MB, `ggml-large-v3-turbo-q5_0.bin` 574 MB | Safe baseline. faster-whisper v1.2.1 (2025-10-31), MIT. |
| [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) keyword spotting | v1.13.8 on 2026-09-10 | Apache-2.0 | small zipformer KWS | Open-vocabulary keywords with no training. Active project. |
| [openWakeWord](https://github.com/dscripka/openWakeWord) | last release v0.6.0, 2024-02 | Code Apache-2.0; **pre-trained models CC BY-NC-SA 4.0** | tiny | Includes a "hey jarvis" model. The training notebook is broken (issue #296). Personal use is fine. |
| microWakeWord | active 2026-07 | Apache-2.0 | tiny | For ESP32. |

---

## 8. Ranked shortlists to benchmark on the CPU server (Xeon 6244, 6 threads, llama.cpp CPU)

Run every candidate through the existing 70-case dev set with the json_schema grammar and **thinking disabled**. Compare against Qwen3-4B-2507 Q4_K_M.

### (a) Decider / planner
1. `LiquidAI/LFM2.5-2.6B-GGUF` / `LFM2.5-2.6B-Q4_K_M.gguf` (1.67 GB). Best vendor tool and structured numbers per GB. Fits in 2.8 GB free VRAM [I].
2. `unsloth/Qwen3.5-4B-GGUF` / `Qwen3.5-4B-Q4_K_M.gguf` (2.74 GB). Direct successor. Watch the RDNA1 Vulkan regression (#30039).
3. `openbmb/MiniCPM5-2B-GGUF` / `MiniCPM5-2B-Q4_K_M.gguf` (1.56 GB). Highest vendor BFCLv4, probably in thinking mode.
4. `ibm-granite/granite-4.2-3b-GGUF` / `granite-4.2-3b-Q4_K_M.gguf` (2.24 GB).
5. `LiquidAI/LFM2.5-8B-A1B-GGUF` / `LFM2.5-8B-A1B-Q4_K_M.gguf` (5.16 GB). CPU-friendly MoE, as an "escalation" tier.
6. `unsloth/Qwen3.5-2B-GGUF` / `Qwen3.5-2B-Q4_K_M.gguf` (1.28 GB). Low-cost control.

### (b) Routing / intent
1. `LiquidAI/LFM2.5-350M-GGUF` / `LFM2.5-350M-Q8_0.gguf` (379 MB)
2. `unsloth/Qwen3.5-0.8B-GGUF` / `Qwen3.5-0.8B-Q4_K_M.gguf` (533 MB)
3. `LiquidAI/LFM2.5-1.2B-Instruct-GGUF` / `LFM2.5-1.2B-Instruct-Q4_K_M.gguf` (731 MB)
4. `openbmb/MiniCPM5-1B-GGUF` / `MiniCPM5-1B-Q4_K_M.gguf` (688 MB)
5. Embedding kNN router using model (c)1, with no LLM [I].

### (c) Embeddings / rerank
1. `onnx-community/harrier-oss-v1-0.6b-ONNX` / `onnx/model_quantized.onnx` + `onnx/model_quantized.onnx_data`
2. `Qwen/Qwen3-Embedding-0.6B-GGUF` / `Qwen3-Embedding-0.6B-Q8_0.gguf`
3. `ggml-org/embeddinggemma-2-GGUF` / `embeddinggemma-2-Q8_0.gguf`
4. Reranker: `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` / `qwen3-reranker-0.6b-q8_0.gguf`

### (d) Claim verification
1. `yaxili96/FactCG-DeBERTa-v3-Large` / `model.safetensors`, exported locally to ONNX int8
2. `vectara/hallucination_evaluation_model` / `model.safetensors` (review the remote code first)
3. `cross-encoder/nli-deberta-v3-large` / `onnx/model_qint8_avx512_vnni.onnx`, the lowest-effort upgrade
4. Control: `cross-encoder/nli-deberta-v3-base` / `onnx/model_qint8_avx512_vnni.onnx`

**Do not benchmark now**
- BitNet 2B4T: too weak, and it needs a separate runtime.
- Dory-4B: needs an unmerged llama.cpp fork and has a 4K context.
- Gemma 3 and FunctionGemma: custom licence and weak tool scores.
- Llama 3.2: weak, with licence baggage.
- Bespoke-MiniCheck-7B and jina v5: non-commercial licences.
- UI-Venus-2-9B: licence pending.
