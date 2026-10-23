# Hardware-aware local model plan for the two laptops (draft, 2026-10-19)

**Status: a PLAN. Nothing in this document was run on either laptop.** It was prepared on the Linux test server (CPU only). No production
routing, model lock, provider configuration or default model was changed, no model was downloaded for it, and no model server was
installed. Everything marked *measured on the laptop* is a figure that was recorded in this repository on 2026-10-15/16 (see
`MODEL_MATRIX.md`, `PC_CONTROL_BENCHMARK.md`, `03_DECISIONS.md`); it was **not** re-run in this session. Everything marked *estimate*
is arithmetic from the GGUF headers and must be confirmed on the target.

Inputs reviewed: `/srv/jarvis/models/LIBRARY.md` + `LIBRARY.json` (manifest of the 16 new files, hashes verified at download), the GGUF
headers of every model on the server (`/srv/jarvis/trials/model-plan-2026-10/gguf-facts/gguf_facts.json`, read-only parser, no model is
loaded), the router trial report (84 labelled requests) and the model-library playground report (47 tasks), the profiles in
`jarvis/local_models/`.

## 1. The two targets (do not treat them as one configuration)

| | MacBook Pro 16" 2019 | ThinkPad P1 |
|---|---|---|
| CPU / RAM | i9-9980HK, 8c/16t, AVX2, 32 GB | Xeon E-2176M, 6c/12t, AVX2, 32 GB |
| GPU | AMD Radeon Pro 5500M, 4 GB (Navi 12, RDNA1) | NVIDIA Quadro P2000 Max-Q, 4 GB (Pascal, compute capability 6.1, no tensor cores) |
| OS | Windows 10 under Boot Camp (current JARVIS target); macOS available | Ubuntu |
| Backend recorded in the repo | **Vulkan** llama.cpp b11669 with `-fa off` (flash attention gave garbage after the first token on this GPU, 2026-10-15); GPU shows 4,080 MiB, about 3.4 GB free | **none recorded** (this laptop is not mentioned anywhere in the repo) |
| Backend to evaluate | Vulkan (known to work); macOS Metal on an Intel Mac with a discrete AMD GPU: **UNVERIFIED, do not assume**; ROCm/HIP: not expected to support this GPU, **UNVERIFIED**; CUDA: **not applicable** | CUDA build (check that the installed toolkit/driver still supports compute capability 6.1: newer CUDA toolkits may drop Pascal, **UNVERIFIED**); Vulkan as a second option; flash attention on/off must be measured (Pascal has no tensor cores) |
| Target for deployment | to be confirmed by the owner before any lock/profile change | to be confirmed by the owner |

Consequences: a configuration that works on one laptop is not evidence for the other; GPU numbers must be recorded per machine, per OS and
per backend; Linux CPU-only numbers (this server) are never merged with laptop GPU numbers.

## 2. Model inventory and compatibility (from the manifest and the GGUF headers)

Architecture facts below are read from the file headers. "Loaded OK on Linux" means: the model loaded and generated in the **CPU build**
of llama.cpp b11714 during the trials; a GPU backend (Vulkan, CUDA) may lack a kernel for a given architecture, so every model must be
smoke-tested on each target backend (Qwen3.5 and LFM2 are hybrid architectures with linear-attention/convolution layers; Qwen3-VL and the
SmolVLM2 need a separate projector file).

