# Saved-reel intake (owner, 2026-10-26)

Captions of 14 saved Instagram reels, judged against JARVIS rules (local-only run time, D50 budget
20% CPU / 6554 MB RAM / 2048 MB VRAM on D:, no unvetted third-party code, no protected-server changes).
"Checked" = vendor page read on 2026-10-26; "caption only" = nothing beyond the reel text, treat as a claim.

| # | Item | Status | Verdict |
|---|------|--------|---------|
| 2 | Baidu Unlimited-OCR | checked (github.com/baidu/Unlimited-OCR) | Skip. MIT, but CUDA-only example (`.cuda()`), no CPU or GGUF path documented, no numbers on the page; a commenter reports it failed their benchmark. doc_read stays LightOnOCR. |
| 6 | Nemotron 3.5 Parse / Docling / LightOnOCR 3 | caption only (one creator's test on a Mac) | Supports the existing doc_read choice (LightOnOCR). Its speed there (3 min 40 s/page) is not our laptop: measure ours. |
| - | LiteParse (reel 6 comment) | checked (github.com/run-llama/liteparse) | ADOPT-CANDIDATE. Apache-2.0, no ML model, PDFium text extraction ~2-5 ms/page, local. Use as the fast path for DIGITAL PDFs before any OCR model; scans still go to LightOnOCR. Vet first: pin version, read dependency list, run on D: only. |
| 9 | Google Harness² | secondary coverage only (primary paper/repo not read) | IDEA ONLY. Propose-Probe-Compose: try alternative setups, compare traces, keep what wins, carry lessons forward. Maps to the nightly self-test and site playbooks (improvement steps 5-6). Do not install: its executors are OpenCode/Codex and it spends model calls. |
| 7 | Spark-X2.6-4B | only an aggregator page found | BENCHMARK LATER, not adopt. Official card not found; X2.5-4B is ~4.1B, ~7.7 GB BF16 (too big for 2 GB VRAM unless quantized). Candidate for ui_decide vs Qwen3-4B only after the official card + license are read. |
| 8 | Google ARTEMIS (Android) | checked (github.com/google/artemis) | NOT NOW. Android phones only; Apache-2.0. Useful pattern: fast observe-act profile vs planner/operator/checker profile, same shape as D65. Revisit if the owner wants phone control. |
| 10 | KittenTTS 2 | checked (github.com/KittenML/KittenTTS) | Skip. No Telugu (Hindi yes), v2 voice cloning needs ~6 GB RAM and a separate community licence. |
| 11 | PrismML 27B at 5.9 GB | caption only; commenters report ~0.2 tok/s | Skip. Does not fit the 2 GB VRAM budget. |
| 1 | NVIDIA Kumo Tabular | checked (github.com/NVIDIA/structured-data-models, build.nvidia.com model card, 2026-10-26) | PARKED, real but not needed yet. Weights OpenMDW-1.1, code Apache-2.0, 28M-215M params, predicts a class or number from labelled rows with no training. Repo page gives no hardware figures and its examples use CUDA; NVIDIA's #1 TabArena ranking is its own, independent reproduction pending, pretraining data/recipe not released. Possible later use: predict from JARVIS's own run logs (which provider/model/strategy succeeds, task duration) for auto routing, but only if a plain baseline (rules / logistic regression) is beaten on held-out rows. |
| 3 | Cindy | not checked | Skip (competing agent; unverified). |
| 4, 5 | CDA, Octopus-SI-OS | caption only | Skip (no usable detail). |
| 12 | Agency Agents (200+ prompt personas) | caption only | Skip for JARVIS: the face prompt budget is ~3.8 KB, and these are coding-assistant prompts. |
| 13 | Awesome Free Models | caption only | Skip. Free tiers that train on or review prompts are rejected by our provider policy. |
| 14, 6b | Hermes add-ons (GBrain, Agent Reach, Open Design, Studio, N2 via OpenRouter) | caption only | Skip. hermes-agent-vm is protected; unvetted skills are supply-chain risk. |
