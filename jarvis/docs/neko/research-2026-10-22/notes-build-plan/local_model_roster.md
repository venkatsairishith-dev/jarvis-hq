# Local model roster for JARVIS on 4 GB RDNA1 + 32 GB RAM + ~19 GB disk (October 2026)

Scope: a recommended set of 10–20 local models, organized by role, for jarvis-hq (Windows 10, AMD RDNA1 4 GB VRAM, llama.cpp Vulkan b11669/b11714 with flash attention off, llama-swap v261, 32 GB RAM, ~19 GB free on D:). An API reasoning model sits on top and directs agents that run the small local models.

Evidence labels used below: **[vendor]** means a claim from the model maker or its model card. **[independent]** means a third-party or community measurement. **[inference]** means my own reasoning, not a sourced fact.

---

## Q1. Which small models (≤9B dense, or MoE with few active parameters) lead on tool calling, instruction following and coding in 2026, and which have reliable GGUF support?

### Takeaway
Qwen3.5 small models (0.8B/2B/4B/9B, Apache-2.0, released March 2026) are the current small family with GGUF support and vision built in. On Qwen's own card, Qwen3.5-2B beats Qwen3-4B-2507 on BFCL-V4 and TAU2 in thinking mode. In non-thinking mode, Qwen3-4B-2507 is far ahead on instruction following (IFEval 83.4 vs 61.2). The repo's current decider is therefore still a good choice for low-latency, non-thinking JSON decisions. Qwen3.5-4B or 9B are worth A/B testing on the repo's 157 fixed cases. Switching on vendor numbers alone is not justified.