| Model (Q4_K_M unless noted) | Role | Arch | Weights GiB | Licence | Loaded OK on Linux CPU | Notes |
|---|---|---|---|---|---|---|
| Qwen3-4B-Instruct-2507 | **reference baseline**, `decider` | qwen3 | 2.33 | Apache-2.0 | yes (and on the laptop, recorded) | keep until a fair comparison beats 152/157 |
| Qwen3-1.7B | low-memory decider, router/fallback candidate | qwen3 | 1.03 | Apache-2.0 | yes (and on the laptop, recorded) | prompt-routed 84.5% on the 84-item set |
| Qwen3-0.6B | tiny general | qwen3 | 0.37 | Apache-2.0 | yes | useless as a prompt router (17/47) |
| Qwen3.5-2B / 4B | general / tool choice | qwen35 (hybrid) | 1.19 / 2.55 | Apache-2.0 | yes | the repo measured the 4B at 10.7 tok/s on Vulkan |
| Qwen2.5-Coder-1.5B | code | qwen2 | 1.04 | Apache-2.0 | yes | in use (80 tok/s on the laptop, recorded) |
| Qwen2.5-Math-1.5B | maths | qwen2 | 0.92 | Apache-2.0 | yes | in use (96 tok/s, recorded); train context only 4096 |
| SmolLM3-3B, SmolLM2-1.7B | general | smollm3, llama | 1.78, 0.98 | Apache-2.0 | yes | SmolLM2 has 32 KV heads (large KV cache) |
| Phi-4-mini-instruct | general/reasoning | phi3 | 2.32 | MIT | yes | weak on Indic (2/6) |
| granite-4.2-3b | general | granite | 2.09 | Apache-2.0 | present, not in the trials | |
| LFM2.5 (350M/1.2B/2.6B/8B-A1B) | general | lfm2, lfm2moe | 0.21–4.8 | "other" (LFM licence: **read it** before any lock) | yes | hybrid architecture: check GPU kernels |
| **Arch-Router-1.5B** | experimental router | qwen2 | 0.92 | **Katanemo Community Licence** (Llama-3.2-based; commercial use needs a separate DigitalOcean licence) | yes | **not for the lock or any distribution** until the licence is settled |
| Qwen3-VL-2B + mmproj Q8 | vision-language | qwen3vl + clip | 1.03 + 0.41 | Apache-2.0 | yes (CPU) | misread digits once; projector on CPU recommended on 4 GB |
| SmolVLM2-500M-Video Q8 + mmproj Q8 | tiny vision, experimental | llama + clip | 0.41 + 0.10 | Apache-2.0 | yes (CPU) | 3/3 on tiny images; experimental |
| bge-m3 Q8 | multilingual embeddings | bert | 0.59 | MIT | yes | 8/8 retrieval at ~0.1 s on CPU |
| bge-reranker-v2-m3 | reranker | bert | 0.41 | Apache-2.0 | yes | on the top 5 only |
| bge-small-en-v1.5 Q8 | English embeddings | bert | 0.03 | MIT | yes | missed the Hindi query |
| multilingual-e5-small Q8 | small multilingual embeddings | bert | 0.12 | MIT (base model; the GGUF repo states none) | yes | 3/8 here, setup may be at fault |
| Ling-3.0-tiny | current generalist/router (laptop) | MoE | 4.49 GB file | MIT | **not on the Linux server** | measured on the laptop only; see section 7 |

Skipped for licence or availability reasons (see the manifest): Sarvam family (no licence stated / GPL-3.0 / too big), IndicTrans2 (no GGUF),
Navarasa Indic-gemma (Gemma terms), Gemma and Llama models (non-permissive), UI-Venus-1.5-2B (already in the lock, 2.1 GB, laptop only).
There is **no permissive small Indic GGUF**: for Hindi/Telugu/Hinglish the options are the Qwen3 family (Qwen3-4B-Instruct-2507 was the best
in the playground, and still failed some Telugu/Hindi factual questions).

## 3. Memory model (estimates; confirm with llama-server's own log)

For one model on the GPU: `weights + KV cache + runtime buffers`. KV cache (f16) = `layers × context × kv_heads × (key_len + value_len) × 2 bytes`.
Runtime buffers (compute graph, Vulkan/CUDA context) are assumed 0.2–0.5 GiB here; they must be measured. Calibration against a recorded laptop
measurement: Qwen3-4B-Instruct-2507, context 2048 → estimate 2.33 + 0.28 = 2.61 GiB before buffers; the repo recorded 2.6 GB of GPU memory
(`PC_CONTROL_BENCHMARK.md`), so the buffer assumption is conservative for that case. Usable VRAM recorded on the MacBook under Boot Camp:
about 3.4 GB free of 4,080 MiB (other applications included). For the P2000 the free figure is **unknown** (display/compositor use must be read
with `nvidia-smi`).

| Model | weights GiB | KV f16 @2048 | KV f16 @4096 | est. total @2048 | Fits 3.4 GB free? |
|---|---|---|---|---|---|
| Qwen3-4B-Instruct-2507 | 2.33 | 0.28 | 0.56 | 2.8–3.1 | tight but recorded as working at ctx 2048; **4096 is not safe** (3.1–3.4) |
| Qwen3.5-4B | 2.55 | ≤0.25 (hybrid: upper bound) | ≤0.50 | ≤3.0–3.3 | tight; the repo measured it slow (10.7 tok/s) |
| Phi-4-mini | 2.32 | 0.25 | 0.50 | 2.8–3.1 | tight |
| granite-4.2-3b | 2.09 | 0.16 | 0.31 | 2.5–2.8 | yes |
| SmolLM3-3B | 1.78 | 0.14 | 0.28 | 2.1–2.4 | yes |
| Qwen3-1.7B | 1.03 | 0.22 | 0.44 | 1.5–1.8 | yes |
| Qwen3.5-2B | 1.19 | ≤0.09 | ≤0.19 | ≤1.5–1.8 | yes |
| Arch-Router-1.5B | 0.92 | 0.06 | 0.11 | 1.2–1.5 | yes (experimental only) |
| Qwen2.5-Coder-1.5B / Math-1.5B | 1.04 / 0.92 | 0.06 | 0.11 | 1.3–1.6 / 1.2–1.5 | yes |
| Qwen3-VL-2B + mmproj Q8 | 1.03 + 0.41 | 0.22 | 0.44 | 1.9–2.2 **plus image activations** | the repo's vision experience on this GPU: 1024 image tokens failed ("failed to process mtmd chunk"), 512 worked once then failed when other apps held VRAM, so the projector ran on the CPU; **put the projector on the CPU** |
| SmolVLM2-500M + mmproj | 0.41 + 0.10 | 0.08 | 0.16 | 0.8–1.1 + activations | probably; unverified |
| bge-m3 / bge-reranker (encoders) | 0.59 / 0.41 | none (no autoregressive KV) | none | ~1 GiB incl. buffers | run on the **CPU** (small, fast enough: ~0.1 s per query on 2 Linux threads) |