### Cited Findings
- The Qwen3.5 small series (0.8B, 2B, 4B, 9B) was released on 2 March 2026 (secondary source) — [techie007 substack guide](https://techie007.substack.com/p/qwen-35-the-complete-guide-benchmarks)
- Qwen3.5 models are "multimodal hybrid reasoning LLMs". The run commands load a separate `mmproj-F16.gguf` vision projector. Native context is 262,144 tokens. On the small models, reasoning is **disabled by default** and is switched on with `--chat-template-kwargs '{"enable_thinking":true}'`. On Windows PowerShell the argument is written `"{\"enable_thinking\":false}"` — [Unsloth Qwen3.5 docs](https://unsloth.ai/docs/models/qwen3.5)
- Unsloth's recommended sampling for Qwen3.5 [vendor-adjacent]:
  - Non-thinking, general: temp 0.7, top_p 0.8, top_k 20, presence_penalty 1.5
  - Thinking, precise coding: temp 0.6, top_p 0.95, top_k 20, presence_penalty 0
  - A chat-template fix improved tool calling for every Qwen3.5 GGUF.
  - Source: [Unsloth Qwen3.5 docs](https://unsloth.ai/docs/models/qwen3.5)
- Approximate memory (RAM+VRAM) at 4-bit: 0.8B/2B 3.5 GB, 4B 5.5 GB, 9B 6.5 GB, 27B 17 GB, 35B-A3B 22 GB. At 3-bit, 35B-A3B needs 17 GB — [Unsloth Qwen3.5 docs](https://unsloth.ai/docs/models/qwen3.5)
- **Vendor benchmark table from the Qwen3.5-2B card** (Qwen3-4B-2507 / Qwen3-1.7B / Qwen3.5-2B / Qwen3.5-0.8B):

  | Benchmark | Qwen3-4B-2507 | Qwen3-1.7B | Qwen3.5-2B | Qwen3.5-0.8B |
  |---|---|---|---|---|
  | BFCL-V4 (thinking) | 39.9 | – | 43.6 | 25.3 |
  | TAU2-Bench (thinking) | 43.2 | – | 48.8 | 11.6 |
  | IFEval (non-thinking) | 83.4 | 68.2 | 61.2 | 52.1 |
  | IFEval (thinking) | 87.4 | 72.5 | 78.6 | 44.0 |
  | MMLU-Pro (non-thinking) | 69.6 | 40.2 | 55.3 | 29.7 |
  | MMMLU (thinking, multilingual) | 70.8 | 57.0 | 63.1 | 44.3 |

  Source: [unsloth/Qwen3.5-2B-GGUF](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF) [vendor]
- Qwen3.5 architecture is a Gated DeltaNet hybrid: "6 × (3 × (Gated DeltaNet → FFN) → 1 × (Gated Attention → FFN))". The 2B has 24 layers and a dense FFN. License is Apache-2.0 — [unsloth/Qwen3.5-2B-GGUF](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF)
- **Qwen3.5-2B GGUF sizes (Unsloth):** Q4_K_M 1.28 GB, UD-Q4_K_XL 1.34 GB, Q5_K_M 1.44 GB, Q6_K 1.57 GB, Q8_0 2.01 GB. The mmproj size is not listed — [unsloth/Qwen3.5-2B-GGUF](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF)
- **Qwen3.5-9B GGUF sizes (bartowski):** Q4_K_M 6.17 GB, Q4_K_S 5.86 GB, IQ4_XS 5.50 GB, Q3_K_M 5.18 GB, Q5_K_M 7.11 GB. These were quantized with llama.cpp b9222. The repo includes MTP layers, which can be enabled with `--spec-type draft-mtp` on llama.cpp b9180 or later. License is Apache-2.0 — [bartowski/Qwen_Qwen3.5-9B-GGUF](https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF)
- An Unsloth Qwen3.5-9B MTP GGUF update says it made parsing of nested objects in tool calls more reliable — [unsloth/Qwen3.5-9B-MTP-GGUF](https://huggingface.co/unsloth/Qwen3.5-9B-MTP-GGUF)
- Independent BFCL "simple" accuracy on llama.cpp: Gemma 4 26B-A4B scored 0.945 and a Gemma 4 12B variant scored 0.950. The 12B also scored 0.961 on GSM8K [independent, single user] — [dev.to RTX 3050 article](https://dev.to/gde/can-a-26b-moe-run-on-an-8-gb-gpu-gemma-4-26b-a4b-on-an-rtx-3050-2efn)

### Inferences
- **Decider/router:** keep Qwen3-4B-Instruct-2507 Q4_K_M (~2.5 GB) as the primary, since it already scores 152/157. Its non-thinking IFEval (83.4) far exceeds Qwen3.5-2B (61.2), and the decider must produce strict typed JSON without thinking.
  - Run a shadow A/B against Qwen3.5-4B Q4_K_M (non-thinking) on the 157 cases.
  - Qwen3.5 is a GDN hybrid and is directly hit by the RDNA1 prompt-processing regression in llama.cpp #30039 (see Q6). On this build it may be slower to ingest prompts than its benchmarks suggest.
- **Fast fallback:** Qwen3.5-2B Q4_K_M (1.28 GB) is a better fallback than Qwen3-1.7B on tool calling (BFCL-V4 43.6, TAU2 48.8, both thinking) and on knowledge. It also adds vision. Its non-thinking IFEval (61.2) is lower than Qwen3-1.7B's 68.2, so validate it on the fixed cases first.
- **Small tool-calling agent model** (multi-step, under the API orchestrator): Qwen3.5-4B, or Qwen3.5-9B IQ4_XS (5.50 GB) with thinking on, are the strongest candidates that fit. A 9B at about 5.5 GB will not fit in 4 GB VRAM, so it runs partly on the CPU. Expect low single-digit to roughly 10 tok/s; this is unmeasured on this hardware.
- **Coder:** Qwen2.5-Coder-1.5B is dated, but it is still useful for fill-in-the-middle (FIM) completion. For agentic or explanatory coding, the general Qwen3.5-4B/9B (thinking, coding sampling) is a reasonable upgrade. I found no 2026 small dedicated coder model with evidence that it beats them.

### Gaps
- I found no vendor BFCL/TAU2/IFEval numbers in text form for Qwen3.5-4B or 9B. On the Unsloth docs they appear only as images.
- I found no independent BFCL leaderboard snapshot for October 2026.
- I did not research Granite-4.x, LFM2.5 or MiniCPM5 tool-calling scores (tool-call budget ran out). Treat them as unverified candidates.
- I found no independent coding benchmark for small 2026 models on llama.cpp.

---

## Q2. Big MoE models (gpt-oss-20b, Qwen3.5/3.6-35B-A3B, Gemma-4-26B-A4B) on 4 GB VRAM + 32 GB RAM with expert offload: measured tok/s and disk sizes

### Takeaway
I found no measurement on a 4 GB card. The closest data points are 8–12 GB GPUs running with most experts in RAM (24–39 tok/s), plus CPU-only Gemma-4-26B-A4B at about 7 tok/s on a 32 GB desktop. Disk is the binding constraint here:
- Qwen3.5/3.6-35B-A3B at Q4 is about 20–22.7 GB and **does not fit in 19 GB free**.
- Gemma-4-26B-A4B QAT Q4_0 (14.4 GB) and gpt-oss-20b (about 12 GB, unverified) fit, but only one of them alongside the small roster.

### Cited Findings
- **Gemma-4-26B-A4B on an RTX 3050 8 GB** (i7-5820K, quad-channel DDR4), [independent]:
  - File: `google/gemma-4-26B-A4B-it-qat-q4_0-gguf`, 14.4 GB, of which 12,251 MiB is expert weights and 1,505 MiB is everything else.
  - Generation speed by number of expert layers held in RAM (`--n-cpu-moe` sweep):

    | Expert layers in RAM | 30 | 24 | 20 | 18 | 17 |
    |---|---|---|---|---|---|
    | Generation, tok/s | 24.35 | 27.66 | 30.65 | 32.27 | 33.31 |

  - Prompt processing was 264–408 tok/s across the sweep.
  - A 252 MB exact Q4_0 MTP drafter gave a 1.25x speedup.
  - Served command: `-ngl 99 -ncmoe 21 -c 131072 -ctk q4_0 -ctv q4_0 -fa 1 -t 8 -tb 12 --reasoning off`.
  - Generation falls from 28.2 to 14.2 tok/s (no drafter) as context grows from 0 to 98k.
  - Source: [dev.to](https://dev.to/gde/can-a-26b-moe-run-on-an-8-gb-gpu-gemma-4-26b-a4b-on-an-rtx-3050-2efn)
- Gemma-4-26B-A4B CPU-only on an i5-8500 with 32 GB RAM: about 7 tok/s. A DDR3 Xeon estimate puts it at about 5 tok/s (secondary source) — [kunalganglani.com](https://www.kunalganglani.com/blog/gemma-4-cpu-inference-benchmark)
- Gemma-4-26B on a GTX 1080 (8 GB): about 20 tok/s at `--n-cpu-moe 20` with 128k context — [dev.to mdda](https://dev.to/mdda/running-gemma-4-26b-on-an-old-gtx-1080-with-llamacpp-4fi5)
- Upstream llama.cpp reached 30.10 t/s tg128 with n_cpu_moe 30 on Gemma-4-26B-A4B. ik_llama.cpp's token generation with CPU expert offload was about 4.4x slower in that report, so stay on upstream — [ik_llama.cpp #1765](https://github.com/ikawrakow/ik_llama.cpp/issues/1765)
- **gpt-oss-20b:** an RTX 5060 8 GB with 32 GB RAM and `--n-cpu-moe 18` reached 33–35 tok/s [independent] — [hindsight discussion #2525](https://github.com/vectorize-io/hindsight/discussions/2525). The official llama.cpp guide gives example commands with 22 layers on the CPU for full context, or 16 for 32k — [llama.cpp discussion #15396](https://github.com/ggml-org/llama.cpp/discussions/15396)
- **Qwen3.6-35B-A3B** (a newer release than Qwen3.5-35B-A3B):
  - On an RTX 3060 12 GB: 38.9 tok/s with the Unsloth UD-Q4_K_M GGUF at **22.66 GB**, `-ncmoe 24`, 9.8 GB VRAM. The write-up recommends 32 GB or more of system RAM — [smeltcore](https://smeltcore.com/recipes/qwen3-6-35b-a3b-on-rtx-3060-38-9-tok-s-from-a-12gb-card-via-moe-expert-cpu-offload/)
  - Other estimates put Q4_K_M weights at about 20 GB — [willitrunai](https://willitrunai.com/blog/qwen-3-6-vram-requirements)
  - The Unsloth docs say Qwen3.5-35B-A3B needs 22 GB at 4-bit and 17 GB at 3-bit — [Unsloth](https://unsloth.ai/docs/models/qwen3.5)
- Issue #30039 reports the same RDNA1 prompt-processing regression on a Qwen3.6-35B-A3B MoE — [llama.cpp #30039](https://github.com/ggml-org/llama.cpp/issues/30039)

### Inferences
- **Disk:** 35B-A3B class (17–22.7 GB) is out unless the owner frees space. A 3-bit variant (about 17 GB) would leave almost nothing for the rest of the roster.
- **Choice of deep offline MoE:**
  - **gpt-oss-20b** (MXFP4, about 12 GB, Apache-2.0, strong tool-use format) is the best disk-efficient pick.
  - **Gemma-4-26B-A4B QAT Q4_0** (14.4 GB) is the stronger multilingual/vision option, at a 2.4 GB higher disk cost.
  - Pick one.
- **Expected speed on 4 GB RDNA1 (estimate, not measured):** with 4 GB, nearly all experts must sit in RAM. Use `--cpu-moe` or a high `--n-cpu-moe` with `-ngl 99`, so attention and shared weights go to the GPU.
  - The CPU-only Gemma-4 figure (~7 tok/s) is the floor.
  - The 8 GB results (24–33 tok/s with 17–30 layers in RAM) are the ceiling.
  - A realistic range is about 8–15 tok/s generation, depending on the laptop's RAM bandwidth (dual-channel DDR4 laptops are much slower than the quad-channel desktop above).
- **No quantized KV cache:** flash attention must stay off on this card. In llama.cpp, a quantized V cache requires flash attention, so the 4-bit KV trick in the Gemma recipe will not be available. Keep context modest (8k–16k) to fit KV in 4 GB alongside the dense layers. This is from my knowledge of llama.cpp behavior, not confirmed for these builds.
- **Scheduling:** run the deep MoE in an `exclusive: true` llama-swap group with a short TTL (e.g. 300 s), so it evicts the GPU LLMs and frees about 13–15 GB of RAM afterwards.

### Gaps
- No 4 GB-VRAM or AMD Vulkan measurement exists for any of these MoEs.
- The exact gpt-oss-20b MXFP4 GGUF size was not verified in this session. It is commonly about 12 GB; check the ggml-org repo listing.
- No laptop-RAM-bandwidth benchmarks.

---

## Q3. Best small vision-language models for screenshots and GUI grounding in llama.cpp

### Takeaway
The easiest path is the general Qwen3.5 small models, which are natively multimodal with an mmproj file. Qwen3.5-2B scores 54.5 on ScreenSpot-Pro (non-thinking) [vendor]. That is close to Qwen3-VL-4B's 59.5, at half the size, and it lets the fallback LLM also serve as the screen reader. Dedicated grounding models are stronger but larger, and I could not confirm GGUF/mmproj support for them in llama.cpp:
- UI-Venus-1.5-2B scores 57.7 and UI-Venus-1.5-8B scores 68.4.
- UI-Venus-2-9B scores 73.0 [vendor].

### Cited Findings
- ScreenSpot-Pro [vendor]:

  | Model | ScreenSpot-Pro |
  |---|---|
  | Qwen3-VL-4B | 59.5 |
  | Qwen3-VL-2B | 48.5 |
  | Qwen3.5-2B (non-thinking) | 54.5 |
  | Qwen3.5-0.8B (non-thinking) | 46.5 |

  Source: [unsloth/Qwen3.5-2B-GGUF](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF)
- llama.cpp runs VLMs as a GGUF plus a separate mmproj projector, for example Qwen3-VL from Unsloth — [HF blog](https://huggingface.co/blog/transformers-llama-cpp-quants); [llama-ui discussion #16938](https://github.com/ggml-org/llama.cpp/discussions/16938)
- UI-Venus-1.5 ScreenSpot-Pro: 2B 57.7%, 8B 68.4%, 30B-A3B 69.6% [vendor report] — [arXiv 2602.09082](https://arxiv.org/pdf/2602.09082); summary table at [perea.ai](https://www.perea.ai/research/gui-grounding-models-2026)
- UI-Venus-2-9B scores 73.0% and Holo2-30B-A3B scores 66.1% on ScreenSpot-Pro. Only 9B and 27B UI-Venus-2 sizes exist — [inclusionAI/UI-Venus GitHub](https://github.com/inclusionAI/UI-Venus); [arXiv 2609.00028](https://arxiv.org/html/2609.00028v1)
- Licenses: UI-Venus-1.5-8B is Apache-2.0 and Holo2 is CC-BY-NC (non-commercial). UI-Venus-2-9B's license was unchecked — [zaxby-hyperv-mcp issue #46](https://github.com/zaxbysauce/zaxby-hyperv-mcp/issues/46)
- UI-TARS-1.5 is vendor-reported at 61.6% ScreenSpot-Pro, with third-party reproductions around 42–49%. Evaluation setup can shift scores by 5–15 points — [perea.ai](https://www.perea.ai/research/gui-grounding-models-2026)
- GUI-Owl-1.5 is recommended in one blog as an MIT-licensed grounding model (no score found) — [ruoqijin.com](https://ruoqijin.com/blog/mac-auto-bridge-cloud-ocr)
- Gemma-4-E4B has a vision projector `mmproj-gemma4-e4b-it-f16.gguf`. Its Q4_K_M is about 5.3 GB (sources vary, about 4.98–5.3 GB) — [AtomicChat GGUF](https://huggingface.co/AtomicChat/gemma4-e4b-it-GGUF); [Jetson AI Lab](https://www.jetson-ai-lab.com/models/gemma4-e4b)

### Inferences
- **Screen understanding role:** Qwen3.5-2B Q4_K_M plus its mmproj (about 1.3 GB plus the projector, likely several hundred MB, unverified) gives GUI description and coarse grounding on the GPU.
- For precise click targets, UI-Venus-1.5-2B (Apache-2.0, Qwen-VL-derived) is the best small candidate if a GGUF and mmproj conversion works in llama.cpp. That needs testing.
- Avoid Holo2 for anything beyond personal use (CC-BY-NC).
- Prefer Windows UI Automation or accessibility trees first, and use the VLM only as the fallback. Screenshots produce many image tokens, so prompt-processing speed matters, and RDNA1 prompt processing is the regressed path (#30039).

### Gaps
- I did not verify GGUF/mmproj availability for UI-Venus-1.5-2B, GUI-Owl, Holo2 or UI-TARS. No llama.cpp coordinate-output accuracy reports were found.
- mmproj file sizes for Qwen3.5 and Gemma-4-E4B were not found.
- SmolVLM and GUI-Actor were not researched.

---

## Q4. Small LLMs that handle Hindi and Telugu

### Takeaway
The evidence is thin, and it is weakest for Telugu. Sarvam-30B has an official GGUF, but it is a 30B model, scored only 33% on Telugu in a field test (versus 83% on Hindi), and is too big for the disk budget next to a deep MoE. An independent arXiv comparison found Gemma 4 31B led Sarvam-30B in every Indian language. Gemma-family models (Gemma-4-E4B locally, Gemma-4-26B-A4B as the deep model) are the best-supported small multilingual choice. The API model should handle high-stakes Telugu.

### Cited Findings
- `sarvamai/sarvam-30b-gguf` is the official GGUF. It claims state-of-the-art performance across 22 Indian languages for its size [vendor] — [HF](https://huggingface.co/sarvamai/sarvam-30b-gguf)
- Field test, official Q4_K_M on llama.cpp, thinking off, offline crop-advisory questions [independent]:

  | Language | Pass rate |
  |---|---|
  | Hindi | 83% |
  | Marathi | 83% |
  | Tamil | 82% |
  | Kannada | 58% |
  | Punjabi | 36% |
  | Telugu | 33% |

  Source: [shivanathd/sarvam-field-test](https://github.com/shivanathd/sarvam-field-test)
- IndicParam (12 low-resource Indian languages): Sarvam-30B scored 30.8% vs Gemini 2.5 Flash 58.0% (secondary write-up) — [Medium](https://medium.com/@indiai/india-first-llms-on-indian-languages-sarvam-30b-and-param2-17b-6676b637f3ab)
- Gemma 4 31B leads Sarvam-30B in every language (56.9–66.9% vs 33.2–48.7%). Most Indic results are developer-reported and use differing suites — [arXiv 2608.11891](https://arxiv.org/pdf/2608.11891); see also [arXiv 2608.15535 IndicQuest v2](https://arxiv.org/pdf/2608.15535)
- Qwen3.5-2B scores 63.1 on MMMLU (thinking), vs Qwen3-4B-2507 at 70.8 [vendor, not Indic-specific] — [unsloth/Qwen3.5-2B-GGUF](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF)

### Inferences
- **Recommendation:** Gemma-4-E4B Q4_K_M (about 5 GB, vision included) as the local Hindi/Telugu conversational model, used for chat and translation drafts. Escalate Telugu to the API model. Skip Sarvam-30B unless Hindi-specific quality is critical and disk space is freed.
- Gemma-4-E4B is listed as a dense "effective 4B". Its 5 GB file exceeds 4 GB VRAM, so use partial offload (`-ngl` tuned) and expect a mid-single-digit to roughly 10 tok/s generation (estimate).

### Gaps
- No Telugu benchmark for Gemma-4-E4B, Qwen3.5-4B or Krutrim was found.
- Krutrim was not researched.
- The exact parameter/active-parameter split for the Gemma-4-E4B "effective" design was not verified.

---

## Q5. llama-swap configuration: groups, matrix, TTL, hooks, and keeping tiny models resident

### Takeaway
llama-swap supports per-model `ttl`, a global `globalTTL`, and `groups` with `swap`/`exclusive`/`persistent`/`members`. A newer `matrix` router has `vars`/`sets`/`evict_costs` with a boolean DSL (`|`, `&`). `hooks.on_startup.preload` loads models at startup. To keep tiny models resident, put them in a `persistent: true, swap: false, exclusive: false` group with `ttl: 0` and preload them. Put GPU LLMs in a `swap: true, exclusive: false` group, and the deep MoE in an `exclusive: true` group.

### Cited Findings
All quoted from `docs/config.example.yaml` in [mostlygeek/llama-swap](https://raw.githubusercontent.com/mostlygeek/llama-swap/main/docs/config.example.yaml); the file moved from the repo root.

- **`ttl`** (per model): "automatically unload the model after ttl seconds"; `-1` = use global, `0` = never unload.
- **`globalTTL`**: default 0 (never auto-unload).
- **`unloadTimeout`**: default 10 s.
- **`healthCheckTimeout`**: default 120, minimum 15.
- **`startPort`**: default 5800, used by the `${PORT}` macro.
- **`macros`**: support `${env.VAR}` and nested macros.
- **Per-model keys**: `cmd` (multi-line with `|`, comments stripped), `cmdStop`, `proxy`, `aliases`, `env`, `unlisted`, `stripParams`, `useModelName`, `concurrencyLimit`, `sendLoadingState`.
- **Other global keys**: `globalConcurrencyLimit`, `maxRequestBodyMB` (default 250), `includeAliasesInList`, `captureBuffer`, `metricsMaxInMemory`, plus `performance`, `store`, `ui`, `apiKeys`, `security`, `upstream`, `profiles`, `selectors`, `routing` (incl. `scheduler`), `peers` (not quoted in detail).
- **Groups** (under `routing`, default engine `use: group`):
  - `swap` (default true; true = only one member runs at a time)
  - `exclusive` (default true; true = running a member unloads every other group)
  - `persistent` (default false; other groups cannot unload this group's members)
  - `members` (required)
- **Matrix** (under `routing.router.settings`):
  - `vars` map short names to model IDs.
  - `evict_costs` give the relative cost of losing a running model (default 1).
  - `sets` are named combinations that may run together, e.g. `standard: "(g | qwen-model | m) & v"` and `with_rerank: "(g | q) & v & e"`.
- **Hooks**:
  ```yaml
  hooks:
    on_startup:
      preload:
        - "llama"
  ```
  "when preloading multiple models at once, define a group otherwise models will be loaded and swapped out".

### Inferences
Suggested layout. Exact key nesting under `routing:` must be checked against v261's own `config.example.yaml`, since this was read from `main`.

```yaml
globalTTL: 0
healthCheckTimeout: 180
macros:
  srv: 'D:\llama\llama-server.exe --port ${PORT} --no-webui'
  gpu: '-ngl 99 -fa off'
  cpu: '-ngl 0 -fa off --threads 6'
models:
  verdict:   { cmd: "...", ttl: 0 }            # 151M classifier, CPU
  embed:     { cmd: "${srv} ${cpu} -m Qwen3-Embedding-0.6B-Q8_0.gguf --embedding --pooling last", ttl: 0 }
  rerank:    { cmd: "${srv} ${cpu} -m bge-reranker-v2-m3-Q8_0.gguf --reranking", ttl: 0 }
  guard:     { cmd: "${srv} ${cpu} -m Qwen3Guard-Gen-0.6B-Q8_0.gguf -c 4096", ttl: 0 }
  decider:   { cmd: "${srv} ${gpu} -m Qwen3-4B-Instruct-2507-Q4_K_M.gguf -c 8192", ttl: 0 }
  fast:      { cmd: "${srv} ${gpu} -m Qwen3.5-2B-Q4_K_M.gguf --mmproj mmproj-Qwen3.5-2B-F16.gguf -c 8192", ttl: 600 }
  planner:   { cmd: "${srv} ${gpu} -m Ling-3.0-tiny-Q4_K_M.gguf -c 8192", ttl: 600 }
  coder:     { ..., ttl: 600 }
  math:      { ..., ttl: 300 }
  indic:     { cmd: "... gemma-4-E4B-it-Q4_K_M.gguf --mmproj ...", ttl: 300 }
  deep:      { cmd: "${srv} -ngl 99 --cpu-moe -fa off -c 8192 -m gpt-oss-20b-mxfp4.gguf", ttl: 300 }
routing:
  groups:
    resident_cpu: { swap: false, exclusive: false, persistent: true, members: [verdict, embed, rerank, guard] }
    gpu_llm:      { swap: true,  exclusive: false, members: [decider, fast, planner, coder, math, indic] }
    deep:         { swap: true,  exclusive: true,  members: [deep] }
hooks:
  on_startup:
    preload: [verdict, embed, rerank, guard]
```

- **Why this layout:** the resident CPU group costs about 2–3 GB of RAM in total and is never evicted. GPU LLMs swap among themselves, one at a time, because 4 GB VRAM holds only one 2.5–3 GB model plus KV cache. The deep MoE evicts the GPU group.
- **Keeping the decider warm:** in the group model, the decider sits in the swap group, so a coder request unloads it. Alternatively, use the matrix with `evict_costs: {decider: 20}` and sets like `"decider & (embed) & ..."`. Note that the decider and the planner cannot share 4 GB of VRAM, so they must swap anyway (estimate).
- Note the docs' warning: preloading several models requires them to be in one group, or they will swap each other out.

### Gaps
- The v261 changelog was not checked. The exact `routing:` wrapper versus legacy top-level `groups:` may differ by version.
- Whether reranking/embedding endpoints (`/v1/rerank`, `/v1/embeddings`) are proxied by v261 was not confirmed in this session.

---

## Q6. Vulkan on AMD RDNA1 in llama.cpp, 2026 caveats

### Takeaway
Issue #30039 is an open, unfixed regression on Windows/AMD Vulkan. It cuts prompt processing by about 41–54% on Qwen3.5 GDN-hybrid models and Qwen3.6-35B-A3B, starting between b10950 and b11010. Token generation is unaffected. The user's b11669/b11714 builds sit after the regression window.

### Cited Findings
- Reported on an RX 5700 XT 8 GB (gfx1010), Windows 10, Vulkan [independent bug report]:
  - Qwen3.5-9B with a 10,890-token prompt: prompt processing fell from 269.7 to 151.6 tok/s (about −45%). Token generation stayed at about 44 tok/s.
  - Bisect: b10950 (2026-09-14) to b11010 (2026-09-17), prompt processing 308.7 → 215.0.
  - Six Qwen3.5 models (4B/9B) showed drops of 41–54%, and Qwen3.6-35B-A3B showed the same pattern.
  - Suspected cause (a hypothesis): PR #28105 "vulkan: support sparse Flash Attention".
  - Others: an RX 6700 XT saw 276 → 129 on Windows, an Intel B580 was unaffected, and an RX 9070 report could not be reproduced on Linux.
  - Ruled out: `-ub 256`, context size and KV quantization.
  - Status: open, no fix. Maintainer 0cc4m tests only on Linux and suggested the Windows AMD driver as a possible cause.
  - Source: [llama.cpp #30039](https://github.com/ggml-org/llama.cpp/issues/30039)
- Background: RDNA1 Vulkan was historically similar in speed to ROCm (5600 XT), after an earlier test showed it at half speed — [debian-ai list](https://lists.debian.org/debian-ai/2025/01/msg00175.html)
- A separate RDNA3 Vulkan prompt-processing regression starting at b10781 was also reported — [weekly llama.cpp report](https://buttondown.com/weekly-project-news/archive/weekly-github-report-for-llamacpp-september-07-6184/)

### Inferences
- **Mitigations:**
  - Keep a pinned older build (≤ b10950) alongside the current one, and point llama-swap `cmd` macros for Qwen3.5/GDN models at it. Use the newer build only where newer architectures need it.
  - Use llama-swap macros (`latest-llama`-style) so each model can have its own binary.
  - Benchmark both builds with `llama-bench -p 2048 -n 128` per model.
- The regression mainly hurts long prompts (screenshots with a VLM, RAG context), not short decider calls.
- Original Qwen3 models (non-GDN, such as Qwen3-4B-2507) were not reported as affected. This is another reason to keep it as the decider.

### Gaps
- It is unconfirmed whether the regression affects non-GDN dense models, Gemma 4 or gpt-oss on RDNA1.
- No root cause or fix has been posted. No 4 GB RDNA1 numbers exist.

---

## Q7. Guard/safety classifier, embeddings and reranker (supporting roles)

### Takeaway
Qwen3Guard-Gen-0.6B (about 0.75B, Apache-2.0) has community GGUFs and does three-level (Safe/Controversial/Unsafe) classification over 9 categories. It suits a resident CPU guard. Keep Qwen3-Embedding-0.6B as planned, and add bge-reranker-v2-m3 (or Qwen3-Reranker-0.6B) on the CPU.

### Cited Findings
- Qwen3Guard-Gen-0.6B: a 751M-parameter safety classifier. It labels prompts or responses as Safe, Controversial or Unsafe, covers 9 categories, detects refusals, and is Apache-2.0 (third-party catalog) — [dev.co](https://dev.co/ai/llms/qwen3guard-gen-0-6b)
- Community GGUFs exist at Q4_K_M; they are not official and their loading was not verified — [QuantFactory](https://huggingface.co/QuantFactory/Qwen3Guard-Gen-0.6B-GGUF); [Hayanie](https://huggingface.co/Hayanie/Qwen3Guard-Gen-0.6B-GGUF)

### Inferences
- **Guard:** Qwen3Guard-Gen-0.6B Q8_0 (about 0.8 GB, estimate) on the CPU, resident. Run it on inbound web/email text before the agent sees it and on outbound actions. Keep the deterministic allowlist in code as the real control.
- **Reranker:** llama-server supports a `--reranking` mode for cross-encoder GGUFs such as bge-reranker-v2-m3 (about 0.6 GB at Q8). This is from my prior knowledge of llama.cpp and was not re-verified this session.
- **Embeddings:** keep Qwen3-Embedding-0.6B. Use the Q8_0 GGUF (about 0.6 GB) on the CPU, which is ample for personal-scale RAG.

### Gaps
- Official Qwen3Guard GGUF status, accuracy on llama.cpp, and Hindi/Telugu safety coverage were not verified.
- No 2026 comparison of small rerankers was found.

---

## Consolidated roster (all evidence above; sizes are GGUF on disk)

| # | Role | Model / file | Disk | Placement | License | Status |
|---|---|---|---|---|---|---|
| 1 | Decider/router | Qwen3-4B-Instruct-2507 Q4_K_M | ~2.5 GB | GPU, swap group (TTL 0) | Apache-2.0 | keep (152/157) |
| 2 | Fast fallback + screen VLM | Qwen3.5-2B Q4_K_M + mmproj | 1.28 GB + mmproj | GPU | Apache-2.0 | add, A/B first |
| 3 | Small tool agent / reasoning | Qwen3.5-4B Q4_K_M (or 9B IQ4_XS 5.50 GB) | ~2.7 GB est. / 5.5 GB | GPU / partial | Apache-2.0 | add, A/B |
| 4 | Planner/general | Ling-3.0-tiny (7.9B MoE, 1.3B active) | existing | GPU+CPU | per card | keep (~31 tok/s) |
| 5 | Coder (FIM) | Qwen2.5-Coder-1.5B | existing | GPU | Apache-2.0 | keep |
| 6 | Maths | Qwen2.5-Math-1.5B | existing | GPU | per card | keep, or use #3 with thinking |
| 7 | Hindi/Telugu + vision | Gemma-4-E4B-it Q4_K_M + mmproj | ~5.0–5.3 GB | partial GPU | Gemma terms | add (optional) |
| 8 | Deep offline MoE | gpt-oss-20b MXFP4 **or** Gemma-4-26B-A4B QAT Q4_0 | ~12 GB / 14.4 GB | `-ngl 99 --cpu-moe` | Apache-2.0 / Gemma | add one |
| 9 | GUI grounding (optional) | UI-Venus-1.5-2B (if GGUF works) | ~1.5–2 GB est. | GPU | Apache-2.0 | test |
| 10 | Typed decisions | OpenJev Verdict 151M | existing | CPU resident | – | keep |
| 11 | PII | GLiNER-PII small | existing | CPU (not llama.cpp) | – | keep |
| 12 | Embeddings | Qwen3-Embedding-0.6B Q8_0 | ~0.6 GB | CPU resident | Apache-2.0 | add |
| 13 | Reranker | bge-reranker-v2-m3 Q8_0 | ~0.6 GB | CPU resident | MIT (verify) | add |
| 14 | Guard | Qwen3Guard-Gen-0.6B Q8_0 | ~0.8 GB | CPU resident | Apache-2.0 | add |
| 15 | Claim checker | MiniCheck-RoBERTa-L / nli-deberta-v3-xsmall | existing | CPU (not llama.cpp) | – | keep |

- **Disk:** new additions are roughly 2.7 (Qwen3.5-4B) + 1.9 (Qwen3.5-2B and mmproj) + 5.3 (Gemma-4-E4B) + 12 (gpt-oss-20b) + 2 (embedding, reranker, guard) ≈ **24 GB**, which exceeds 19 GB free.
  - Option 1: drop Gemma-4-E4B (about 19 GB, too tight).
  - Option 2: choose the deep model as Gemma-4-26B-A4B (14.4 GB, which covers Indic and vision) and skip E4B and gpt-oss (about 21 GB). Still tight.
  - Most realistic: deep = gpt-oss-20b, no Gemma, Indic via the API. Or free space by deleting Qwen3-1.7B and Qwen2.5-Math once Qwen3.5-2B/4B pass the A/B.
  - All of these totals are [inference] arithmetic.
- **Memory budget:**
  - VRAM 4 GB holds one GPU LLM at a time.
  - The resident CPU set uses about 3 GB of RAM.
  - The deep MoE uses about 13–15 GB of RAM while active.
  - About 32 GB of RAM total leaves room for the OS and apps, but only if the deep model is exclusive and has a short TTL.