Rules that follow: one LLM on the GPU at a time (the others stay unloaded); context 2048 for the decider, never raised without re-measuring;
encoders and the router on the CPU unless a measurement shows a GPU gain; unload → verify that the GPU memory fell back (nvidia-smi on the
P1; the Windows GPU memory counters or llama-server's log on the MacBook) → load the next model.

## 4. Proposed allocation (proposal only; not applied)

The reference baseline stays Qwen3-4B-Instruct-2507 as `decider` and as the general fallback. Nothing below changes a route by itself; each
row is a candidate for the comparison in section 6.

| Role | MacBook Pro (Windows, Vulkan, `-fa off`) | ThinkPad P1 (Ubuntu) | Memory pressure | Open compatibility questions |
|---|---|---|---|---|
| Intent router, first line | current: rules → Verdict → Ling one-word (recorded 11/12 on the tuning set). Candidate: rules → Qwen3-1.7B constrained one-word, on **CPU** | candidate: same Qwen3-1.7B on CPU | ~1.5–2 GB RAM, 0 VRAM | does the Ling router stay needed? the Ling file (4.49 GB, MoE) was only run on the laptop |
| Router, experimental | Arch-Router-1.5B on CPU, **non-shipping**, local test only | same | ~1.5 GB RAM | licence; it never emitted valid JSON (single-quoted pseudo-JSON, needs a tolerant parser); longer prompt (~320 tokens) |
| Generalist / fallback / `decider` | Qwen3-4B-Instruct-2507, GPU, ctx 2048 (recorded) | same file, CUDA, ctx 2048 (**unmeasured**) | 2.6–3.1 GiB VRAM | P2000 free VRAM; Pascal FP16 speed; `-fa` on/off |
| Low-memory fallback | Qwen3-1.7B (recorded) | same | 1.5–1.8 GiB | none known |
| Code | Qwen2.5-Coder-1.5B GPU (in use) **only if it beats the 4B on a held-out code set** (playground code tasks: routed to the Coder 5/7, Qwen3-4B alone 6/7, Qwen3-1.7B alone 4/7; one failure was a tie-break/punctuation bug) | same | 1.3–1.6 GiB when loaded; replaces the 4B while loaded | swap cost per request (loads took 1.5–10.6 s warm in the Linux trial) |
| Maths | Qwen2.5-Math-1.5B **only if** it beats the 4B (on the playground it tied it, 6/6, with no latency gain) | same | 1.2–1.5 GiB | context 4096 train limit |
| Retrieval | bge-m3 Q8 on CPU; bge-reranker on the top 5 | same | ~1 GiB RAM, 0 VRAM | cosine thresholds per language |
| Vision | experimental only: SmolVLM2-500M or Qwen3-VL-2B with the projector on CPU; the existing UI-Venus-2B grounding model stays the recorded one | same, after a CUDA/Vulkan smoke test | 0.8–2.2 GiB + activations; never loaded together with the 4B | tokens per image (the 5500M failed at 512–1024), projector backend |
| Multilingual (Hindi, Telugu, Hinglish) | route to the 4B (best measured) | same | as the 4B | nothing permissive and small exists as a GGUF specialist |

Safe-fallback rules (apply to every router candidate; thresholds are fixed on the **tuning** set only):
1. **Vision is never chosen by a text classifier.** The vision route requires an attached image or screenshot flag set by the caller. A Hindi,
   Telugu or Hinglish request without an image can never reach a vision model (the untuned Arch-Router did send three such requests to vision).
2. Script/language detection (Devanagari / Telugu Unicode blocks, Roman-script Hindi markers) pins those requests to the 4B generalist.
3. An unknown, "other", malformed, low-margin or error route goes to the defined fallback (the 4B generalist; the 1.7B when RAM-constrained),
   fails closed, and is counted: **fallback frequency** is a reported metric.
4. A generation-based router reports a confidence (log-probability margin of the route token); an embedding router reports the cosine margin.
5. Local models stay bound to `127.0.0.1`; every request goes through the JARVIS kernel proxy (scrubbing, lanes, approvals, kill switch, audit).
   No local endpoint is exposed on another interface and no plugin may call a model endpoint directly to bypass the kernel.

## 5. Evidence status

| Item | Verified on Linux (CPU, this server) | Recorded on a real laptop (repo docs, not re-run now) | Planned | Not run |
|---|---|---|---|---|
| Models load and generate (CPU build b11714) | yes (all in the trials; hashes verified) | — | — | Vulkan/CUDA backends of the new models |
| Qwen3-4B decider quality | — | 65/70 dev, 27/30 held-out, 152/157 combined (MacBook, Vulkan) | re-run in the hardware plan | P1 |
| Qwen3-1.7B as decider | — | 58/70 dev, 82.9%, 1.0 s median (MacBook) | re-run | P1 |
| Ling-3.0-tiny as `decider` | — | 53/70 (75.7%), 2.87/6.13 s (MacBook); router 11/12 (0.94) on its tuning set | — | not on the server, so Linux router results use **stand-ins** (Qwen3.5-2B, LFM2.5-1.2B, Qwen3-1.7B) |
| Router accuracy (84 requests, 3 routes) | Arch-Router 92.9% (78/84), Qwen3-1.7B 84.5%, Qwen3.5-2B 78.6%, LFM2.5-1.2B 56.0%; wide intervals (Arch 85–97%) | — | held-out run | laptop |
| Router + specialists vs one model (47 tasks) | routed 37/44 text tasks, Qwen3-4B alone 42/44, Qwen3-1.7B alone 37/44; retrieval bge-m3 8/8 | — | held-out run | laptop |
| Latency / memory | Linux CPU, 1–2 threads, shared box (router median 7.3 s, p95 9.7 s; not comparable to a GPU laptop) | Vulkan: 4B decider 3.26 s median, GPU 2.6 GB / RAM 3.6 GB; Ling 31 tok/s; Coder 80 tok/s; Math 96 tok/s; llama-bench GPU 89.9 vs CPU 21.2 tok/s (small model) | measure all candidates per machine | thermals under sustained load (recorded as not measured), P1 |
| AMD Vulkan flash attention | — | broken on this GPU (`-fa off` required) | re-test on b11714 | — |
| macOS Metal on this Mac | — | — | decide whether macOS is a target at all | everything |
| NVIDIA P2000 CUDA/Vulkan | — | — | smoke test, `-fa` on/off | everything |
| Microphone / wake word / TTS / ASR | — | — | — | all |
| Real desktop (PC control, Windows UI Automation, Chrome profiles) | — | partly recorded in earlier laptop runs | `LAPTOP_VERIFICATION_2026-10.md` | all in this plan |

## 6. Comparison plan (repeatable; not a completed benchmark)

Candidates (each a full configuration, not just a model): **C0** Qwen3-4B-Instruct-2507 alone (reference); **C1** Qwen3-1.7B alone; **C2**
rules → Qwen3-1.7B router → {4B generalist, Coder, Math} (the Coder/Math arms only if their held-out numbers beat C0); **C3** current router
(rules → Verdict → Ling) where Ling is available (laptop); **C4** Arch-Router-1.5B as router, **non-shipping/experimental**; **C5**
retrieval: bge-m3 (± reranker) vs the LLM picking a passage; **C6** SmolVLM2-500M (and Qwen3-VL-2B) for image items, **experimental**.
Results that used substitute models are labelled "substitute" and never merged with Ling's own results.

Test set:
- **Tuning/dev set** (may be used to choose prompts, route descriptions, thresholds): the 47 playground tasks and the 84-item routing set. Both are
  already *contaminated*: the Arch-Router route descriptions were retuned on the 47 items, and 16 of the 84 are the repo's prompt-tuning set.
- **Final held-out set** (new, frozen before any model sees it): at least 160 items, stratified by language {English, Hindi (Devanagari),
  Telugu, Hinglish (Roman script)} × category {general, code with hidden unit tests, maths with numeric answers, memory/retrieval over a
  passage set, PC-task decision, image-related (synthetic images with known text/digits/shapes), ambiguous, multi-turn follow-up}, at least 5
  items per populated cell, including no-answer retrieval queries and near-duplicate distractor passages. Write the items without looking at any
  model output; record its SHA-256 and the date in the report before the first run; keep tuning and final files in separate directories; never
  re-tune after the final run. Scoring is deterministic (unit tests, numeric match, JSON schema + field match, top-1/top-k hit, route label).
- Labels for ambiguous items list the acceptable route set in advance.

Metrics recorded per candidate and per language/category: task success; route accuracy; invalid/malformed output rate; median and p95
latency (first token and total); peak system RAM and peak GPU VRAM (sampled every 0.5 s); model load and unload time and whether the GPU memory
returned to baseline; CPU utilisation, CPU temperature and clock (throttling), GPU temperature/clock; context length, quantisation, batch,
`-ngl`, `-fa`; fallback frequency; licence and distribution constraint. Run each configuration three times (determinism and timing spread), warm
and cold. Report intervals (Wilson for proportions) and paired differences (McNemar) against C0; with ~160 items a difference of 1–3 items is noise.

**Reporting rule:** three separate tables: (1) Linux CPU-only (this server), (2) MacBook Pro / Windows / Vulkan (and macOS only if it is made a
target), (3) ThinkPad P1 / Ubuntu / CUDA (or Vulkan). No combined headline score. The reference stays C0 until a replacement is better on the
held-out set **and** the safe-fallback behaviour is measured.

Reference harness from the Linux trials (kept outside the repository, **not tested on Windows or the P1**, scripts hard-code Linux paths and
must be adapted): `/srv/jarvis/trials/model-plan-2026-10/` (`playground/`, `router-trial/`, `gguf-facts/` with the header reader).

Commands to prepare for the first session (proposals; NOT RUN; paths follow the repository's own layout):
- Windows PowerShell, MacBook: `python jarvis\tools\fetch.py --check --machine laptop` (hash check, no download); then for each candidate
  `D:\jarvis\tools\llama.cpp\b11669\vulkan\llama-bench.exe -m <file> -ngl 99 -fa 0 -p 512 -n 128 -r 3` and `... -ngl 0` for CPU; load with
  `llama-server.exe --host 127.0.0.1 --port 18080 -m <file> -ngl 99 -fa off -c 2048 -np 1 --jinja`, read the memory lines of its log, and watch
  GPU memory with the Windows GPU counters or Task Manager; unload by stopping the process and re-reading free VRAM before the next load.
- Ubuntu, P1: `nvidia-smi --query-gpu=memory.used,memory.total,temperature.gpu,clocks.sm --format=csv -lms 500 > vram.csv &` during each run;
  CUDA build of `llama-bench`/`llama-server` with `-fa 0` and `-fa 1`; `sensors` or `turbostat` for CPU temperature/clock.
- Both: bind to `127.0.0.1` only; drive the models through the JARVIS kernel proxy for the end-to-end rows, and directly only for raw speed rows.

## 7. Licence and distribution constraints

| Model | Constraint |
|---|---|
| Arch-Router-1.5B | Katanemo Community Licence: commercial use needs a separate DigitalOcean licence; redistribution needs the licence text, "Built with DigitalOcean" and a notice file; acceptable-use policy. **Not added to `jarvis.lock.json`, not shipped.** Personal non-commercial testing only; not legal advice. |
| LFM2.5 family | licence "other" in the GGUF header: read the LFM licence before any lock entry or distribution. |
| Qwen3 / Qwen3.5 / Qwen2.5-Coder/Math / Qwen3-VL / SmolLM2/3 / granite / bge-reranker | Apache-2.0 |
| Phi-4-mini, bge-m3, bge-small, multilingual-e5 (base model) | MIT |
| Ling-3.0-tiny | MIT (recorded in the repo) |

## 8. Prioritised checklist for the first session on a laptop

Do these in order; stop and report at the first failure. Items needing hardware are marked.
1. **Decide the target** (MacBook/Windows, MacBook/macOS, ThinkPad/Ubuntu) and record OS, driver and backend versions. [owner]
2. **Pre-flight** (any machine): `git status` of the working tree (expect the uncommitted remediation changes), `fetch.py --check` for the
   models you will use, free disk, close other GPU applications; record the free VRAM with the usual desktop apps open. [MacBook: Windows GPU
   tooling; P1: `nvidia-smi`]
3. **Backend smoke test**, one tiny model first (Qwen3-1.7B), then the 4B: does it load, offload all layers, produce correct output, with `-fa`
   on and off? [real GPU: AMD+Vulkan on the MacBook, NVIDIA+CUDA/Vulkan on the P1] macOS/Metal only if macOS is a target.
4. **Reproduce the recorded baseline** on that machine: Qwen3-4B decider latency and VRAM at context 2048 (recorded: 3.26 s median, 2.6 GB). If
   it does not reproduce, fix the environment before measuring anything else. [real GPU]
5. **Load/unload cycle**: load 4B → unload → verify VRAM returns → load Coder → unload → load 4B; record times and any leak. [real GPU]
6. **Run the held-out set** for C0, C1, C2, C4 (Arch, experimental), C5, in the three-table format; CPU-only rows and GPU rows separately.
   Log CPU/GPU temperature and clocks through a sustained run (10–20 minutes). [real GPU, thermals]
7. **Vision experiments last** (SmolVLM2-500M, then Qwen3-VL-2B with the projector on CPU): image tokens per image, success/failure, VRAM.
   [real GPU]
8. **JARVIS integration checks that need the real machine** (see `LAPTOP_VERIFICATION_2026-10.md` for the full list): the restarted widget/wake/
   speech servers with the new control tokens; Stop during a PC task and the PC lock; the lab verdict channel (lab box); approval cards in the real
   console; OCR/screen-text opt-in with the real gateway. [Windows desktop, microphone, lab box]
9. **Write the report**: measured vs estimated, three separate tables, what failed, and the recommendation; update the lock only for
   models with a permissive, checked licence, and never for Arch-Router without an owner decision.

Which checks need what: **Windows** (Boot Camp Vulkan runs, PowerShell scripts, Windows UI Automation, Chrome profiles, `msvcrt` PC lock);
**macOS** (Metal/AMD viability, the Mac secrets file and Tailscale); **NVIDIA P2000** (CUDA build, Pascal fp16/flash-attention behaviour,
free VRAM with the desktop running); **AMD 5500M** (Vulkan with `-fa off`, projector/image-token limits, thermals); **real microphone** (wake
word, ASR, barge-in, Hindi/Telugu stop words); **real desktop** (PC control, new-window detection, Stop). The Linux server can run none of these.
